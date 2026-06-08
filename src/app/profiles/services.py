from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
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
from app.profiles.schemas import UserProfileAnalyzeRequest


class UserProfileOutputValidationError(Exception):
    pass


def run_user_profile_analysis(
    session: Session,
    *,
    payload: UserProfileAnalyzeRequest,
    dify_client: DifyClient,
) -> dict[str, Any]:
    start_time = _parse_datetime(payload.time_range.start)
    end_time = _parse_datetime(payload.time_range.end)
    if start_time >= end_time:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "time_range.start must be earlier than end")

    messages = _messages_in_window(session, payload.userid, start_time, end_time)
    if not messages:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            f"No messages found for userid={payload.userid} in the requested window",
        )

    workflow = get_enabled_workflow(
        session,
        workflow_code="user_profile_analysis",
        version="v1",
    )
    current_profile = _latest_profile(session, payload.userid)
    next_version = _next_profile_version(session, payload.userid)
    conversation_summaries = _conversation_summaries_for_user(
        session,
        userid=payload.userid,
        start_time=start_time,
        end_time=end_time,
    )
    input_json = _build_profile_input(
        userid=payload.userid,
        profile_version=next_version,
        messages=messages,
        conversation_summaries=conversation_summaries,
        current_profile=current_profile,
        mode=payload.mode,
    )
    validate_json_schema(input_json, workflow.input_schema)

    ai_run = AiRun(
        run_id=_new_run_id(),
        workflow_code=workflow.workflow_code,
        workflow_version=workflow.version,
        trigger_event_id=None,
        input_json=input_json,
        response_mode=workflow.response_mode,
        status="running",
        created_at=now_utc(),
        started_at=now_utc(),
    )
    session.add(ai_run)
    session.flush()

    started = perf_counter()
    profile: UserProfile | None = None
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        ai_run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        _validate_profile_output(output_json, input_json)
        profile = _write_profile(session, ai_run, input_json, output_json)
        ai_run.status = "success"
        ai_run.error_message = None
    except (JsonSchemaValidationError, UserProfileOutputValidationError) as exc:
        ai_run.status = "invalid_output"
        ai_run.error_message = getattr(exc, "message", str(exc))
    except Exception as exc:
        ai_run.status = "failed"
        ai_run.error_message = str(exc)
    finally:
        ai_run.latency_ms = int((perf_counter() - started) * 1000)
        ai_run.finished_at = now_utc()

    session.commit()

    return {
        "run_id": ai_run.run_id,
        "status": ai_run.status,
        "profile_version": profile.version if profile else None,
        "profile": user_profile_to_dict(profile, []) if profile else None,
    }


def get_latest_user_profile(session: Session, userid: str) -> dict[str, Any]:
    profile = _latest_profile(session, userid)
    if not profile:
        raise AppError(ErrorCode.NOT_FOUND, f"User profile not found: {userid}")

    facts = session.scalars(
        select(UserProfileFact)
        .where(
            UserProfileFact.profile_id == profile.id,
            UserProfileFact.status.in_(["active", "low_confidence"]),
        )
        .order_by(UserProfileFact.confidence.desc(), UserProfileFact.id.asc())
    ).all()
    return user_profile_to_dict(profile, facts)


