from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode
from app.wecom.crypto import (
    WeComCryptoError,
    decrypt_wecom_payload,
    extract_encrypt_from_xml,
    parse_wecom_xml,
    verify_signature,
)


@dataclass(frozen=True)
class CallbackVerificationResult:
    valid: bool
    error_message: str | None = None
    decrypted_xml: str | None = None
    decrypted_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class UrlVerificationResult:
    valid: bool
    plaintext: str | None = None
    error_message: str | None = None


class WeComCallbackVerifier(Protocol):
    def verify_url(self, query: Mapping[str, str]) -> UrlVerificationResult:
        """Verify WeCom URL verification query and return plaintext echostr."""

    def verify(
        self,
        query: Mapping[str, str],
        body: bytes,
    ) -> CallbackVerificationResult:
        """Verify callback source.

        The real MCP signature shape is still an open decision for this project.
        Keeping it behind this protocol lets the production verifier replace the
        MVP mock without changing route or persistence code.
        """


class MockWeComCallbackVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify_url(self, query: Mapping[str, str]) -> UrlVerificationResult:
        if query.get("mock_signature_valid", "true").lower() == "false":
            return UrlVerificationResult(False, error_message="Mock callback signature rejected")
        return UrlVerificationResult(True, plaintext=query.get("echostr", "success"))

    def verify(
        self,
        query: Mapping[str, str],
        body: bytes,
    ) -> CallbackVerificationResult:
        if query.get("mock_signature_valid", "true").lower() == "false":
            return CallbackVerificationResult(False, "Mock callback signature rejected")
        return CallbackVerificationResult(True)


class RealWeComCallbackVerifier:
    def __init__(self, settings: Settings) -> None:
        missing = []
        if not settings.wecom_mcp_token:
            missing.append("WECOM_MCP_TOKEN")
        if not settings.wecom_mcp_encoding_aes_key:
            missing.append("WECOM_MCP_ENCODING_AES_KEY")
        if not settings.wecom_corp_id:
            missing.append("WECOM_CORP_ID")
        if missing:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT,
                f"{', '.join(missing)} are required for WECOM_MCP_VERIFY_MODE=real",
            )

        self.token = settings.wecom_mcp_token
        self.encoding_aes_key = settings.wecom_mcp_encoding_aes_key
        self.receive_id = settings.wecom_corp_id

    def verify_url(self, query: Mapping[str, str]) -> UrlVerificationResult:
        missing = _missing_query_params(query, ["msg_signature", "timestamp", "nonce", "echostr"])
        if missing:
            return UrlVerificationResult(
                False,
                error_message=f"Missing WeCom URL verification params: {', '.join(missing)}",
            )

        echostr = query["echostr"]
        if not verify_signature(
            self.token,
            query["timestamp"],
            query["nonce"],
            echostr,
            query["msg_signature"],
        ):
            return UrlVerificationResult(False, error_message="Invalid WeCom callback signature")

        try:
            plaintext = decrypt_wecom_payload(
                echostr,
                self.encoding_aes_key,
                self.receive_id,
            )
        except WeComCryptoError as exc:
            return UrlVerificationResult(False, error_message=str(exc))
        return UrlVerificationResult(True, plaintext=plaintext)

    def verify(
        self,
        query: Mapping[str, str],
        body: bytes,
    ) -> CallbackVerificationResult:
        missing = _missing_query_params(query, ["msg_signature", "timestamp", "nonce"])
        if missing:
            return CallbackVerificationResult(
                False,
                f"Missing WeCom callback params: {', '.join(missing)}",
            )

        body_text = body.decode("utf-8", "replace")
        try:
            encrypt_text = extract_encrypt_from_xml(body_text)
        except WeComCryptoError as exc:
            return CallbackVerificationResult(False, str(exc))

        if not verify_signature(
            self.token,
            query["timestamp"],
            query["nonce"],
            encrypt_text,
            query["msg_signature"],
        ):
            return CallbackVerificationResult(False, "Invalid WeCom callback signature")

        try:
            decrypted_xml = decrypt_wecom_payload(
                encrypt_text,
                self.encoding_aes_key,
                self.receive_id,
            )
            decrypted_payload = parse_wecom_xml(decrypted_xml)
        except WeComCryptoError as exc:
            return CallbackVerificationResult(False, str(exc))

        return CallbackVerificationResult(
            True,
            decrypted_xml=decrypted_xml,
            decrypted_payload=decrypted_payload,
        )


def build_wecom_callback_verifier(settings: Settings) -> WeComCallbackVerifier:
    if settings.wecom_mcp_verify_mode == "mock":
        return MockWeComCallbackVerifier(settings)
    return RealWeComCallbackVerifier(settings)


def _missing_query_params(query: Mapping[str, str], names: list[str]) -> list[str]:
    return [name for name in names if not query.get(name)]
