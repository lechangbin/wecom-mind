from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun, AiWorkflow, UserProfile, UserProfileFact
from app.main import create_app


class UserIdMismatchDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "userid": "USER_B",
            "profile_action": "create",
            "summary": "错误用户画像。",
            "facts_to_add": [
                {
                    "fact_type": "interest",
                    "label": "错误证据",
                    "description": "userid 与输入不一致。",
                    "evidence_msgids": [input_json["recent_messages"][0]["msgid"]],
                    "evidence_conversation_nos": [],
                    "confidence": 0.86,
                }
            ],
            "facts_to_update": [],
            "facts_to_retire": [],
            "confidence": 0.8,
        }


class MissingEvidenceDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "userid": input_json["userid"],
            "profile_action": "create",
            "summary": "缺少证据的画像。",
            "facts_to_add": [
                {
                    "fact_type": "interest",
                    "label": "不存在的消息证据",
                    "description": "证据 msgid 不在本次输入窗口内。",
                    "evidence_msgids": ["MSG_NOT_IN_INPUT"],
                    "evidence_conversation_nos": [],
                    "confidence": 0.86,
                }
            ],
            "facts_to_update": [],
            "facts_to_retire": [],
            "confidence": 0.8,
        }


class LowConfidenceDifyClient:
    def run_workflow(self, workflow, input_json):
        return {
            "userid": input_json["userid"],
            "profile_action": "create",
            "summary": "低置信度画像。",
            "facts_to_add": [
                {
                    "fact_type": "interest",
                    "label": "可能关注报价",
                    "description": "证据不足，仅弱相关。",
                    "evidence_msgids": [input_json["recent_messages"][0]["msgid"]],
                    "evidence_conversation_nos": [],
                    "confidence": 0.6,
                },
                {
                    "fact_type": "preference",
                    "label": "非常弱的偏好",
                    "description": "置信度过低。",
                    "evidence_msgids": [input_json["recent_messages"][0]["msgid"]],
                    "evidence_conversation_nos": [],
                    "confidence": 0.4,
                },
            ],
            "facts_to_update": [],
            "facts_to_retire": [],
            "confidence": 0.62,
        }


def make_client(tmp_path, *, dify_client=None):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage6.db'}",
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
    userid: str = "USER_A",
    content: str = "报价和客户跟进怎么安排？",
    create_time: int = 1777827600,
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


def seed_user_messages(client: TestClient):
    ingest_message(client, msgid="MSG_1", content="这个报价和折扣策略怎么定？")
    ingest_message(client, msgid="MSG_2", content="客户跟进和交付排期今天确认。", create_time=1777827660)
    ingest_message(client, msgid="MSG_OTHER", userid="USER_B", content="旁路用户消息。", create_time=1777827720)


def run_profile_analysis(client: TestClient, *, userid: str = "USER_A"):
    return client.post(
        "/api/profiles/analyze/run",
        json={
            "userid": userid,
            "mode": "incremental",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T02:00:00+08:00",
            },
        },
        headers={"X-Request-ID": "req-profile"},
    )


def scalar_count(app, model):
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_default_user_profile_analysis_workflow_exists_after_init(tmp_path):
    _client, app = make_client(tmp_path)

    with app.state.SessionLocal() as session:
        workflow = session.scalar(
            select(AiWorkflow).where(
                AiWorkflow.workflow_code == "user_profile_analysis",
                AiWorkflow.version == "v1",
            )
        )

        assert workflow is not None
        assert workflow.response_mode == "blocking"
        assert workflow.enabled is True


