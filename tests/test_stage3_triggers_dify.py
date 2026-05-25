from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun, Message, TriggerEvent
from app.main import create_app


class InvalidMentionReplyDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "action": "reply",
            "reply": {"reply_type": "markdown", "content": ""},
            "metadata": {"intent": "bad_mock"},
            "confidence": 0.9,
        }


def make_client(tmp_path, *, invalid_dify=False):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage3.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    if invalid_dify:
        app.state.dify_client = InvalidMentionReplyDifyClient()
    return TestClient(app), app


def ingest_text_message(client: TestClient, *, msgid: str, content: str, mentioned_bot: bool):
    payload = {
        "source": "mcp",
        "idempotency_key": f"mcp_msg_{msgid}",
        "raw_message": {
            "msgid": msgid,
            "chatid": "CHAT_STAGE3",
            "chattype": "group",
            "from": {"userid": "USER_STAGE3", "name": "Alice"},
            "msgtype": "text",
            "text": {"content": content},
            "mentioned_users": ["BOT_ID"] if mentioned_bot else [],
            "create_time": 1777827600,
        },
    }
    response = client.post("/api/wecom/messages/ingest", json=payload)
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def evaluate_message(client: TestClient, message_id: int):
    return client.post(
        "/api/triggers/evaluate",
        json={"message_id": message_id},
        headers={"X-Request-ID": "req-evaluate"},
    )


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_non_mention_message_evaluate_does_not_create_trigger_event(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_NO_MENTION",
        content="hello",
        mentioned_bot=False,
    )

    response = evaluate_message(client, message_id)

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-evaluate"
    assert response.json()["data"]["matched"] is False
    assert response.json()["data"]["events"] == []
    assert scalar_count(app, TriggerEvent) == 0
    assert scalar_count(app, AiRun) == 0


def test_mention_message_evaluate_creates_trigger_event(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_MENTION_EVENT",
        content="@机器人 帮我总结一下",
        mentioned_bot=True,
    )

    response = evaluate_message(client, message_id)

    assert response.status_code == 200
    assert response.json()["data"]["matched"] is True
    assert response.json()["data"]["events"][0]["workflow_code"] == "reply_generation"

    with app.state.SessionLocal() as session:
        event = session.scalar(select(TriggerEvent))
        message = session.get(Message, message_id)

        assert event.rule_code == "default_mention_reply"
        assert event.trigger_type == "mention"
        assert event.message_id == message_id
        assert event.chatid == message.chatid
        assert event.userid == message.userid
        assert event.workflow_code == "reply_generation"
        assert event.status == "handled"


def test_mention_message_evaluate_creates_ai_run(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_MENTION_RUN",
        content="@机器人 写个回复",
        mentioned_bot=True,
    )

    response = evaluate_message(client, message_id)

    assert response.status_code == 200
    assert response.json()["data"]["events"][0]["ai_run"]["status"] == "success"

    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))

        assert run.workflow_code == "reply_generation"
        assert run.workflow_version == "v1"
        assert run.trigger_event_id is not None
        assert run.response_mode == "blocking"
        assert run.status == "success"
        assert run.input_json["reply_scene"] == "mention"
        assert run.input_json["source_msgid"] == "MSG_MENTION_RUN"
        assert run.input_json["user_message"] == "@机器人 写个回复"
        assert run.output_json["action"] == "reply"
        assert run.latency_ms >= 0
        assert run.error_message is None


def test_duplicate_evaluate_same_message_does_not_create_duplicate_trigger_event(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_DUP_EVALUATE",
        content="@机器人 再说一次",
        mentioned_bot=True,
    )

    first = evaluate_message(client, message_id)
    second = evaluate_message(client, message_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["events"][0]["duplicated"] is True
    assert scalar_count(app, TriggerEvent) == 1
    assert scalar_count(app, AiRun) == 1


def test_mock_dify_valid_output_sets_ai_run_status_success(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_VALID_DIFY",
        content="@机器人 hello mock",
        mentioned_bot=True,
    )

    evaluate_message(client, message_id)

    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))

        assert run.status == "success"
        assert run.output_json == {
            "action": "reply",
            "reply": {
                "reply_type": "markdown",
                "content": "收到：@机器人 hello mock",
            },
            "metadata": {
                "reply_scene": "mention",
                "intent_type": "mock_reply",
                "evidence_msgids": ["MSG_VALID_DIFY"],
            },
            "confidence": 0.9,
        }


def test_mock_dify_invalid_output_sets_ai_run_status_invalid_output(tmp_path):
    client, app = make_client(tmp_path, invalid_dify=True)
    message_id = ingest_text_message(
        client,
        msgid="MSG_INVALID_DIFY",
        content="@机器人 返回坏结果",
        mentioned_bot=True,
    )

    response = evaluate_message(client, message_id)

    assert response.status_code == 200
    assert response.json()["data"]["events"][0]["ai_run"]["status"] == "invalid_output"

    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))

        assert run.status == "invalid_output"
        assert run.output_json["action"] == "reply"
        assert run.error_message is not None


def test_get_ai_runs_lists_stage3_run_records(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text_message(
        client,
        msgid="MSG_QUERY_RUNS",
        content="@机器人 查询运行记录",
        mentioned_bot=True,
    )
    evaluate_message(client, message_id)

    response = client.get("/api/ai-runs")

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    item = response.json()["data"]["items"][0]
    assert item["workflow_code"] == "reply_generation"
    assert item["status"] == "success"

    detail = client.get(f"/api/ai-runs/{item['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["run_id"] == item["run_id"]
