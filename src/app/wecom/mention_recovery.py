from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiRun, Message, OutboxMessage, TriggerEvent, TriggerRule
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow, run_dify_workflow_for_trigger
from app.outbound.services import create_reply_outbox_for_ai_run, outbox_to_dict
from app.wecom.reply_sessions import attach_reply_session_to_message_outbox


def run_mention_recovery(
    session: Session,
    *,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
    dify_client: DifyClient,
    auto_enqueue: bool = True,
) -> dict[str, Any]:
    messages = _unreplied_mention_messages(session, chatid, start_time, end_time)
    if not messages:
        return {
            "status": "skipped_no_messages",
            "recovered_count": 0,
            "outboxes": [],
        }

    rule = _enabled_mention_rule(session)
    if rule is None:
        return {
            "status": "skipped_no_rule",
            "recovered_count": 0,
            "outboxes": [],
        }

    workflow = get_enabled_workflow(session, workflow_code=rule.workflow_code)
    outboxes: list[dict[str, Any]] = []
    recovered_count = 0
    for message in messages:
        event, _duplicated = _get_or_create_recovery_event(session, rule, message)
        ai_run = run_dify_workflow_for_trigger(
            session,
            trigger_event=event,
            message=message,
            workflow=workflow,
            dify_client=dify_client,
        )
        outbox_result = None
        if auto_enqueue:
            outbox_result = create_reply_outbox_for_ai_run(session, ai_run)
            if outbox_result:
                outbox = session.scalar(
                    select(OutboxMessage).where(
                        OutboxMessage.outbox_id == outbox_result["outbox_id"]
                    )
                )
                if outbox:
                    outbox.scene = "reply_recovery"
                    attach_reply_session_to_message_outbox(
                        session,
                        message=message,
                        trigger_event_id=event.id,
                        outbox_id=outbox.outbox_id,
                    )
                    outbox_result = outbox_to_dict(
                        outbox,
                        duplicated=bool(outbox_result.get("duplicated")),
                    )
                    outboxes.append(outbox_result)

        if ai_run.status == "success":
            event.status = "handled"
            recovered_count += 1
        else:
            event.status = ai_run.status
        session.flush()

    session.commit()
    return {
        "status": "success" if recovered_count else "skipped_no_success",
        "recovered_count": recovered_count,
        "outboxes": outboxes,
    }


def _unreplied_mention_messages(
    session: Session,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    start_utc = _to_utc(start_time)
    end_utc = _to_utc(end_time)
    messages = session.scalars(
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.create_time >= start_utc,
            Message.create_time <= end_utc,
            Message.sender_type == "user",
            Message.mentioned_bot.is_(True),
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()
    return [message for message in messages if not _has_pending_or_sent_reply(session, message)]


def _has_pending_or_sent_reply(session: Session, message: Message) -> bool:
    rows = session.execute(
        select(AiRun, OutboxMessage)
        .join(TriggerEvent, AiRun.trigger_event_id == TriggerEvent.id)
        .join(OutboxMessage, OutboxMessage.source_id == AiRun.run_id)
        .where(
            TriggerEvent.message_id == message.id,
            TriggerEvent.trigger_type == "mention",
            OutboxMessage.scene.in_(["reply", "reply_recovery"]),
            OutboxMessage.status.in_(["pending", "sending", "sent"]),
        )
    ).first()
    return rows is not None


def _enabled_mention_rule(session: Session) -> TriggerRule | None:
    return session.scalar(
        select(TriggerRule)
        .where(
            TriggerRule.trigger_type == "mention",
            TriggerRule.enabled.is_(True),
        )
        .order_by(TriggerRule.priority.desc(), TriggerRule.id.asc())
    )


def _get_or_create_recovery_event(
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
            "source": "message_reconcile_mention_recovery",
        },
        status="pending",
    )
    session.add(event)
    session.flush()
    return event, False


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
