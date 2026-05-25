from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun, AiWorkflow, OutboxMessage, ScheduledIntent
from app.main import create_app


class LowConfidenceIntentDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "actions": [
                {
                    "action_type": "reply",
                    "intent_type": "follow_up",
                    "target_userids": [input_json["known_users"][0]],
                    "evidence_msgids": [input_json["messages"][0]["msgid"]],
                    "reason": "置信度不足。",
                    "reply_instruction": "请确认一下报价方案。",
                    "priority": "medium",
                    "confidence": 0.6,
                }
            ]
        }


class UnknownTargetUserDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "actions": [
                {
                    "action_type": "reply",
                    "intent_type": "follow_up",
                    "target_userids": ["USER_NOT_IN_WINDOW"],
                    "evidence_msgids": [input_json["messages"][0]["msgid"]],
                    "reason": "目标用户不存在。",
                    "reply_instruction": "请确认一下报价方案。",
                    "priority": "medium",
                    "confidence": 0.86,
                }
            ]
        }


class MissingEvidenceDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "actions": [
                {
                    "action_type": "reply",
                    "intent_type": "follow_up",
                    "target_userids": [input_json["known_users"][0]],
                    "evidence_msgids": ["MSG_NOT_IN_INPUT"],
                    "reason": "证据消息不存在。",
                    "reply_instruction": "请确认一下报价方案。",
                    "priority": "medium",
                    "confidence": 0.86,
                }
            ]
        }


def make_client(tmp_path, *, dify_client=None):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage7.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    if dify_client:
        app.state.dify_client = dify_client
    return TestClient(app), app


def ingest_message(
    client: TestClient,
    *,
    msgid: str,
    userid: str,
    content: str,
    create_time: int,
):
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": f"mcp_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_A",
                "chattype": "group",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": [],
                "create_time": create_time,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def seed_window_messages(client: TestClient):
    ingest_message(
        client,
        msgid="MSG_1",
        userid="USER_A",
        content="报价方案需要我确认一下。",
        create_time=1777827600,
    )
    ingest_message(
        client,
        msgid="MSG_2",
        userid="USER_B",
        content="那请今天跟进客户。",
        create_time=1777827660,
    )


def run_scheduled_intents(client: TestClient, *, chatid: str = "CHAT_A", auto_enqueue=True):
    return client.post(
        "/api/scheduled-intents/run",
        json={
            "chatid": chatid,
            "job_id": "job_manual_001",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T02:00:00+08:00",
            },
            "auto_enqueue": auto_enqueue,
        },
        headers={"X-Request-ID": "req-scheduled"},
    )


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_default_intent_detection_workflow_exists_after_init(tmp_path):
    _client, app = make_client(tmp_path)

    with app.state.SessionLocal() as session:
        workflow = session.scalar(
            select(AiWorkflow).where(
                AiWorkflow.workflow_code == "intent_detection",
                AiWorkflow.version == "v1",
            )
        )

        assert workflow is not None
        assert workflow.response_mode == "blocking"
        assert workflow.enabled is True


def test_run_scheduled_intent_detection_with_empty_window_returns_clear_error(tmp_path):
    client, app = make_client(tmp_path)

    response = run_scheduled_intents(client, chatid="CHAT_EMPTY")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert "No messages found" in response.json()["error"]["message"]
    assert scalar_count(app, AiRun) == 0
    assert scalar_count(app, ScheduledIntent) == 0
    assert scalar_count(app, OutboxMessage) == 0


def test_normal_run_creates_success_ai_run(tmp_path):
    client, app = make_client(tmp_path)
    seed_window_messages(client)

    response = run_scheduled_intents(client)

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-scheduled"
    assert response.json()["data"]["status"] == "success"
    with app.state.SessionLocal() as session:
        run = session.scalar(
            select(AiRun).where(AiRun.workflow_code == "intent_detection")
        )

        assert run.status == "success"
        assert run.input_json["trigger_source"] == "schedule_scan"
        assert run.input_json["chatid"] == "CHAT_A"
        assert run.input_json["known_users"] == ["USER_A", "USER_B"]
        assert run.output_json["actions"][0]["action_type"] == "reply"
        assert [message["msgid"] for message in run.input_json["messages"]] == [
            "MSG_1",
            "MSG_2",
        ]


def test_high_confidence_intent_creates_scheduled_intent_and_proactive_outbox(tmp_path):
    client, app = make_client(tmp_path)
    seed_window_messages(client)

    response = run_scheduled_intents(client)

    assert response.status_code == 200
    assert response.json()["data"]["intents"][0]["status"] == "enqueued"
    with app.state.SessionLocal() as session:
        intent = session.scalar(select(ScheduledIntent))
        outbox = session.scalar(select(OutboxMessage))

        assert intent.status == "enqueued"
        assert intent.chatid == "CHAT_A"
        assert intent.target_userid == "USER_A"
        assert intent.evidence_msgids == ["MSG_1"]
        assert float(intent.confidence) == 0.86
        assert intent.outbox_id == outbox.outbox_id
        assert outbox.scene == "proactive"
        assert outbox.target_userids == ["USER_A"]
        assert outbox.msgtype == "markdown"
        assert "<@USER_A>" in outbox.content["markdown"]["content"]
        assert "请确认一下报价方案。" in outbox.content["markdown"]["content"]
        assert outbox.source_type == "ai_run"


def test_low_confidence_intent_does_not_create_outbox(tmp_path):
    client, app = make_client(tmp_path, dify_client=LowConfidenceIntentDifyClient())
    seed_window_messages(client)

    response = run_scheduled_intents(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "success"
    assert scalar_count(app, OutboxMessage) == 0
    with app.state.SessionLocal() as session:
        intent = session.scalar(select(ScheduledIntent))
        assert intent.status == "ignored_low_confidence"
        assert intent.outbox_id is None


def test_unknown_target_user_marks_invalid_output_and_writes_no_intent_or_outbox(tmp_path):
    client, app = make_client(tmp_path, dify_client=UnknownTargetUserDifyClient())
    seed_window_messages(client)

    response = run_scheduled_intents(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert response.json()["data"]["intents"] == []
    assert scalar_count(app, ScheduledIntent) == 0
    assert scalar_count(app, OutboxMessage) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "target_userids" in run.error_message


def test_missing_evidence_msgid_marks_invalid_output(tmp_path):
    client, app = make_client(tmp_path, dify_client=MissingEvidenceDifyClient())
    seed_window_messages(client)

    response = run_scheduled_intents(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert scalar_count(app, ScheduledIntent) == 0
    assert scalar_count(app, OutboxMessage) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "evidence_msgids" in run.error_message


def test_repeated_run_for_same_window_does_not_duplicate_outbox(tmp_path):
    client, app = make_client(tmp_path)
    seed_window_messages(client)

    first = run_scheduled_intents(client)
    second = run_scheduled_intents(client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert scalar_count(app, ScheduledIntent) == 1
    assert scalar_count(app, OutboxMessage) == 1
    assert second.json()["data"]["intents"][0]["duplicated"] is True


def test_list_scheduled_intents_by_chatid_and_status(tmp_path):
    client, _app = make_client(tmp_path)
    seed_window_messages(client)
    run_scheduled_intents(client)

    response = client.get(
        "/api/scheduled-intents",
        params={"chatid": "CHAT_A", "status": "enqueued"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["chatid"] == "CHAT_A"
    assert response.json()["data"]["items"][0]["status"] == "enqueued"
