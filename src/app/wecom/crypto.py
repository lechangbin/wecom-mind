import base64
import hashlib
import hmac
import struct
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCryptoError(ValueError):
    """Raised when WeCom callback signature, XML, or AES payload is invalid."""


def calculate_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypted_value: str,
) -> str:
    values = sorted([token, timestamp, nonce, encrypted_value])
    return hashlib.sha1("".join(values).encode("utf-8")).hexdigest()


def verify_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypted_value: str,
    expected_signature: str,
) -> bool:
    actual_signature = calculate_signature(token, timestamp, nonce, encrypted_value)
    return hmac.compare_digest(actual_signature, expected_signature or "")


def decrypt_wecom_payload(
    encrypt_text: str,
    encoding_aes_key: str,
    expected_receive_id: str,
) -> str:
    aes_key = _decode_aes_key(encoding_aes_key)
    try:
        encrypted = base64.b64decode(encrypt_text, validate=True)
    except Exception as exc:
        raise WeComCryptoError("Encrypted callback payload is not valid base64") from exc

    try:
        decryptor = Cipher(
            algorithms.AES(aes_key),
            modes.CBC(aes_key[:16]),
        ).decryptor()
        padded_plaintext = decryptor.update(encrypted) + decryptor.finalize()
    except Exception as exc:
        raise WeComCryptoError("Failed to decrypt WeCom callback payload") from exc

    plaintext = _pkcs7_unpad(padded_plaintext)
    if len(plaintext) < 20:
        raise WeComCryptoError("Decrypted WeCom callback payload is too short")

    msg_len = struct.unpack(">I", plaintext[16:20])[0]
    msg_start = 20
    msg_end = msg_start + msg_len
    if msg_end > len(plaintext):
        raise WeComCryptoError("Decrypted WeCom callback message length is invalid")

    receiveid = plaintext[msg_end:].decode("utf-8", "replace")
    if receiveid != expected_receive_id:
        raise WeComCryptoError("Decrypted WeCom callback receiveid does not match")

    return plaintext[msg_start:msg_end].decode("utf-8")


def extract_encrypt_from_xml(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeComCryptoError("WeCom callback XML is invalid") from exc

    for element in root.iter():
        if _local_name(element.tag) == "Encrypt":
            encrypt_text = (element.text or "").strip()
            if encrypt_text:
                return encrypt_text
            break

    raise WeComCryptoError("WeCom callback XML missing Encrypt")


def parse_wecom_xml(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeComCryptoError("Decrypted WeCom callback XML is invalid") from exc

    payload: dict[str, str] = {}
    for child in list(root):
        payload[_local_name(child.tag)] = child.text or ""
    return payload


def _decode_aes_key(encoding_aes_key: str) -> bytes:
    key_text = encoding_aes_key.strip()
    padded_key = key_text + ("=" * ((4 - len(key_text) % 4) % 4))
    try:
        aes_key = base64.b64decode(padded_key, validate=True)
    except Exception as exc:
        raise WeComCryptoError("WECOM_MCP_ENCODING_AES_KEY is not valid base64") from exc
    if len(aes_key) != 32:
        raise WeComCryptoError("WECOM_MCP_ENCODING_AES_KEY must decode to 32 bytes")
    return aes_key


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise WeComCryptoError("Decrypted WeCom callback payload is empty")

    padding_size = data[-1]
    if padding_size < 1 or padding_size > 32:
        raise WeComCryptoError("WeCom callback payload padding is invalid")
    if data[-padding_size:] != bytes([padding_size]) * padding_size:
        raise WeComCryptoError("WeCom callback payload padding is invalid")
    return data[:-padding_size]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
