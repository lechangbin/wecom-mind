from collections import Counter, defaultdict
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    AiRun,
    Message,
    OutboxMessage,
    ScheduledIntent,
    TriggerEvent,
    UserProfile,
    WeComChat,
    WeComUser,
)

DISPLAY_TZ = timezone(timedelta(hours=8))


def get_dashboard_overview(session: Session, *, date: str | None = None) -> dict[str, Any]:
    target_date = _parse_date(date) if date else datetime.now(DISPLAY_TZ).date()
    start_time, end_time = _day_window(target_date)
    messages = session.scalars(select(Message)).all()
    trigger_events = session.scalars(select(TriggerEvent)).all()
    ai_runs = session.scalars(select(AiRun)).all()
    scheduled_intents = session.scalars(select(ScheduledIntent)).all()
    message_by_id = {message.id: message for message in messages}
    trigger_by_id = {event.id: event for event in trigger_events}
    ai_run_by_id = {run.id: run for run in ai_runs}

    today_messages = [
        message for message in messages if _in_window(message.create_time, start_time, end_time)
    ]
    today_triggers = [
        event
        for event in trigger_events
        if _in_window(
            _trigger_event_business_time(event, message_by_id),
            start_time,
            end_time,
        )
    ]
    today_ai_runs = [
        run
        for run in ai_runs
        if _in_window(
            _ai_run_business_time(run, trigger_by_id, message_by_id),
            start_time,
            end_time,
        )
    ]
    today_scheduled_intents = [
        intent
        for intent in scheduled_intents
        if _in_window(
            _scheduled_intent_business_time(
                intent,
                ai_run_by_id,
                trigger_by_id,
                message_by_id,
            ),
            start_time,
            end_time,
        )
    ]

    latencies = [run.latency_ms for run in today_ai_runs if run.latency_ms is not None]
    return {
        "date": target_date.isoformat(),
        "today_message_count": len(today_messages),
        "total_message_count": len(messages),
        "active_chat_count": _count_where(session, WeComChat.status == "active", WeComChat),
        "active_user_count": _count_where(session, WeComUser.status == "active", WeComUser),
        "today_trigger_count": len(today_triggers),
        "ai_run_count": len(today_ai_runs),
        "ai_success_rate": _success_rate(today_ai_runs),
        "avg_ai_latency_ms": _avg_int(latencies),
        "pending_outbox_count": _count_where(
            session,
            OutboxMessage.status == "pending",
            OutboxMessage,
        ),
        "failed_outbox_count": _count_where(
            session,
            OutboxMessage.status == "failed",
            OutboxMessage,
        ),
        "scheduled_intent_count": len(today_scheduled_intents),
    }


def list_chats(session: Session, *, limit: int, offset: int) -> dict[str, Any]:
    total = session.scalar(select(func.count()).select_from(WeComChat)) or 0
    chats = session.scalars(
        select(WeComChat)
        .order_by(WeComChat.last_message_at.desc(), WeComChat.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    message_counts = _message_counts_by(session, Message.chatid)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": chat.id,
                "chatid": chat.chatid,
                "chattype": chat.chattype,
                "name": chat.name,
                "status": chat.status,
                "last_message_at": _iso(chat.last_message_at),
                "message_count": message_counts.get(chat.chatid, 0),
            }
            for chat in chats
        ],
    }


def list_chat_messages(
    session: Session,
    *,
    chatid: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    total = session.scalar(
        select(func.count()).select_from(Message).where(Message.chatid == chatid)
    ) or 0
    messages = session.scalars(
        select(Message)
        .where(Message.chatid == chatid)
        .order_by(Message.create_time.desc(), Message.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [message_to_dict(message) for message in messages],
    }


def list_users(session: Session, *, limit: int, offset: int) -> dict[str, Any]:
    total = session.scalar(select(func.count()).select_from(WeComUser)) or 0
    users = session.scalars(
        select(WeComUser)
        .order_by(WeComUser.last_active_at.desc(), WeComUser.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    message_counts = _message_counts_by(session, Message.userid)
    profile_summaries = _latest_profile_summaries(session)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": user.id,
                "userid": user.userid,
                "name": user.name,
                "status": user.status,
                "last_active_at": _iso(user.last_active_at),
                "message_count": message_counts.get(user.userid, 0),
                "latest_profile_summary": profile_summaries.get(user.userid),
            }
            for user in users
        ],
    }


def get_message_stats(
    session: Session,
    *,
    start: str | None = None,
    end: str | None = None,
    group_by: str = "day",
) -> dict[str, Any]:
    if group_by not in {"day", "hour"}:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "group_by must be day or hour")

    start_time = _parse_datetime(start) if start else None
    end_time = _parse_datetime(end) if end else None
    messages = _messages_between(session.scalars(select(Message)).all(), start_time, end_time)
    return {
        "trend": _message_trend(messages, group_by),
        "msgtype_distribution": _ranking(
            Counter(message.msgtype for message in messages),
            key_name="msgtype",
        ),
        "chat_ranking": _ranking(
            Counter(message.chatid for message in messages),
            key_name="chatid",
        ),
        "user_ranking": _ranking(
            Counter(message.userid for message in messages if message.userid),
            key_name="userid",
        ),
    }


