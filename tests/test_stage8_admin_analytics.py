from datetime import datetime, timezone

from sqlalchemy import select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage8.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def ingest_message(
    client: TestClient,
    *,
    msgid: str,
    userid: str,
    content: str,
    create_time: int,
    mentioned_bot: bool = False,
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
                "chat_name": "项目群",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": ["BOT_ID"] if mentioned_bot else [],
                "quote": {"msgid": "QUOTE_1"} if mentioned_bot else None,
                "create_time": create_time,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def seed_dashboard_data(client: TestClient):
    message_id = ingest_message(
        client,
        msgid="MSG_1",
        userid="USER_A",
        content="@机器人 帮我看看报价",
        create_time=1777827600,
        mentioned_bot=True,
    )
    ingest_message(
        client,
        msgid="MSG_2",
        userid="USER_B",
        content="报价方案需要确认。",
        create_time=1777827660,
    )

    trigger_response = client.post("/api/triggers/evaluate", json={"message_id": message_id})
    assert trigger_response.status_code == 200

    scheduled_response = client.post(
        "/api/scheduled-intents/run",
        json={
            "chatid": "CHAT_A",
            "job_id": "job_stage8",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T02:00:00+08:00",
            },
            "auto_enqueue": True,
        },
    )
    assert scheduled_response.status_code == 200


def test_dashboard_overview_returns_core_counts(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)

    response = client.get("/api/dashboard/overview", params={"date": "2026-05-04"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["date"] == "2026-05-04"
    assert data["today_message_count"] == 2
    assert data["total_message_count"] == 2
    assert data["active_chat_count"] == 1
    assert data["active_user_count"] == 2
    assert data["today_trigger_count"] == 1
    assert data["ai_run_count"] == 2
    assert data["ai_success_rate"] == 1.0
    assert data["pending_outbox_count"] == 2
    assert data["failed_outbox_count"] == 0
    assert data["scheduled_intent_count"] == 1


def test_chats_support_pagination_and_return_message_count(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)

    response = client.get("/api/chats", params={"limit": 1, "offset": 0})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["chatid"] == "CHAT_A"
    assert data["items"][0]["name"] == "项目群"
    assert data["items"][0]["message_count"] == 2


def test_chat_messages_returns_messages_for_chat(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)

    response = client.get("/api/chats/CHAT_A/messages", params={"limit": 20, "offset": 0})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [item["external_msgid"] for item in data["items"]] == ["MSG_2", "MSG_1"]
    assert data["items"][1]["mentioned_bot"] is True
    assert data["items"][1]["quote_msgid"] == "QUOTE_1"


def test_users_returns_user_list_with_message_count_and_profile_summary(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)
    profile_response = client.post(
        "/api/profiles/analyze/run",
        json={
            "userid": "USER_A",
            "mode": "incremental",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T02:00:00+08:00",
            },
        },
    )
    assert profile_response.status_code == 200

    response = client.get("/api/users", params={"limit": 20, "offset": 0})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    users = {item["userid"]: item for item in data["items"]}
    assert users["USER_A"]["message_count"] == 1
    assert users["USER_A"]["latest_profile_summary"] == "该用户近期主要关注报价、客户跟进和交付排期。"
    assert users["USER_B"]["message_count"] == 1
    assert users["USER_B"]["latest_profile_summary"] is None


def test_message_stats_returns_trend_distribution_and_rankings(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)

    response = client.get(
        "/api/stats/messages",
        params={
            "start": "2026-05-04T00:00:00+08:00",
            "end": "2026-05-04T23:59:59+08:00",
            "group_by": "hour",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["trend"] == [{"bucket": "2026-05-04 01:00", "count": 2}]
    assert data["msgtype_distribution"] == [{"msgtype": "text", "count": 2}]
    assert data["chat_ranking"] == [{"chatid": "CHAT_A", "count": 2}]
    assert data["user_ranking"] == [
        {"userid": "USER_A", "count": 1},
        {"userid": "USER_B", "count": 1},
    ]


def test_workflow_stats_returns_aggregates(tmp_path):
    client, app = make_client(tmp_path)
    seed_dashboard_data(client)
    with app.state.SessionLocal() as session:
        failed = AiRun(
            run_id="airun_failed_stage8",
            workflow_code="reply_generation",
            workflow_version="v1",
            input_json={},
            response_mode="blocking",
            status="failed",
            latency_ms=200,
            created_at=datetime(2026, 5, 3, 18, 0, tzinfo=timezone.utc),
        )
        session.add(failed)
        session.commit()

    response = client.get(
        "/api/stats/workflows",
        params={
            "start": "2026-05-04T00:00:00+08:00",
            "end": "2026-05-04T23:59:59+08:00",
        },
    )

    assert response.status_code == 200
    items = {item["workflow_code"]: item for item in response.json()["data"]["items"]}
    assert items["reply_generation"]["total"] == 2
    assert items["reply_generation"]["success"] == 1
    assert items["reply_generation"]["failed"] == 1
    assert items["reply_generation"]["invalid_output"] == 0
    assert items["reply_generation"]["success_rate"] == 0.5
    assert items["reply_generation"]["avg_latency_ms"] is not None
    assert items["intent_detection"]["total"] == 1


def test_ai_runs_support_workflow_code_status_filtering(tmp_path):
    client, _app = make_client(tmp_path)
    seed_dashboard_data(client)

    response = client.get(
        "/api/ai-runs",
        params={
            "workflow_code": "intent_detection",
            "status": "success",
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["workflow_code"] == "intent_detection"
    assert data["items"][0]["status"] == "success"


def test_admin_analytics_empty_data_returns_empty_results(tmp_path):
    client, _app = make_client(tmp_path)

    assert client.get("/api/dashboard/overview").status_code == 200
    assert client.get("/api/chats").json()["data"]["total"] == 0
    assert client.get("/api/users").json()["data"]["total"] == 0
    assert client.get("/api/chats/CHAT_EMPTY/messages").json()["data"]["total"] == 0
    assert client.get("/api/stats/messages").json()["data"]["trend"] == []
    assert client.get("/api/stats/workflows").json()["data"]["items"] == []
