from datetime import datetime, timezone

from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun, AiWorkflow, ConversationSegment
from app.main import create_app


class MissingBoundaryDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "segments": [
                {
                    "title": "坏边界",
                    "start_msgid": "MSG_NOT_EXIST",
                    "end_msgid": input_json["messages"][-1]["msgid"],
                    "summary": "边界不存在。",
                    "keywords": ["坏边界"],
                    "participants": [input_json["messages"][0]["userid"]],
                    "confidence": 0.8,
                }
            ]
        }


class UnknownParticipantDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "segments": [
                {
                    "title": "坏参与人",
                    "start_msgid": input_json["messages"][0]["msgid"],
                    "end_msgid": input_json["messages"][-1]["msgid"],
                    "summary": "参与人不存在。",
                    "keywords": ["坏参与人"],
                    "participants": ["USER_NOT_IN_WINDOW"],
                    "confidence": 0.8,
                }
            ]
        }


def make_client(tmp_path, *, dify_client=None):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage5.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    if dify_client:
        app.state.dify_client = dify_client
    return TestClient(app), app


def ingest_message(client: TestClient, *, msgid: str, userid: str, content: str, create_time: int):
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
    ingest_message(client, msgid="MSG_1", userid="USER_A", content="第一条", create_time=1777827600)
    ingest_message(client, msgid="MSG_2", userid="USER_B", content="第二条", create_time=1777827660)
    ingest_message(client, msgid="MSG_3", userid="USER_A", content="第三条", create_time=1777827720)


def run_segmentation(client: TestClient, *, chatid: str = "CHAT_A"):
    return client.post(
        "/api/conversations/segment/run",
        json={
            "chatid": chatid,
            "start_time": "2026-05-04T00:00:00+08:00",
            "end_time": "2026-05-04T02:00:00+08:00",
            "mode": "auto",
        },
        headers={"X-Request-ID": "req-segment"},
    )


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_default_conversation_segmentation_workflow_exists_after_init(tmp_path):
    _client, app = make_client(tmp_path)

    with app.state.SessionLocal() as session:
        workflow = session.scalar(
            select(AiWorkflow).where(
                AiWorkflow.workflow_code == "conversation_segmentation",
                AiWorkflow.version == "v1",
            )
        )

        assert workflow is not None
        assert workflow.response_mode == "blocking"
        assert workflow.enabled is True


def test_run_conversation_segmentation_creates_ai_run_from_messages(tmp_path):
    client, app = make_client(tmp_path)
    seed_window_messages(client)

    response = run_segmentation(client)

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-segment"
    assert response.json()["data"]["status"] == "success"
    with app.state.SessionLocal() as session:
        run = session.scalar(
            select(AiRun).where(AiRun.workflow_code == "conversation_segmentation")
        )

        assert run.workflow_version == "v1"
        assert run.input_json["chatid"] == "CHAT_A"
        assert [message["msgid"] for message in run.input_json["messages"]] == [
            "MSG_1",
            "MSG_2",
            "MSG_3",
        ]
        assert run.output_json["segments"][0]["start_msgid"] == "MSG_1"


def test_valid_dify_output_creates_conversation_segment_with_system_conversation_no(tmp_path):
    client, app = make_client(tmp_path)
    seed_window_messages(client)

    response = run_segmentation(client)

    assert response.status_code == 200
    assert response.json()["data"]["segments"][0]["conversation_no"].startswith("conv_")
    with app.state.SessionLocal() as session:
        segment = session.scalar(select(ConversationSegment))

        assert segment.conversation_no.startswith("conv_")
        assert segment.title == "模拟会话"
        assert segment.summary == "本轮会话主要围绕消息窗口内容进行讨论。"
        assert segment.start_time < segment.end_time
        assert segment.keywords == ["模拟", "会话"]
        assert segment.participants == ["USER_A", "USER_B"]
        assert float(segment.confidence) == 0.88
        assert segment.version == 1
        assert segment.status == "active"


def test_missing_start_or_end_msgid_marks_ai_run_invalid_output_and_writes_no_segment(tmp_path):
    client, app = make_client(tmp_path, dify_client=MissingBoundaryDifyClient())
    seed_window_messages(client)

    response = run_segmentation(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert response.json()["data"]["segments"] == []
    assert scalar_count(app, ConversationSegment) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "not found in input messages" in run.error_message


def test_unknown_participant_marks_ai_run_invalid_output_and_writes_no_segment(tmp_path):
    client, app = make_client(tmp_path, dify_client=UnknownParticipantDifyClient())
    seed_window_messages(client)

    response = run_segmentation(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert scalar_count(app, ConversationSegment) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "participant" in run.error_message


def test_get_conversations_by_chatid_and_detail(tmp_path):
    client, _app = make_client(tmp_path)
    seed_window_messages(client)
    created = run_segmentation(client)
    conversation_no = created.json()["data"]["segments"][0]["conversation_no"]

    list_response = client.get("/api/conversations", params={"chatid": "CHAT_A"})

    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1
    assert list_response.json()["data"]["items"][0]["conversation_no"] == conversation_no

    detail_response = client.get(f"/api/conversations/{conversation_no}")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["conversation_no"] == conversation_no
    assert detail_response.json()["data"]["chatid"] == "CHAT_A"


def test_run_conversation_segmentation_with_empty_window_returns_clear_error(tmp_path):
    client, app = make_client(tmp_path)

    response = run_segmentation(client, chatid="CHAT_EMPTY")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert "No messages found" in response.json()["error"]["message"]
    assert scalar_count(app, AiRun) == 0
    assert scalar_count(app, ConversationSegment) == 0
