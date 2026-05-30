from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import (
    AiRun,
    MentionRequest,
    Message,
    OutboxMessage,
    TriggerEvent,
    WeComMcpPullCursor,
    WeComReplySession,
)
from app.main import create_app
from app.proactive_replies.services import (
    ProactiveReplyOutputValidationError,
    _validate_proactive_output,
    _messages_in_window,
    build_chat_proactive_reminder_input,
)
from app.wecom.message_reconcile import (
    calculate_reconcile_window,
    run_message_reconcile_once,
)
from app.wecom.services import backfill_message_sender_classification


class RecordingMessageSource:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def fetch_messages(self, *, chatid, start_time, end_time):
        self.calls.append(
            {
                "chatid": chatid,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        return self.messages


class ChatProactiveReminderDifyClient:
    def __init__(self):
        self.calls = []

    def run_workflow(self, workflow, input_json):
        self.calls.append((workflow, input_json))
        return {
            "should_send": True,
            "target_userids": ["USER_A"],
            "quote_msgid": "MSG_RECONCILE_USER",
            "content": "猜你可能想了解审批要求。",
            "confidence": 0.82,
        }


class ReplyAndProactiveDifyClient:
    def __init__(self):
        self.calls = []

    def run_workflow(self, workflow, input_json):
        self.calls.append((workflow.workflow_code, input_json))
        if workflow.workflow_code == "group_knowledge_reply":
            return {
                "action": "reply",
                "content": "这是恢复后的 @ 回复。",
                "reason": "补漏识别到未处理 @ 消息。",
                "confidence": 0.91,
            }
        return {
            "should_send": True,
            "target_userids": ["USER_A"],
            "quote_msgid": "MSG_RECONCILE_USER",
            "content": "猜你可能想了解审批要求。",
            "confidence": 0.82,
        }


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage15.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_aibot_name="意图机器人",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_reply_aibot_name="回复机器人",
        wecom_intent_aibot_id="INTENT_BOT",
        wecom_intent_aibot_name="意图机器人",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def ingest_text(
    client,
    *,
    msgid,
    userid,
    content,
    mentioned_users=None,
    create_time=1777827600,
):
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "aibot_ws",
            "idempotency_key": f"aibot_ws_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": mentioned_users or [],
                "create_time": create_time,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def test_ingested_messages_have_sender_classification(tmp_path):
    client, app = make_client(tmp_path)

    user_message_id = ingest_text(
        client,
        msgid="MSG_USER",
        userid="USER_A",
        content="客户问报价审批要求。",
    )
    reply_bot_message_id = ingest_text(
        client,
        msgid="MSG_REPLY_BOT",
        userid="REPLY_BOT",
        content="这是机器人回复。",
    )

    with app.state.SessionLocal() as session:
        user_message = session.get(Message, user_message_id)
        bot_message = session.get(Message, reply_bot_message_id)

        assert user_message.sender_type == "user"
        assert user_message.bot_role is None
        assert bot_message.sender_type == "bot"
        assert bot_message.bot_role == "reply_bot"


def test_backfill_message_sender_classification_corrects_legacy_bot_rows(tmp_path):
    client, app = make_client(tmp_path)
    bot_message_id = ingest_text(
        client,
        msgid="MSG_LEGACY_BOT",
        userid="REPLY_BOT",
        content="升级前的机器人消息。",
    )

    with app.state.SessionLocal() as session:
        bot_message = session.get(Message, bot_message_id)
        bot_message.sender_type = "user"
        bot_message.bot_role = None
        session.commit()

        result = backfill_message_sender_classification(session, app.state.settings)
        refreshed = session.get(Message, bot_message_id)

        assert result == {"updated_count": 1}
        assert refreshed.sender_type == "bot"
        assert refreshed.bot_role == "reply_bot"


def test_proactive_window_reads_only_non_mentioned_user_messages(tmp_path):
    client, app = make_client(tmp_path)
    ingest_text(
        client,
        msgid="MSG_USER_2",
        userid="USER_A",
        content="我不确定审批要求。",
    )
    ingest_text(
        client,
        msgid="MSG_BOT_2",
        userid="REPLY_BOT",
        content="机器人上一条回复。",
    )
    ingest_text(
        client,
        msgid="MSG_MENTION_2",
        userid="USER_A",
        content="@回复机器人 查一下审批要求。",
        mentioned_users=["REPLY_BOT"],
    )

    with app.state.SessionLocal() as session:
        messages = _messages_in_window(
            session,
            "CHAT_STAGE15",
            datetime.fromtimestamp(1777827500, tz=timezone.utc),
            datetime.fromtimestamp(1777827700, tz=timezone.utc),
        )

        assert [message.external_msgid for message in messages] == ["MSG_USER_2"]


def test_proactive_payload_marks_already_handled_mentions(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text(
        client,
        msgid="MSG_HANDLED_AT",
        userid="USER_A",
        content="@回复机器人 查一下审批要求",
        mentioned_users=["REPLY_BOT"],
    )

    with app.state.SessionLocal() as session:
        session.add(
            TriggerEvent(
                rule_code="default_mention_reply",
                trigger_type="mention",
                message_id=message_id,
                chatid="CHAT_STAGE15",
                userid="USER_A",
                workflow_code="group_knowledge_reply",
                reason={"source": "long_connection"},
                status="handled",
            )
        )
        session.commit()

        messages = [session.get(Message, message_id)]
        payload = build_chat_proactive_reminder_input(
            session=session,
            messages=messages,
        )

        assert payload["payload"]["handled_records"] == [
            {
                "msgid": "MSG_HANDLED_AT",
                "message_id": str(message_id),
                "handler": "group_knowledge_reply",
                "reason": "mention_already_handled",
            }
        ]


def test_proactive_payload_marks_cross_source_duplicate_mentions_as_handled(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text(
        client,
        msgid="MSG_LONG_CONNECTION_AT",
        userid="USER_A",
        content="@回复机器人 查一下审批要求",
        mentioned_users=["REPLY_BOT"],
        create_time=1777827600,
    )

    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "wecom_reconcile",
            "idempotency_key": "reconcile_payload_cross_source_at",
            "raw_message": {
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人 查一下审批要求"},
                "mentioned_users": [],
                "create_time": 1777827601,
            },
        },
    )
    assert response.status_code == 200
    duplicate_message_id = response.json()["data"]["message_id"]

    with app.state.SessionLocal() as session:
        session.add(
            TriggerEvent(
                rule_code="default_mention_reply",
                trigger_type="mention",
                message_id=message_id,
                chatid="CHAT_STAGE15",
                userid="USER_A",
                workflow_code="group_knowledge_reply",
                reason={"source": "long_connection"},
                status="handled",
            )
        )
        session.commit()

        duplicate_message = session.get(Message, duplicate_message_id)
        payload = build_chat_proactive_reminder_input(
            session=session,
            messages=[duplicate_message],
        )

        assert payload["payload"]["handled_records"] == [
            {
                "msgid": duplicate_message.external_msgid,
                "message_id": str(duplicate_message.id),
                "handler": "group_knowledge_reply",
                "reason": "mention_already_handled",
            }
        ]


def test_proactive_output_cannot_quote_already_handled_message(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text(
        client,
        msgid="MSG_HANDLED_QUOTE",
        userid="USER_A",
        content="@回复机器人 查一下审批要求",
        mentioned_users=["REPLY_BOT"],
    )

    with app.state.SessionLocal() as session:
        session.add(
            TriggerEvent(
                rule_code="default_mention_reply",
                trigger_type="mention",
                message_id=message_id,
                chatid="CHAT_STAGE15",
                userid="USER_A",
                workflow_code="group_knowledge_reply",
                reason={"source": "long_connection"},
                status="handled",
            )
        )
        session.commit()

        messages = [session.get(Message, message_id)]
        payload = build_chat_proactive_reminder_input(
            session=session,
            messages=messages,
        )

        with pytest.raises(ProactiveReplyOutputValidationError):
            _validate_proactive_output(
                {
                    "should_send": True,
                    "target_userids": ["USER_A"],
                    "quote_msgid": "MSG_HANDLED_QUOTE",
                    "content": "猜你想了解审批要求。",
                    "confidence": 0.9,
                },
                payload,
            )


def test_reconcile_window_uses_last_pull_minus_overlap_when_available():
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)
    last_pulled_at = datetime.fromtimestamp(1777827600, tz=timezone.utc)

    start, end = calculate_reconcile_window(
        now=now,
        last_pulled_at=last_pulled_at,
        lookback_seconds=12,
        overlap_seconds=2,
    )

    assert start == datetime.fromtimestamp(1777827598, tz=timezone.utc)
    assert end == now


def test_reconcile_window_uses_lookback_without_cursor():
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)

    start, end = calculate_reconcile_window(
        now=now,
        last_pulled_at=None,
        lookback_seconds=12,
        overlap_seconds=2,
    )

    assert start == datetime.fromtimestamp(1777827598, tz=timezone.utc)
    assert end == now


def test_message_reconcile_ingests_then_runs_proactive_scan_and_updates_cursor(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "MSG_RECONCILE_USER",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "客户问审批要求，我不确定。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ChatProactiveReminderDifyClient()
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=now,
        )

        cursor = session.scalar(
            select(WeComMcpPullCursor).where(
                WeComMcpPullCursor.chatid == "CHAT_STAGE15",
                WeComMcpPullCursor.cursor_type == "message_reconcile",
            )
        )
        stored_message = session.scalar(
            select(Message).where(Message.external_msgid == "MSG_RECONCILE_USER")
        )

        assert result["fetched_count"] == 1
        assert result["ingested_count"] == 1
        assert result["duplicated_count"] == 0
        assert result["proactive_status"] == "success"
        assert result["outbox_count"] == 1
        assert cursor.last_pulled_at.replace(tzinfo=timezone.utc) == now
        assert stored_message.sender_type == "user"
        assert dify_client.calls[0][1]["payload"]["messages"][0]["msgid"] == (
            "MSG_RECONCILE_USER"
        )
        assert message_source.calls == [
            {
                "chatid": "CHAT_STAGE15",
                "start_time": datetime.fromtimestamp(1777827598, tz=timezone.utc),
                "end_time": now,
            }
        ]


def test_message_reconcile_reports_duplicate_pulled_messages(tmp_path):
    _client, app = make_client(tmp_path)
    raw_message = {
        "msgid": "MSG_RECONCILE_DUP",
        "chatid": "CHAT_STAGE15",
        "chattype": "group",
        "from": {"userid": "USER_A", "name": "USER_A"},
        "msgtype": "text",
        "text": {"content": "客户问审批要求，我不确定。"},
        "mentioned_users": [],
        "create_time": 1777827600,
    }
    first_source = RecordingMessageSource([raw_message])
    second_source = RecordingMessageSource([raw_message])
    dify_client = ChatProactiveReminderDifyClient()

    with app.state.SessionLocal() as session:
        run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=first_source,
            dify_client=dify_client,
            auto_enqueue=False,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=second_source,
            dify_client=dify_client,
            auto_enqueue=False,
            now=datetime.fromtimestamp(1777827620, tz=timezone.utc),
        )

        assert result["fetched_count"] == 1
        assert result["ingested_count"] == 0
        assert result["duplicated_count"] == 1


