import json

import httpx
import pytest

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import OutboxMessage
from app.main import create_app
from app.outbound.sender import (
    MockWeComMessageSender,
    WeComAppMessageSender,
    WeComWebhookMessageSender,
    build_wecom_message_sender,
)
from app.wecom.client import WeComApiClient, WeComApiError


def make_http_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def app_settings(**overrides):
    data = {
        "wecom_sender_mode": "app",
        "wecom_corp_id": "corp_id",
        "wecom_aibot_secret": "secret",
        "wecom_agent_id": "100001",
        "wecom_api_base_url": "https://qyapi.weixin.qq.com/cgi-bin",
        "wecom_timeout_seconds": 3,
        "wecom_max_retries": 1,
    }
    data.update(overrides)
    return Settings(**data)


def outbox(
    *,
    target_userids=None,
    msgtype="markdown",
    content=None,
    chatid="CHAT_A",
    scene="manual",
) -> OutboxMessage:
    return OutboxMessage(
        outbox_id="out_test",
        scene=scene,
        chatid=chatid,
        target_userids=target_userids or [],
        msgtype=msgtype,
        content=content or {msgtype: {"content": "请确认一下报价方案。"}},
        source_type="manual",
        source_id="manual_1",
        status="pending",
        idempotency_key="idem_1",
    )


def create_outbox(client: TestClient, *, msgtype="markdown", target_userids=None):
    response = client.post(
        "/api/outbox-messages",
        json={
            "scene": "manual",
            "chatid": "CHAT_A",
            "target_userids": target_userids or ["USER_A"],
            "msgtype": msgtype,
            "content": {msgtype: {"content": "请确认一下报价方案。"}},
            "source_type": "manual",
            "source_id": "manual_1",
            "idempotency_key": f"manual_{msgtype}_{'_'.join(target_userids or ['USER_A'])}",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["outbox_id"]


def test_default_config_uses_mock_sender():
    sender = build_wecom_message_sender(Settings())

    assert isinstance(sender, MockWeComMessageSender)


def test_app_mode_requires_corpid_secret_and_agentid():
    with pytest.raises(WeComApiError) as exc:
        build_wecom_message_sender(
            Settings(
                wecom_sender_mode="app",
                wecom_corp_id="",
                wecom_aibot_secret="",
                wecom_agent_id="",
            )
        )

    message = str(exc.value)
    assert "WECOM_CORP_ID" in message
    assert "WECOM_AIBOT_SECRET" in message
    assert "WECOM_AGENT_ID" in message


def test_gettoken_is_cached_for_consecutive_sends():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"})

    api_client = WeComApiClient(app_settings(), http_client=make_http_client(handler))
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    assert sender.send(outbox(target_userids=["USER_A"]))["success"] is True
    assert sender.send(outbox(target_userids=["USER_B"]))["success"] is True
    assert calls.count("/cgi-bin/gettoken") == 1
    assert calls.count("/cgi-bin/message/send") == 2


def test_token_expired_refreshes_token_and_retries_request():
    sent_tokens = []
    token_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            token_count["value"] += 1
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": f"token_{token_count['value']}",
                    "expires_in": 7200,
                },
            )

        sent_tokens.append(request.url.params.get("access_token"))
        if len(sent_tokens) == 1:
            return httpx.Response(200, json={"errcode": 42001, "errmsg": "access_token expired"})
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"})

    api_client = WeComApiClient(app_settings(), http_client=make_http_client(handler))
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    result = sender.send(outbox(target_userids=["USER_A"]))

    assert result["success"] is True
    assert sent_tokens == ["token_1", "token_2"]


def test_errcode_minus_one_retries_by_configuration():
    send_attempts = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )

        send_attempts["value"] += 1
        if send_attempts["value"] == 1:
            return httpx.Response(200, json={"errcode": -1, "errmsg": "system busy"})
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"})

    api_client = WeComApiClient(
        app_settings(wecom_max_retries=1),
        http_client=make_http_client(handler),
    )
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    result = sender.send(outbox(target_userids=["USER_A"]))

    assert result["success"] is True
    assert send_attempts["value"] == 2


