from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import Settings
from app.db.models import Message, MessageIngestionJob, MessageRaw, WeComMcpCallback
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage12.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def post_callback(client: TestClient, body: dict):
    response = client.post(
        "/api/wecom/callbacks/mcp",
        params={"msg_signature": "mock", "timestamp": "1777827600", "nonce": "n1"},
        json=body,
    )
    assert response.status_code == 200
    return response.json()["data"]["callback_id"]


def raw_message(msgid="MSG_1", chatid="CHAT_A", userid="USER_A", content="hello"):
    return {
        "msgid": msgid,
        "chatid": chatid,
        "chattype": "group",
        "from": {"userid": userid, "name": userid},
        "msgtype": "text",
        "text": {"content": content},
        "create_time": 1777827600,
    }


def first_job(app, *, callback_id: str | None = None) -> MessageIngestionJob:
    with app.state.SessionLocal() as session:
        statement = select(MessageIngestionJob)
        if callback_id:
            statement = statement.where(MessageIngestionJob.callback_id == callback_id)
        return session.scalar(statement)


def count_rows(app, model) -> int:
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_raw_message_callback_job_ingests_raw_and_normalized_message(tmp_path):
    client, app = make_client(tmp_path)
    callback_id = post_callback(client, {"raw_message": raw_message("MSG_RAW")})
    job = first_job(app, callback_id=callback_id)

    response = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "succeeded"
    assert data["ingested_count"] == 1
    assert data["duplicated_count"] == 0
    with app.state.SessionLocal() as session:
        stored_job = session.get(MessageIngestionJob, job.id)
        callback = session.scalar(select(WeComMcpCallback).where(WeComMcpCallback.callback_id == callback_id))
        message = session.scalar(select(Message))
        raw = session.scalar(select(MessageRaw))

        assert stored_job.status == "succeeded"
        assert stored_job.started_at is not None
        assert stored_job.finished_at is not None
        assert stored_job.error_message is None
        assert callback.status == "processed"
        assert callback.processed_at is not None
        assert raw.external_msgid == "MSG_RAW"
        assert raw.idempotency_key == "mcp_msg_MSG_RAW"
        assert message.external_msgid == "MSG_RAW"
        assert message.content_text == "hello"


def test_messages_array_callback_job_ingests_multiple_messages_with_batch_run(tmp_path):
    client, app = make_client(tmp_path)
    post_callback(
        client,
        {
            "messages": [
                raw_message("MSG_ARRAY_1", userid="USER_A"),
                raw_message("MSG_ARRAY_2", userid="USER_B", content="second"),
            ]
        },
    )

    response = client.post("/api/wecom/ingestion-jobs/run", json={"limit": 20})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["processed_jobs"] == 1
    assert data["succeeded_jobs"] == 1
    assert data["ingested_messages"] == 2
    assert count_rows(app, MessageRaw) == 2
    assert count_rows(app, Message) == 2


def test_wecom_xml_text_dict_callback_job_converts_and_ingests_message(tmp_path):
    client, app = make_client(tmp_path)
    callback_id = post_callback(
        client,
        {
            "ToUserName": "ww_corp",
            "FromUserName": "USER_XML",
            "CreateTime": "1777827600",
            "MsgType": "text",
            "MsgId": "MSG_XML",
            "ChatId": "CHAT_XML",
            "Content": "XML hello",
        },
    )
    job = first_job(app, callback_id=callback_id)

    response = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"
    with app.state.SessionLocal() as session:
        message = session.scalar(select(Message))

        assert message.external_msgid == "MSG_XML"
        assert message.chatid == "CHAT_XML"
        assert message.userid == "USER_XML"
        assert message.msgtype == "text"
        assert message.content_text == "XML hello"


def test_wecom_xml_text_dict_without_chatid_skips_job_and_writes_no_message(tmp_path):
    client, app = make_client(tmp_path)
    callback_id = post_callback(
        client,
        {
            "FromUserName": "USER_XML",
            "CreateTime": "1777827600",
            "MsgType": "text",
            "MsgId": "MSG_XML_NO_CHAT",
            "Content": "no chat",
        },
    )
    job = first_job(app, callback_id=callback_id)

    response = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "skipped"
    assert data["skipped_count"] == 1
    assert data["error_message"] == "no_normalizable_messages"
    assert count_rows(app, MessageRaw) == 0


