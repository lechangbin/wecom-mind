from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.models import AiRun, ConversationSegment, Message, now_utc
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow
from app.conversations.schemas import ConversationSegmentRunRequest
from app.ai_memory.full_test import (
    DayWindow,
    run_conversation_boundary_detection,
)
from app.admin_frontend.services import message_to_dict


class ConversationOutputValidationError(Exception):
    pass


def run_conversation_segmentation(
    session: Session,
    *,
    payload: ConversationSegmentRunRequest,
    dify_client: DifyClient,
) -> dict[str, Any]:
    start_time = _parse_datetime(payload.start_time)
    end_time = _parse_datetime(payload.end_time)
    if start_time >= end_time:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "start_time must be earlier than end_time")

    messages = _messages_in_window(session, payload.chatid, start_time, end_time)
    if not messages:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            f"No messages found for chatid={payload.chatid} in the requested window",
        )

    result = run_conversation_boundary_detection(
        session,
        chatid=payload.chatid,
        target_date=start_time.date(),
        window=DayWindow(start=start_time, end=end_time),
        messages=messages,
        dify_client=dify_client,
        force=payload.force,
    )
    session.commit()

    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "force": result.get("force", payload.force),
        "superseded_count": result.get("superseded_count", 0),
        "segments": [
            conversation_segment_to_dict(session.get(ConversationSegment, item["id"]))
            for item in result.get("segments", [])
            if session.get(ConversationSegment, item["id"])
        ],
    }


def list_conversation_segments(
    session: Session,
    *,
    chatid: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    statement = select(ConversationSegment).where(ConversationSegment.status == "active")
    if chatid:
        statement = statement.where(ConversationSegment.chatid == chatid)
    if start_date or end_date:
        start_time, end_time = _date_range(start_date, end_date)
        statement = statement.where(
            ConversationSegment.start_time >= start_time,
            ConversationSegment.start_time < end_time,
        )

    items = session.scalars(
        statement.order_by(ConversationSegment.start_time.desc(), ConversationSegment.id.desc())
    ).all()
    matched_items = _filter_conversations_by_query(session, items, q)
    page_items = matched_items[offset : offset + limit]
    return {
        "total": len(matched_items),
        "limit": limit,
        "offset": offset,
        "items": [
            conversation_segment_to_dict(item, match_source=match_source)
            for item, match_source in page_items
        ],
    }


def get_conversation_segment(
    session: Session,
    *,
    conversation_no: str,
) -> ConversationSegment:
    segment = session.scalar(
        select(ConversationSegment).where(
            ConversationSegment.conversation_no == conversation_no,
            ConversationSegment.status == "active",
        )
    )
    if not segment:
        raise AppError(ErrorCode.NOT_FOUND, f"Conversation not found: {conversation_no}")
    return segment


def conversation_segment_to_dict(
    segment: ConversationSegment,
    *,
    match_source: str = "none",
) -> dict[str, Any]:
    return _conversation_segment_to_dict(segment, match_source=match_source)


def _conversation_segment_to_dict(
    segment: ConversationSegment,
    *,
    match_source: str,
) -> dict[str, Any]:
    return {
        "id": segment.id,
        "conversation_no": segment.conversation_no,
        "chatid": segment.chatid,
        "start_message_id": segment.start_message_id,
        "end_message_id": segment.end_message_id,
        "start_time": _iso(segment.start_time),
        "end_time": _iso(segment.end_time),
        "title": segment.title,
        "summary": segment.summary,
        "keywords": segment.keywords,
        "participants": segment.participants,
        "ai_run_id": segment.ai_run_id,
        "confidence": float(segment.confidence),
        "version": segment.version,
        "status": segment.status,
        "match_source": match_source,
        "created_at": _iso(segment.created_at),
        "updated_at": _iso(segment.updated_at),
    }


def list_conversation_messages(
    session: Session,
    *,
    conversation_no: str,
) -> dict[str, Any]:
    segment = get_conversation_segment(session, conversation_no=conversation_no)
    messages = _messages_for_segment(session, segment)
    return {
        "total": len(messages),
        "items": [message_to_dict(message) for message in messages],
    }


def _filter_conversations_by_query(
    session: Session,
    items: list[ConversationSegment],
    q: str | None,
) -> list[tuple[ConversationSegment, str]]:
    if not q:
        return [(item, "none") for item in items]

    needle = q.lower()
    summary_matches = []
    message_matches = []
    for item in items:
        if needle in (item.summary or "").lower() or needle in (item.title or "").lower():
            summary_matches.append((item, "summary"))
            continue
        if _segment_messages_contain(session, item, needle):
            message_matches.append((item, "message"))

    summary_matches.sort(key=lambda pair: pair[0].start_time, reverse=True)
    message_matches.sort(key=lambda pair: pair[0].start_time, reverse=True)
    return summary_matches + message_matches


def _segment_messages_contain(
    session: Session,
    segment: ConversationSegment,
    needle: str,
) -> bool:
    start = session.get(Message, segment.start_message_id)
    end = session.get(Message, segment.end_message_id)
    if not start or not end:
        return False
    count = session.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.chatid == segment.chatid,
            Message.create_time >= _to_utc(start.create_time),
            Message.create_time <= _to_utc(end.create_time),
            func.lower(Message.content_text).like(f"%{needle}%"),
        )
    )
    return bool(count)


