import base64
import hashlib
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import Settings
from app.core.errors import AppError
from app.db.models import WeComMcpCallback
from app.main import create_app
from app.wecom.crypto import (
    WeComCryptoError,
    calculate_signature,
    decrypt_wecom_payload,
)
from app.wecom.verifier import build_wecom_callback_verifier


TOKEN = "stage11-token"
CORP_ID = "ww_stage11_corp"
AES_KEY_BYTES = b"0123456789abcdef0123456789abcdef"
ENCODING_AES_KEY = base64.b64encode(AES_KEY_BYTES).decode("utf-8").rstrip("=")


def make_real_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage11.db'}",
        wecom_mcp_verify_mode="real",
        wecom_mcp_token=TOKEN,
        wecom_mcp_encoding_aes_key=ENCODING_AES_KEY,
        wecom_corp_id=CORP_ID,
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def encrypt_wecom_plaintext(plaintext: str, receiveid: str = CORP_ID) -> str:
    message = plaintext.encode("utf-8")
    raw = b"r" * 16 + len(message).to_bytes(4, "big") + message + receiveid.encode("utf-8")
    padded = _pkcs7_pad(raw)
    encryptor = Cipher(
        algorithms.AES(AES_KEY_BYTES),
        modes.CBC(AES_KEY_BYTES[:16]),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def encrypted_callback_xml(encrypt_text: str) -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{CORP_ID}]]></ToUserName>"
        f"<Encrypt><![CDATA[{encrypt_text}]]></Encrypt>"
        "</xml>"
    )


def callback_plain_xml(**overrides: Any) -> str:
    payload = {
        "ToUserName": CORP_ID,
        "FromUserName": "USER_A",
        "CreateTime": "1777827600",
        "MsgType": "event",
        "Event": "message_changed",
        "ChatId": "CHAT_A",
        "Cursor": "CURSOR_1",
    }
    payload.update(overrides)
    return "<xml>" + "".join(
        f"<{key}><![CDATA[{value}]]></{key}>" for key, value in payload.items()
    ) + "</xml>"


def signed_params(encrypt_text: str, *, timestamp: str = "1777827600", nonce: str = "n1"):
    return {
        "msg_signature": calculate_signature(TOKEN, timestamp, nonce, encrypt_text),
        "timestamp": timestamp,
        "nonce": nonce,
    }


def callback_count(app) -> int:
    with app.state.SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(WeComMcpCallback))


def _pkcs7_pad(data: bytes) -> bytes:
    block_size = 32
    padding_size = block_size - (len(data) % block_size)
    return data + bytes([padding_size]) * padding_size


def test_signature_calculation_matches_wecom_sort_and_sha1_rule():
    assert calculate_signature("token", "1", "abc", "encrypted") == hashlib.sha1(
        b"1abcencryptedtoken"
    ).hexdigest()


def test_aes_decrypt_success_and_receiveid_check():
    encrypt_text = encrypt_wecom_plaintext("<xml><Content><![CDATA[hello]]></Content></xml>")

    decrypted = decrypt_wecom_payload(encrypt_text, ENCODING_AES_KEY, CORP_ID)

    assert "<Content><![CDATA[hello]]></Content>" in decrypted


def test_aes_decrypt_rejects_mismatched_receiveid():
    encrypt_text = encrypt_wecom_plaintext("hello", receiveid="other_corp")

    with pytest.raises(WeComCryptoError, match="receiveid"):
        decrypt_wecom_payload(encrypt_text, ENCODING_AES_KEY, CORP_ID)


def test_get_url_verification_success_returns_plain_text(tmp_path):
    client, _app = make_real_client(tmp_path)
    echostr = encrypt_wecom_plaintext("url verification ok")

    response = client.get(
        "/api/wecom/callbacks/mcp",
        params={**signed_params(echostr), "echostr": echostr},
    )

    assert response.status_code == 200
    assert response.text == "url verification ok"
    assert response.headers["content-type"].startswith("text/plain")


def test_get_url_verification_bad_signature_returns_403(tmp_path):
    client, _app = make_real_client(tmp_path)
    echostr = encrypt_wecom_plaintext("url verification ok")

    response = client.get(
        "/api/wecom/callbacks/mcp",
        params={
            "msg_signature": "bad-signature",
            "timestamp": "1777827600",
            "nonce": "n1",
            "echostr": echostr,
        },
    )

    assert response.status_code == 403


def test_post_encrypted_xml_saves_decrypted_callback_record(tmp_path):
    client, app = make_real_client(tmp_path)
    encrypted = encrypt_wecom_plaintext(callback_plain_xml())
    response = client.post(
        "/api/wecom/callbacks/mcp",
        params=signed_params(encrypted),
        content=encrypted_callback_xml(encrypted),
        headers={"content-type": "application/xml"},
    )

    assert response.status_code == 200
    assert response.text == "success"

    with app.state.SessionLocal() as session:
        callback = session.scalar(select(WeComMcpCallback))

        assert callback is not None
        assert callback.signature_valid is True
        assert callback.event_type == "message_changed"
        assert callback.chatid == "CHAT_A"
        assert callback.raw_body["Event"] == "message_changed"
        assert callback.raw_body["ChatId"] == "CHAT_A"


def test_post_bad_signature_does_not_write_callback(tmp_path):
    client, app = make_real_client(tmp_path)
    encrypted = encrypt_wecom_plaintext(callback_plain_xml())

    response = client.post(
        "/api/wecom/callbacks/mcp",
        params={
            "msg_signature": "bad-signature",
            "timestamp": "1777827600",
            "nonce": "n1",
        },
        content=encrypted_callback_xml(encrypted),
        headers={"content-type": "application/xml"},
    )

    assert response.status_code == 403
    assert callback_count(app) == 0


def test_mock_mode_keeps_json_callback_behavior(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage11_mock.db'}",
    )
    client = TestClient(create_app(settings=settings))

    response = client.post(
        "/api/wecom/callbacks/mcp",
        params={"msg_signature": "mock", "timestamp": "1777827600", "nonce": "n1"},
        json={"event_type": "message_changed", "chatid": "CHAT_A", "cursor": "CURSOR_1"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["accepted"] is True


def test_real_mode_missing_config_raises_clear_error():
    with pytest.raises(AppError) as exc:
        build_wecom_callback_verifier(
            Settings(
                wecom_mcp_verify_mode="real",
                wecom_mcp_token="",
                wecom_mcp_encoding_aes_key="",
                wecom_corp_id="",
            )
        )

    message = str(exc.value)
    assert "WECOM_MCP_TOKEN" in message
    assert "WECOM_MCP_ENCODING_AES_KEY" in message
    assert "WECOM_CORP_ID" in message
