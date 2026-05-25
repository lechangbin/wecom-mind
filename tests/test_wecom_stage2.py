from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import Settings
from app.db.models import (
    Message,
    MessageIngestionJob,
    MessageRaw,
    WeComChat,
    WeComMcpCallback,
    WeComUser,
)
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage2.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def post_callback(client: TestClient):
    return client.post(
        "/api/wecom/callbacks/mcp",
        params={"msg_signature": "mock-signature", "timestamp": "1777827600", "nonce": "n1"},
        json={"event_type": "message_changed", "chatid": "CHAT_A", "cursor": "CURSOR_1"},
        headers={"X-Request-ID": "req-callback"},
    )


def sample_text_ingest_payload(**overrides):
    raw_message = {
        "msgid": "MSG_1",
        "chatid": "CHAT_A",
        "chattype": "group",
        "from": {"userid": "USER_A", "name": "Alice"},
        "msgtype": "text",
        "text": {"content": "hello"},
        "create_time": 1777827600,
    }
    raw_message.update(overrides.pop("raw_message_overrides", {}))
    return {
        "source": "mcp",
        "idempotency_key": "mcp_msg_MSG_1",
        "raw_message": raw_message,
        **overrides,
    }


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_callback_endpoint_saves_callback_and_creates_ingestion_job(tmp_path):
    client, app = make_client(tmp_path)

    response = post_callback(client)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["request_id"] == "req-callback"
    assert body["data"]["accepted"] is True
    assert body["data"]["duplicated"] is False
    assert body["data"]["callback_id"].startswith("cb_")

    with app.state.SessionLocal() as session:
        callback = session.scalar(select(WeComMcpCallback))
        job = session.scalar(select(MessageIngestionJob))

        assert callback.callback_id == body["data"]["callback_id"]
        assert callback.chatid == "CHAT_A"
        assert callback.status == "received"
        assert callback.signature_valid is True
        assert callback.raw_query["nonce"] == "n1"
        assert callback.raw_body["cursor"] == "CURSOR_1"
        assert job.callback_id == callback.callback_id
        assert job.chatid == "CHAT_A"
        assert job.job_type == "pull"
        assert job.status == "pending"
        assert job.idempotency_key == "pull:CHAT_A:CURSOR_1"


def test_duplicate_callback_is_idempotent_and_does_not_create_duplicate_job(tmp_path):
    client, app = make_client(tmp_path)

    first = post_callback(client)
    second = post_callback(client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["duplicated"] is True
    assert second.json()["data"]["callback_id"] == first.json()["data"]["callback_id"]
    assert scalar_count(app, WeComMcpCallback) == 1
    assert scalar_count(app, MessageIngestionJob) == 1


def test_text_message_ingest_saves_raw_and_normalized_message(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/api/wecom/messages/ingest",
        json=sample_text_ingest_payload(),
        headers={"X-Request-ID": "req-ingest"},
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-ingest"
    assert response.json()["data"]["duplicated"] is False

    with app.state.SessionLocal() as session:
        raw = session.scalar(select(MessageRaw))
        message = session.scalar(select(Message))
        chat = session.scalar(select(WeComChat).where(WeComChat.chatid == "CHAT_A"))
        user = session.scalar(select(WeComUser).where(WeComUser.userid == "USER_A"))

        assert raw.external_msgid == "MSG_1"
        assert raw.chatid == "CHAT_A"
        assert raw.userid == "USER_A"
        assert raw.idempotency_key == "mcp_msg_MSG_1"
        assert message.raw_message_id == raw.id
        assert message.external_msgid == "MSG_1"
        assert message.chatid == "CHAT_A"
        assert message.content_text == "hello"
        assert message.normalized_content == {"type": "text", "text": "hello"}
        assert chat.chatid == "CHAT_A"
        assert chat.last_message_at is not None
        assert user.userid == "USER_A"
        assert user.name == "Alice"
        assert user.last_active_at is not None


def test_duplicate_message_does_not_create_duplicate_raw_or_normalized_rows(tmp_path):
    client, app = make_client(tmp_path)

    first = client.post("/api/wecom/messages/ingest", json=sample_text_ingest_payload())
    second = client.post(
        "/api/wecom/messages/ingest",
        json=sample_text_ingest_payload(idempotency_key="different-key"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["duplicated"] is True
    assert second.json()["data"]["raw_message_id"] == first.json()["data"]["raw_message_id"]
    assert second.json()["data"]["message_id"] == first.json()["data"]["message_id"]
    assert scalar_count(app, MessageRaw) == 1
    assert scalar_count(app, Message) == 1


def test_mentions_bot_when_bot_userid_is_in_mentioned_users(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/api/wecom/messages/ingest",
        json=sample_text_ingest_payload(
            raw_message_overrides={
                "msgid": "MSG_MENTION",
                "text": {"content": "@机器人 帮我总结一下"},
                "mentioned_users": ["BOT_ID", "USER_B"],
            },
            idempotency_key="mcp_msg_MSG_MENTION",
        ),
    )

    assert response.status_code == 200
    with app.state.SessionLocal() as session:
        message = session.scalar(select(Message))

        assert message.mentioned_bot is True
        assert message.mentioned_users == ["BOT_ID", "USER_B"]


def test_quote_content_is_saved_on_normalized_message(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/api/wecom/messages/ingest",
        json=sample_text_ingest_payload(
            raw_message_overrides={
                "msgid": "MSG_QUOTE",
                "quote": {"msgid": "MSG_OLD", "content": "previous message"},
            },
            idempotency_key="mcp_msg_MSG_QUOTE",
        ),
    )

    assert response.status_code == 200
    with app.state.SessionLocal() as session:
        message = session.scalar(select(Message))

        assert message.quote_msgid == "MSG_OLD"
        assert message.quote_message == {"msgid": "MSG_OLD", "content": "previous message"}


def test_message_is_associated_with_chatid_for_chat_queries(tmp_path):
    client, app = make_client(tmp_path)
    client.post(
        "/api/wecom/messages/ingest",
        json=sample_text_ingest_payload(
            raw_message_overrides={"msgid": "MSG_CHAT", "chatid": "CHAT_QUERY"},
            idempotency_key="mcp_msg_MSG_CHAT",
        ),
    )

    with app.state.SessionLocal() as session:
        messages = session.scalars(
            select(Message).where(Message.chatid == "CHAT_QUERY")
        ).all()

    assert len(messages) == 1
    assert messages[0].external_msgid == "MSG_CHAT"
