import asyncio
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.config.settings import Settings
from app.db.models import OutboxMessage
from app.wecom.client import WeComApiClient, WeComApiError


class WeComMessageSender(Protocol):
    def send(self, outbox: OutboxMessage) -> dict[str, Any]:
        """Send one outbox message through a WeCom channel."""


class MockWeComMessageSender:
    def send(self, outbox: OutboxMessage) -> dict[str, Any]:
        return {
            "external_msgid": f"mock_msg_{uuid4().hex[:12]}",
            "raw_response": {
                "errcode": 0,
                "errmsg": "ok",
            },
        }


class WeComAppMessageSender:
    def __init__(
        self,
        *,
        settings: Settings,
        api_client: WeComApiClient | None = None,
    ) -> None:
        missing = []
        if not settings.wecom_corp_id:
            missing.append("WECOM_CORP_ID")
        if not settings.wecom_aibot_secret:
            missing.append("WECOM_AIBOT_SECRET")
        if not settings.wecom_agent_id:
            missing.append("WECOM_AGENT_ID")
        if missing:
            raise WeComApiError(
                f"{', '.join(missing)} are required for WECOM_SENDER_MODE=app"
            )

        self.agent_id = str(settings.wecom_agent_id)
        self.api_client = api_client or WeComApiClient(settings)

    def send(self, outbox: OutboxMessage) -> dict[str, Any]:
        payload = _app_payload(outbox, agent_id=self.agent_id)
        path = _app_send_path(outbox)
        try:
            raw_response = self.api_client.request("POST", path, json=payload)
        except WeComApiError as exc:
            return _failure_result(exc)
        return _success_result(raw_response)


class WeComWebhookMessageSender:
    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not settings.wecom_group_bot_webhook_url:
            raise WeComApiError(
                "WECOM_GROUP_BOT_WEBHOOK_URL is required for WECOM_SENDER_MODE=webhook"
            )

        self.webhook_url = settings.wecom_group_bot_webhook_url
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=settings.wecom_timeout_seconds)

    def send(self, outbox: OutboxMessage) -> dict[str, Any]:
        payload = _webhook_payload(outbox)
        try:
            response = self.http_client.post(self.webhook_url, json=payload)
            raw_response = response.json()
        except Exception as exc:
            return {
                "success": False,
                "error_code": "WECOM_WEBHOOK_ERROR",
                "error_message": str(exc),
                "raw_response": {"errmsg": str(exc)},
            }

        if isinstance(raw_response, dict) and raw_response.get("errcode") == 0:
            return _success_result(raw_response)
        return {
            "success": False,
            "error_code": _errcode(raw_response),
            "error_message": _errmsg(raw_response),
            "raw_response": raw_response,
        }

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()


class WeComAiBotWsMessageSender:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        ws_client: Any | None = None,
    ) -> None:
        self._owns_client = ws_client is None
        if ws_client is not None:
            self.ws_client = ws_client
            return

        if settings is None:
            raise WeComApiError("settings are required for WECOM_SENDER_MODE=aibot_ws")
        missing = []
        bot_id = settings.effective_reply_aibot_id
        bot_secret = settings.effective_reply_aibot_secret
        if not bot_id:
            missing.append("WECOM_REPLY_AIBOT_ID")
        if not bot_secret:
            missing.append("WECOM_REPLY_AIBOT_SECRET")
        if missing:
            raise WeComApiError(
                f"{', '.join(missing)} are required for WECOM_SENDER_MODE=aibot_ws"
            )

        try:
            from wecom_aibot_sdk import WSClient
        except ImportError as exc:
            raise WeComApiError(
                "wecom-aibot-sdk is required for WECOM_SENDER_MODE=aibot_ws"
            ) from exc

        self.ws_client = WSClient(
            bot_id,
            bot_secret,
            ws_url=settings.effective_reply_aibot_ws_url or "",
            request_timeout=settings.wecom_timeout_seconds * 1000,
            max_reconnect_attempts=settings.wecom_max_retries,
        )

    async def send_async(self, outbox: OutboxMessage) -> dict[str, Any]:
        payload = _aibot_ws_payload(outbox)
        try:
            if self._owns_client:
                await self.ws_client.connect()
            raw_response = await self.ws_client.send_message(outbox.chatid, payload)
        except Exception as exc:
            return {
                "success": False,
                "error_code": "WECOM_AIBOT_WS_ERROR",
                "error_message": str(exc),
                "raw_response": {"errmsg": str(exc)},
            }
        finally:
            if self._owns_client:
                try:
                    await self.ws_client.disconnect()
                except Exception:
                    pass

        if isinstance(raw_response, dict) and raw_response.get("errcode") not in {None, 0}:
            return {
                "success": False,
                "error_code": _errcode(raw_response),
                "error_message": _errmsg(raw_response),
                "raw_response": raw_response,
            }
        return _success_result(_normalize_ws_response(raw_response))

    async def begin_callback_stream_async(
        self,
        frame: dict[str, Any],
        content: str = "正在查询相关资料，请稍等。",
    ) -> dict[str, Any]:
        stream_id = f"stream_{uuid4().hex[:12]}"
        try:
            if self._owns_client:
                await self.ws_client.connect()
            raw_response = await self.ws_client.reply_stream(
                frame,
                stream_id,
                content,
                False,
            )
        except Exception as exc:
            return {
                "success": False,
                "error_code": "WECOM_AIBOT_WS_ERROR",
                "error_message": str(exc),
                "raw_response": {"errmsg": str(exc)},
            }

        result = _success_result(_normalize_ws_response(raw_response))
        result["stream_id"] = stream_id
        return result

    async def send_callback_final_async(
        self,
        outbox: OutboxMessage,
        *,
        frame: dict[str, Any],
        stream_id: str | None,
    ) -> dict[str, Any]:
        payload = _aibot_ws_payload(outbox)
        try:
            if self._owns_client:
                await self.ws_client.connect()

            if payload.get("msgtype") == "markdown":
                final_stream_id = stream_id or f"stream_{uuid4().hex[:12]}"
                raw_response = await self.ws_client.reply_stream(
                    frame,
                    final_stream_id,
                    payload["markdown"]["content"],
                    True,
                )
            elif payload.get("msgtype") == "template_card":
                raw_response = await self.ws_client.reply_template_card(
                    frame,
                    payload["template_card"],
                )
            else:
                raw_response = await self.ws_client.reply(frame, payload)
        except Exception as exc:
            return {
                "success": False,
                "error_code": "WECOM_AIBOT_WS_ERROR",
                "error_message": str(exc),
                "raw_response": {"errmsg": str(exc)},
            }
        finally:
            if self._owns_client:
                try:
                    await self.ws_client.disconnect()
                except Exception:
                    pass

        return _success_result(_normalize_ws_response(raw_response))

    def send(self, outbox: OutboxMessage) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.send_async(outbox))
        raise WeComApiError(
            "Cannot synchronously send through aibot_ws inside a running event loop; "
            "use send_outbox_message_async"
        )