def test_run_user_profile_analysis_with_empty_window_returns_clear_error(tmp_path):
    client, app = make_client(tmp_path)

    response = run_profile_analysis(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert "No messages found" in response.json()["error"]["message"]
    assert scalar_count(app, AiRun) == 0
    assert scalar_count(app, UserProfile) == 0
    assert scalar_count(app, UserProfileFact) == 0


def test_valid_user_profile_analysis_creates_success_ai_run_profile_and_fact(tmp_path):
    client, app = make_client(tmp_path)
    seed_user_messages(client)

    response = run_profile_analysis(client)

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-profile"
    assert response.json()["data"]["status"] == "success"
    assert response.json()["data"]["profile_version"] == 1
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun).where(AiRun.workflow_code == "user_profile_analysis"))
        profile = session.scalar(select(UserProfile).where(UserProfile.userid == "USER_A"))
        fact = session.scalar(select(UserProfileFact).where(UserProfileFact.userid == "USER_A"))

        assert run.status == "success"
        assert run.output_json["profile_action"] == "create"
        assert run.output_json["facts_to_add"][0]["label"] == "关注报价策略"
        assert run.input_json["userid"] == "USER_A"
        assert [message["msgid"] for message in run.input_json["recent_messages"]] == [
            "MSG_1",
            "MSG_2",
        ]
        assert profile.summary == "该用户近期主要关注报价、客户跟进和交付排期。"
        assert profile.version == 1
        assert profile.status == "active"
        assert profile.profile_json["facts"][0]["label"] == "关注报价策略"
        assert fact.profile_id == profile.id
        assert fact.fact_type == "interest"
        assert fact.evidence_msgids == ["MSG_1"]
        assert fact.status == "active"


def test_get_user_profile_returns_latest_version_and_facts(tmp_path):
    client, _app = make_client(tmp_path)
    seed_user_messages(client)
    run_profile_analysis(client)

    response = client.get("/api/users/USER_A/profile")

    assert response.status_code == 200
    assert response.json()["data"]["userid"] == "USER_A"
    assert response.json()["data"]["version"] == 1
    assert response.json()["data"]["facts"][0]["label"] == "关注报价策略"


def test_output_userid_mismatch_marks_invalid_output_and_writes_no_profile(tmp_path):
    client, app = make_client(tmp_path, dify_client=UserIdMismatchDifyClient())
    seed_user_messages(client)

    response = run_profile_analysis(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert response.json()["data"]["profile"] is None
    assert scalar_count(app, UserProfile) == 0
    assert scalar_count(app, UserProfileFact) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "userid" in run.error_message


def test_missing_evidence_msgid_marks_invalid_output_and_writes_no_profile(tmp_path):
    client, app = make_client(tmp_path, dify_client=MissingEvidenceDifyClient())
    seed_user_messages(client)

    response = run_profile_analysis(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "invalid_output"
    assert scalar_count(app, UserProfile) == 0
    assert scalar_count(app, UserProfileFact) == 0
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        assert run.status == "invalid_output"
        assert "evidence_msgids" in run.error_message


def test_low_confidence_facts_are_saved_by_rule(tmp_path):
    client, app = make_client(tmp_path, dify_client=LowConfidenceDifyClient())
    seed_user_messages(client)

    response = run_profile_analysis(client)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "success"
    with app.state.SessionLocal() as session:
        profile = session.scalar(select(UserProfile).where(UserProfile.userid == "USER_A"))
        facts = session.scalars(
            select(UserProfileFact)
            .where(UserProfileFact.userid == "USER_A")
            .order_by(UserProfileFact.id.asc())
        ).all()

        assert len(facts) == 1
        assert facts[0].status == "low_confidence"
        assert facts[0].label == "可能关注报价"
        assert profile.profile_json["facts"] == []


def test_repeated_analysis_creates_new_profile_version(tmp_path):
    client, app = make_client(tmp_path)
    seed_user_messages(client)

    first = run_profile_analysis(client)
    second = run_profile_analysis(client)

    assert first.json()["data"]["profile_version"] == 1
    assert second.json()["data"]["profile_version"] == 2
    with app.state.SessionLocal() as session:
        profiles = session.scalars(
            select(UserProfile)
            .where(UserProfile.userid == "USER_A")
            .order_by(UserProfile.version.asc())
        ).all()

        assert [profile.version for profile in profiles] == [1, 2]
        assert profiles[0].status == "superseded"
        assert profiles[1].status == "active"
