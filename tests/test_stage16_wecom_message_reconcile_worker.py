from datetime import datetime, timezone
import json
import logging

import httpx

from app.config.settings import Settings
from app.db.models import OutboxMessage
from app.main import create_app
from app.wecom.message_reconcile_worker import MessageReconcileWorker
from app.wecom.message_source import WeComMcpMessageSource
from app.wecom.message_reconcile import run_message_reconcile_once


class ChatProactiveReminderDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "should_send": True,
            "target_userids": ["USER_A"],
            "quote_msgid": "MSG_AUTO_SEND",
            "content": "猜你可能想了解审批要求。",
            "confidence": 0.82,
        }


class RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, outbox):
        self.sent.append(outbox.outbox_id)
        return {
            "success": True,
            "external_msgid": f"SENT_{len(self.sent)}",
            "raw_response": {"errcode": 0, "errmsg": "ok", "msgid": f"SENT_{len(self.sent)}"},
        }


class FailingSender:
    def __init__(self):
        self.sent = []

    def send(self, outbox):
        self.sent.append(outbox.outbox_id)
        return {
            "success": False,
            "error_code": "SEND_FAILED",
            "error_message": "network closed",
            "raw_response": {"errmsg": "network closed"},
        }


class StaticMessageSource:
    def __init__(self):
        self.calls = []

    def fetch_messages(self, *, chatid, start_time, end_time):
        self.calls.append(chatid)
        return [
            {
                "msgid": "MSG_AUTO_SEND",
                "chatid": chatid,
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "客户问审批要求，我不确定。"},
                "mentioned_users": [],
                "create_time": "2026-05-29T02:00:03+00:00",
            }
        ]