def test_successful_job_rerun_does_not_duplicate_messages(tmp_path):
    client, app = make_client(tmp_path)
    callback_id = post_callback(client, {"raw_message": raw_message("MSG_DUP")})
    job = first_job(app, callback_id=callback_id)

    first = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")
    second = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "succeeded"
    assert second.json()["data"]["ingested_count"] == 0
    assert second.json()["data"]["duplicated_count"] == 0
    assert count_rows(app, MessageRaw) == 1
    assert count_rows(app, Message) == 1


def test_failed_job_can_be_retried_and_succeed(tmp_path):
    client, app = make_client(tmp_path)
    callback_id = post_callback(
        client,
        {"raw_message": {"msgid": "MSG_FAIL_ONCE", "msgtype": "text"}},
    )
    job = first_job(app, callback_id=callback_id)

    failed = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")
    assert failed.status_code == 200
    assert failed.json()["data"]["status"] == "failed"

    with app.state.SessionLocal() as session:
        callback = session.scalar(select(WeComMcpCallback).where(WeComMcpCallback.callback_id == callback_id))
        callback.raw_body = {"raw_message": raw_message("MSG_FAIL_ONCE_FIXED")}
        session.commit()

    retried = client.post(f"/api/wecom/ingestion-jobs/{job.id}/run")

    assert retried.status_code == 200
    assert retried.json()["data"]["status"] == "succeeded"
    with app.state.SessionLocal() as session:
        stored_job = session.get(MessageIngestionJob, job.id)
        callback = session.scalar(select(WeComMcpCallback).where(WeComMcpCallback.callback_id == callback_id))

        assert stored_job.retry_count == 1
        assert callback.status == "processed"
        assert callback.error_message is None
    assert count_rows(app, MessageRaw) == 1


def test_batch_run_only_processes_pending_normalize_and_leaves_pull_pending(tmp_path):
    client, app = make_client(tmp_path)
    normalize_callback = post_callback(client, {"raw_message": raw_message("MSG_BATCH_NORMALIZE")})
    pull_callback = post_callback(
        client,
        {"event_type": "message_changed", "chatid": "CHAT_PULL", "cursor": "CURSOR_1"},
    )

    response = client.post("/api/wecom/ingestion-jobs/run", json={"limit": 20})

    assert response.status_code == 200
    assert response.json()["data"]["processed_jobs"] == 1
    with app.state.SessionLocal() as session:
        normalize_job = session.scalar(
            select(MessageIngestionJob).where(MessageIngestionJob.callback_id == normalize_callback)
        )
        pull_job = session.scalar(
            select(MessageIngestionJob).where(MessageIngestionJob.callback_id == pull_callback)
        )

        assert normalize_job.status == "succeeded"
        assert pull_job.job_type == "pull"
        assert pull_job.status == "pending"


def test_ingestion_jobs_list_filters_by_status_and_job_type(tmp_path):
    client, app = make_client(tmp_path)
    post_callback(client, {"raw_message": raw_message("MSG_LIST")})
    post_callback(
        client,
        {"event_type": "message_changed", "chatid": "CHAT_PULL", "cursor": "CURSOR_LIST"},
    )
    client.post("/api/wecom/ingestion-jobs/run", json={"limit": 20})

    succeeded = client.get(
        "/api/wecom/ingestion-jobs",
        params={"status": "succeeded", "job_type": "normalize"},
    )
    pending_pull = client.get(
        "/api/wecom/ingestion-jobs",
        params={"status": "pending", "job_type": "pull"},
    )

    assert succeeded.status_code == 200
    assert pending_pull.status_code == 200
    assert succeeded.json()["data"]["total"] == 1
    assert succeeded.json()["data"]["items"][0]["job_type"] == "normalize"
    assert succeeded.json()["data"]["items"][0]["status"] == "succeeded"
    assert pending_pull.json()["data"]["total"] == 1
    assert pending_pull.json()["data"]["items"][0]["job_type"] == "pull"
    assert pending_pull.json()["data"]["items"][0]["status"] == "pending"
