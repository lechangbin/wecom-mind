from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import (
    AiRun,
    ConversationSegment,
    Message,
    MessageRaw,
    OutboxMessage,
    UserProfile,
    UserProfileFact,
    WeComUser,
)
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage18.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
        dify_client_mode="mock",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def test_messages_api_filters_by_date_range_and_query_without_full_range_leak(tmp_path):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        _add_message(
            session,
            msgid="MSG_TODAY_MATCH",
            chatid="CHAT_A",
            userid="USER_A",
            content="今天讨论报价方案",
            create_time=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
        )
        _add_message(
            session,
            msgid="MSG_TODAY_OTHER",
            chatid="CHAT_A",
            userid="USER_B",
            content="今天讨论排期",
            create_time=datetime(2026, 6, 4, 2, 0, tzinfo=timezone.utc),
        )
        _add_message(
            session,
            msgid="MSG_TOMORROW_MATCH",
            chatid="CHAT_A",
            userid="USER_C",
            content="明天也讨论报价方案",
            create_time=datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc),
        )
        session.commit()

    response = client.get(
        "/api/messages",
        params={
            "start_date": "2026-06-04",
            "end_date": "2026-06-04",
            "q": "报价",
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert [item["external_msgid"] for item in data["items"]] == ["MSG_TODAY_MATCH"]
    assert data["items"][0]["sender_type"] == "user"


def test_messages_and_conversations_api_allow_one_month_and_reject_longer_ranges(tmp_path):
    client, _app = make_client(tmp_path)

    messages_response = client.get(
        "/api/messages",
        params={"start_date": "2026-06-01", "end_date": "2026-07-01"},
    )
    conversations_response = client.get(
        "/api/conversations",
        params={"start_date": "2026-06-01", "end_date": "2026-07-01"},
    )
    too_long_response = client.get(
        "/api/messages",
        params={"start_date": "2026-06-01", "end_date": "2026-07-02"},
    )
    reversed_response = client.get(
        "/api/messages",
        params={"start_date": "2026-06-10", "end_date": "2026-06-01"},
    )

    assert messages_response.status_code == 200
    assert conversations_response.status_code == 200
    assert too_long_response.status_code == 400
    assert too_long_response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert too_long_response.json()["error"]["message"] == "最多只能查询一个月内的数据"
    assert reversed_response.status_code == 400
    assert reversed_response.json()["error"]["message"] == "开始日期不能晚于截止日期"


def test_messages_api_hides_cross_source_duplicates_by_default(tmp_path):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        canonical = _add_message(
            session,
            msgid="MSG_REAL",
            chatid="CHAT_A",
            userid="USER_A",
            content="@机器人 同一条消息",
            create_time=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
        )
        duplicate = _add_message(
            session,
            msgid="payload:MSG_DUPLICATE",
            chatid="CHAT_A",
            userid="USER_A",
            content="@机器人 同一条消息",
            create_time=datetime(2026, 6, 4, 1, 0, 2, tzinfo=timezone.utc),
        )
        canonical.business_identity_key = "bizmsg_same"
        canonical.canonical_message_id = canonical.id
        duplicate.business_identity_key = "bizmsg_same"
        duplicate.canonical_message_id = canonical.id
        session.commit()

    response = client.get(
        "/api/messages",
        params={"start_date": "2026-06-04", "end_date": "2026-06-04"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["external_msgid"] == "MSG_REAL"

    response = client.get(
        "/api/messages",
        params={
            "start_date": "2026-06-04",
            "end_date": "2026-06-04",
            "include_source_duplicates": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert {item["external_msgid"] for item in data["items"]} == {
        "MSG_REAL",
        "payload:MSG_DUPLICATE",
    }
    assert any(item["is_business_duplicate"] for item in data["items"])


def test_messages_api_includes_sent_bot_outbox_messages_in_full_view(tmp_path):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        _add_message(
            session,
            msgid="MSG_USER",
            chatid="CHAT_A",
            userid="USER_A",
            content="用户提问",
            create_time=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
        )
        session.add(
            OutboxMessage(
                outbox_id="out_stage18_bot",
                scene="reply",
                chatid="CHAT_A",
                target_userids=[],
                msgtype="markdown",
                content={"markdown": {"content": "机器人回复"}},
                source_type="ai_run",
                source_id="airun_stage18_bot",
                status="sent",
                idempotency_key="out_stage18_bot",
                sent_at=datetime(2026, 6, 4, 1, 1, tzinfo=timezone.utc),
                created_at=datetime(2026, 6, 4, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.commit()

    response = client.get(
        "/api/messages",
        params={"start_date": "2026-06-04", "end_date": "2026-06-04"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [item["sender_type"] for item in data["items"]] == ["bot", "user"]
    assert data["items"][0]["content_text"] == "机器人回复"
    assert data["items"][0]["external_msgid"] == "out_stage18_bot"

    bot_only = client.get(
        "/api/messages",
        params={
            "start_date": "2026-06-04",
            "end_date": "2026-06-04",
            "sender_type": "bot",
        },
    )
    assert bot_only.status_code == 200
    assert bot_only.json()["data"]["total"] == 1


def test_reply_tasks_api_maps_mention_and_proactive_replies_to_node_statuses(tmp_path):
    client, app = make_client(tmp_path)
    mention_message_id = _ingest_message(
        client,
        msgid="MSG_MENTION",
        userid="USER_A",
        content="@机器人 帮我回答报价问题",
        create_time=1777827600,
        mentioned_bot=True,
    )
    trigger_response = client.post(
        "/api/triggers/evaluate",
        json={"message_id": mention_message_id},
    )
    assert trigger_response.status_code == 200

    _ingest_message(
        client,
        msgid="MSG_PROACTIVE",
        userid="USER_B",
        content="这个报价规则在哪里可以看？",
        create_time=1777827660,
    )
    proactive_response = client.post(
        "/api/proactive-replies/run",
        json={
            "chatid": "CHAT_STAGE18",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T23:59:59+08:00",
            },
            "auto_enqueue": True,
        },
    )
    assert proactive_response.status_code == 200

    with app.state.SessionLocal() as session:
        outbox = session.scalar(
            select(OutboxMessage).where(OutboxMessage.scene == "reply")
        )
        outbox.status = "sent"
        session.commit()

    response = client.get(
        "/api/admin/reply-tasks",
        params={"start_date": "2026-05-04", "end_date": "2026-05-04", "limit": 20},
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    by_type = {item["task_type"]: item for item in items}
    assert by_type["mention"]["status"] == "replied"
    assert by_type["proactive"]["status"] == "replying"
    assert [node["node_key"] for node in by_type["mention"]["nodes"]] == [
        "message_ingested",
        "trigger_claimed",
        "dify_run",
        "outbox_created",
        "wecom_send",
    ]


def test_conversations_api_searches_summary_before_messages_and_returns_segment_messages(
    tmp_path,
):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        first_start = _add_message(
            session,
            msgid="MSG_SUMMARY_START",
            chatid="CHAT_A",
            userid="USER_A",
            content="先聊预算",
            create_time=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
        )
        first_end = _add_message(
            session,
            msgid="MSG_SUMMARY_END",
            chatid="CHAT_A",
            userid="USER_B",
            content="确认方案",
            create_time=datetime(2026, 6, 4, 1, 1, tzinfo=timezone.utc),
        )
        second_start = _add_message(
            session,
            msgid="MSG_BODY_START",
            chatid="CHAT_A",
            userid="USER_A",
            content="普通开场",
            create_time=datetime(2026, 6, 4, 2, 0, tzinfo=timezone.utc),
        )
        second_end = _add_message(
            session,
            msgid="MSG_BODY_END",
            chatid="CHAT_A",
            userid="USER_B",
            content="报价细则在这里",
            create_time=datetime(2026, 6, 4, 2, 1, tzinfo=timezone.utc),
        )
        ai_run = AiRun(
            run_id="airun_stage18_conversation",
            workflow_code="conversation_boundary_detection",
            workflow_version="v1",
            input_json={},
            response_mode="blocking",
            status="success",
        )
        session.add(ai_run)
        session.flush()
        session.add_all(
            [
                ConversationSegment(
                    conversation_no="conv_summary",
                    chatid="CHAT_A",
                    start_message_id=first_start.id,
                    end_message_id=first_end.id,
                    start_time=first_start.create_time,
                    end_time=first_end.create_time,
                    title="预算讨论",
                    summary="报价策略讨论",
                    keywords=["报价"],
                    participants=["USER_A", "USER_B"],
                    ai_run_id=ai_run.id,
                    confidence=0.9,
                    version=1,
                    status="active",
                ),
                ConversationSegment(
                    conversation_no="conv_body",
                    chatid="CHAT_A",
                    start_message_id=second_start.id,
                    end_message_id=second_end.id,
                    start_time=second_start.create_time,
                    end_time=second_end.create_time,
                    title="普通讨论",
                    summary="普通项目讨论",
                    keywords=[],
                    participants=["USER_A", "USER_B"],
                    ai_run_id=ai_run.id,
                    confidence=0.8,
                    version=1,
                    status="active",
                ),
            ]
        )
        session.commit()

    response = client.get(
        "/api/conversations",
        params={
            "start_date": "2026-06-04",
            "end_date": "2026-06-04",
            "q": "报价",
        },
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["conversation_no"] for item in items] == ["conv_summary", "conv_body"]
    assert [item["match_source"] for item in items] == ["summary", "message"]

    detail = client.get("/api/conversations/conv_body/messages")
    assert detail.status_code == 200
    assert [item["external_msgid"] for item in detail.json()["data"]["items"]] == [
        "MSG_BODY_START",
        "MSG_BODY_END",
    ]


def test_ai_memory_full_test_run_is_locked_while_previous_run_is_active(tmp_path):
    client, app = make_client(tmp_path)
    assert hasattr(app.state, "ai_memory_full_test_lock")
    assert app.state.ai_memory_full_test_lock.acquire(blocking=False) is True
    try:
        response = client.post(
            "/api/ai-memory/full-test/run",
            json={"target_date": "2026-06-04", "chatids": ["CHAT_A"]},
        )
    finally:
        app.state.ai_memory_full_test_lock.release()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATED"
    assert response.json()["error"]["details"]["status"] == "running"


def test_outbox_messages_api_supports_status_filter_and_pagination(tmp_path):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        for index in range(3):
            session.add(
                OutboxMessage(
                    outbox_id=f"out_stage18_page_{index}",
                    scene="reply",
                    chatid="CHAT_A",
                    target_userids=[],
                    msgtype="markdown",
                    content={"markdown": {"content": f"回复 {index}"}},
                    source_type="ai_run",
                    source_id=f"airun_stage18_page_{index}",
                    status="failed" if index != 1 else "sent",
                    idempotency_key=f"out_stage18_page_{index}",
                    created_at=datetime(2026, 6, 4, index, 0, tzinfo=timezone.utc),
                )
            )
        session.commit()

    response = client.get(
        "/api/outbox-messages",
        params={"status": "failed", "limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["limit"] == 1
    assert data["offset"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["outbox_id"] == "out_stage18_page_0"


def test_user_profile_versions_api_returns_profile_history(tmp_path):
    client, app = make_client(tmp_path)
    with app.state.SessionLocal() as session:
        session.add(
            WeComUser(
                userid="USER_PROFILE",
                name="画像用户",
                status="active",
                last_active_at=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
            )
        )
        ai_run = AiRun(
            run_id="airun_stage18_profile",
            workflow_code="user_profile_update",
            workflow_version="v1",
            input_json={},
            response_mode="blocking",
            status="success",
        )
        session.add(ai_run)
        session.flush()
        older = UserProfile(
            userid="USER_PROFILE",
            summary="旧画像",
            profile_json={"tone": "formal"},
            ai_run_id=ai_run.id,
            confidence=0.6,
            version=1,
            status="retired",
        )
        active = UserProfile(
            userid="USER_PROFILE",
            summary="新画像",
            profile_json={"tone": "friendly"},
            ai_run_id=ai_run.id,
            confidence=0.9,
            version=2,
            status="active",
        )
        session.add_all([older, active])
        session.flush()
        session.add(
            UserProfileFact(
                userid="USER_PROFILE",
                profile_id=active.id,
                source_ai_run_id=ai_run.id,
                fact_type="preference",
                label="语气偏好",
                description="喜欢简洁自然的回复",
                evidence_msgids=["MSG_PROFILE"],
                evidence_conversation_nos=["conv_profile"],
                confidence=0.85,
                status="active",
            )
        )
        session.commit()

    current = client.get("/api/users/USER_PROFILE/profile")
    assert current.status_code == 200
    assert current.json()["data"]["summary"] == "新画像"
    assert current.json()["data"]["facts"][0]["label"] == "语气偏好"

    response = client.get(
        "/api/users/USER_PROFILE/profile/versions",
        params={"limit": 10, "offset": 0},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["items"][0]["version"] == 2
    assert data["items"][1]["version"] == 1


def test_system_config_summary_is_desensitized_and_exposes_runtime_switches(tmp_path):
    settings = Settings(
        app_env="test",
        app_base_url="http://127.0.0.1:8010",
        admin_web_host="127.0.0.1",
        admin_web_port=5173,
        database_url=f"sqlite:///{tmp_path / 'stage18.db'}",
        wecom_sender_mode="aibot_ws",
        wecom_mcp_verify_mode="mock",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_reply_aibot_secret="SECRET_SHOULD_NOT_LEAK",
        wecom_intent_aibot_id="INTENT_BOT",
        wecom_message_reconcile_enabled=True,
        wecom_message_reconcile_chatids="CHAT_A,CHAT_B",
        wecom_message_reconcile_auto_send=True,
        dify_client_mode="real",
        dify_base_url="http://localhost/v1",
        dify_group_knowledge_reply_api_key="app-secret",
        ai_memory_full_test_enabled=True,
        ai_memory_full_test_chatids="CHAT_A",
    )
    app = create_app(settings=settings)
    client = TestClient(app)

    response = client.get("/api/system/config-summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["app"]["app_env"] == "test"
    assert data["app"]["admin_web_url"] == "http://127.0.0.1:5173"
    assert data["database"]["kind"] == "sqlite"
    assert data["wecom"]["sender_mode"] == "aibot_ws"
    assert data["wecom"]["reply_bot_configured"] is True
    assert data["wecom"]["message_reconcile_chatids_count"] == 2
    assert data["dify"]["client_mode"] == "real"
    assert data["dify"]["workflow_api_keys"]["group_knowledge_reply"] is True
    assert data["ai_memory"]["enabled"] is True
    assert "SECRET_SHOULD_NOT_LEAK" not in response.text
    assert "app-secret" not in response.text


def test_system_workers_reports_configured_worker_states(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage18.db'}",
        wecom_sender_mode="aibot_ws",
        wecom_message_reconcile_enabled=True,
        wecom_message_reconcile_chatids="CHAT_A",
        ai_memory_full_test_enabled=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    app.state.ai_memory_full_test_state["status"] = "running"

    response = client.get("/api/system/workers")

    assert response.status_code == 200
    items = {item["worker_key"]: item for item in response.json()["data"]["items"]}
    assert items["api"]["status"] == "running"
    assert items["wecom_aibot_worker"]["enabled"] is True
    assert items["message_reconcile_worker"]["enabled"] is True
    assert items["message_reconcile_worker"]["details"]["chatids_count"] == 1
    assert items["ai_memory_full_test_worker"]["enabled"] is True
    assert items["ai_memory_full_test_worker"]["status"] == "running"


def _ingest_message(
    client: TestClient,
    *,
    msgid: str,
    userid: str,
    content: str,
    create_time: int,
    mentioned_bot: bool = False,
) -> int:
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": f"mcp_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_STAGE18",
                "chattype": "group",
                "chat_name": "阶段18测试群",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": ["BOT_ID"] if mentioned_bot else [],
                "create_time": create_time,
            },
        },
    )
    assert response.status_code == 200
    return int(response.json()["data"]["message_id"])


def _add_message(
    session,
    *,
    msgid: str,
    chatid: str,
    userid: str,
    content: str,
    create_time: datetime,
    sender_type: str = "user",
) -> Message:
    raw = MessageRaw(
        source="test",
        external_msgid=msgid,
        chatid=chatid,
        userid=userid,
        msgtype="text",
        raw_payload={"text": {"content": content}},
        idempotency_key=f"raw_{msgid}",
        create_time=create_time,
    )
    session.add(raw)
    session.flush()
    message = Message(
        raw_message_id=raw.id,
        external_msgid=msgid,
        chatid=chatid,
        chattype="group",
        userid=userid,
        msgtype="text",
        sender_type=sender_type,
        content_text=content,
        normalized_content={"text": content},
        mentioned_bot=False,
        mentioned_users=[],
        create_time=create_time,
    )
    session.add(message)
    session.flush()
    return message
