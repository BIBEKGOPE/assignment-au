import hashlib
import hmac

import pytest

from solution.pii import get_key, last4, mask, phone_hash


def test_get_key_creates_and_reuses_hex_encoded_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PII_KEY", raising=False)
    db_path = tmp_path / "nested" / "data.sqlite"
    key_path = tmp_path / "nested" / "data.sqlite.key"

    key = get_key(db_path)

    assert len(key) == 32
    assert key_path.read_text(encoding="ascii").strip() == key.hex()
    assert get_key(db_path) == key


def test_get_key_uses_hex_environment_key_without_creating_file(tmp_path, monkeypatch):
    encoded_key = "ab" * 32
    monkeypatch.setenv("PII_KEY", encoded_key)
    db_path = tmp_path / "data.sqlite"

    assert get_key(db_path) == bytes.fromhex(encoded_key)
    assert not (tmp_path / "data.sqlite.key").exists()


def test_get_key_rejects_invalid_environment_key(monkeypatch):
    monkeypatch.setenv("PII_KEY", "not-a-hex-key")

    with pytest.raises(ValueError, match="PII_KEY"):
        get_key("data.sqlite")


def test_get_key_rejects_malformed_existing_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PII_KEY", raising=False)
    db_path = tmp_path / "data.sqlite"
    (tmp_path / "data.sqlite.key").write_text("bad-key", encoding="ascii")

    with pytest.raises(ValueError, match="64-character hexadecimal"):
        get_key(db_path)


def test_phone_hash_is_hmac_sha256_and_deterministic():
    key = bytes(range(32))
    phone = "+919876543210"

    expected = hmac.new(key, phone.encode("ascii"), hashlib.sha256).hexdigest()

    assert phone_hash(key, phone) == expected
    assert phone_hash(key, phone) == phone_hash(key, phone)


def test_last4_returns_phone_suffix():
    assert last4("+919876543210") == "3210"


def test_mask_formats_phone_with_only_last_four_digits_visible():
    assert mask(last4("+919876543210")) == "+91******3210"
