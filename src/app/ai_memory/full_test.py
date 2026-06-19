from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.models import (
    AiRun,
    ConversationSegment,
    Message,
    UserProfile,
    UserProfileFact,
    now_utc,
)
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow

CHINA_TZ = timezone(timedelta(hours=8))


class AiMemoryFullTestValidationError(Exception):
    pass


@dataclass(frozen=True)
class DayWindow:
    start: datetime
    end: datetime


def run_ai_memory_full_test_once(
    session: Session,
    *,
    target_date: date,
    chatids: list[str] | None,
    run_profiles: bool,
    dify_client: DifyClient,
) -> dict[str, Any]:
    window = _day_window(target_date)
    resolved_chatids = chatids or _chatids_with_messages(session, window)
    chat_results = []
    profile_results = []

    for chatid in resolved_chatids:
        messages = _messages_in_chat_window(session, chatid, window)
        if not messages:
            chat_results.append(
                {
                    "chatid": chatid,
                    "status": "skipped",
                    "reason": "no messages in target date",
                    "segment_count": 0,
                }
            )
            continue

        boundary_result = run_conversation_boundary_detection(
            session,
            chatid=chatid,
            target_date=target_date,
            window=window,
            messages=messages,
            dify_client=dify_client,
        )
        chat_results.append(boundary_result)

        if not run_profiles:
            continue

        for segment in boundary_result.get("segments", []):
            segment_model = session.get(ConversationSegment, segment["id"])
            if not segment_model:
                continue
            for userid in _segment_userids(session, segment_model):
                if _profile_already_updated_for_conversation(
                    session,
                    userid=userid,
                    conversation_no=segment_model.conversation_no,
                ):
                    profile_results.append(
                        {
                            "conversation_no": segment_model.conversation_no,
                            "userid": userid,
                            "status": "skipped",
                            "profile_action": "already_updated",
                            "error": None,
                        }
                    )
                    continue
                profile_results.append(
                    run_user_profile_update_for_segment(
                        session,
                        segment=segment_model,
                        userid=userid,
                        dify_client=dify_client,
                    )
                )

    session.commit()

    return {
        "target_date": target_date.isoformat(),
        "chat_count": len(resolved_chatids),
        "segment_count": sum(item.get("segment_count", 0) for item in chat_results),
        "profile_update_count": sum(
            1
            for item in profile_results
            if item.get("status") == "success"
            and item.get("profile_action") in {"create", "update"}
        ),
        "chat_results": chat_results,
        "profile_results": profile_results,
    }


def run_conversation_boundary_detection(
    session: Session,
    *,
    chatid: str,
    target_date: date,
    window: DayWindow,
    messages: list[Message],
    dify_client: DifyClient,
    force: bool = False,
) -> dict[str, Any]:
    existing_segments = _existing_segments_in_window(session, chatid, window)
    if existing_segments and not force:
        return {
            "chatid": chatid,
            "run_id": None,
            "status": "reused",
            "segment_count": len(existing_segments),
            "segments": [_segment_to_result(segment) for segment in existing_segments],
            "force": False,
            "superseded_count": 0,
            "error": None,
        }

    workflow = get_enabled_workflow(
        session,
        workflow_code="conversation_boundary_detection",
        version="v1",
    )
    input_json = {
        "payload": _build_boundary_payload(
            chatid=chatid,
            target_date=target_date,
            window=window,
            messages=messages,
        )
    }
    validate_json_schema(input_json, workflow.input_schema)

    ai_run = _new_ai_run(workflow_code=workflow.workflow_code, input_json=input_json)
    session.add(ai_run)
    session.flush()

    started = perf_counter()
    segments: list[ConversationSegment] = []
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        ai_run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        if output_json["status"] != "success":
            raise AiMemoryFullTestValidationError(str(output_json.get("error") or "failed"))
        ranges = _ranges_from_split_positions(output_json, messages)
        superseded_count = _supersede_segments(existing_segments) if force else 0
        segments = _write_conversation_segments(
            session,
            ai_run=ai_run,
            chatid=chatid,
            ranges=ranges,
            confidence=float(output_json.get("confidence") or 0),
        )
        ai_run.status = "success"
        ai_run.error_message = None
    except (JsonSchemaValidationError, AiMemoryFullTestValidationError) as exc:
        ai_run.status = "invalid_output"
        ai_run.error_message = getattr(exc, "message", str(exc))
    except Exception as exc:
        ai_run.status = "failed"
        ai_run.error_message = str(exc)
    finally:
        ai_run.latency_ms = int((perf_counter() - started) * 1000)
        ai_run.finished_at = now_utc()
        session.flush()

    return {
        "chatid": chatid,
        "run_id": ai_run.run_id,
        "status": ai_run.status,
        "segment_count": len(segments),
        "segments": [_segment_to_result(segment) for segment in segments],
        "force": force,
        "superseded_count": superseded_count if ai_run.status == "success" else 0,
        "error": ai_run.error_message,
    }


