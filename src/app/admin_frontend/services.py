import asyncio
import json
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.db.models import AiRun, MentionRequest, Message, OutboxMessage, TriggerEvent

DISPLAY_TZ = timezone(timedelta(hours=8))
MAX_RANGE_DAYS = 31


def list_messages(
    session: Session,
    *,
    start_date: str | None,
    end_date: str | None,
    chatid: str | None,
    sender_type: str | None,
    mentioned_bot: bool | None,
    include_source_duplicates: bool,
    q: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    start_time, end_time = _date_range(start_date, end_date)
    rows: list[dict[str, Any]] = []
    total = 0

    if sender_type != "bot":
        message_filters = _message_filters(
            start_time=start_time,
            end_time=end_time,
            chatid=chatid,
            sender_type=sender_type,
            mentioned_bot=mentioned_bot,
            include_source_duplicates=include_source_duplicates,
            q=q,
        )
        message_total = (
            session.scalar(select(func.count()).select_from(Message).where(*message_filters))
            or 0
        )
        total += message_total
        messages = session.scalars(
            select(Message)
            .where(*message_filters)
            .order_by(Message.create_time.desc(), Message.id.desc())
            .limit(offset + limit)
        ).all()
        rows.extend(message_to_dict(message) for message in messages)

    if sender_type in {None, "", "bot"} and mentioned_bot is not True:
        outbox_filters = _outbox_message_filters(
            start_time=start_time,
            end_time=end_time,
            chatid=chatid,
        )
        outbox_statement = (
            select(OutboxMessage)
            .where(*outbox_filters)
            .order_by(
                func.coalesce(OutboxMessage.sent_at, OutboxMessage.created_at).desc(),
                OutboxMessage.id.desc(),
            )
        )
        if q:
            outboxes = session.scalars(outbox_statement).all()
        else:
            total += (
                session.scalar(
                    select(func.count()).select_from(OutboxMessage).where(*outbox_filters)
                )
                or 0
            )
            outboxes = session.scalars(outbox_statement.limit(offset + limit)).all()
        outbox_rows = [_outbox_to_message_dict(outbox) for outbox in outboxes]
        if q:
            needle = q.lower()
            outbox_rows = [
                row
                for row in outbox_rows
                if needle in str(row.get("content_text") or "").lower()
            ]
            total += len(outbox_rows)
        rows.extend(outbox_rows)

    rows = _dedupe_message_rows(rows)
    rows.sort(key=_message_row_sort_key, reverse=True)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": rows[offset : offset + limit],
    }


def _message_filters(
    *,
    start_time: datetime,
    end_time: datetime,
    chatid: str | None,
    sender_type: str | None,
    mentioned_bot: bool | None,
    include_source_duplicates: bool,
    q: str | None,
) -> list[Any]:
    filters: list[Any] = [
        Message.create_time >= start_time,
        Message.create_time < end_time,
    ]
    if chatid:
        filters.append(Message.chatid == chatid)
    if sender_type:
        filters.append(Message.sender_type == sender_type)
    if mentioned_bot is not None:
        filters.append(Message.mentioned_bot.is_(mentioned_bot))
    if not include_source_duplicates:
        filters.append(
            or_(
                Message.canonical_message_id.is_(None),
                Message.canonical_message_id == Message.id,
            )
        )
    if q:
        filters.append(func.lower(Message.content_text).like(f"%{q.lower()}%"))
    return filters


def _outbox_message_filters(
    *,
    start_time: datetime,
    end_time: datetime,
    chatid: str | None,
) -> list[Any]:
    filters: list[Any] = [
        OutboxMessage.status == "sent",
        or_(
            and_(
                OutboxMessage.sent_at.is_not(None),
                OutboxMessage.sent_at >= start_time,
                OutboxMessage.sent_at < end_time,
            ),
            and_(
                OutboxMessage.sent_at.is_(None),
                OutboxMessage.created_at >= start_time,
                OutboxMessage.created_at < end_time,
            ),
        ),
    ]
    if chatid:
        filters.append(OutboxMessage.chatid == chatid)
    return filters


