from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import BotReply, OutboxMessage
from app.main import create_app


class FailingMockWeComMessageSender:
    def send(self, outbox):
        return {
            "success": False,
            "error_code": "MOCK_SEND_FAILED",
            "error_message": "mock failure",
            "raw_response": {"errcode": 50001, "errmsg": "mock failure"},
        }


def make_client(tmp_path, *, failing_sender=False):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage4.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    if failing_sender:
        app.state.wecom_message_sender = FailingMockWeComMessageSender()
    return TestClient(app), app


def ingest_mention_message(client: TestClient, *, msgid: str = "MSG_STAGE4") -> int:
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": f"mcp_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_STAGE4",
                "chattype": "group",
                "from": {"userid": "USER_STAGE4", "name": "Alice"},
                "msgtype": "text",
                "text": {"content": "@机器人 帮我写个回复"},
                "mentioned_users": ["BOT_ID"],
                "create_time": 1777827600,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def evaluate(client: TestClient, message_id: int):
    response = client.post("/api/triggers/evaluate", json={"message_id": message_id})
    assert response.status_code == 200
    return response


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_mention_reply_success_ai_run_creates_bot_reply(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_mention_message(client)

    evaluate(client, message_id)

    with app.state.SessionLocal() as session:
        reply = session.scalar(select(BotReply))

        assert reply.reply_id.startswith("reply_")
        assert reply.source_message_id == message_id
        assert reply.chatid == "CHAT_STAGE4"
        assert reply.userid == "USER_STAGE4"
        assert reply.reply_type == "markdown"
        assert reply.content == "收到：@机器人 帮我写个回复"
        assert reply.status == "pending"


def test_mention_reply_success_ai_run_creates_pending_outbox(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_mention_message(client, msgid="MSG_STAGE4_OUTBOX")

    response = evaluate(client, message_id)

    assert response.json()["data"]["events"][0]["ai_run"]["status"] == "success"
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))

        assert outbox.outbox_id.startswith("out_")
        assert outbox.scene == "reply"
        assert outbox.chatid == "CHAT_STAGE4"
        assert outbox.source_type == "ai_run"
        assert outbox.source_id == response.json()["data"]["events"][0]["ai_run"]["run_id"]
        assert outbox.msgtype == "markdown"
        assert outbox.content == {
            "markdown": {"content": "收到：@机器人 帮我写个回复"}
        }
        assert outbox.status == "pending"
        assert outbox.idempotency_key == f"reply_{outbox.source_id}"


def test_duplicate_evaluate_same_message_does_not_create_duplicate_outbox(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_mention_message(client, msgid="MSG_STAGE4_DUP")

    evaluate(client, message_id)
    evaluate(client, message_id)

    assert scalar_count(app, BotReply) == 1
    assert scalar_count(app, OutboxMessage) == 1


def test_post_outbox_messages_can_create_manual_outbox(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/api/outbox-messages",
        json={
            "scene": "proactive",
            "chatid": "CHAT_MANUAL",
            "target_userids": ["USER_A"],
            "msgtype": "text",
            "content": {"text": {"content": "manual hello"}},
            "source_type": "manual",
            "source_id": "manual_1",
            "idempotency_key": "manual_key_1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["duplicated"] is False
    assert response.json()["data"]["status"] == "pending"
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))

        assert outbox.chatid == "CHAT_MANUAL"
        assert outbox.target_userids == ["USER_A"]
        assert outbox.msgtype == "text"


def test_duplicate_idempotency_key_create_outbox_returns_existing(tmp_path):
    client, app = make_client(tmp_path)
    payload = {
        "scene": "proactive",
        "chatid": "CHAT_DUP",
        "target_userids": [],
        "msgtype": "markdown",
        "content": {"markdown": {"content": "same"}},
        "source_type": "manual",
        "source_id": "manual_dup",
        "idempotency_key": "manual_dup_key",
    }

    first = client.post("/api/outbox-messages", json=payload)
    second = client.post("/api/outbox-messages", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["duplicated"] is True
    assert second.json()["data"]["outbox_id"] == first.json()["data"]["outbox_id"]
    assert scalar_count(app, OutboxMessage) == 1


def test_get_outbox_messages_can_query_by_status_and_chatid(tmp_path):
    client, _app = make_client(tmp_path)
    client.post(
        "/api/outbox-messages",
        json={
            "scene": "proactive",
            "chatid": "CHAT_QUERY",
            "target_userids": [],
            "msgtype": "text",
            "content": {"text": {"content": "query me"}},
            "source_type": "manual",
            "source_id": "manual_query",
            "idempotency_key": "manual_query_key",
        },
    )

    response = client.get("/api/outbox-messages", params={"status": "pending", "chatid": "CHAT_QUERY"})

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    item = response.json()["data"]["items"][0]
    assert item["chatid"] == "CHAT_QUERY"
    assert item["status"] == "pending"

    detail = client.get(f"/api/outbox-messages/{item['outbox_id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["outbox_id"] == item["outbox_id"]


def test_send_outbox_with_mock_sender_marks_sent(tmp_path):
    client, app = make_client(tmp_path)
    created = client.post(
        "/api/outbox-messages",
        json={
            "scene": "reply",
            "chatid": "CHAT_SEND",
            "target_userids": [],
            "msgtype": "markdown",
            "content": {"markdown": {"content": "send me"}},
            "source_type": "manual",
            "source_id": "manual_send",
            "idempotency_key": "manual_send_key",
        },
    )
    outbox_id = created.json()["data"]["outbox_id"]

    response = client.post(f"/api/outbox-messages/{outbox_id}/send")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "sent"
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))

        assert outbox.status == "sent"
        assert outbox.external_msgid.startswith("mock_msg_")
        assert outbox.sent_at is not None
        assert outbox.raw_response == {"errcode": 0, "errmsg": "ok"}


def test_sent_outbox_send_again_does_not_resend(tmp_path):
    client, app = make_client(tmp_path)
    created = client.post(
        "/api/outbox-messages",
        json={
            "scene": "reply",
            "chatid": "CHAT_RESEND",
            "target_userids": [],
            "msgtype": "text",
            "content": {"text": {"content": "only once"}},
            "source_type": "manual",
            "source_id": "manual_resend",
            "idempotency_key": "manual_resend_key",
        },
    )
    outbox_id = created.json()["data"]["outbox_id"]

    first = client.post(f"/api/outbox-messages/{outbox_id}/send")
    second = client.post(f"/api/outbox-messages/{outbox_id}/send")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "sent"
    assert second.json()["data"]["duplicated"] is True
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))

        assert outbox.retry_count == 0


def test_mock_sender_failure_marks_outbox_failed(tmp_path):
    client, app = make_client(tmp_path, failing_sender=True)
    created = client.post(
        "/api/outbox-messages",
        json={
            "scene": "reply",
            "chatid": "CHAT_FAIL",
            "target_userids": [],
            "msgtype": "text",
            "content": {"text": {"content": "fail me"}},
            "source_type": "manual",
            "source_id": "manual_fail",
            "idempotency_key": "manual_fail_key",
        },
    )
    outbox_id = created.json()["data"]["outbox_id"]

    response = client.post(f"/api/outbox-messages/{outbox_id}/send")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    with app.state.SessionLocal() as session:
        outbox = session.scalar(select(OutboxMessage))

        assert outbox.status == "failed"
        assert outbox.error_code == "MOCK_SEND_FAILED"
        assert outbox.error_message == "mock failure"
        assert outbox.retry_count == 1
