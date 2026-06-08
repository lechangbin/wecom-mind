from typing import Any
from urllib.parse import urlparse

from app.config.settings import Settings


def get_config_summary(settings: Settings) -> dict[str, Any]:
    return {
        "app": {
            "app_env": settings.app_env,
            "app_base_url": settings.app_base_url,
            "admin_web_host": settings.admin_web_host,
            "admin_web_port": settings.admin_web_port,
            "admin_web_url": f"http://{settings.admin_web_host}:{settings.admin_web_port}",
            "log_level": settings.log_level,
        },
        "database": {
            "kind": _database_kind(settings.database_url),
            "configured": bool(settings.database_url),
        },
        "redis": {
            "configured": bool(settings.redis_url),
        },
        "wecom": {
            "sender_mode": settings.wecom_sender_mode,
            "verify_mode": settings.wecom_mcp_verify_mode,
            "reply_bot_configured": bool(
                settings.effective_reply_aibot_id and settings.effective_reply_aibot_secret
            ),
            "intent_bot_configured": bool(
                settings.effective_intent_aibot_id and settings.effective_intent_aibot_secret
            ),
            "message_reconcile_enabled": settings.wecom_message_reconcile_enabled,
            "message_reconcile_chatids_count": len(settings.message_reconcile_chatid_list),
            "message_reconcile_auto_enqueue": settings.wecom_message_reconcile_auto_enqueue,
            "message_reconcile_auto_send": settings.wecom_message_reconcile_auto_send,
            "timeout_seconds": settings.wecom_timeout_seconds,
            "max_retries": settings.wecom_max_retries,
        },
        "dify": {
            "client_mode": settings.dify_client_mode,
            "base_url_configured": bool(settings.dify_base_url),
            "timeout_seconds": settings.dify_timeout_seconds,
            "max_retries": settings.dify_max_retries,
            "user": settings.dify_user,
            "workflow_api_keys": {
                "group_knowledge_reply": bool(settings.dify_group_knowledge_reply_api_key),
                "chat_proactive_reminder": bool(
                    settings.dify_chat_proactive_reminder_api_key
                ),
                "conversation_boundary_detection": bool(
                    settings.dify_conversation_boundary_detection_api_key
                ),
                "user_profile_update": bool(settings.dify_user_profile_update_api_key),
            },
        },
        "ai_memory": {
            "enabled": settings.ai_memory_full_test_enabled,
            "chatids_count": len(settings.ai_memory_full_test_chatid_list),
            "interval_seconds": settings.ai_memory_full_test_interval_seconds,
            "days_back": settings.ai_memory_full_test_days_back,
            "run_profiles": settings.ai_memory_full_test_run_profiles,
        },
    }


def get_worker_statuses(settings: Settings, ai_memory_state: dict[str, Any]) -> dict[str, Any]:
    items = [
        {
            "worker_key": "api",
            "title": "API 服务",
            "enabled": True,
            "status": "running",
            "observable": True,
            "details": {
                "app_base_url": settings.app_base_url,
            },
        },
        {
            "worker_key": "wecom_aibot_worker",
            "title": "企微长连接 worker",
            "enabled": settings.wecom_sender_mode == "aibot_ws",
            "status": _enabled_status(settings.wecom_sender_mode == "aibot_ws"),
            "observable": False,
            "details": {
                "sender_mode": settings.wecom_sender_mode,
                "reply_bot_configured": bool(
                    settings.effective_reply_aibot_id and settings.effective_reply_aibot_secret
                ),
                "intent_bot_configured": bool(
                    settings.effective_intent_aibot_id and settings.effective_intent_aibot_secret
                ),
            },
        },
        {
            "worker_key": "message_reconcile_worker",
            "title": "历史补漏 worker",
            "enabled": settings.wecom_message_reconcile_enabled,
            "status": _enabled_status(settings.wecom_message_reconcile_enabled),
            "observable": False,
            "details": {
                "chatids_count": len(settings.message_reconcile_chatid_list),
                "interval_seconds": settings.wecom_message_reconcile_interval_seconds,
                "lookback_seconds": settings.wecom_message_reconcile_lookback_seconds,
                "auto_send": settings.wecom_message_reconcile_auto_send,
            },
        },
        {
            "worker_key": "ai_memory_full_test_worker",
            "title": "AI memory worker",
            "enabled": settings.ai_memory_full_test_enabled,
            "status": _ai_memory_worker_status(settings, ai_memory_state),
            "observable": True,
            "details": {
                "current_status": ai_memory_state.get("status", "idle"),
                "chatids_count": len(settings.ai_memory_full_test_chatid_list),
                "interval_seconds": settings.ai_memory_full_test_interval_seconds,
                "days_back": settings.ai_memory_full_test_days_back,
                "run_profiles": settings.ai_memory_full_test_run_profiles,
                "last_error": ai_memory_state.get("last_error"),
            },
        },
    ]
    return {"items": items}


def _database_kind(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme.startswith("sqlite"):
        return "sqlite"
    return parsed.scheme or "unknown"


def _enabled_status(enabled: bool) -> str:
    return "unknown" if enabled else "disabled"


def _ai_memory_worker_status(settings: Settings, ai_memory_state: dict[str, Any]) -> str:
    if not settings.ai_memory_full_test_enabled:
        return "disabled"
    state = str(ai_memory_state.get("status") or "idle")
    if state == "running":
        return "running"
    return "unknown"
