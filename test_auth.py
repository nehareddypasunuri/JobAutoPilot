"""Tests for auth.py — password hashing and API key encryption."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("HIREPILOT_SECRET_KEY", "test-secret-key-for-unit-tests-only-32x")

from auth import hash_password, verify_password, encrypt_api_key, decrypt_api_key


def test_hash_password_produces_stored_format():
    h = hash_password("MyPassword123")
    parts = h.split(":")
    assert len(parts) == 3, "Hash should be salt:iterations:hash"
    salt, iterations, hexhash = parts
    assert len(salt) == 64, "Salt should be 32 bytes = 64 hex chars"
    assert int(iterations) >= 100_000
    assert len(hexhash) == 64, "SHA-256 = 32 bytes = 64 hex chars"


def test_verify_password_correct():
    h = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", h) is True


def test_verify_password_wrong():
    h = hash_password("correct-horse-battery")
    assert verify_password("wrong-password", h) is False


def test_verify_password_empty():
    h = hash_password("somepassword")
    assert verify_password("", h) is False


def test_verify_password_invalid_stored():
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "") is False


def test_hash_is_nondeterministic():
    """Different salts → different hashes for same password."""
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2, "Two hashes of same password must differ (different salts)"


def test_encrypt_decrypt_api_key():
    key = "sk-ant-api03-test-key-12345"
    encrypted = encrypt_api_key(key)
    assert encrypted != key, "Encrypted key should not equal plaintext"
    assert len(encrypted) > len(key)
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == key


def test_encrypt_empty_key():
    assert encrypt_api_key("") == ""
    assert decrypt_api_key("") == ""


def test_decrypt_invalid_ciphertext():
    result = decrypt_api_key("not-valid-ciphertext")
    assert result == "", "Invalid ciphertext should return empty string, not raise"


def test_decrypt_tampered_ciphertext():
    enc = encrypt_api_key("sk-ant-test")
    tampered = enc[:-5] + "XXXXX"
    assert decrypt_api_key(tampered) == ""


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
