from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.db.defaults import initialize_default_records
from app.db.models import AiRun, MentionRequest, Message, TriggerEvent, TriggerRule
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow, run_dify_workflow_for_trigger
from app.outbound.services import create_reply_outbox_for_ai_run
from app.triggers.schemas import TriggerEvaluateRequest
from app.wecom.mention_requests import mark_outbox_pending


def evaluate_triggers(
    session: Session,
    *,
    payload: TriggerEvaluateRequest,
    dify_client: DifyClient,
    mention_request_id: int | None = None,
) -> dict[str, Any]:
    message_id = payload.resolved_message_id()
    if message_id is None:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "message_id is required")

    message = session.get(Message, message_id)
    if not message:
        raise AppError(ErrorCode.NOT_FOUND, f"Message not found: {message_id}")

    if not message.mentioned_bot:
        return {"matched": False, "events": []}

    rules = session.scalars(
        select(TriggerRule)
        .where(
            TriggerRule.trigger_type == "mention",
            TriggerRule.enabled.is_(True),
        )
        .order_by(TriggerRule.priority.desc(), TriggerRule.id.asc())
    ).all()

    mention_request = (
        session.get(MentionRequest, mention_request_id)
        if mention_request_id is not None
        else None
    )
    events = []
    for rule in rules:
        event, duplicated = _get_or_create_trigger_event(session, rule, message)
        session.commit()
        workflow = get_enabled_workflow(session, workflow_code=rule.workflow_code)
        ai_run = run_dify_workflow_for_trigger(
            session,
            trigger_event=event,
            message=message,
            workflow=workflow,
            dify_client=dify_client,
            mention_request=mention_request,
        )
        outbox_result = create_reply_outbox_for_ai_run(session, ai_run)
        if outbox_result:
            mark_outbox_pending(
                session,
                mention_request,
                outbox_id=str(outbox_result["outbox_id"]),
            )
        event.status = "handled"
        session.commit()
        events.append(
            _event_result(
                event,
                ai_run,
                duplicated=duplicated,
                outbox_result=outbox_result,
            )
        )

    return {"matched": bool(events), "events": events}


def _get_or_create_trigger_event(
    session: Session,
    rule: TriggerRule,
    message: Message,
) -> tuple[TriggerEvent, bool]:
    existing = session.scalar(
        select(TriggerEvent).where(
            TriggerEvent.rule_code == rule.rule_code,
            TriggerEvent.message_id == message.id,
        )
    )
    if existing:
        return existing, True

    event = TriggerEvent(
        rule_code=rule.rule_code,
        trigger_type=rule.trigger_type,
        message_id=message.id,
        chatid=message.chatid,
        userid=message.userid,
        workflow_code=rule.workflow_code,
        reason={
            "mentioned_bot": True,
            "message_id": message.id,
            "content_text": message.content_text,
        },
        status="pending",
    )
    session.add(event)
    session.flush()
    return event, False


def _event_result(
    event: TriggerEvent,
    ai_run: AiRun,
    *,
    duplicated: bool,
    outbox_result: dict[str, Any] | None,
) -> dict[str, Any]:
    result = {
        "trigger_event_id": event.id,
        "trigger_type": event.trigger_type,
        "workflow_code": event.workflow_code,
        "duplicated": duplicated,
        "ai_run": {
            "run_id": ai_run.run_id,
            "status": ai_run.status,
        },
    }
    if outbox_result is not None:
        result["outbox"] = outbox_result
    return result
