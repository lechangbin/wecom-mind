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
        if workflow.workflow_code == "user_profile_analysis":
            return self._run_user_profile_analysis(input_json)
        if workflow.workflow_code == "conversation_segmentation":
            return self._run_conversation_segmentation(input_json)
        if workflow.workflow_code == "intent_detection":
            return self._run_intent_detection(input_json)
        return self._run_reply_generation(input_json)

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
        if not settings.dify_base_url or not settings.dify_api_key:
            raise DifyClientError(
                "DIFY_BASE_URL and DIFY_API_KEY are required when DIFY_CLIENT_MODE=real"
            )

        self.base_url = _normalize_dify_base_url(settings.dify_base_url)
        self.api_key = settings.dify_api_key
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
            "Authorization": f"Bearer {self.api_key}",
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
