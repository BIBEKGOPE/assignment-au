"""Protect phone numbers at rest with keyed hashes and canonical masking.

HMAC is used instead of plain SHA-256 because the roughly four billion valid Indian
mobile numbers could be exhaustively hashed and matched against an unkeyed digest.
Without the secret key, an attacker cannot perform that lookup against the stored
HMACs.
"""

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path


_KEY_HEX_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")


def _decode_key(encoded_key: str, source: str) -> bytes:
    if not _KEY_HEX_PATTERN.fullmatch(encoded_key):
        raise ValueError(f"{source} must be a 64-character hexadecimal key")
    return bytes.fromhex(encoded_key)


def get_key(db_path: str | Path) -> bytes:
    """Get the 32-byte PII key from PII_KEY or a sibling hex-encoded key file.

    PII_KEY, when set, must contain 64 hexadecimal characters.
    Example: ``get_key("data.sqlite")`` reads or creates ``data.sqlite.key``.
    """
    environment_key = os.environ.get("PII_KEY")
    if environment_key is not None:
        return _decode_key(environment_key, "PII_KEY")

    key_path = Path(f"{db_path}.key")
    try:
        encoded_key = key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        try:
            file_descriptor = os.open(
                key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            encoded_key = key_path.read_text(encoding="ascii").strip()
            return _decode_key(encoded_key, str(key_path))

        with os.fdopen(file_descriptor, "w", encoding="ascii") as key_file:
            key_file.write(key.hex() + "\n")
            key_file.flush()
            os.fsync(key_file.fileno())
        return key

    return _decode_key(encoded_key, str(key_path))


def phone_hash(key: bytes, canonical_phone: str) -> str:
    """Return a deterministic HMAC-SHA256 digest of a canonical phone number.

    Example: calling ``phone_hash(key, "+919876543210")`` twice returns the same
    hexadecimal digest.
    """
    return hmac.new(
        key,
        canonical_phone.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def last4(canonical_phone: str) -> str:
    """Return the last four digits of a canonical phone number.

    Example: ``last4("+919876543210")`` returns ``"3210"``.
    """
    return canonical_phone[-4:]


def mask(phone_last4: str) -> str:
    """Format the masked phone representation using its last four digits.

    Example: ``mask("3210")`` returns ``"+91******3210"``.
    """
    return "+91******" + phone_last4
