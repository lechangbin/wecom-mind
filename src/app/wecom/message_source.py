import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config.settings import Settings
from app.wecom.client import WeComApiError

BEIJING_TZ = timezone(timedelta(hours=8))
USER_AGENT = "wecom-dify-bot/0.1 python"
logger = logging.getLogger(__name__)


class WeComMcpMessageSource:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=settings.wecom_timeout_seconds)
        self._mcp_url: str | None = None

    def fetch_messages(
        self,
        *,
        chatid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        messages = []
        cursor = None
        pages = max(1, self.settings.wecom_message_reconcile_pages)
        for _page in range(pages):
            arguments = {
                "chat_type": 2,
                "chatid": chatid,
                "begin_time": _format_beijing_time(start_time),
                "end_time": _format_beijing_time(end_time),
            }
            if cursor:
                arguments["cursor"] = cursor

            result = self._call_tool("get_message", arguments)
            messages.extend(
                _normalize_message(item, chatid=chatid, chat_type=2)
                for item in result.get("messages") or []
                if isinstance(item, dict)
            )
            cursor = result.get("next_cursor")
            if not cursor:
                break
        return messages

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def _call_tool(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        mcp_url = self._mcp_url or self._fetch_mcp_url()
        response = self.http_client.post(
            mcp_url,
            headers=_json_headers(),
            json={
                "jsonrpc": "2.0",
                "id": f"mcp_rpc_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
                "method": "tools/call",
                "params": {
                    "name": method,
                    "arguments": arguments,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise WeComApiError(f"MCP JSON-RPC error: {payload['error']}")
        if (payload.get("result") or {}).get("isError") is True:
            raise WeComApiError(f"MCP tool returned error: {payload}")
        return _extract_tool_json(payload)

    def _fetch_mcp_url(self) -> str:
        bot_id = self.settings.effective_intent_aibot_id
        bot_secret = self.settings.effective_intent_aibot_secret
        if not bot_id:
            raise WeComApiError("WECOM_INTENT_AIBOT_ID is required for message reconcile")
        if not bot_secret:
            raise WeComApiError("WECOM_INTENT_AIBOT_SECRET is required for message reconcile")

        now_seconds = int(time.time())
        nonce = f"mcp_{now_seconds}_{secrets.token_hex(4)}"
        response = self.http_client.post(
            self.settings.wecom_mcp_config_endpoint,
            headers=_json_headers(),
            json={
                "bot_id": bot_id,
                "time": now_seconds,
                "nonce": nonce,
                "signature": _sign_mcp_config(bot_secret, bot_id, now_seconds, nonce),
                "bind_source": 1,
                "cli_version": USER_AGENT,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode") not in {None, 0}:
            raise WeComApiError(payload.get("errmsg") or "MCP config request failed")
        config_list = payload.get("list")
        if not isinstance(config_list, list):
            raise WeComApiError("MCP config response does not contain list")

        for item in config_list:
            if isinstance(item, dict) and item.get("biz_type") == "msg" and item.get("url"):
                self._mcp_url = str(item["url"])
                return self._mcp_url
        raise WeComApiError("Current bot did not return msg MCP config")


def _sign_mcp_config(secret: str, bot_id: str, now_seconds: int, nonce: str) -> str:
    raw = f"{secret}{bot_id}{now_seconds}{nonce}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_headers() -> dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }


def _extract_tool_json(rpc_payload: dict[str, Any]) -> dict[str, Any]:
    content = (rpc_payload.get("result") or {}).get("content")
    if not isinstance(content, list) or not content:
        raise WeComApiError("Unexpected MCP tool response shape")
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        raise WeComApiError("Unexpected MCP tool content shape")
    parsed = json.loads(str(first.get("text") or "{}"))
    if parsed.get("errcode") not in {None, 0}:
        raise WeComApiError(parsed.get("errmsg") or "WeCom MCP business error")
    return parsed


def _normalize_message(
    message: dict[str, Any],
    *,
    chatid: str,
    chat_type: int,
) -> dict[str, Any]:
    msgtype = str(message.get("msgtype") or "text")
    from_user = message.get("from") if isinstance(message.get("from"), dict) else {}
    userid = message.get("userid") or message.get("from_userid") or from_user.get("userid")
    normalized = {
        "msgid": str(message.get("msgid") or message.get("external_msgid") or ""),
        "chatid": str(message.get("chatid") or chatid),
        "chattype": "group" if chat_type == 2 else "single",
        "from": {"userid": str(userid or "")},
        "msgtype": msgtype,
        "mentioned_users": _extract_mentioned_users(message),
        "create_time": _normalize_mcp_message_time(
            message.get("send_time") or message.get("create_time")
        ),
    }
    if from_user:
        normalized["from"].update(from_user)
    if not normalized["from"].get("userid"):
        logger.error(
            "WeCom MCP message missing userid chatid=%s msgid=%s msgtype=%s",
            normalized["chatid"],
            normalized["msgid"],
            msgtype,
        )
    if isinstance(message.get("text"), dict):
        normalized["text"] = message["text"]
    elif message.get("content") is not None:
        normalized["text"] = {"content": str(message["content"])}
    if isinstance(message.get("mixed"), dict):
        normalized["mixed"] = message["mixed"]
    return normalized


def _normalize_mcp_message_time(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc).isoformat()


def _extract_mentioned_users(message: dict[str, Any]) -> list[Any]:
    for key in ("mentioned_users", "mentions"):
        value = message.get(key)
        if isinstance(value, list):
            return value
    text = message.get("text")
    if isinstance(text, dict):
        for key in ("mentioned_users", "mentions"):
            value = text.get(key)
            if isinstance(value, list):
                return value
    return []


def _format_beijing_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
