from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.models import AiRun, AiWorkflow, MentionRequest, Message, TriggerEvent, now_utc
from app.dify.client import DifyClient
from app.profiles.reply_context import latest_reply_profile_payload
from app.wecom.mention_requests import mark_failed, mark_running, mark_succeeded


def run_dify_workflow_for_trigger(
    session: Session,
    *,
    trigger_event: TriggerEvent,
    message: Message,
    workflow: AiWorkflow,
    dify_client: DifyClient,
    mention_request: MentionRequest | None = None,
) -> AiRun:
    existing = session.scalar(
        select(AiRun).where(
            AiRun.trigger_event_id == trigger_event.id,
            AiRun.workflow_code == workflow.workflow_code,
            AiRun.workflow_version == workflow.version,
        )
    )
    if existing:
        if mention_request is not None:
            if existing.status == "success":
                mark_succeeded(session, mention_request, ai_run_id=existing.id)
            elif existing.status in {"running", "pending"}:
                mark_running(
                    session,
                    mention_request,
                    trigger_event_id=trigger_event.id,
                    ai_run_id=existing.id,
                )
            elif existing.status in {"failed", "invalid_output"}:
                mark_failed(
                    session,
                    mention_request,
                    error_message=existing.error_message,
                )
            session.commit()
        return existing

    input_json = build_trigger_workflow_input(
        session=session,
        trigger_event=trigger_event,
        message=message,
        workflow=workflow,
    )
    validate_json_schema(input_json, workflow.input_schema)
    run = AiRun(
        run_id=_new_run_id(),
        workflow_code=workflow.workflow_code,
        workflow_version=workflow.version,
        trigger_event_id=trigger_event.id,
        input_json=input_json,
        response_mode=workflow.response_mode,
        status="running",
        created_at=now_utc(),
        started_at=now_utc(),
    )
    session.add(run)
    session.flush()
    mark_running(
        session,
        mention_request,
        trigger_event_id=trigger_event.id,
        ai_run_id=run.id,
    )
    session.commit()

    started = perf_counter()
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        run.status = "success"
        run.error_message = None
        mark_succeeded(session, mention_request, ai_run_id=run.id)
    except JsonSchemaValidationError as exc:
        run.status = "invalid_output"
        run.error_message = exc.message
        mark_failed(session, mention_request, error_message=exc.message)
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        mark_failed(session, mention_request, error_message=str(exc))
    finally:
        run.latency_ms = int((perf_counter() - started) * 1000)
        run.finished_at = now_utc()
        session.commit()

    return run


def get_enabled_workflow(
    session: Session,
    *,
    workflow_code: str,
    version: str = "v1",
) -> AiWorkflow:
    workflow = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == workflow_code,
            AiWorkflow.version == version,
            AiWorkflow.enabled.is_(True),
        )
    )
    if not workflow:
        raise AppError(ErrorCode.NOT_FOUND, f"Workflow not found: {workflow_code}/{version}")
    return workflow


def build_trigger_workflow_input(
    session: Session,
    *,
    trigger_event: TriggerEvent,
    message: Message,
    workflow: AiWorkflow,
) -> dict[str, Any]:
    if workflow.workflow_code == "group_knowledge_reply":
        return build_group_knowledge_reply_input(
            session=session,
            trigger_event=trigger_event,
            message=message,
        )

    return build_legacy_reply_generation_input(
        trigger_event=trigger_event,
        message=message,
        response_mode=workflow.response_mode,
    )


def build_group_knowledge_reply_input(
    session: Session,
    *,
    trigger_event: TriggerEvent,
    message: Message,
) -> dict[str, Any]:
    user_profile = latest_reply_profile_payload(session, message.userid)
    profile_maps = {message.userid: user_profile} if message.userid and user_profile else {}
    payload = {
        "question": message.content_text or "",
        "message": {
            "message_id": str(message.id),
            "msgid": message.external_msgid,
            "chatid": message.chatid,
            "userid": message.userid,
            "content": message.content_text,
            "create_time": message.create_time.isoformat(),
        },
        "group": {
            "chatid": message.chatid,
            "chattype": message.chattype,
        },
        "recent_messages": [
            {
                "message_id": str(message.id),
                "msgid": message.external_msgid,
                "chatid": message.chatid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": message.create_time.isoformat(),
            }
        ],
        "user_profile": user_profile,
        "user_profiles": list(profile_maps.values()),
        "profiles_by_userid": profile_maps,
        "reply_profile_contexts": profile_maps,
        "image_summaries": [],
        "runtime": {
            "trigger_type": trigger_event.trigger_type,
            "workflow_code": "group_knowledge_reply",
        },
    }
    return {"payload": payload}


def build_legacy_reply_generation_input(
    *,
    trigger_event: TriggerEvent,
    message: Message,
    response_mode: str,
) -> dict[str, Any]:
    return {
        "reply_scene": "mention",
        "chatid": message.chatid,
        "source_msgid": message.external_msgid,
        "request_userid": message.userid,
        "target_userids": [],
        "user_message": message.content_text,
        "reply_instruction": "直接回应用户 @ 机器人的消息，基于输入上下文生成简洁回复。",
        "evidence_msgids": [message.external_msgid],
        "recent_messages": [
            {
                "msgid": message.external_msgid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": message.create_time.isoformat(),
            }
        ],
        "conversation_summary": None,
        "user_profile": None,
        "runtime": {
            "response_mode": response_mode,
            "reply_type": "markdown",
            "language": "zh-CN",
        },
    }



def ai_run_to_dict(run: AiRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "workflow_code": run.workflow_code,
        "workflow_version": run.workflow_version,
        "trigger_event_id": run.trigger_event_id,
        "input_json": run.input_json,
        "output_json": run.output_json,
        "response_mode": run.response_mode,
        "status": run.status,
        "latency_ms": run.latency_ms,
        "token_usage": run.token_usage,
        "error_message": run.error_message,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def _new_run_id() -> str:
    return f"airun_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _iso(value):
    return value.isoformat() if value else None