def run_user_profile_update_for_segment(
    session: Session,
    *,
    segment: ConversationSegment,
    userid: str,
    dify_client: DifyClient,
    force_rewrite: bool = False,
    reset_current_profile: bool = False,
) -> dict[str, Any]:
    workflow = get_enabled_workflow(
        session,
        workflow_code="user_profile_update",
        version="v1",
    )
    messages = _messages_for_segment(session, segment)
    target_messages = [
        message
        for message in messages
        if message.userid == userid and message.sender_type == "user"
    ]
    current_profile = None if reset_current_profile else _latest_profile(session, userid)
    input_json = {
        "payload": _build_profile_payload(
            segment=segment,
            userid=userid,
            messages=messages,
            target_messages=target_messages,
            current_profile=current_profile,
            force_rewrite=force_rewrite,
            reset_current_profile=reset_current_profile,
        )
    }
    validate_json_schema(input_json, workflow.input_schema)

    ai_run = _new_ai_run(workflow_code=workflow.workflow_code, input_json=input_json)
    session.add(ai_run)
    session.flush()

    started = perf_counter()
    profile: UserProfile | None = None
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        ai_run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        _validate_profile_update_output(output_json, input_json)
        if output_json["profile_action"] in {"create", "update"}:
            profile = _write_updated_profile(session, ai_run, output_json)
        ai_run.status = "success"
        ai_run.error_message = None
    except (JsonSchemaValidationError, AiMemoryFullTestValidationError) as exc:
        ai_run.status = "invalid_output"
        ai_run.error_message = getattr(exc, "message", str(exc))
    except Exception as exc:
        ai_run.status = "failed"
        ai_run.error_message = str(exc)
    finally:
        ai_run.latency_ms = int((perf_counter() - started) * 1000)
        ai_run.finished_at = now_utc()
        session.flush()

    return {
        "conversation_no": segment.conversation_no,
        "userid": userid,
        "run_id": ai_run.run_id,
        "status": ai_run.status,
        "profile_action": (ai_run.output_json or {}).get("profile_action"),
        "profile_version": profile.version if profile else None,
        "error": ai_run.error_message,
    }


def default_target_date(days_back: int) -> date:
    return datetime.now(CHINA_TZ).date() - timedelta(days=max(days_back, 0))


def parse_target_date(value: str | None, *, days_back: int = 1) -> date:
    if not value:
        return default_target_date(days_back)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "target_date must be YYYY-MM-DD") from exc


def _day_window(target_date: date) -> DayWindow:
    start_local = datetime.combine(target_date, time.min, tzinfo=CHINA_TZ)
    end_local = start_local + timedelta(days=1)
    return DayWindow(
        start=start_local.astimezone(timezone.utc),
        end=end_local.astimezone(timezone.utc),
    )


def _chatids_with_messages(session: Session, window: DayWindow) -> list[str]:
    rows = session.scalars(
        select(Message.chatid)
        .where(Message.create_time >= window.start, Message.create_time < window.end)
        .distinct()
        .order_by(Message.chatid.asc())
    ).all()
    return [str(chatid) for chatid in rows]