def get_workflow_stats(
    session: Session,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    start_time = _parse_datetime(start) if start else None
    end_time = _parse_datetime(end) if end else None
    messages = session.scalars(select(Message)).all()
    trigger_events = session.scalars(select(TriggerEvent)).all()
    message_by_id = {message.id: message for message in messages}
    trigger_by_id = {event.id: event for event in trigger_events}
    runs = [
        run
        for run in session.scalars(select(AiRun)).all()
        if _matches_optional_window(
            _ai_run_business_time(run, trigger_by_id, message_by_id),
            start_time,
            end_time,
        )
    ]

    grouped: dict[str, list[AiRun]] = defaultdict(list)
    for run in runs:
        grouped[run.workflow_code].append(run)

    items = []
    for workflow_code in sorted(grouped):
        workflow_runs = grouped[workflow_code]
        latencies = [
            run.latency_ms for run in workflow_runs if run.latency_ms is not None
        ]
        items.append(
            {
                "workflow_code": workflow_code,
                "total": len(workflow_runs),
                "success": _status_count(workflow_runs, "success"),
                "failed": _status_count(workflow_runs, "failed"),
                "invalid_output": _status_count(workflow_runs, "invalid_output"),
                "success_rate": _success_rate(workflow_runs),
                "avg_latency_ms": _avg_int(latencies),
            }
        )
    return {"items": items}


def message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "external_msgid": message.external_msgid,
        "chatid": message.chatid,
        "userid": message.userid,
        "msgtype": message.msgtype,
        "content_text": message.content_text,
        "mentioned_bot": message.mentioned_bot,
        "quote_msgid": message.quote_msgid,
        "create_time": _iso(message.create_time),
    }


def _message_counts_by(session: Session, field) -> dict[str, int]:
    rows = session.execute(
        select(field, func.count()).where(field.is_not(None)).group_by(field)
    ).all()
    return {str(key): count for key, count in rows}


def _latest_profile_summaries(session: Session) -> dict[str, str]:
    profiles = session.scalars(
        select(UserProfile)
        .where(UserProfile.status == "active")
        .order_by(UserProfile.userid.asc(), UserProfile.version.desc())
    ).all()
    summaries = {}
    for profile in profiles:
        summaries.setdefault(profile.userid, profile.summary)
    return summaries


def _message_trend(messages: list[Message], group_by: str) -> list[dict[str, Any]]:
    counts = Counter(_bucket(_to_display_tz(message.create_time), group_by) for message in messages)
    return [
        {"bucket": bucket, "count": counts[bucket]}
        for bucket in sorted(counts)
    ]


def _ranking(counter: Counter, *, key_name: str) -> list[dict[str, Any]]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    ]


def _bucket(value: datetime, group_by: str) -> str:
    if group_by == "hour":
        return value.strftime("%Y-%m-%d %H:00")
    return value.strftime("%Y-%m-%d")


def _messages_between(
    messages: list[Message],
    start: datetime | None,
    end: datetime | None,
) -> list[Message]:
    return [
        message
        for message in messages
        if _matches_optional_window(message.create_time, start, end)
    ]


def _matches_optional_window(
    value: datetime,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    value_utc = _to_utc(value)
    if start and value_utc < start:
        return False
    if end and value_utc > end:
        return False
    return True


def _trigger_event_business_time(
    event: TriggerEvent,
    message_by_id: dict[int, Message],
) -> datetime:
    message = message_by_id.get(event.message_id)
    return message.create_time if message else event.created_at


def _ai_run_business_time(
    run: AiRun,
    trigger_by_id: dict[int, TriggerEvent],
    message_by_id: dict[int, Message],
) -> datetime:
    if run.trigger_event_id:
        trigger_event = trigger_by_id.get(run.trigger_event_id)
        if trigger_event:
            return _trigger_event_business_time(trigger_event, message_by_id)

    input_json = run.input_json if isinstance(run.input_json, dict) else {}
    for value in (
        (input_json.get("message") or {}).get("create_time"),
        input_json.get("start_time"),
        (input_json.get("scope") or {}).get("start_time"),
        (input_json.get("window") or {}).get("start_time"),
    ):
        parsed = _parse_datetime_or_none(value)
        if parsed:
            return parsed

    recent_messages = input_json.get("recent_messages") or []
    if recent_messages:
        parsed = _parse_datetime_or_none(recent_messages[0].get("create_time"))
        if parsed:
            return parsed

    return run.created_at


def _scheduled_intent_business_time(
    intent: ScheduledIntent,
    ai_run_by_id: dict[int, AiRun],
    trigger_by_id: dict[int, TriggerEvent],
    message_by_id: dict[int, Message],
) -> datetime:
    ai_run = ai_run_by_id.get(intent.ai_run_id)
    if ai_run:
        return _ai_run_business_time(ai_run, trigger_by_id, message_by_id)
    return intent.created_at


def _in_window(value: datetime, start: datetime, end: datetime) -> bool:
    value_utc = _to_utc(value)
    return start <= value_utc < end


def _day_window(value: date_type) -> tuple[datetime, datetime]:
    local_start = datetime.combine(value, time.min, tzinfo=DISPLAY_TZ)
    return local_start.astimezone(timezone.utc), (
        local_start + timedelta(days=1)
    ).astimezone(timezone.utc)


def _parse_date(value: str) -> date_type:
    return date_type.fromisoformat(value)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_display_tz(value: datetime) -> datetime:
    return _to_utc(value).astimezone(DISPLAY_TZ)


def _count_where(session: Session, condition, model) -> int:
    return session.scalar(select(func.count()).select_from(model).where(condition)) or 0


def _status_count(runs: list[AiRun], status: str) -> int:
    return sum(1 for run in runs if run.status == status)


def _success_rate(runs: list[AiRun]) -> float:
    if not runs:
        return 0.0
    return round(_status_count(runs, "success") / len(runs), 2)


def _avg_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _iso(value):
    return value.isoformat() if value else None