def test_app_sender_target_users_calls_message_send_with_expected_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"})

    api_client = WeComApiClient(app_settings(), http_client=make_http_client(handler))
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    result = sender.send(outbox(target_userids=["USER_A", "USER_B"], msgtype="markdown"))

    assert result["success"] is True
    assert seen["path"] == "/cgi-bin/message/send"
    assert seen["payload"]["touser"] == "USER_A|USER_B"
    assert seen["payload"]["agentid"] == "100001"
    assert seen["payload"]["msgtype"] == "markdown"
    assert seen["payload"]["markdown"]["content"] == "请确认一下报价方案。"


def test_app_sender_without_target_users_calls_appchat_send():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "APPCHAT_SENT"})

    api_client = WeComApiClient(app_settings(), http_client=make_http_client(handler))
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    result = sender.send(outbox(target_userids=[]))

    assert result["success"] is True
    assert seen["path"] == "/cgi-bin/appchat/send"
    assert seen["payload"]["chatid"] == "CHAT_A"
    assert seen["payload"]["msgtype"] == "markdown"


def test_app_sender_proactive_scene_calls_appchat_even_with_target_users():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "APPCHAT_SENT"})

    api_client = WeComApiClient(app_settings(), http_client=make_http_client(handler))
    sender = WeComAppMessageSender(settings=app_settings(), api_client=api_client)

    result = sender.send(
        outbox(
            scene="proactive",
            target_userids=["USER_A"],
            msgtype="markdown",
            content={"markdown": {"content": "<@USER_A> 猜你可能想了解这个答案。"}},
        )
    )

    assert result["success"] is True
    assert seen["path"] == "/cgi-bin/appchat/send"
    assert seen["payload"]["chatid"] == "CHAT_A"
    assert seen["payload"]["msgtype"] == "markdown"
    assert seen["payload"]["markdown"]["content"] == "<@USER_A> 猜你可能想了解这个答案。"


def test_webhook_sender_posts_directly_without_gettoken():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "WEBHOOK_SENT"})

    sender = WeComWebhookMessageSender(
        settings=Settings(
            wecom_sender_mode="webhook",
            wecom_group_bot_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        ),
        http_client=make_http_client(handler),
    )

    result = sender.send(
        outbox(
            target_userids=["USER_A"],
            msgtype="text",
            content={"text": {"content": "hello"}},
        )
    )

    assert result["success"] is True
    assert seen["path"] == "/cgi-bin/webhook/send"
    assert seen["payload"] == {
        "msgtype": "text",
        "text": {
            "content": "hello",
            "mentioned_list": ["USER_A"],
        },
    }


def test_wecom_nonzero_errcode_marks_outbox_failed(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage10_fail.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        return httpx.Response(200, json={"errcode": 40003, "errmsg": "invalid userid"})

    app.state.wecom_message_sender = WeComAppMessageSender(
        settings=app_settings(),
        api_client=WeComApiClient(app_settings(), http_client=make_http_client(handler)),
    )
    client = TestClient(app)
    outbox_id = create_outbox(client, target_userids=["BAD_USER"])

    response = client.post(f"/api/outbox-messages/{outbox_id}/send")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["error_code"] == "40003"
    assert data["error_message"] == "invalid userid"


def test_successful_real_sender_marks_outbox_sent_and_saves_response(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage10_success.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "token_1", "expires_in": 7200},
            )
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"})

    app.state.wecom_message_sender = WeComAppMessageSender(
        settings=app_settings(),
        api_client=WeComApiClient(app_settings(), http_client=make_http_client(handler)),
    )
    client = TestClient(app)
    outbox_id = create_outbox(client, target_userids=["USER_A"])

    response = client.post(f"/api/outbox-messages/{outbox_id}/send")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "sent"
    assert data["external_msgid"] == "MSG_SENT"
    assert data["raw_response"] == {"errcode": 0, "errmsg": "ok", "msgid": "MSG_SENT"}