def _messages_in_chat_window(
    session: Session,
    chatid: str,
    window: DayWindow,
) -> list[Message]:
    return session.scalars(
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.create_time >= window.start,
            Message.create_time < window.end,
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()


def _build_boundary_payload(
    *,
    chatid: str,
    target_date: date,
    window: DayWindow,
    messages: list[Message],
) -> dict[str, Any]:
    users = []
    seen_userids = set()
    for message in messages:
        if not message.userid or message.userid in seen_userids:
            continue
        seen_userids.add(message.userid)
        users.append({"userid": message.userid, "sender_type": message.sender_type})

    return {
        "chatid": chatid,
        "date": target_date.isoformat(),
        "window": {
            "start_time": window.start.isoformat(),
            "end_time": window.end.isoformat(),
        },
        "users": users,
        "messages": [_message_payload(message) for message in messages],
        "runtime": {
            "mode": "full_test",
            "language": "zh-CN",
            "workflow_code": "conversation_boundary_detection",
        },
    }


def _ranges_from_split_positions(
    output_json: dict[str, Any],
    messages: list[Message],
) -> list[tuple[Message, Message]]:
    if not messages:
        return []

    by_msgid = {message.external_msgid: message for message in messages}
    ordered_msgids = [message.external_msgid for message in messages]
    adjacent = {
        previous.external_msgid: current.external_msgid
        for previous, current in zip(messages, messages[1:])
    }
    split_after_indexes: list[int] = []
    seen_after = set()

    for index, position in enumerate(output_json.get("split_positions") or []):
        after_msgid = str(position.get("after_msgid") or "")
        before_msgid = str(position.get("before_msgid") or "")
        if after_msgid not in by_msgid:
            raise AiMemoryFullTestValidationError(
                f"split_positions[{index}].after_msgid not found in input messages"
            )
        if before_msgid not in by_msgid:
            raise AiMemoryFullTestValidationError(
                f"split_positions[{index}].before_msgid not found in input messages"
            )
        if adjacent.get(after_msgid) != before_msgid:
            raise AiMemoryFullTestValidationError(
                f"split_positions[{index}] must reference adjacent messages"
            )
        if after_msgid in seen_after:
            raise AiMemoryFullTestValidationError("duplicate split position")
        seen_after.add(after_msgid)
        split_after_indexes.append(ordered_msgids.index(after_msgid))

    ranges = []
    start_index = 0
    for end_index in sorted(split_after_indexes):
        ranges.append((messages[start_index], messages[end_index]))
        start_index = end_index + 1
    ranges.append((messages[start_index], messages[-1]))
    return ranges


def _write_conversation_segments(
    session: Session,
    *,
    ai_run: AiRun,
    chatid: str,
    ranges: list[tuple[Message, Message]],
    confidence: float,
) -> list[ConversationSegment]:
    written = []
    for index, (start_message, end_message) in enumerate(ranges, start=1):
        existing = session.scalar(
            select(ConversationSegment).where(
                ConversationSegment.chatid == chatid,
                ConversationSegment.start_message_id == start_message.id,
                ConversationSegment.end_message_id == end_message.id,
            )
        )
        if existing:
            segment_messages = _messages_between(session, chatid, start_message, end_message)
            existing.ai_run_id = ai_run.id
            existing.title = f"AI 全量测试会话 {index}"
            existing.summary = "系统根据 Dify 切分点从消息库生成的会话片段。"
            existing.keywords = []
            existing.participants = _participants(segment_messages)
            existing.confidence = max(0.0, min(confidence, 1.0))
            existing.status = "active"
            existing.updated_at = now_utc()
            session.flush()
            written.append(existing)
            continue

        segment_messages = _messages_between(session, chatid, start_message, end_message)
        segment = ConversationSegment(
            conversation_no=_new_conversation_no(),
            chatid=chatid,
            start_message_id=start_message.id,
            end_message_id=end_message.id,
            start_time=_to_utc(start_message.create_time),
            end_time=_to_utc(end_message.create_time),
            title=f"AI 全量测试会话 {index}",
            summary="系统根据 Dify 切分点从消息库生成的会话片段。",
            keywords=[],
            participants=_participants(segment_messages),
            ai_run_id=ai_run.id,
            confidence=max(0.0, min(confidence, 1.0)),
            version=1,
            status="active",
        )
        session.add(segment)
        session.flush()
        written.append(segment)
    return written


def _supersede_segments(segments: list[ConversationSegment]) -> int:
    updated_at = now_utc()
    for segment in segments:
        segment.status = "superseded"
        segment.updated_at = updated_at
    return len(segments)


def _existing_segments_in_window(
    session: Session,
    chatid: str,
    window: DayWindow,
) -> list[ConversationSegment]:
    return session.scalars(
        select(ConversationSegment)
        .where(
            ConversationSegment.chatid == chatid,
            ConversationSegment.status == "active",
            ConversationSegment.start_time >= window.start,
            ConversationSegment.start_time < window.end,
        )
        .order_by(ConversationSegment.start_time.asc(), ConversationSegment.id.asc())
    ).all()


def _segment_to_result(segment: ConversationSegment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "conversation_no": segment.conversation_no,
        "start_message_id": segment.start_message_id,
        "end_message_id": segment.end_message_id,
    }


def _segment_userids(session: Session, segment: ConversationSegment) -> list[str]:
    return sorted(
        {
            str(message.userid)
            for message in _messages_for_segment(session, segment)
            if message.userid and message.sender_type == "user"
        }
    )


def _messages_for_segment(session: Session, segment: ConversationSegment) -> list[Message]:
    start = session.get(Message, segment.start_message_id)
    end = session.get(Message, segment.end_message_id)
    if not start or not end:
        return []
    return _messages_between(session, segment.chatid, start, end)


def _messages_between(
    session: Session,
    chatid: str,
    start_message: Message,
    end_message: Message,
) -> list[Message]:
    return session.scalars(
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.create_time >= start_message.create_time,
            Message.create_time <= end_message.create_time,
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()


def _build_profile_payload(
    *,
    segment: ConversationSegment,
    userid: str,
    messages: list[Message],
    target_messages: list[Message],
    current_profile: UserProfile | None,
    force_rewrite: bool = False,
    reset_current_profile: bool = False,
) -> dict[str, Any]:
    return {
        "userid": userid,
        "conversation": {
            "conversation_no": segment.conversation_no,
            "chatid": segment.chatid,
            "started_at": _to_utc(segment.start_time).isoformat(),
            "ended_at": _to_utc(segment.end_time).isoformat(),
            "summary": segment.summary,
            "title": segment.title,
        },
        "messages": [_message_payload(message) for message in messages],
        "target_user_messages": [_message_payload(message) for message in target_messages],
        "current_profile": _current_profile_payload(current_profile),
        "runtime": {
            "mode": "force_rewrite" if force_rewrite else "full_test",
            "language": "zh-CN",
            "workflow_code": "user_profile_update",
            "force_rewrite": force_rewrite,
            "reset_current_profile": reset_current_profile,
        },
    }


def _validate_profile_update_output(
    output_json: dict[str, Any],
    input_json: dict[str, Any],
) -> None:
    payload = input_json["payload"]
    userid = payload["userid"]
    conversation_no = payload["conversation"]["conversation_no"]
    valid_msgids = {message["msgid"] for message in payload["messages"]}
    current_fact_ids = {
        str(fact.get("fact_id"))
        for fact in (payload.get("current_profile") or {}).get("facts", [])
        if fact.get("fact_id")
    }

    if output_json["userid"] != userid:
        raise AiMemoryFullTestValidationError("output userid must match input userid")
    if output_json["evidence"].get("conversation_no") != conversation_no:
        raise AiMemoryFullTestValidationError("evidence.conversation_no must match input")
    for msgid in output_json["evidence"].get("msgids") or []:
        if msgid not in valid_msgids:
            raise AiMemoryFullTestValidationError(
                f"evidence.msgids {msgid} not found in input messages"
            )

    for fact in output_json["updated_profile"].get("facts") or []:
        for msgid in fact.get("evidence_msgids") or []:
            if msgid not in valid_msgids:
                raise AiMemoryFullTestValidationError(
                    f"fact evidence_msgid {msgid} not found in input messages"
                )
        for item in fact.get("evidence_conversation_nos") or []:
            if item != conversation_no:
                raise AiMemoryFullTestValidationError(
                    "fact evidence_conversation_nos must match input conversation"
                )

    for fact in output_json["changes"].get("facts_updated") or []:
        fact_id = str(fact.get("fact_id") or "")
        if fact_id and current_fact_ids and fact_id not in current_fact_ids:
            raise AiMemoryFullTestValidationError(
                f"changes.facts_updated fact_id {fact_id} not found in current_profile"
            )


def _write_updated_profile(
    session: Session,
    ai_run: AiRun,
    output_json: dict[str, Any],
) -> UserProfile:
    userid = output_json["userid"]
    updated_profile = output_json["updated_profile"]
    for profile in session.scalars(
        select(UserProfile).where(UserProfile.userid == userid, UserProfile.status == "active")
    ).all():
        profile.status = "superseded"
        profile.updated_at = now_utc()

    profile = UserProfile(
        userid=userid,
        summary=str(updated_profile.get("summary") or ""),
        profile_json=updated_profile,
        ai_run_id=ai_run.id,
        confidence=float(output_json.get("confidence") or 0),
        version=_next_profile_version(session, userid),
        status="active",
        last_analyzed_at=now_utc(),
    )
    session.add(profile)
    session.flush()

    for fact in updated_profile.get("facts") or []:
        confidence = float(fact.get("confidence") or 0)
        if confidence < 0.5:
            continue
        fact_type = str(fact.get("fact_type") or fact.get("type") or "other")
        label = str(fact.get("label") or fact_type)
        description = str(fact.get("description") or fact.get("value") or "")
        if not description:
            continue
        session.add(
            UserProfileFact(
                userid=userid,
                profile_id=profile.id,
                source_ai_run_id=ai_run.id,
                fact_type=fact_type,
                label=label,
                description=description,
                evidence_msgids=fact.get("evidence_msgids") or [],
                evidence_conversation_nos=fact.get("evidence_conversation_nos") or [],
                confidence=confidence,
                status="active" if confidence >= 0.75 else "low_confidence",
            )
        )
    session.flush()
    return profile


def _message_payload(message: Message) -> dict[str, Any]:
    return {
        "message_id": str(message.id),
        "msgid": message.external_msgid,
        "chatid": message.chatid,
        "userid": message.userid,
        "sender_type": message.sender_type,
        "content": message.content_text or "",
        "created_at": _to_utc(message.create_time).isoformat(),
    }


def _participants(messages: list[Message]) -> list[str]:
    participants = []
    for message in messages:
        if message.userid and message.userid not in participants:
            participants.append(message.userid)
    return participants


def _current_profile_payload(profile: UserProfile | None) -> dict[str, Any]:
    if not profile:
        return {"summary": "", "facts": []}
    profile_json = profile.profile_json or {}
    return {
        "userid": profile.userid,
        "summary": profile.summary,
        "facts": profile_json.get("facts") or [],
        "version": profile.version,
        "status": profile.status,
        "confidence": float(profile.confidence),
    }


def _latest_profile(session: Session, userid: str) -> UserProfile | None:
    return session.scalar(
        select(UserProfile)
        .where(UserProfile.userid == userid, UserProfile.status == "active")
        .order_by(UserProfile.version.desc(), UserProfile.id.desc())
    )


def _profile_already_updated_for_conversation(
    session: Session,
    *,
    userid: str,
    conversation_no: str,
) -> bool:
    facts = session.scalars(
        select(UserProfileFact).where(UserProfileFact.userid == userid)
    ).all()
    for fact in facts:
        if conversation_no in {str(item) for item in fact.evidence_conversation_nos or []}:
            return True
    return False


def _next_profile_version(session: Session, userid: str) -> int:
    latest = session.scalar(
        select(UserProfile)
        .where(UserProfile.userid == userid)
        .order_by(UserProfile.version.desc(), UserProfile.id.desc())
    )
    return latest.version + 1 if latest else 1


def _new_ai_run(*, workflow_code: str, input_json: dict[str, Any]) -> AiRun:
    return AiRun(
        run_id=f"airun_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}",
        workflow_code=workflow_code,
        workflow_version="v1",
        trigger_event_id=None,
        input_json=input_json,
        response_mode="blocking",
        status="running",
        created_at=now_utc(),
        started_at=now_utc(),
    )


def _new_conversation_no() -> str:
    return f"conv_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:6]}"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
