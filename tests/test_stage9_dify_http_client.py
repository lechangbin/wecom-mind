import json

import httpx
import pytest
from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import AiRun, AiWorkflow, OutboxMessage
from app.dify.client import (
    DifyClientError,
    DifyHttpClient,
    DifyWebhookClient,
    MockDifyClient,
    build_dify_client,
)
from app.main import create_app


def workflow(*, workflow_id: str | None = None) -> AiWorkflow:
    return AiWorkflow(
        workflow_code="reply_generation",
        workflow_name="通用回复生成",
        version="v1",
        response_mode="blocking",
        dify_workflow_id=workflow_id,
        input_schema={},
        output_schema={},
    )


def webhook_workflow(
    *, webhook_url: str | None = "https://dify.local/hook/abc"
) -> AiWorkflow:
    return AiWorkflow(
        workflow_code="reply_generation",
        workflow_name="通用回复生成",
        version="v1",
        response_mode="blocking",
        dify_webhook_url=webhook_url,
        input_schema={},
        output_schema={},
    )


def real_settings(**overrides):
    data = {
        "dify_client_mode": "real",
        "dify_base_url": "https://api.dify.ai/v1",
        "dify_api_key": "test-key",
        "dify_timeout_seconds": 3,
        "dify_max_retries": 0,
        "dify_user": "test-user",
    }
    data.update(overrides)
    return Settings(**data)


def make_http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_default_config_uses_mock_dify_client():
    client = build_dify_client(Settings())

    assert isinstance(client, MockDifyClient)


def test_real_mode_requires_base_url_and_api_key():
    with pytest.raises(DifyClientError) as exc:
        build_dify_client(
            Settings(
                dify_client_mode="real",
                dify_base_url="",
                dify_api_key="",
            )
        )

    assert "DIFY_BASE_URL" in str(exc.value)
    assert "DIFY_API_KEY" in str(exc.value)


def test_real_client_parses_outputs_object_directly():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "action": "ignore",
                        "confidence": 0.2,
                    },
                    "error": None,
                }
            },
        )

    client = DifyHttpClient(real_settings(), http_client=make_http_client(handler))

    result = client.run_workflow(workflow(), {"message": {"content": "hello"}})

    assert result == {"action": "ignore", "confidence": 0.2}
    assert seen["path"] == "/v1/workflows/run"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"] == {
        "inputs": {"message": {"content": "hello"}},
        "response_mode": "blocking",
        "user": "test-user",
    }


def test_real_client_parses_outputs_result_json_string():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "result": '{"action":"ignore","confidence":0.3}',
                    },
                }
            },
        )

    client = DifyHttpClient(real_settings(dify_base_url="https://api.dify.ai"), http_client=make_http_client(handler))

    result = client.run_workflow(workflow(), {})

    assert result == {"action": "ignore", "confidence": 0.3}


def test_real_client_uses_workflow_id_endpoint_when_available():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "output": {"action": "ignore", "confidence": 0.4},
                    },
                }
            },
        )

    client = DifyHttpClient(real_settings(), http_client=make_http_client(handler))

    result = client.run_workflow(workflow(workflow_id="wf_123"), {})

    assert seen["path"] == "/v1/workflows/wf_123/run"
    assert result == {"action": "ignore", "confidence": 0.4}


def test_webhook_mode_builds_webhook_client():
    client = build_dify_client(Settings(dify_client_mode="webhook"))

    assert isinstance(client, DifyWebhookClient)


def test_webhook_client_posts_business_json_directly_and_parses_outputs():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "action": "reply",
                        "reply": {"reply_type": "markdown", "content": "你好"},
                        "metadata": {"reply_scene": "manual", "intent_type": "test"},
                        "confidence": 1,
                    },
                }
            },
        )

    client = DifyWebhookClient(
        Settings(dify_client_mode="webhook", dify_timeout_seconds=3, dify_max_retries=0),
        http_client=make_http_client(handler),
    )

    result = client.run_workflow(
        webhook_workflow(),
        {"reply_scene": "manual", "recent_messages": []},
    )

    assert seen["url"] == "https://dify.local/hook/abc"
    assert seen["body"] == {"reply_scene": "manual", "recent_messages": []}
    assert result == {
        "action": "reply",
        "reply": {"reply_type": "markdown", "content": "你好"},
        "metadata": {"reply_scene": "manual", "intent_type": "test"},
        "confidence": 1,
    }


def test_webhook_client_parses_direct_business_json_response():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": []})

    client = DifyWebhookClient(
        Settings(dify_client_mode="webhook", dify_timeout_seconds=3, dify_max_retries=0),
        http_client=make_http_client(handler),
    )

    result = client.run_workflow(webhook_workflow(), {"chatid": "CHAT_A"})

    assert result == {"segments": []}