def list_reply_tasks(
    session: Session,
    *,
    status: str | None,
    task_type: str | None,
    chatid: str | None,
    start_date: str | None,
    end_date: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    start_time, end_time = _date_range(start_date, end_date)
    tasks = []
    if task_type in {None, "mention"}:
        mention_tasks, covered_trigger_event_ids = _mention_tasks(
            session,
            start_time=start_time,
            end_time=end_time,
        )
        tasks.extend(mention_tasks)
        tasks.extend(
            _trigger_reply_tasks(
                session,
                start_time=start_time,
                end_time=end_time,
                covered_trigger_event_ids=covered_trigger_event_ids,
            )
        )
    if task_type in {None, "proactive"}:
        tasks.extend(_proactive_tasks(session, start_time=start_time, end_time=end_time))

    if chatid:
        tasks = [task for task in tasks if task["chatid"] == chatid]
    if status:
        tasks = [task for task in tasks if task["status"] == status]
    if q:
        needle = q.lower()
        tasks = [
            task
            for task in tasks
            if needle in str(task["source_message"].get("content_text") or "").lower()
        ]

    tasks.sort(key=lambda item: item["updated_at"] or item["created_at"], reverse=True)
    return {
        "total": len(tasks),
        "limit": limit,
        "offset": offset,
        "items": tasks[offset : offset + limit],
    }


async def stream_admin_events(session_factory) -> Any:
    seen: dict[str, str] = {}
    event_index = 0
    while True:
        with session_factory() as session:
            result = list_reply_tasks(
                session,
                status=None,
                task_type=None,
                chatid=None,
                start_date=None,
                end_date=None,
                q=None,
                limit=100,
                offset=0,
            )
        for task in result["items"]:
            signature = json.dumps(
                {
                    "status": task["status"],
                    "nodes": task["nodes"],
                    "updated_at": task["updated_at"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if seen.get(task["task_id"]) == signature:
                continue
            seen[task["task_id"]] = signature
            event_index += 1
            yield _sse_event(
                {
                    "event_id": f"admin_event_{event_index}",
                    "event_type": "reply_task_updated",
                    "entity_type": "reply_task",
                    "entity_id": task["task_id"],
                    "payload": task,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        await asyncio.sleep(1)


def message_to_dict(message: Message | None) -> dict[str, Any]:
    if message is None:
        return {}
    return {
        "id": message.id,
        "external_msgid": message.external_msgid,
        "chatid": message.chatid,
        "chattype": message.chattype,
        "userid": message.userid,
        "msgtype": message.msgtype,
        "sender_type": message.sender_type,
        "bot_role": message.bot_role,
        "content_text": message.content_text,
        "mentioned_bot": message.mentioned_bot,
        "mentioned_users": message.mentioned_users,
        "quote_msgid": message.quote_msgid,
        "business_identity_key": message.business_identity_key,
        "canonical_message_id": message.canonical_message_id,
        "is_business_duplicate": bool(
            message.canonical_message_id
            and message.canonical_message_id != message.id
        ),
        "create_time": _iso(message.create_time),
        "created_at": _iso(message.created_at),
    }


def _outbox_to_message_dict(outbox: OutboxMessage) -> dict[str, Any]:
    create_time = _outbox_message_time(outbox)
    return {
        "id": f"outbox:{outbox.id}",
        "external_msgid": outbox.external_msgid or outbox.outbox_id,
        "chatid": outbox.chatid,
        "chattype": "group",
        "userid": None,
        "msgtype": outbox.msgtype,
        "sender_type": "bot",
        "bot_role": _outbox_bot_role(outbox),
        "content_text": _outbox_content_text(outbox),
        "mentioned_bot": False,
        "mentioned_users": [],
        "quote_msgid": _outbox_quote_msgid(outbox),
        "business_identity_key": None,
        "canonical_message_id": None,
        "is_business_duplicate": False,
        "create_time": _iso(create_time),
        "created_at": _iso(outbox.created_at),
    }


def _outbox_message_time(outbox: OutboxMessage) -> datetime:
    return outbox.sent_at or outbox.created_at


def _outbox_bot_role(outbox: OutboxMessage) -> str:
    if outbox.scene == "proactive":
        return "proactive_bot"
    return "reply_bot"


def _outbox_content_text(outbox: OutboxMessage) -> str:
    content = outbox.content
    if not isinstance(content, dict):
        return ""
    typed_content = content.get(outbox.msgtype)
    if isinstance(typed_content, dict):
        value = typed_content.get("content") or typed_content.get("text") or ""
        return str(value)
    if isinstance(typed_content, str):
        return typed_content
    value = content.get("content") or content.get("text") or ""
    return str(value)


def _outbox_quote_msgid(outbox: OutboxMessage) -> str | None:
    content = outbox.content
    if not isinstance(content, dict):
        return None
    typed_content = content.get(outbox.msgtype)
    if isinstance(typed_content, dict) and typed_content.get("quote_msgid"):
        return str(typed_content["quote_msgid"])
    if content.get("quote_msgid"):
        return str(content["quote_msgid"])
    return None


def _dedupe_message_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for row in rows:
        external_msgid = row.get("external_msgid")
        if not external_msgid:
            deduped.append(row)
            continue
        key = (
            str(row.get("sender_type") or ""),
            str(row.get("chatid") or ""),
            str(external_msgid),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _message_row_sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
    create_time = row.get("create_time")
    parsed_time = (
        datetime.fromisoformat(create_time)
        if isinstance(create_time, str) and create_time
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return _to_utc(parsed_time), str(row.get("id") or "")


def _mention_tasks(
    session: Session,
    *,
    start_time: datetime,
    end_time: datetime,
) -> tuple[list[dict[str, Any]], set[int]]:
    requests = session.scalars(
        select(MentionRequest).order_by(
            MentionRequest.updated_at.desc(),
            MentionRequest.id.desc(),
        )
    ).all()
    tasks = []
    covered_trigger_event_ids: set[int] = set()
    for request in requests:
        if request.trigger_event_id:
            covered_trigger_event_ids.add(request.trigger_event_id)
        message = _message_for_mention_request(session, request)
        business_time = _to_utc(message.create_time) if message else _to_utc(request.created_at)
        if not start_time <= business_time < end_time:
            continue
        ai_run = session.get(AiRun, request.ai_run_id) if request.ai_run_id else None
        if not ai_run and request.trigger_event_id:
            ai_run = session.scalar(
                select(AiRun).where(AiRun.trigger_event_id == request.trigger_event_id)
            )
        outbox = _outbox_for_request(session, request, ai_run)
        nodes = _reply_nodes(
            message=message,
            trigger_status=request.status,
            ai_run=ai_run,
            outbox=outbox,
        )
        tasks.append(
            {
                "task_id": request.request_id,
                "task_type": "mention",
                "status": _reply_task_status(nodes, request.status),
                "chatid": request.chatid,
                "source_message": message_to_dict(message),
                "nodes": nodes,
                "ai_run_id": ai_run.run_id if ai_run else None,
                "outbox_id": outbox.outbox_id if outbox else request.outbox_id,
                "error_message": _first_error(request.last_error, ai_run, outbox),
                "created_at": _iso(request.created_at),
                "updated_at": _iso(_max_datetime(request.updated_at, ai_run, outbox)),
            }
        )
    return tasks, covered_trigger_event_ids


def _trigger_reply_tasks(
    session: Session,
    *,
    start_time: datetime,
    end_time: datetime,
    covered_trigger_event_ids: set[int],
) -> list[dict[str, Any]]:
    events = session.scalars(
        select(TriggerEvent)
        .where(TriggerEvent.trigger_type == "mention")
        .order_by(TriggerEvent.created_at.desc(), TriggerEvent.id.desc())
    ).all()
    tasks = []
    for event in events:
        if event.id in covered_trigger_event_ids:
            continue
        message = session.get(Message, event.message_id)
        business_time = _to_utc(message.create_time) if message else _to_utc(event.created_at)
        if not start_time <= business_time < end_time:
            continue
        ai_run = session.scalar(select(AiRun).where(AiRun.trigger_event_id == event.id))
        outbox = _outbox_for_request(session, request=None, ai_run=ai_run)
        nodes = _reply_nodes(
            message=message,
            trigger_status=event.status,
            ai_run=ai_run,
            outbox=outbox,
        )
        tasks.append(
            {
                "task_id": f"mention_trigger_{event.id}",
                "task_type": "mention",
                "status": _reply_task_status(nodes, event.status),
                "chatid": event.chatid,
                "source_message": message_to_dict(message),
                "nodes": nodes,
                "ai_run_id": ai_run.run_id if ai_run else None,
                "outbox_id": outbox.outbox_id if outbox else None,
                "error_message": _first_error(None, ai_run, outbox),
                "created_at": _iso(event.created_at),
                "updated_at": _iso(_max_datetime(event.created_at, ai_run, outbox)),
            }
        )
    return tasks


def _proactive_tasks(
    session: Session,
    *,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    outboxes = session.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.scene == "proactive")
        .order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
    ).all()
    tasks = []
    for outbox in outboxes:
        ai_run = session.scalar(select(AiRun).where(AiRun.run_id == outbox.source_id))
        message = _source_message_for_ai_run(session, ai_run)
        business_time = (
            _to_utc(message.create_time)
            if message
            else _to_utc(ai_run.created_at if ai_run else outbox.created_at)
        )
        if not start_time <= business_time < end_time:
            continue
        nodes = _reply_nodes(
            message=message,
            trigger_status="window_scanned",
            ai_run=ai_run,
            outbox=outbox,
        )
        tasks.append(
            {
                "task_id": f"proactive_{outbox.outbox_id}",
                "task_type": "proactive",
                "status": _reply_task_status(nodes, None),
                "chatid": outbox.chatid,
                "source_message": message_to_dict(message),
                "nodes": nodes,
                "ai_run_id": ai_run.run_id if ai_run else outbox.source_id,
                "outbox_id": outbox.outbox_id,
                "error_message": _first_error(None, ai_run, outbox),
                "created_at": _iso(outbox.created_at),
                "updated_at": _iso(_max_datetime(outbox.created_at, ai_run, outbox)),
            }
        )
    return tasks


def _reply_nodes(
    *,
    message: Message | None,
    trigger_status: str | None,
    ai_run: AiRun | None,
    outbox: OutboxMessage | None,
) -> list[dict[str, Any]]:
    return [
        {
            "node_key": "message_ingested",
            "title": "消息入库",
            "status": "success" if message else "pending",
            "detail": message.external_msgid if message else "",
        },
        {
            "node_key": "trigger_claimed",
            "title": "触发认领",
            "status": _trigger_node_status(trigger_status),
            "detail": trigger_status or "",
        },
        {
            "node_key": "dify_run",
            "title": "Dify 调用",
            "status": _ai_run_node_status(ai_run),
            "detail": ai_run.run_id if ai_run else "",
        },
        {
            "node_key": "outbox_created",
            "title": "Outbox 创建",
            "status": _outbox_created_status(ai_run, outbox),
            "detail": outbox.outbox_id if outbox else "",
        },
        {
            "node_key": "wecom_send",
            "title": "企微发送",
            "status": _send_node_status(outbox),
            "detail": outbox.status if outbox else "",
        },
    ]


def _reply_task_status(nodes: list[dict[str, Any]], request_status: str | None) -> str:
    if request_status in {"failed", "stalled"}:
        return "error"
    if any(node["status"] == "error" for node in nodes):
        return "error"
    send_node = next(node for node in nodes if node["node_key"] == "wecom_send")
    if send_node["status"] == "success" or request_status == "completed":
        return "replied"
    return "replying"


def _trigger_node_status(status: str | None) -> str:
    if status in {"failed", "stalled"}:
        return "error"
    if status in {"claimed", "running"}:
        return "running"
    return "success" if status else "pending"


def _ai_run_node_status(ai_run: AiRun | None) -> str:
    if not ai_run:
        return "pending"
    if ai_run.status in {"pending", "running"}:
        return "running"
    if ai_run.status == "success":
        return "success"
    return "error"


def _outbox_created_status(ai_run: AiRun | None, outbox: OutboxMessage | None) -> str:
    if outbox:
        return "success"
    if ai_run and ai_run.status in {"failed", "invalid_output"}:
        return "error"
    if ai_run and ai_run.status == "success":
        return "running"
    return "pending"


def _send_node_status(outbox: OutboxMessage | None) -> str:
    if not outbox:
        return "pending"
    if outbox.status == "sent":
        return "success"
    if outbox.status == "failed":
        return "error"
    if outbox.status == "sending":
        return "running"
    return "running"


def _message_for_mention_request(session: Session, request: MentionRequest) -> Message | None:
    if request.canonical_message_id:
        message = session.get(Message, request.canonical_message_id)
        if message:
            return message
    if request.source_msgid:
        return session.scalar(
            select(Message).where(
                Message.chatid == request.chatid,
                Message.external_msgid == request.source_msgid,
            )
        )
    return None


def _outbox_for_request(
    session: Session,
    request: MentionRequest | None,
    ai_run: AiRun | None,
) -> OutboxMessage | None:
    if request is not None and request.outbox_id:
        outbox = session.scalar(
            select(OutboxMessage).where(OutboxMessage.outbox_id == request.outbox_id)
        )
        if outbox:
            return outbox
    if ai_run:
        return session.scalar(
            select(OutboxMessage).where(OutboxMessage.source_id == ai_run.run_id)
        )
    return None


def _source_message_for_ai_run(session: Session, ai_run: AiRun | None) -> Message | None:
    if not ai_run or not isinstance(ai_run.input_json, dict):
        return None
    payload = ai_run.input_json.get("payload")
    if not isinstance(payload, dict):
        return None
    message_payload = payload.get("message")
    if isinstance(message_payload, dict) and message_payload.get("message_id"):
        message = session.get(Message, int(message_payload["message_id"]))
        if message:
            return message
    if isinstance(message_payload, dict) and message_payload.get("msgid"):
        return session.scalar(
            select(Message).where(Message.external_msgid == str(message_payload["msgid"]))
        )
    return None


def _first_error(
    request_error: str | None,
    ai_run: AiRun | None,
    outbox: OutboxMessage | None,
) -> str | None:
    return request_error or (ai_run.error_message if ai_run else None) or (
        outbox.error_message if outbox else None
    )


def _max_datetime(
    base: datetime,
    ai_run: AiRun | None,
    outbox: OutboxMessage | None,
) -> datetime:
    values = [base]
    if ai_run:
        values.extend(value for value in [ai_run.created_at, ai_run.finished_at] if value)
    if outbox:
        values.extend(value for value in [outbox.created_at, outbox.sent_at] if value)
    return max(_to_utc(value) for value in values)


def _date_range(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime]:
    today = datetime.now(DISPLAY_TZ).date()
    start = _parse_date(start_date) if start_date else today
    end = _parse_date(end_date) if end_date else start
    if start > end:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "开始日期不能晚于截止日期")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "最多只能查询一个月内的数据")
    local_start = datetime.combine(start, time.min, tzinfo=DISPLAY_TZ)
    local_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _parse_date(value: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "日期格式必须为 YYYY-MM-DD") from exc


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