def build_wecom_message_sender(settings: Settings) -> WeComMessageSender:
    if settings.wecom_sender_mode == "mock":
        return MockWeComMessageSender()
    if settings.wecom_sender_mode == "app":
        return WeComAppMessageSender(settings=settings)
    if settings.wecom_sender_mode == "webhook":
        return WeComWebhookMessageSender(settings=settings)
    return WeComAiBotWsMessageSender(settings=settings)


def _app_payload(outbox: OutboxMessage, *, agent_id: str) -> dict[str, Any]:
    content = _message_content(outbox)
    if outbox.target_userids and not _should_send_to_appchat(outbox):
        payload = {
            "touser": "|".join(str(userid) for userid in outbox.target_userids),
            "agentid": agent_id,
            "msgtype": outbox.msgtype,
            outbox.msgtype: {"content": content},
        }
    else:
        payload = {
            "chatid": outbox.chatid,
            "msgtype": outbox.msgtype,
            outbox.msgtype: {"content": content},
        }
    return payload


def _app_send_path(outbox: OutboxMessage) -> str:
    return "/appchat/send" if _should_send_to_appchat(outbox) else "/message/send"


def _should_send_to_appchat(outbox: OutboxMessage) -> bool:
    return outbox.scene in {"reply", "proactive"} or not outbox.target_userids


def _webhook_payload(outbox: OutboxMessage) -> dict[str, Any]:
    content = _message_content(outbox)
    if outbox.msgtype == "text":
        payload = {
            "msgtype": "text",
            "text": {"content": content},
        }
        if outbox.target_userids:
            payload["text"]["mentioned_list"] = [
                str(userid) for userid in outbox.target_userids
            ]
        return payload

    return {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }


def _aibot_ws_payload(outbox: OutboxMessage) -> dict[str, Any]:
    content = _message_content(outbox)
    if outbox.msgtype == "template_card":
        body = outbox.content.get("template_card") if isinstance(outbox.content, dict) else None
        if not isinstance(body, dict):
            raise WeComApiError("template_card outbox content must be an object")
        return {"msgtype": "template_card", "template_card": body}
    if outbox.msgtype in {"text", "markdown"}:
        return {"msgtype": "markdown", "markdown": {"content": content}}
    raise WeComApiError(f"Unsupported aibot_ws outbox msgtype: {outbox.msgtype}")


def _message_content(outbox: OutboxMessage) -> str:
    if outbox.msgtype not in {"text", "markdown"}:
        raise WeComApiError(f"Unsupported outbox msgtype: {outbox.msgtype}")
    body = outbox.content.get(outbox.msgtype) if isinstance(outbox.content, dict) else None
    if isinstance(body, dict):
        return str(body.get("content") or "")
    return str(body or "")


def _success_result(raw_response: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "external_msgid": raw_response.get("msgid") or raw_response.get("external_msgid"),
        "raw_response": raw_response,
    }


def _normalize_ws_response(raw_response: Any) -> dict[str, Any]:
    if not isinstance(raw_response, dict):
        return {"errcode": 0, "errmsg": "ok", "raw": raw_response}
    body = raw_response.get("body")
    if isinstance(body, dict):
        return {
            **raw_response,
            "msgid": body.get("msgid") or body.get("external_msgid") or raw_response.get("msgid"),
        }
    return raw_response


def _failure_result(exc: WeComApiError) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": str(exc.errcode) if exc.errcode is not None else "WECOM_API_ERROR",
        "error_message": str(exc),
        "raw_response": exc.raw_response or {"errmsg": str(exc)},
    }


def _errcode(raw_response: Any) -> str:
    if isinstance(raw_response, dict) and raw_response.get("errcode") is not None:
        return str(raw_response["errcode"])
    return "WECOM_SEND_FAILED"


def _errmsg(raw_response: Any) -> str:
    if isinstance(raw_response, dict) and raw_response.get("errmsg") is not None:
        return str(raw_response["errmsg"])
    return "WeCom send failed"
