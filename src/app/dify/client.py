import json
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.config.settings import Settings
from app.db.models import AiWorkflow


class DifyClient(Protocol):
    def run_workflow(
        self,
        workflow: AiWorkflow,
        input_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a Dify workflow and return parsed JSON output."""


class DifyClientError(Exception):
    pass


class MockDifyClient:
    def run_workflow(
        self,
        workflow: AiWorkflow,
        input_json: dict[str, Any],
    ) -> dict[str, Any]:
        if workflow.workflow_code == "group_knowledge_reply":
            return self._run_group_knowledge_reply(input_json)
        if workflow.workflow_code == "chat_proactive_reminder":
            return self._run_chat_proactive_reminder(input_json)
        if workflow.workflow_code == "user_profile_analysis":
            return self._run_user_profile_analysis(input_json)
        if workflow.workflow_code == "conversation_segmentation":
            return self._run_conversation_segmentation(input_json)
        if workflow.workflow_code == "intent_detection":
            return self._run_intent_detection(input_json)
        return self._run_reply_generation(input_json)

    def _run_group_knowledge_reply(self, input_json: dict[str, Any]) -> dict[str, Any]:
        payload = input_json.get("payload") if isinstance(input_json, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        question = payload.get("question") or ""
        if not str(question).strip():
            return {
                "action": "out_of_scope",
                "content": "",
                "reason": "输入中没有可用于答疑的问题文本",
                "confidence": 0.0,
            }
        return {
            "action": "reply",
            "content": f"收到：{question}",
            "reason": "mock group knowledge reply",
            "confidence": 0.9,
        }

    def _run_chat_proactive_reminder(self, input_json: dict[str, Any]) -> dict[str, Any]:
        payload = input_json.get("payload") if isinstance(input_json, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        members = payload.get("members") if isinstance(payload.get("members"), list) else []
        target_userid = ""
        for member in members:
            if isinstance(member, dict) and member.get("userid"):
                target_userid = str(member["userid"])
                break
        quote_msgid = ""
        if messages and isinstance(messages[0], dict):
            quote_msgid = str(messages[0].get("msgid") or "")
        if not target_userid or not quote_msgid:
            return {
                "should_send": False,
                "target_userids": [],
                "quote_msgid": "",
                "content": "",
                "confidence": 0.0,
            }
        return {
            "should_send": True,
            "target_userids": [target_userid],
            "quote_msgid": quote_msgid,
            "content": "猜你可能想了解这条知识：这是 mock 主动答疑。",
            "confidence": 0.86,
        }

    def _run_reply_generation(self, input_json: dict[str, Any]) -> dict[str, Any]:
        content = input_json.get("user_message") or ""
        source_msgid = input_json.get("source_msgid")
        return {
            "action": "reply",
            "reply": {
                "reply_type": "markdown",
                "content": f"收到：{content}",
            },
            "metadata": {
                "reply_scene": input_json.get("reply_scene") or "mention",
                "intent_type": "mock_reply",
                "evidence_msgids": [source_msgid] if source_msgid else [],
            },
            "confidence": 0.9,
        }

    def _run_conversation_segmentation(self, input_json: dict[str, Any]) -> dict[str, Any]:
        messages = input_json.get("messages") or []
        if not messages:
            return {"segments": []}

        participants = []
        for message in messages:
            userid = message.get("userid")
            if userid and userid not in participants:
                participants.append(userid)

        return {
            "segments": [
                {
                    "title": "模拟会话",
                    "start_msgid": messages[0]["msgid"],
                    "end_msgid": messages[-1]["msgid"],
                    "summary": "本轮会话主要围绕消息窗口内容进行讨论。",
                    "keywords": ["模拟", "会话"],
                    "participants": participants,
                    "confidence": 0.88,
                }
            ]
        }

    def _run_user_profile_analysis(self, input_json: dict[str, Any]) -> dict[str, Any]:
        userid = input_json.get("userid") or ""
        messages = input_json.get("recent_messages") or []
        if not messages:
            return {
                "userid": userid,
                "profile_action": "no_change",
                "summary": "",
                "facts_to_add": [],
                "facts_to_update": [],
                "facts_to_retire": [],
                "confidence": 0.0,
            }

        return {
            "userid": userid,
            "profile_action": "create",
            "summary": "该用户近期主要关注报价、客户跟进和交付排期。",
            "facts_to_add": [
                {
                    "fact_type": "interest",
                    "label": "关注报价策略",
                    "description": "多次询问报价、折扣和交付成本。",
                    "evidence_msgids": [messages[0]["msgid"]],
                    "evidence_conversation_nos": [],
                    "confidence": 0.86,
                }
            ],
            "facts_to_update": [],
            "facts_to_retire": [],
            "confidence": 0.82,
        }

    def _run_intent_detection(self, input_json: dict[str, Any]) -> dict[str, Any]:
        messages = input_json.get("messages") or []
        known_users = input_json.get("known_users") or []
        if not messages or not known_users:
            return {"actions": []}

        return {
            "actions": [
                {
                    "action_type": "reply",
                    "intent_type": "follow_up",
                    "target_userids": [known_users[0]],
                    "evidence_msgids": [messages[0]["msgid"]],
                    "reason": "用户提到需要确认报价。",
                    "reply_instruction": "请确认一下报价方案。",
                    "priority": "medium",
                    "confidence": 0.86,
                }
            ]
        }


class DifyHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        has_any_api_key = any(
            (
                settings.dify_api_key,
                settings.dify_group_knowledge_reply_api_key,
                settings.dify_chat_proactive_reminder_api_key,
            )
        )
        if not settings.dify_base_url or not has_any_api_key:
            raise DifyClientError(
                "DIFY_BASE_URL and at least one Dify API key are required when DIFY_CLIENT_MODE=real"
            )

        self.base_url = _normalize_dify_base_url(settings.dify_base_url)
        self.api_key = settings.dify_api_key or ""
        self.workflow_api_keys = {
            "group_knowledge_reply": settings.dify_group_knowledge_reply_api_key,
            "chat_proactive_reminder": settings.dify_chat_proactive_reminder_api_key,
        }
        self.user = settings.dify_user
        self.max_retries = max(settings.dify_max_retries, 0)
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=settings.dify_timeout_seconds,
        )

    def run_workflow(
        self,
        workflow: AiWorkflow,
        input_json: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self._workflow_endpoint(workflow)
        payload = {
            "inputs": input_json,
            "response_mode": "blocking",
            "user": self.user,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key_for_workflow(workflow)}",
            "Content-Type": "application/json",
        }
        response = self._post(endpoint, json_payload=payload, headers=headers)
        return self._parse_response(response)

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def _workflow_endpoint(self, workflow: AiWorkflow) -> str:
        if workflow.dify_workflow_id:
            workflow_id = quote(workflow.dify_workflow_id, safe="")
            return f"{self.base_url}/workflows/{workflow_id}/run"
        return f"{self.base_url}/workflows/run"

    def _api_key_for_workflow(self, workflow: AiWorkflow) -> str:
        api_key = self.workflow_api_keys.get(workflow.workflow_code) or self.api_key
        if not api_key:
            raise DifyClientError(
                f"Dify API key missing for workflow_code={workflow.workflow_code}"
            )
        return api_key

    def _post(
        self,
        url: str,
        *,
        json_payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        attempts = self.max_retries + 1
        last_error: DifyClientError | None = None
        for _attempt in range(attempts):
            try:
                response = self.http_client.post(
                    url,
                    json=json_payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                last_error = DifyClientError(f"Dify request timed out: {exc}")
                continue
            except httpx.HTTPError as exc:
                last_error = DifyClientError(f"Dify request failed: {exc}")
                continue

            if response.status_code < 200 or response.status_code >= 300:
                raise DifyClientError(
                    "Dify HTTP error "
                    f"status_code={response.status_code}: {_short_text(response.text)}"
                )
            return response

        raise last_error or DifyClientError("Dify request failed")

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError("Dify response is not valid JSON") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise DifyClientError("Dify response missing data object")

        status = data.get("status")
        if status and status != "succeeded":
            error = data.get("error") or data.get("message") or "unknown error"
            raise DifyClientError(f"Dify workflow status={status}: {error}")

        return parse_dify_outputs(data.get("outputs"))


class DifyWebhookClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.max_retries = max(settings.dify_max_retries, 0)
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=settings.dify_timeout_seconds,
        )

    def run_workflow(
        self,
        workflow: AiWorkflow,
        input_json: dict[str, Any],
    ) -> dict[str, Any]:
        if not workflow.dify_webhook_url:
            raise DifyClientError(
                f"Workflow {workflow.workflow_code}/{workflow.version} missing dify_webhook_url"
            )

        response = self._post(workflow.dify_webhook_url, json_payload=input_json)
        return self._parse_response(response)

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def _post(
        self,
        url: str,
        *,
        json_payload: dict[str, Any],
    ) -> httpx.Response:
        attempts = self.max_retries + 1
        last_error: DifyClientError | None = None
        for _attempt in range(attempts):
            try:
                response = self.http_client.post(
                    url,
                    json=json_payload,
                    headers={"Content-Type": "application/json"},
                )
            except httpx.TimeoutException as exc:
                last_error = DifyClientError(f"Dify webhook timed out: {exc}")
                continue
            except httpx.HTTPError as exc:
                last_error = DifyClientError(f"Dify webhook request failed: {exc}")
                continue

            if response.status_code < 200 or response.status_code >= 300:
                raise DifyClientError(
                    "Dify webhook HTTP error "
                    f"status_code={response.status_code}: {_short_text(response.text)}"
                )
            return response

        raise last_error or DifyClientError("Dify webhook request failed")

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError("Dify webhook response is not valid JSON") from exc

        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                status = data.get("status")
                if status and status != "succeeded":
                    error = data.get("error") or data.get("message") or "unknown error"
                    raise DifyClientError(f"Dify workflow status={status}: {error}")
                return parse_dify_outputs(data.get("outputs"))

            if "outputs" in payload:
                return parse_dify_outputs(payload.get("outputs"))

            if _looks_like_business_output(payload):
                return payload

        raise DifyClientError(
            "Dify webhook did not return workflow outputs; "
            "async webhook acknowledgements need a result retrieval path"
        )


def parse_dify_outputs(outputs: Any) -> dict[str, Any]:
    if not isinstance(outputs, dict):
        raise DifyClientError("Dify outputs must be an object")

    if set(outputs.keys()) == {"result"}:
        return _parse_output_candidate(outputs["result"], field_name="outputs.result")

    if set(outputs.keys()) == {"output"}:
        return _parse_output_candidate(outputs["output"], field_name="outputs.output")

    if isinstance(outputs.get("result"), dict):
        return outputs["result"]

    if isinstance(outputs.get("output"), dict):
        return outputs["output"]

    if isinstance(outputs.get("result"), str):
        return _parse_output_candidate(outputs["result"], field_name="outputs.result")

    if isinstance(outputs.get("output"), str):
        return _parse_output_candidate(outputs["output"], field_name="outputs.output")

    return outputs


def build_dify_client(settings: Settings) -> DifyClient:
    if settings.dify_client_mode == "mock":
        return MockDifyClient()
    if settings.dify_client_mode == "webhook":
        return DifyWebhookClient(settings)
    return DifyHttpClient(settings)


def _parse_output_candidate(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DifyClientError(f"{field_name} is not valid JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DifyClientError(f"{field_name} must be an object or JSON object string")


def _normalize_dify_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _short_text(value: str) -> str:
    value = value.replace("\n", " ").strip()
    return value[:300]


def _looks_like_business_output(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    acknowledgement_keys = {"status", "message"}
    if set(payload.keys()).issubset(acknowledgement_keys):
        return False
    return any(
        key in payload
        for key in (
            "actions",
            "action",
            "segments",
            "userid",
            "profile_action",
            "facts_to_add",
        )
    )