def test_message_reconcile_keeps_and_links_messages_already_ingested_by_long_connection(
    tmp_path,
):
    client, app = make_client(tmp_path)
    ingest_text(
        client,
        msgid="MSG_CROSS_SOURCE",
        userid="USER_A",
        content="长连接已经收到的消息。",
    )
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "MSG_CROSS_SOURCE",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "长连接已经收到的消息。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ChatProactiveReminderDifyClient()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=False,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        stored_messages = session.scalars(
            select(Message).where(Message.external_msgid == "MSG_CROSS_SOURCE")
        ).all()

        assert result["fetched_count"] == 1
        assert result["ingested_count"] == 1
        assert result["duplicated_count"] == 0
        assert len(stored_messages) == 2
        assert stored_messages[0].business_identity_key
        assert stored_messages[1].business_identity_key == stored_messages[0].business_identity_key
        assert stored_messages[1].canonical_message_id == stored_messages[0].id


def test_cross_source_payload_message_keeps_row_but_links_business_identity(tmp_path):
    client, app = make_client(tmp_path)
    first_message_id = ingest_text(
        client,
        msgid="MSG_BUSINESS_IDENTITY_WS",
        userid="USER_A",
        content="@回复机器人 查一下审批要求。",
        mentioned_users=["REPLY_BOT"],
        create_time=1777827600,
    )

    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "wecom_reconcile",
            "idempotency_key": "reconcile_business_identity_payload",
            "raw_message": {
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827602,
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["duplicated"] is False
    assert result["business_duplicated"] is True
    assert result["canonical_message_id"] == first_message_id

    with app.state.SessionLocal() as session:
        messages = session.scalars(select(Message).order_by(Message.id)).all()

        assert len(messages) == 2
        assert messages[0].business_identity_key
        assert messages[1].business_identity_key == messages[0].business_identity_key
        assert messages[0].canonical_message_id == messages[0].id
        assert messages[1].canonical_message_id == messages[0].id


def test_message_reconcile_skips_mention_recovery_when_request_is_running(tmp_path):
    client, app = make_client(tmp_path)
    first_message_id = ingest_text(
        client,
        msgid="MSG_RUNNING_REQUEST_WS",
        userid="USER_A",
        content="@回复机器人 查一下审批要求。",
        mentioned_users=["REPLY_BOT"],
        create_time=1777827600,
    )

    with app.state.SessionLocal() as session:
        first_message = session.get(Message, first_message_id)
        session.add(
            MentionRequest(
                request_id="mention_request_running",
                business_identity_key=first_message.business_identity_key,
                canonical_message_id=first_message.id,
                chatid=first_message.chatid,
                userid=first_message.userid,
                content_fingerprint="running-request-fingerprint",
                source_msgid=first_message.external_msgid,
                req_id="REQ_RUNNING_REQUEST",
                status="running",
                owner="long_connection",
            )
        )
        session.commit()

        message_source = RecordingMessageSource(
            [
                {
                    "msgid": "",
                    "chatid": "CHAT_STAGE15",
                    "chattype": "group",
                    "from": {"userid": "USER_A", "name": "USER_A"},
                    "msgtype": "text",
                    "text": {"content": "@回复机器人查一下审批要求。"},
                    "mentioned_users": [],
                    "create_time": 1777827602,
                }
            ]
        )
        dify_client = ReplyAndProactiveDifyClient()

        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        stored_messages = session.scalars(select(Message).order_by(Message.id)).all()
        request = session.scalar(select(MentionRequest))

        assert len(stored_messages) == 2
        assert stored_messages[1].canonical_message_id == first_message_id
        assert result["mention_recovery_count"] == 0
        assert result["mention_recovery_outbox_count"] == 0
        assert [call[0] for call in dify_client.calls] == []
        assert request.status == "running"


def test_message_reconcile_marks_running_mention_request_stalled_without_rerun(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text(
        client,
        msgid="MSG_STALLED_REQUEST_WS",
        userid="USER_A",
        content="@回复机器人 查一下审批要求。",
        mentioned_users=["REPLY_BOT"],
        create_time=1777827600,
    )

    with app.state.SessionLocal() as session:
        message = session.get(Message, message_id)
        session.add(
            MentionRequest(
                request_id="mention_request_stalled",
                business_identity_key=message.business_identity_key,
                canonical_message_id=message.id,
                chatid=message.chatid,
                userid=message.userid,
                content_fingerprint="stalled-request-fingerprint",
                source_msgid=message.external_msgid,
                req_id="REQ_STALLED_REQUEST",
                status="running",
                owner="long_connection",
                started_at=datetime.now(timezone.utc) - timedelta(seconds=181),
            )
        )
        session.commit()

        message_source = RecordingMessageSource(
            [
                {
                    "msgid": "",
                    "chatid": "CHAT_STAGE15",
                    "chattype": "group",
                    "from": {"userid": "USER_A", "name": "USER_A"},
                    "msgtype": "text",
                    "text": {"content": "@回复机器人查一下审批要求。"},
                    "mentioned_users": [],
                    "create_time": 1777827602,
                }
            ]
        )
        dify_client = ReplyAndProactiveDifyClient()

        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        request = session.scalar(select(MentionRequest))

        assert result["mention_recovery_count"] == 0
        assert request.status == "stalled"
        assert request.stalled_at is not None
        assert dify_client.calls == []


def test_message_reconcile_skips_dify_when_window_has_only_bot_messages(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "MSG_RECONCILE_BOT",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "REPLY_BOT", "name": "回复机器人"},
                "msgtype": "text",
                "text": {"content": "机器人回复内容。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ChatProactiveReminderDifyClient()
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=now,
        )

        cursor = session.scalar(
            select(WeComMcpPullCursor).where(
                WeComMcpPullCursor.chatid == "CHAT_STAGE15",
                WeComMcpPullCursor.cursor_type == "message_reconcile",
            )
        )

        assert result["fetched_count"] == 1
        assert result["ingested_count"] == 1
        assert result["duplicated_count"] == 0
        assert result["proactive_status"] == "skipped_no_messages"
        assert result["outbox_count"] == 0
        assert dify_client.calls == []
        assert cursor.last_pulled_at.replace(tzinfo=timezone.utc) == now


def test_message_reconcile_routes_only_mentions_to_reply_recovery(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人 查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ReplyAndProactiveDifyClient()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        assert result["fetched_count"] == 1
        assert result["ingested_count"] == 1
        assert result["mention_recovery_count"] == 1
        assert result["proactive_status"] == "skipped_no_messages"
        assert [call[0] for call in dify_client.calls] == ["group_knowledge_reply"]


def test_message_reconcile_recovers_unhandled_mention_through_reply_workflow(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人 查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ReplyAndProactiveDifyClient()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        ai_run = session.scalar(select(AiRun))
        outbox = session.scalar(select(OutboxMessage))

        assert result["mention_recovery_count"] == 1
        assert result["proactive_status"] == "skipped_no_messages"
        assert [call[0] for call in dify_client.calls] == ["group_knowledge_reply"]
        assert ai_run.workflow_code == "group_knowledge_reply"
        assert outbox.scene == "reply_recovery"
        assert outbox.content["markdown"]["content"] == "这是恢复后的 @ 回复。"


def test_message_reconcile_reuses_callback_placeholder_when_pulled_text_spacing_differs(
    tmp_path,
):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ReplyAndProactiveDifyClient()

    with app.state.SessionLocal() as session:
        session.add(
            WeComReplySession(
                session_id="reply_session_spacing",
                chatid="CHAT_STAGE15",
                userid="USER_A",
                source_msgid="MSG_LONG_CONNECTION",
                req_id="REQ_LONG_CONNECTION",
                content_fingerprint="legacy_spacing_fingerprint",
                frame_json={
                    "headers": {"req_id": "REQ_LONG_CONNECTION"},
                    "body": {
                        "msgid": "MSG_LONG_CONNECTION",
                        "chatid": "CHAT_STAGE15",
                        "from": {"userid": "USER_A"},
                        "msgtype": "text",
                        "text": {"content": "@回复机器人 查一下审批要求。"},
                    },
                },
                stream_id="STREAM_LONG_CONNECTION",
                placeholder_status="sent",
                final_status="pending",
            )
        )
        session.commit()

        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        outbox = session.scalar(select(OutboxMessage))
        reply_session = session.scalar(select(WeComReplySession))

        assert result["mention_recovery_count"] == 1
        assert outbox.scene == "reply_recovery"
        assert reply_session.outbox_id == outbox.outbox_id
        assert reply_session.stream_id == "STREAM_LONG_CONNECTION"


def test_sent_callback_placeholder_is_not_overwritten_by_recovery(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            }
        ]
    )
    dify_client = ReplyAndProactiveDifyClient()

    with app.state.SessionLocal() as session:
        session.add(
            WeComReplySession(
                session_id="reply_session_sent",
                chatid="CHAT_STAGE15",
                userid="USER_A",
                source_msgid="MSG_ALREADY_SENT",
                req_id="REQ_ALREADY_SENT",
                content_fingerprint="legacy_sent_fingerprint",
                frame_json={
                    "headers": {"req_id": "REQ_ALREADY_SENT"},
                    "body": {
                        "msgid": "MSG_ALREADY_SENT",
                        "chatid": "CHAT_STAGE15",
                        "from": {"userid": "USER_A"},
                        "msgtype": "text",
                        "text": {"content": "@回复机器人 查一下审批要求。"},
                    },
                },
                stream_id="STREAM_ALREADY_SENT",
                placeholder_status="sent",
                final_status="sent",
                outbox_id="out_already_sent",
            )
        )
        session.commit()

        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        reply_session = session.scalar(select(WeComReplySession))
        outbox = session.scalar(select(OutboxMessage))

        assert result["mention_recovery_count"] == 1
        assert outbox.scene == "reply_recovery"
        assert reply_session.outbox_id == "out_already_sent"
        assert reply_session.final_status == "sent"


def test_message_reconcile_excludes_mentions_from_proactive_payload(tmp_path):
    _client, app = make_client(tmp_path)
    message_source = RecordingMessageSource(
        [
            {
                "msgid": "MSG_RECONCILE_USER",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "客户问审批要求，我不确定。"},
                "mentioned_users": [],
                "create_time": 1777827600,
            },
            {
                "msgid": "",
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@回复机器人 查一下审批要求。"},
                "mentioned_users": [],
                "create_time": 1777827601,
            },
        ]
    )
    dify_client = ReplyAndProactiveDifyClient()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE15",
            settings=app.state.settings,
            message_source=message_source,
            dify_client=dify_client,
            auto_enqueue=True,
            now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
        )

        proactive_call = next(
            call for call in dify_client.calls if call[0] == "chat_proactive_reminder"
        )
        input_json = proactive_call[1]
        assert result["fetched_count"] == 2
        assert result["mention_recovery_count"] == 1
        assert result["proactive_status"] == "success"
        assert [item["msgid"] for item in input_json["payload"]["messages"]] == [
            "MSG_RECONCILE_USER"
        ]