def test_wecom_mcp_message_source_fetches_and_normalizes_messages():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config":
            body = json.loads(request.content.decode("utf-8"))
            assert body["bot_id"] == "INTENT_BOT"
            assert "signature" in body
            assert "intent-secret" not in request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "list": [
                        {
                            "biz_type": "msg",
                            "url": "https://mcp.example.local/rpc",
                        }
                    ],
                },
            )

        assert str(request.url) == "https://mcp.example.local/rpc"
        rpc = json.loads(request.content.decode("utf-8"))
        assert rpc["method"] == "tools/call"
        assert rpc["params"]["name"] == "get_message"
        assert rpc["params"]["arguments"]["chat_type"] == 2
        assert rpc["params"]["arguments"]["chatid"] == "CHAT_STAGE16"
        assert rpc["params"]["arguments"]["begin_time"] == "2026-05-29 10:00:00"
        assert rpc["params"]["arguments"]["end_time"] == "2026-05-29 10:00:12"
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "errcode": 0,
                                    "messages": [
                                        {
                                            "msgid": "MSG_MCP_1",
                                            "userid": "USER_A",
                                            "send_time": "2026-05-29 10:00:03",
                                            "msgtype": "text",
                                            "text": {"content": "客户问审批要求。"},
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            },
        )

    source = WeComMcpMessageSource(
        Settings(
            wecom_intent_aibot_id="INTENT_BOT",
            wecom_intent_aibot_secret="intent-secret",
            wecom_message_reconcile_pages=1,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    messages = source.fetch_messages(
        chatid="CHAT_STAGE16",
        start_time=datetime(2026, 5, 29, 2, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc),
    )

    assert messages == [
        {
            "msgid": "MSG_MCP_1",
            "chatid": "CHAT_STAGE16",
            "chattype": "group",
            "from": {"userid": "USER_A"},
            "msgtype": "text",
            "text": {"content": "客户问审批要求。"},
            "mentioned_users": [],
            "create_time": "2026-05-29T02:00:03+00:00",
        }
    ]
    assert len(requests) == 2


def test_wecom_mcp_message_source_logs_missing_userid(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "list": [
                        {
                            "biz_type": "msg",
                            "url": "https://mcp.example.local/rpc",
                        }
                    ],
                },
            )

        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "errcode": 0,
                                    "messages": [
                                        {
                                            "msgid": "MSG_MISSING_USERID",
                                            "send_time": "2026-05-29 10:00:03",
                                            "msgtype": "text",
                                            "text": {"content": "客户问审批要求。"},
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            },
        )

    source = WeComMcpMessageSource(
        Settings(
            wecom_intent_aibot_id="INTENT_BOT",
            wecom_intent_aibot_secret="intent-secret",
            wecom_message_reconcile_pages=1,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with caplog.at_level(logging.ERROR, logger="app.wecom.message_source"):
        messages = source.fetch_messages(
            chatid="CHAT_STAGE16",
            start_time=datetime(2026, 5, 29, 2, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc),
        )

    assert messages[0]["from"]["userid"] == ""
    assert "WeCom MCP message missing userid" in caplog.text
    assert "CHAT_STAGE16" in caplog.text
    assert "MSG_MISSING_USERID" in caplog.text


def test_message_reconcile_auto_sends_created_proactive_outbox(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage16.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_message_reconcile_auto_enqueue=True,
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    sender = RecordingSender()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE16",
            settings=settings,
            message_source=StaticMessageSource(),
            dify_client=ChatProactiveReminderDifyClient(),
            auto_enqueue=True,
            auto_send=True,
            sender=sender,
            now=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc),
        )
        outbox = session.query(OutboxMessage).one()

        assert result["outbox_count"] == 1
        assert result["sent_outbox_count"] == 1
        assert sender.sent == [outbox.outbox_id]
        assert outbox.status == "sent"


def test_message_reconcile_does_not_count_failed_send_as_sent(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage16_failed_send.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_message_reconcile_auto_enqueue=True,
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    sender = FailingSender()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE16",
            settings=settings,
            message_source=StaticMessageSource(),
            dify_client=ChatProactiveReminderDifyClient(),
            auto_enqueue=True,
            auto_send=True,
            sender=sender,
            now=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc),
        )
        outbox = session.query(OutboxMessage).one()

        assert result["outbox_count"] == 1
        assert result["sent_outbox_count"] == 0
        assert sender.sent == [outbox.outbox_id]
        assert outbox.status == "failed"


def test_message_reconcile_defers_aibot_ws_send_to_long_connection_worker(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage16_defer_aibot_ws.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_sender_mode="aibot_ws",
        wecom_message_reconcile_auto_enqueue=True,
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    sender = RecordingSender()

    with app.state.SessionLocal() as session:
        result = run_message_reconcile_once(
            session,
            chatid="CHAT_STAGE16",
            settings=settings,
            message_source=StaticMessageSource(),
            dify_client=ChatProactiveReminderDifyClient(),
            auto_enqueue=True,
            auto_send=True,
            sender=sender,
            now=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc),
        )
        outbox = session.query(OutboxMessage).one()

        assert result["outbox_count"] == 1
        assert result["sent_outbox_count"] == 0
        assert sender.sent == []
        assert outbox.status == "pending"


def test_message_reconcile_worker_runs_configured_chatids_once(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage16_worker.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_message_reconcile_chatids="CHAT_STAGE16",
        wecom_message_reconcile_auto_enqueue=True,
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    message_source = StaticMessageSource()
    sender = RecordingSender()
    worker = MessageReconcileWorker(
        settings=settings,
        session_factory=app.state.SessionLocal,
        message_source=message_source,
        dify_client=ChatProactiveReminderDifyClient(),
        sender=sender,
    )

    result = worker.run_once(now=datetime(2026, 5, 29, 2, 0, 12, tzinfo=timezone.utc))

    assert result["chat_count"] == 1
    assert result["results"][0]["chatid"] == "CHAT_STAGE16"
    assert result["results"][0]["sent_outbox_count"] == 1
    assert message_source.calls == ["CHAT_STAGE16"]
    assert len(sender.sent) == 1
