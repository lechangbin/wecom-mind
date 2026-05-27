import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import Settings
from app.db.models import Message, MessageRaw, OutboxMessage
from app.main import create_app
from app.outbound.sender import WeComAiBotWsMessageSender
from app.wecom.aibot import (
    AiBotFrameNormalizer,
    WeComAiBotLongConnectionWorker,
    process_incoming_aibot_frame,
)


class GroupKnowledgeReplyDifyClient:
    def __init__(self):
        self.calls = []

    def run_workflow(self, workflow, input_json):
        self.calls.append((workflow, input_json))
        return {
            "action": "reply",
            "content": "这是长连接实机回复。",
            "reason": "测试命中。",
            "confidence": 0.91,
        }


class AsyncRecordingSender:
    def __init__(self):
        self.sent = []

    async def send_async(self, outbox):
        self.sent.append(outbox)
        return {
            "success": True,
            "external_msgid": f"WS_SENT_{len(self.sent)}",
            "raw_response": {"errcode": 0, "errmsg": "ok", "msgid": f"WS_SENT_{len(self.sent)}"},
        }


class FakeWsClient:
    def __init__(self):
        self.handlers = {}
        self.connected = False
        self.disconnected = False

    def on(self, event, handler):
        self.handlers[event] = handler
        return self

    async def connect(self):
        self.connected = True
        return self

    async def disconnect(self):
        self.disconnected = True


def make_app(tmp_path, *, dify_client=None):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage14.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    if dify_client is not None:
        app.state.dify_client = dify_client
    return app


def text_frame(*, msgid="MSG_WS_1", content="@机器人 查一下标签要求", mentioned_users=None):
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": f"REQ_{msgid}"},
        "body": {
            "msgid": msgid,
            "chatid": "CHAT_WS",
            "chattype": "group",
            "from": {"userid": "USER_A", "name": "Alice"},
            "msgtype": "text",
            "text": {"content": content},
            "mentioned_users": mentioned_users if mentioned_users is not None else ["BOT_ID"],
            "create_time": 1777827600,
        },
    }


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_aibot_frame_normalizer_maps_text_frame_without_inventing_ids():
    payload = AiBotFrameNormalizer().normalize(text_frame())

    assert payload.source == "aibot_ws"
    assert payload.idempotency_key == "aibot_ws_msg_MSG_WS_1"
    assert payload.raw_message["msgid"] == "MSG_WS_1"
    assert payload.raw_message["req_id"] == "REQ_MSG_WS_1"
    assert payload.raw_message["chatid"] == "CHAT_WS"
    assert payload.raw_message["from"]["userid"] == "USER_A"
    assert payload.raw_message["text"]["content"] == "@机器人 查一下标签要求"


def test_aibot_frame_normalizer_uses_req_id_for_idempotency_when_msgid_missing():
    frame = text_frame()
    frame["body"].pop("msgid")

    payload = AiBotFrameNormalizer().normalize(frame)

    assert "msgid" not in payload.raw_message
    assert payload.idempotency_key == "aibot_ws_req_REQ_MSG_WS_1"


def test_aibot_frame_normalizer_keeps_mixed_message_and_combines_text_items():
    frame = text_frame(msgid="MSG_MIXED")
    frame["body"]["msgtype"] = "mixed"
    frame["body"].pop("text")
    frame["body"]["mixed"] = {
        "items": [
            {"type": "text", "text": {"content": "@机器人 "}},
            {"type": "image", "image": {"media_id": "MEDIA_1"}},
            {"type": "text", "text": {"content": "这张图怎么处理？"}},
        ]
    }

    payload = AiBotFrameNormalizer().normalize(frame)

    assert payload.raw_message["msgtype"] == "mixed"
    assert payload.raw_message["mixed"]["items"][1]["image"]["media_id"] == "MEDIA_1"
    assert payload.raw_message["text"]["content"] == "@机器人 这张图怎么处理？"


def test_process_incoming_aibot_mention_runs_dify_creates_and_sends_outbox(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = AsyncRecordingSender()
    app = make_app(tmp_path, dify_client=dify_client)

    with app.state.SessionLocal() as session:
        result = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )

    assert result["ingest"]["duplicated"] is False
    assert result["trigger"]["matched"] is True
    assert result["sent_outboxes"][0]["status"] == "sent"
    assert dify_client.calls[0][1]["payload"]["message"]["msgid"] == "MSG_WS_1"
    assert len(sender.sent) == 1

    with app.state.SessionLocal() as session:
        message = session.scalar(select(Message))
        outbox = session.scalar(select(OutboxMessage))

        assert message.mentioned_bot is True
        assert outbox.status == "sent"
        assert outbox.external_msgid == "WS_SENT_1"


def test_process_incoming_aibot_non_mention_only_ingests(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = AsyncRecordingSender()
    app = make_app(tmp_path, dify_client=dify_client)

    with app.state.SessionLocal() as session:
        result = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(content="这是一条普通讨论", mentioned_users=[]),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )

    assert result["trigger"]["matched"] is False
    assert result["sent_outboxes"] == []
    assert dify_client.calls == []
    assert sender.sent == []
    assert scalar_count(app, MessageRaw) == 1
    assert scalar_count(app, OutboxMessage) == 0


def test_process_incoming_aibot_duplicate_message_does_not_resend(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = AsyncRecordingSender()
    app = make_app(tmp_path, dify_client=dify_client)

    with app.state.SessionLocal() as session:
        first = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )
        second = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )

    assert first["ingest"]["duplicated"] is False
    assert second["ingest"]["duplicated"] is True
    assert second["trigger"]["matched"] is False
    assert len(sender.sent) == 1
    assert scalar_count(app, MessageRaw) == 1
    assert scalar_count(app, OutboxMessage) == 1


def test_aibot_ws_sender_sends_markdown_outbox_with_sdk_payload():
    class FakeSdkClient:
        def __init__(self):
            self.calls = []

        async def send_message(self, chatid, body):
            self.calls.append((chatid, body))
            return {"body": {"msgid": "SDK_MSG_1"}, "errcode": 0, "errmsg": "ok"}

    sdk_client = FakeSdkClient()
    sender = WeComAiBotWsMessageSender(ws_client=sdk_client)
    outbox = OutboxMessage(
        outbox_id="out_ws",
        scene="reply",
        chatid="CHAT_WS",
        target_userids=[],
        msgtype="markdown",
        content={"markdown": {"content": "这是长连接回复。"}},
        source_type="manual",
        source_id="manual_1",
        status="pending",
        idempotency_key="idem_ws",
    )

    result = asyncio.run(sender.send_async(outbox))

    assert result["success"] is True
    assert result["external_msgid"] == "SDK_MSG_1"
    assert sdk_client.calls == [
        ("CHAT_WS", {"msgtype": "markdown", "markdown": {"content": "这是长连接回复。"}})
    ]


def test_aibot_worker_registers_message_handlers_and_processes_frame(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = AsyncRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    asyncio.run(worker.start())
    asyncio.run(ws_client.handlers["message.text"](text_frame()))

    assert ws_client.connected is True
    assert "message.text" in ws_client.handlers
    assert "message.mixed" in ws_client.handlers
    assert len(sender.sent) == 1

    client = TestClient(app)
    response = client.get("/api/outbox-messages")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