def test_webhook_client_rejects_ack_without_outputs():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "success", "message": "Webhook processed successfully"},
        )

    client = DifyWebhookClient(
        Settings(dify_client_mode="webhook", dify_timeout_seconds=3, dify_max_retries=0),
        http_client=make_http_client(handler),
    )

    with pytest.raises(DifyClientError) as exc:
        client.run_workflow(webhook_workflow(), {"chatid": "CHAT_A"})

    assert "did not return workflow outputs" in str(exc.value)


def test_webhook_client_requires_workflow_webhook_url():
    client = DifyWebhookClient(
        Settings(dify_client_mode="webhook", dify_timeout_seconds=3, dify_max_retries=0),
        http_client=make_http_client(lambda _request: httpx.Response(200, json={})),
    )

    with pytest.raises(DifyClientError) as exc:
        client.run_workflow(webhook_workflow(webhook_url=None), {})

    assert "dify_webhook_url" in str(exc.value)


def test_real_client_non_2xx_raises_dify_client_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized token")

    client = DifyHttpClient(real_settings(), http_client=make_http_client(handler))

    with pytest.raises(DifyClientError) as exc:
        client.run_workflow(workflow(), {})

    assert "status_code=401" in str(exc.value)
    assert "unauthorized" in str(exc.value)


def test_real_client_failed_dify_status_raises_dify_client_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "failed",
                    "outputs": {},
                    "error": "workflow node failed",
                }
            },
        )

    client = DifyHttpClient(real_settings(), http_client=make_http_client(handler))

    with pytest.raises(DifyClientError) as exc:
        client.run_workflow(workflow(), {})

    assert "workflow node failed" in str(exc.value)


def test_existing_workflow_marks_ai_run_failed_when_real_dify_errors(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage9.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"status": "failed", "outputs": {}, "error": "boom"}},
        )

    app.state.dify_client = DifyHttpClient(
        real_settings(),
        http_client=make_http_client(handler),
    )
    client = TestClient(app)
    message_response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": "mcp_msg_STAGE9",
            "raw_message": {
                "msgid": "MSG_STAGE9",
                "chatid": "CHAT_A",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@机器人 帮我看看"},
                "mentioned_users": ["BOT_ID"],
                "create_time": 1777827600,
            },
        },
    )
    assert message_response.status_code == 200

    evaluate_response = client.post(
        "/api/triggers/evaluate",
        json={"message_id": message_response.json()["data"]["message_id"]},
    )

    assert evaluate_response.status_code == 200
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun).where(AiRun.workflow_code == "reply_generation"))
        outbox_count = session.scalar(select(func.count()).select_from(OutboxMessage))

        assert run.status == "failed"
        assert "boom" in run.error_message
        assert outbox_count == 0


def test_existing_workflow_saves_ai_run_success_when_webhook_returns_outputs(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage9_webhook.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "succeeded",
                    "outputs": {
                        "action": "reply",
                        "reply": {
                            "reply_type": "markdown",
                            "content": "你好",
                        },
                        "metadata": {
                            "reply_scene": "mention",
                            "intent_type": "summary_request",
                            "evidence_msgids": ["MSG_STAGE9_WEBHOOK"],
                        },
                        "confidence": 0.9,
                    },
                }
            },
        )

    app.state.dify_client = DifyWebhookClient(
        Settings(dify_client_mode="webhook", dify_timeout_seconds=3, dify_max_retries=0),
        http_client=make_http_client(handler),
    )

    with app.state.SessionLocal() as session:
        workflow_record = session.scalar(
            select(AiWorkflow).where(AiWorkflow.workflow_code == "reply_generation")
        )
        workflow_record.dify_webhook_url = "https://dify.local/hook/reply"
        session.commit()

    client = TestClient(app)
    message_response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": "mcp_msg_STAGE9_WEBHOOK",
            "raw_message": {
                "msgid": "MSG_STAGE9_WEBHOOK",
                "chatid": "CHAT_A",
                "chattype": "group",
                "from": {"userid": "USER_A", "name": "USER_A"},
                "msgtype": "text",
                "text": {"content": "@机器人 帮我看看"},
                "mentioned_users": ["BOT_ID"],
                "create_time": 1777827600,
            },
        },
    )
    assert message_response.status_code == 200

    evaluate_response = client.post(
        "/api/triggers/evaluate",
        json={"message_id": message_response.json()["data"]["message_id"]},
    )

    assert evaluate_response.status_code == 200
    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun).where(AiRun.workflow_code == "reply_generation"))

        assert run.status == "success"
        assert run.output_json == {
            "action": "reply",
            "reply": {
                "reply_type": "markdown",
                "content": "你好",
            },
            "metadata": {
                "reply_scene": "mention",
                "intent_type": "summary_request",
                "evidence_msgids": ["MSG_STAGE9_WEBHOOK"],
            },
            "confidence": 0.9,
        }