def list_user_profile_versions(
    session: Session,
    userid: str,
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    total = (
        session.scalar(
            select(func.count()).select_from(UserProfile).where(UserProfile.userid == userid)
        )
        or 0
    )
    profiles = session.scalars(
        select(UserProfile)
        .where(UserProfile.userid == userid)
        .order_by(UserProfile.version.desc(), UserProfile.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [user_profile_to_dict(profile, []) for profile in profiles],
    }


def user_profile_to_dict(
    profile: UserProfile,
    facts: list[UserProfileFact],
) -> dict[str, Any]:
    return {
        "id": profile.id,
        "userid": profile.userid,
        "summary": profile.summary,
        "profile_json": profile.profile_json,
        "ai_run_id": profile.ai_run_id,
        "confidence": float(profile.confidence),
        "version": profile.version,
        "status": profile.status,
        "facts": [user_profile_fact_to_dict(fact) for fact in facts],
        "last_analyzed_at": _iso(profile.last_analyzed_at),
        "created_at": _iso(profile.created_at),
        "updated_at": _iso(profile.updated_at),
    }


def user_profile_fact_to_dict(fact: UserProfileFact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "userid": fact.userid,
        "profile_id": fact.profile_id,
        "source_ai_run_id": fact.source_ai_run_id,
        "fact_type": fact.fact_type,
        "label": fact.label,
        "description": fact.description,
        "evidence_msgids": fact.evidence_msgids,
        "evidence_conversation_nos": fact.evidence_conversation_nos,
        "confidence": float(fact.confidence),
        "status": fact.status,
        "created_at": _iso(fact.created_at),
    }


def _messages_in_window(
    session: Session,
    userid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    candidates = session.scalars(
        select(Message).where(Message.userid == userid).order_by(Message.create_time.asc())
    ).all()
    return [
        message
        for message in candidates
        if start_time <= _to_utc(message.create_time) <= end_time
    ]


def _conversation_summaries_for_user(
    session: Session,
    *,
    userid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    segments = session.scalars(
        select(ConversationSegment)
        .where(ConversationSegment.status == "active")
        .order_by(ConversationSegment.start_time.asc(), ConversationSegment.id.asc())
    ).all()

    summaries = []
    for segment in segments:
        if userid not in {str(participant) for participant in segment.participants}:
            continue
        if _to_utc(segment.end_time) < start_time or _to_utc(segment.start_time) > end_time:
            continue
        summaries.append(
            {
                "conversation_no": segment.conversation_no,
                "title": segment.title,
                "summary": segment.summary,
                "keywords": segment.keywords,
                "participants": segment.participants,
                "start_time": _to_utc(segment.start_time).isoformat(),
                "end_time": _to_utc(segment.end_time).isoformat(),
            }
        )
    return summaries


def _build_profile_input(
    *,
    userid: str,
    profile_version: int,
    messages: list[Message],
    conversation_summaries: list[dict[str, Any]],
    current_profile: UserProfile | None,
    mode: str,
) -> dict[str, Any]:
    active_chats = {message.chatid for message in messages}
    top_keywords = []
    for summary in conversation_summaries:
        for keyword in summary.get("keywords") or []:
            if keyword not in top_keywords:
                top_keywords.append(keyword)

    return {
        "userid": userid,
        "profile_version": profile_version,
        "recent_messages": [
            {
                "message_id": str(message.id),
                "msgid": message.external_msgid,
                "chatid": message.chatid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": _to_utc(message.create_time).isoformat(),
            }
            for message in messages
        ],
        "conversation_summaries": conversation_summaries,
        "current_profile": _current_profile_payload(current_profile),
        "statistics": {
            "message_count_30d": len(messages),
            "active_chats": len(active_chats),
            "top_keywords": top_keywords,
        },
        "runtime": {
            "mode": mode,
            "language": "zh-CN",
        },
    }


def _validate_profile_output(output_json: dict[str, Any], input_json: dict[str, Any]) -> None:
    if output_json["userid"] != input_json["userid"]:
        raise UserProfileOutputValidationError("output userid must match input userid")

    valid_msgids = {message["msgid"] for message in input_json["recent_messages"]}
    valid_conversation_nos = {
        summary["conversation_no"] for summary in input_json["conversation_summaries"]
    }
    current_fact_ids = {
        fact["fact_id"]
        for fact in (input_json.get("current_profile") or {}).get("facts", [])
        if fact.get("fact_id")
    }

    for fact in output_json.get("facts_to_add", []):
        evidence_msgids = fact.get("evidence_msgids") or []
        if not evidence_msgids:
            raise UserProfileOutputValidationError("fact must include evidence")

        missing_msgids = [msgid for msgid in evidence_msgids if msgid not in valid_msgids]
        if missing_msgids:
            raise UserProfileOutputValidationError(
                f"evidence_msgids {missing_msgids[0]} not found in input recent_messages"
            )

    for fact in output_json.get("facts_to_update", []):
        fact_id = fact["fact_id"]
        if fact_id not in current_fact_ids:
            raise UserProfileOutputValidationError(
                f"fact_id {fact_id} not found in current_profile.facts"
            )
        evidence_msgids = fact.get("evidence_msgids") or []
        if not evidence_msgids:
            raise UserProfileOutputValidationError("fact must include evidence")
        missing_msgids = [msgid for msgid in evidence_msgids if msgid not in valid_msgids]
        if missing_msgids:
            raise UserProfileOutputValidationError(
                f"evidence_msgids {missing_msgids[0]} not found in input recent_messages"
            )

    for retired_fact in output_json.get("facts_to_retire", []):
        fact_id = retired_fact["fact_id"]
        if fact_id not in current_fact_ids:
            raise UserProfileOutputValidationError(
                f"fact_id {fact_id} not found in current_profile.facts"
            )

    for fact in output_json.get("facts_to_add", []):
        evidence_conversation_nos = fact.get("evidence_conversation_nos") or []
        missing_conversations = [
            conversation_no
            for conversation_no in evidence_conversation_nos
            if conversation_no not in valid_conversation_nos
        ]
        if missing_conversations:
            raise UserProfileOutputValidationError(
                "evidence_conversation_nos "
                f"{missing_conversations[0]} not found in input conversation_summaries"
            )


def _write_profile(
    session: Session,
    ai_run: AiRun,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
) -> UserProfile:
    userid = input_json["userid"]
    active_profiles = session.scalars(
        select(UserProfile).where(
            UserProfile.userid == userid,
            UserProfile.status == "active",
        )
    ).all()
    for profile in active_profiles:
        profile.status = "superseded"
        profile.updated_at = now_utc()

    facts_to_write = [
        *output_json.get("facts_to_add", []),
        *output_json.get("facts_to_update", []),
    ]
    active_facts = [
        _fact_snapshot(fact)
        for fact in facts_to_write
        if float(fact["confidence"]) >= 0.75
    ]
    profile = UserProfile(
        userid=userid,
        summary=output_json["summary"],
        profile_json={
            "summary": output_json["summary"],
            "facts": active_facts,
            "statistics": input_json["statistics"],
        },
        ai_run_id=ai_run.id,
        confidence=output_json["confidence"],
        version=input_json["profile_version"],
        status="active",
        last_analyzed_at=now_utc(),
    )
    session.add(profile)
    session.flush()

    for fact_data in facts_to_write:
        confidence = float(fact_data["confidence"])
        if confidence < 0.5:
            continue

        status = "active" if confidence >= 0.75 else "low_confidence"
        session.add(
            UserProfileFact(
                userid=userid,
                profile_id=profile.id,
                source_ai_run_id=ai_run.id,
                fact_type=fact_data["fact_type"],
                label=fact_data["label"],
                description=fact_data["description"],
                evidence_msgids=fact_data.get("evidence_msgids") or [],
                evidence_conversation_nos=fact_data.get("evidence_conversation_nos") or [],
                confidence=confidence,
                status=status,
            )
        )
    session.flush()
    return profile


def _fact_snapshot(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_type": fact["fact_type"],
        "label": fact["label"],
        "description": fact["description"],
        "evidence_msgids": fact.get("evidence_msgids") or [],
        "evidence_conversation_nos": fact.get("evidence_conversation_nos") or [],
        "confidence": fact["confidence"],
    }


def _latest_profile(session: Session, userid: str) -> UserProfile | None:
    return session.scalar(
        select(UserProfile)
        .where(UserProfile.userid == userid, UserProfile.status == "active")
        .order_by(UserProfile.version.desc(), UserProfile.id.desc())
    )


def _next_profile_version(session: Session, userid: str) -> int:
    latest = session.scalar(
        select(UserProfile)
        .where(UserProfile.userid == userid)
        .order_by(UserProfile.version.desc(), UserProfile.id.desc())
    )
    return latest.version + 1 if latest else 1


def _current_profile_payload(profile: UserProfile | None) -> dict[str, Any] | None:
    if not profile:
        return None
    return {
        "userid": profile.userid,
        "summary": profile.summary,
        "facts": profile.profile_json.get("facts", []),
        "confidence": float(profile.confidence),
        "version": profile.version,
        "status": profile.status,
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _new_run_id() -> str:
    return f"airun_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _iso(value):
    return value.isoformat() if value else None
