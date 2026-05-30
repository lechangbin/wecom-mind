import asyncio
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import func, select

from app.config.settings import Settings
from app.db.models import (
    MentionRequest,
    Message,
    MessageRaw,
    OutboxMessage,
    WeComReplySession,
)
from app.main import create_app
from app.outbound.sender import WeComAiBotWsMessageSender
from app.wecom.aibot import (
    AiBotFrameNormalizer,
    WeComAiBotLongConnectionWorker,
    process_incoming_aibot_frame,
)
from app.wecom.message_reconcile import run_message_reconcile_once


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


class SlowGroupKnowledgeReplyDifyClient(GroupKnowledgeReplyDifyClient):
    def __init__(self, delay_seconds):
        super().__init__()
        self.delay_seconds = delay_seconds

    def run_workflow(self, workflow, input_json):
        time.sleep(self.delay_seconds)
        return super().run_workflow(workflow, input_json)


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


class CallbackRecordingSender:
    def __init__(self):
        self.started = []
        self.finished = []
        self.active_sent = []

    async def begin_callback_stream_async(self, frame, content):
        stream_id = f"STREAM_{len(self.started) + 1}"
        self.started.append((frame, content, stream_id))
        return {
            "success": True,
            "stream_id": stream_id,
            "raw_response": {"errcode": 0, "errmsg": "ok"},
        }

    async def send_callback_final_async(self, outbox, *, frame, stream_id):
        self.finished.append((outbox, frame, stream_id))
        return {
            "success": True,
            "external_msgid": f"WS_REPLY_{len(self.finished)}",
            "raw_response": {"errcode": 0, "errmsg": "ok", "msgid": f"WS_REPLY_{len(self.finished)}"},
        }

    async def send_async(self, outbox):
        self.active_sent.append(outbox)
        return {
            "success": True,
            "external_msgid": f"WS_ACTIVE_{len(self.active_sent)}",
            "raw_response": {"errcode": 0, "errmsg": "ok", "msgid": f"WS_ACTIVE_{len(self.active_sent)}"},
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


class StaticHistoryMessageSource:
    def __init__(self, messages):
        self.messages = messages

    def fetch_messages(self, *, chatid, start_time, end_time):
        return self.messages


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


def make_split_bot_app(tmp_path, *, dify_client=None):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage14_split.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_aibot_name="意图机器人",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_reply_aibot_secret="reply-secret",
        wecom_reply_aibot_name="回复机器人",
        wecom_intent_aibot_id="INTENT_BOT",
        wecom_intent_aibot_secret="intent-secret",
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


async def wait_until(predicate, *, timeout=2.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for async condition")


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


def test_aibot_frame_normalizer_infers_text_and_hashes_payload_when_ids_missing():
    frame = text_frame()
    frame["headers"] = {}
    frame["body"].pop("msgid")
    frame["body"].pop("msgtype")

    payload = AiBotFrameNormalizer().normalize(frame)

    assert payload.source == "aibot_ws"
    assert payload.idempotency_key.startswith("aibot_ws_payload_")
    assert "msgid" not in payload.raw_message
    assert payload.raw_message["msgtype"] == "text"
    assert payload.raw_message["text"]["content"] == "@机器人 查一下标签要求"


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


def test_split_bot_config_only_treats_reply_bot_as_mention(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = AsyncRecordingSender()
    app = make_split_bot_app(tmp_path, dify_client=dify_client)

    with app.state.SessionLocal() as session:
        reply_result = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(
                    msgid="MSG_REPLY_BOT",
                    content="查一下标签要求",
                    mentioned_users=["REPLY_BOT"],
                ),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )
        intent_result = asyncio.run(
            process_incoming_aibot_frame(
                session,
                frame=text_frame(
                    msgid="MSG_INTENT_BOT",
                    content="查一下标签要求",
                    mentioned_users=["INTENT_BOT"],
                ),
                settings=app.state.settings,
                dify_client=dify_client,
                sender=sender,
                auto_send=True,
            )
        )

    assert reply_result["trigger"]["matched"] is True
    assert intent_result["trigger"]["matched"] is False
    assert len(sender.sent) == 1
    assert len(dify_client.calls) == 1


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


def test_aibot_ws_sender_can_reply_on_callback_stream():
    class FakeSdkClient:
        def __init__(self):
            self.stream_calls = []

        async def reply_stream(self, frame, stream_id, content, finish):
            self.stream_calls.append((frame, stream_id, content, finish))
            return {"body": {"msgid": f"SDK_REPLY_{len(self.stream_calls)}"}, "errcode": 0, "errmsg": "ok"}

    sdk_client = FakeSdkClient()
    sender = WeComAiBotWsMessageSender(ws_client=sdk_client)
    frame = text_frame()
    outbox = OutboxMessage(
        outbox_id="out_ws_reply",
        scene="reply",
        chatid="CHAT_WS",
        target_userids=[],
        msgtype="markdown",
        content={"markdown": {"content": "最终长连接回复。"}},
        source_type="manual",
        source_id="manual_2",
        status="pending",
        idempotency_key="idem_ws_reply",
    )

    begin = asyncio.run(sender.begin_callback_stream_async(frame, "正在查询。"))
    result = asyncio.run(
        sender.send_callback_final_async(
            outbox,
            frame=frame,
            stream_id=begin["stream_id"],
        )
    )

    assert begin["success"] is True
    assert result["success"] is True
    assert sdk_client.stream_calls == [
        (frame, begin["stream_id"], "正在查询。", False),
        (frame, begin["stream_id"], "最终长连接回复。", True),
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

    async def run_frame():
        await worker.start()
        await ws_client.handlers["message.text"](text_frame())
        await wait_until(lambda: len(sender.sent) == 1)

    asyncio.run(run_frame())

    assert ws_client.connected is True
    assert "message.text" in ws_client.handlers
    assert "message.mixed" in ws_client.handlers
    assert len(sender.sent) == 1

    client = TestClient(app)
    response = client.get("/api/outbox-messages")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1


def test_aibot_worker_dispatches_new_pending_proactive_outbox(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage14_dispatcher.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    sender = AsyncRecordingSender()
    ws_client = FakeWsClient()
    worker = WeComAiBotLongConnectionWorker(
        settings=settings,
        session_factory=app.state.SessionLocal,
        dify_client=GroupKnowledgeReplyDifyClient(),
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_dispatcher():
        await worker.start()
        try:
            with app.state.SessionLocal() as session:
                session.add(
                    OutboxMessage(
                        outbox_id="out_proactive_dispatch",
                        scene="proactive",
                        chatid="CHAT_WS",
                        target_userids=["USER_A"],
                        msgtype="markdown",
                        content={"markdown": {"content": "<@USER_A> 主动补充回复。"}},
                        source_type="ai_run",
                        source_id="airun_dispatch",
                        status="pending",
                        idempotency_key="proactive_dispatch",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                session.commit()
            await wait_until(lambda: len(sender.sent) == 1)
        finally:
            await worker.stop()

    asyncio.run(run_dispatcher())

    assert sender.sent[0].outbox_id == "out_proactive_dispatch"
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))
        assert outbox.status == "sent"
        assert outbox.external_msgid == "WS_SENT_1"


def test_aibot_worker_records_callback_reply_session_before_dify_finishes(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=0.3)
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_frame():
        await worker.start()
        await ws_client.handlers["message.text"](text_frame())
        await wait_until(lambda: len(sender.started) == 1)
        await wait_until(lambda: scalar_count(app, WeComReplySession) == 1)
        assert dify_client.calls == []
        await wait_until(lambda: len(sender.finished) == 1)

    asyncio.run(run_frame())

    with app.state.SessionLocal() as session:
        reply_session = session.scalar(select(WeComReplySession))

        assert reply_session.chatid == "CHAT_WS"
        assert reply_session.userid == "USER_A"
        assert reply_session.source_msgid == "MSG_WS_1"
        assert reply_session.req_id == "REQ_MSG_WS_1"
        assert reply_session.stream_id == sender.started[0][2]
        assert reply_session.placeholder_status == "sent"
        assert reply_session.final_status == "sent"
        assert reply_session.frame_json["headers"]["req_id"] == "REQ_MSG_WS_1"


def test_aibot_worker_continues_when_callback_reply_session_record_is_locked(
    tmp_path,
    monkeypatch,
):
    dify_client = GroupKnowledgeReplyDifyClient()
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    def locked_record(*_args, **_kwargs):
        raise OperationalError("insert into wecom_reply_sessions", {}, "database is locked")

    monkeypatch.setattr("app.wecom.aibot.record_reply_session_placeholder", locked_record)

    async def run_frame():
        await worker.start()
        await ws_client.handlers["message.text"](text_frame())
        await wait_until(lambda: len(sender.started) == 1)
        await wait_until(lambda: len(sender.finished) == 1)

    asyncio.run(run_frame())

    assert len(dify_client.calls) == 1
    assert sender.finished[0][2] == sender.started[0][2]
    assert scalar_count(app, MessageRaw) == 1
    assert scalar_count(app, OutboxMessage) == 1


def test_aibot_worker_dispatches_reply_recovery_outbox_on_recorded_callback_stream(
    tmp_path,
):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage14_recovery_dispatcher.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
        wecom_message_reconcile_auto_send=True,
    )
    app = create_app(settings=settings)
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    worker = WeComAiBotLongConnectionWorker(
        settings=settings,
        session_factory=app.state.SessionLocal,
        dify_client=GroupKnowledgeReplyDifyClient(),
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_dispatcher():
        await worker.start()
        try:
            with app.state.SessionLocal() as session:
                session.add(
                    OutboxMessage(
                        outbox_id="out_reply_recovery_dispatch",
                        scene="reply_recovery",
                        chatid="CHAT_WS",
                        target_userids=[],
                        msgtype="markdown",
                        content={"markdown": {"content": "恢复后的最终回复。"}},
                        source_type="ai_run",
                        source_id="airun_recovery_dispatch",
                        status="pending",
                        idempotency_key="reply_recovery_dispatch",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                session.add(
                    WeComReplySession(
                        session_id="reply_session_dispatch",
                        chatid="CHAT_WS",
                        userid="USER_A",
                        source_msgid="MSG_RECOVERY",
                        req_id="REQ_MSG_RECOVERY",
                        content_fingerprint="fp_recovery",
                        frame_json=text_frame(msgid="MSG_RECOVERY"),
                        stream_id="STREAM_RECOVERY",
                        placeholder_status="sent",
                        final_status="pending",
                        outbox_id="out_reply_recovery_dispatch",
                    )
                )
                session.commit()
            await wait_until(lambda: len(sender.finished) == 1)
        finally:
            await worker.stop()

    asyncio.run(run_dispatcher())

    assert sender.finished[0][0].outbox_id == "out_reply_recovery_dispatch"
    assert sender.finished[0][2] == "STREAM_RECOVERY"
    assert sender.active_sent == []
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))
        reply_session = session.scalar(select(WeComReplySession))

        assert outbox.status == "sent"
        assert outbox.external_msgid == "WS_REPLY_1"
        assert reply_session.final_status == "sent"


def test_aibot_worker_handler_returns_before_dify_finishes(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=0.3)
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

    async def run_frame():
        await worker.start()
        started = time.perf_counter()
        await ws_client.handlers["message.text"](text_frame())
        handler_elapsed = time.perf_counter() - started
        await wait_until(lambda: len(sender.sent) == 1)
        return handler_elapsed

    handler_elapsed = asyncio.run(run_frame())

    assert handler_elapsed < 0.1
    assert len(dify_client.calls) == 1
    assert scalar_count(app, MessageRaw) == 1
    assert scalar_count(app, OutboxMessage) == 1


def test_aibot_worker_does_not_block_event_loop_while_dify_is_running(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=0.3)
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

    async def run_overlapping_frames():
        await worker.start()
        started = time.perf_counter()
        first = asyncio.create_task(
            ws_client.handlers["message.text"](text_frame(msgid="MSG_WS_A"))
        )
        await asyncio.sleep(0.05)
        event_loop_delay = time.perf_counter() - started
        second = asyncio.create_task(
            ws_client.handlers["message.text"](
                text_frame(msgid="MSG_WS_B", content="@机器人 春季防病怎么做")
            )
        )
        await asyncio.gather(first, second)
        await wait_until(lambda: len(sender.sent) == 2)
        return event_loop_delay

    event_loop_delay = asyncio.run(run_overlapping_frames())

    assert event_loop_delay < 0.2
    assert len(dify_client.calls) == 2
    assert len(sender.sent) == 2
    assert scalar_count(app, MessageRaw) == 2
    assert scalar_count(app, OutboxMessage) == 2


def test_aibot_worker_begins_callback_stream_before_dify_finishes(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=0.3)
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_frame():
        await worker.start()
        started = time.perf_counter()
        await ws_client.handlers["message.text"](text_frame())
        await wait_until(lambda: len(sender.started) == 1)
        start_elapsed = time.perf_counter() - started
        assert dify_client.calls == []
        await wait_until(lambda: len(sender.finished) == 1)
        return start_elapsed

    start_elapsed = asyncio.run(run_frame())

    assert start_elapsed < 0.1
    assert sender.started[0][0]["headers"]["req_id"] == "REQ_MSG_WS_1"
    assert sender.finished[0][1]["headers"]["req_id"] == "REQ_MSG_WS_1"
    assert sender.finished[0][2] == sender.started[0][2]
    assert sender.active_sent == []


def test_aibot_worker_does_not_hold_sqlite_write_lock_while_dify_runs(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=5.5)
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_overlapping_frames():
        await worker.start()
        await ws_client.handlers["message.text"](text_frame(msgid="MSG_LOCK_A"))
        await asyncio.sleep(0.1)
        await ws_client.handlers["message.text"](
            text_frame(msgid="MSG_LOCK_B", content="@机器人 打疫苗有什么注意事项")
        )
        await wait_until(lambda: len(sender.started) == 2)
        await wait_until(lambda: len(sender.finished) == 2, timeout=7.5)

    asyncio.run(run_overlapping_frames())

    assert len(dify_client.calls) == 2
    assert len(sender.finished) == 2
    assert scalar_count(app, MessageRaw) == 2
    assert scalar_count(app, OutboxMessage) == 2


def test_reconcile_skips_same_mention_while_long_connection_request_is_running(tmp_path):
    dify_client = SlowGroupKnowledgeReplyDifyClient(delay_seconds=0.4)
    sender = CallbackRecordingSender()
    ws_client = FakeWsClient()
    app = make_app(tmp_path, dify_client=dify_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=app.state.settings,
        session_factory=app.state.SessionLocal,
        dify_client=dify_client,
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    async def run_overlap():
        await worker.start()
        try:
            await ws_client.handlers["message.text"](
                text_frame(
                    msgid="MSG_RUNNING_WS",
                    content="@机器人 查一下审批要求。",
                )
            )
            await wait_until(lambda: scalar_count(app, MentionRequest) == 1)
            await wait_until(
                lambda: _mention_request_status(app) == "running",
                timeout=1.0,
            )
            with app.state.SessionLocal() as session:
                result = run_message_reconcile_once(
                    session,
                    chatid="CHAT_WS",
                    settings=app.state.settings,
                    message_source=StaticHistoryMessageSource(
                        [
                            {
                                "msgid": "",
                                "chatid": "CHAT_WS",
                                "chattype": "group",
                                "from": {"userid": "USER_A", "name": "Alice"},
                                "msgtype": "text",
                                "text": {"content": "@机器人查一下审批要求。"},
                                "mentioned_users": [],
                                "create_time": 1777827602,
                            }
                        ]
                    ),
                    dify_client=dify_client,
                    auto_enqueue=True,
                    now=datetime.fromtimestamp(1777827610, tz=timezone.utc),
                )
            await wait_until(lambda: len(sender.finished) == 1)
            return result
        finally:
            await worker.stop()

    result = asyncio.run(run_overlap())

    assert result["mention_recovery_count"] == 0
    assert result["mention_recovery_outbox_count"] == 0
    assert len(dify_client.calls) == 1
    with app.state.SessionLocal() as session:
        requests = session.scalars(select(MentionRequest)).all()
        messages = session.scalars(select(Message).order_by(Message.id)).all()

        assert len(requests) == 1
        assert requests[0].status == "completed"
        assert len(messages) == 2
        assert messages[1].canonical_message_id == messages[0].id


def _mention_request_status(app) -> str | None:
    with app.state.SessionLocal() as session:
        request = session.scalar(select(MentionRequest))
        return request.status if request else None