def _messages_for_segment(
    session: Session,
    segment: ConversationSegment,
) -> list[Message]:
    start = session.get(Message, segment.start_message_id)
    end = session.get(Message, segment.end_message_id)
    if not start or not end:
        return []
    return session.scalars(
        select(Message)
        .where(
            Message.chatid == segment.chatid,
            Message.create_time >= _to_utc(start.create_time),
            Message.create_time <= _to_utc(end.create_time),
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()


def _date_range(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime]:
    from app.admin_frontend.services import _date_range as admin_date_range

    return admin_date_range(start_date, end_date)


def _messages_in_window(
    session: Session,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    candidates = session.scalars(
        select(Message).where(Message.chatid == chatid).order_by(Message.create_time.asc())
    ).all()
    return [
        message
        for message in candidates
        if start_time <= _to_utc(message.create_time) <= end_time
    ]


def _build_segmentation_input(
    *,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
    messages: list[Message],
    mode: str,
) -> dict[str, Any]:
    return {
        "chatid": chatid,
        "window": {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "mode": mode,
        },
        "messages": [
            {
                "message_id": str(message.id),
                "msgid": message.external_msgid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": _to_utc(message.create_time).isoformat(),
            }
            for message in messages
        ],
        "candidate_boundaries": [],
        "previous_conversation": None,
    }


def _validate_segmentation_output(
    output_json: dict[str, Any],
    messages: list[Message],
) -> list[dict[str, Any]]:
    msgid_to_message = {message.external_msgid: message for message in messages}
    valid_userids = {message.userid for message in messages if message.userid}
    ordered_msgids = [message.external_msgid for message in messages]
    intervals = []
    validated = []

    for segment in output_json.get("segments", []):
        start_msgid = segment["start_msgid"]
        end_msgid = segment["end_msgid"]
        if start_msgid not in msgid_to_message:
            raise ConversationOutputValidationError(
                f"start_msgid {start_msgid} not found in input messages"
            )
        if end_msgid not in msgid_to_message:
            raise ConversationOutputValidationError(
                f"end_msgid {end_msgid} not found in input messages"
            )

        start_index = ordered_msgids.index(start_msgid)
        end_index = ordered_msgids.index(end_msgid)
        if start_index > end_index:
            raise ConversationOutputValidationError("segment start is after end")

        participants = segment.get("participants") or []
        unknown_participants = [user for user in participants if user not in valid_userids]
        if unknown_participants:
            raise ConversationOutputValidationError(
                f"participant {unknown_participants[0]} not found in input messages"
            )

        intervals.append((start_index, end_index))
        validated.append(segment)

    for previous, current in zip(sorted(intervals), sorted(intervals)[1:]):
        if current[0] <= previous[1]:
            raise ConversationOutputValidationError("conversation segments overlap")

    return validated


def _write_segments(
    session: Session,
    ai_run: AiRun,
    chatid: str,
    segments: list[dict[str, Any]],
) -> list[ConversationSegment]:
    written = []
    for segment_data in segments:
        start_message = _message_by_msgid(session, chatid, segment_data["start_msgid"])
        end_message = _message_by_msgid(session, chatid, segment_data["end_msgid"])
        existing = session.scalar(
            select(ConversationSegment).where(
                ConversationSegment.chatid == chatid,
                ConversationSegment.start_message_id == start_message.id,
                ConversationSegment.end_message_id == end_message.id,
            )
        )
        if existing:
            written.append(existing)
            continue

        segment = ConversationSegment(
            conversation_no=_new_conversation_no(),
            chatid=chatid,
            start_message_id=start_message.id,
            end_message_id=end_message.id,
            start_time=_to_utc(start_message.create_time),
            end_time=_to_utc(end_message.create_time),
            title=segment_data["title"],
            summary=segment_data["summary"],
            keywords=segment_data.get("keywords") or [],
            participants=segment_data.get("participants") or [],
            ai_run_id=ai_run.id,
            confidence=segment_data["confidence"],
            version=1,
            status="active",
        )
        session.add(segment)
        session.flush()
        written.append(segment)
    return written


def _message_by_msgid(session: Session, chatid: str, msgid: str) -> Message:
    message = session.scalar(
        select(Message).where(Message.chatid == chatid, Message.external_msgid == msgid)
    )
    if not message:
        raise ConversationOutputValidationError(f"msgid {msgid} not found in database")
    return message


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


def _new_conversation_no() -> str:
    return f"conv_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:6]}"


def _iso(value):
    return value.isoformat() if value else None
