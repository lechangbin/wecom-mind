from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import Message, WeComMcpPullCursor, TriggerEvent
from app.main import create_app
from app.proactive_replies.services import (
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


def test_proactive_window_reads_only_user_messages(tmp_path):
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

        messages = _messages_in_window(
            session,
            "CHAT_STAGE15",
            datetime.fromtimestamp(1777827500, tz=timezone.utc),
            datetime.fromtimestamp(1777827700, tz=timezone.utc),
        )
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


def test_message_reconcile_deduplicates_messages_already_ingested_by_long_connection(
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
        assert result["ingested_count"] == 0
        assert result["duplicated_count"] == 1
        assert len(stored_messages) == 1


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
