from dataclasses import dataclass
from time import time
from typing import Any

import httpx

from app.config.settings import Settings


class WeComApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        errcode: int | str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.errcode = errcode
        self.raw_response = raw_response


@dataclass
class _TokenCache:
    access_token: str
    expires_at: float


class WeComApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        missing = []
        if not settings.wecom_corp_id:
            missing.append("WECOM_CORP_ID")
        if not settings.wecom_aibot_secret:
            missing.append("WECOM_AIBOT_SECRET")
        if missing:
            raise WeComApiError(
                f"{', '.join(missing)} are required for WECOM_SENDER_MODE=app"
            )

        self.corp_id = settings.wecom_corp_id
        self.secret = settings.wecom_aibot_secret
        self.base_url = settings.wecom_api_base_url.rstrip("/")
        self.max_retries = max(settings.wecom_max_retries, 0)
        self._token_cache: _TokenCache | None = None
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=settings.wecom_timeout_seconds)

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_cache and self._token_cache.expires_at > time():
            return self._token_cache.access_token

        response = self.http_client.get(
            f"{self.base_url}/gettoken",
            params={
                "corpid": self.corp_id,
                "corpsecret": self.secret,
            },
        )
        payload = self._json_payload(response)
        self._ensure_http_ok(response, payload)
        self._ensure_wecom_ok(payload)

        access_token = payload.get("access_token")
        if not access_token:
            raise WeComApiError("WeCom gettoken response missing access_token", raw_response=payload)

        expires_in = int(payload.get("expires_in") or 7200)
        self._token_cache = _TokenCache(
            access_token=str(access_token),
            expires_at=time() + max(expires_in - 300, 0),
        )
        return self._token_cache.access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token_refreshed = False
        busy_attempts = 0

        while True:
            token = self.get_access_token(force_refresh=token_refreshed)
            response = self.http_client.request(
                method,
                f"{self.base_url}{path}",
                params={"access_token": token},
                json=json,
            )
            payload = self._json_payload(response)
            self._ensure_http_ok(response, payload)

            errcode = payload.get("errcode")
            if errcode == 0:
                return payload

            if errcode in {42001, 40014} and not token_refreshed:
                token_refreshed = True
                self._token_cache = None
                continue

            if errcode == -1 and busy_attempts < self.max_retries:
                busy_attempts += 1
                continue

            self._ensure_wecom_ok(payload)

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def _json_payload(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeComApiError(
                f"WeCom response is not valid JSON, status_code={response.status_code}"
            ) from exc
        if not isinstance(payload, dict):
            raise WeComApiError("WeCom response must be a JSON object")
        return payload

    def _ensure_http_ok(self, response: httpx.Response, payload: dict[str, Any]) -> None:
        if 200 <= response.status_code < 300:
            return
        raise WeComApiError(
            f"WeCom HTTP error status_code={response.status_code}",
            errcode=response.status_code,
            raw_response=payload,
        )

    def _ensure_wecom_ok(self, payload: dict[str, Any]) -> None:
        if payload.get("errcode") == 0:
            return
        raise WeComApiError(
            str(payload.get("errmsg") or "WeCom API error"),
            errcode=payload.get("errcode"),
            raw_response=payload,
        )
