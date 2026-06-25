"""
auth.py — Authentication layer for HirePilot.

Provides:
  - PBKDF2-based password hashing (stdlib only — no bcrypt dependency)
  - Fernet encryption for API keys stored in the database
  - Streamlit session management
  - require_auth() gate used at the top of every page

Security decisions
------------------
- Passwords: PBKDF2-HMAC-SHA256, 260,000 iterations (OWASP 2023 minimum), 32-byte salt
- API keys: Fernet (AES-128-CBC + HMAC-SHA256) keyed from HIREPILOT_SECRET_KEY env var
- Sessions: _st().session_state['user_id'] — Streamlit handles session isolation per browser
- Timing: constant-time comparison via hmac.compare_digest
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger("hirepilot.auth")


def _st():
    """Lazy streamlit import — allows auth.py to be imported without streamlit for tests."""
    import streamlit as _streamlit
    return _streamlit

# ── Encryption key (must be set in production env) ─────────────────────
def _get_fernet():
    """Return a Fernet instance keyed from the environment."""
    from cryptography.fernet import Fernet
    raw = os.environ.get("HIREPILOT_SECRET_KEY", "")
    if not raw:
        # Development fallback: generate and log a warning
        # In production this MUST be set
        fallback = Fernet.generate_key().decode()
        logger.warning(
            "HIREPILOT_SECRET_KEY not set — using ephemeral key. "
            "Encrypted API keys will be unreadable after restart. "
            "Set HIREPILOT_SECRET_KEY in your .env file."
        )
        return Fernet(fallback.encode())
    # Accept either a raw 32-byte URL-safe base64 key or a plain string
    try:
        return Fernet(raw.encode())
    except Exception:
        # Derive a valid key from the raw string via PBKDF2
        derived = hashlib.pbkdf2_hmac(
            "sha256", raw.encode(), b"hirepilot-key-salt", 100_000
        )
        import base64
        return Fernet(base64.urlsafe_b64encode(derived))


# ── Password hashing ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return a stored password hash string: 'salt:iterations:hash'."""
    salt = secrets.token_hex(32)
    iterations = 260_000
    h = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"{salt}:{iterations}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time password verification."""
    try:
        salt, iterations_str, expected_hex = stored.split(":")
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return hmac.compare_digest(computed.hex(), expected_hex)


# ── API key encryption ─────────────────────────────────────────────────

def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for storage. Returns base64 ciphertext."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a stored API key. Returns empty string on failure."""
    if not ciphertext:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt API key — key may have changed")
        return ""


# ── Streamlit session gate ─────────────────────────────────────────────

def require_auth(db) -> str:
    """
    Return the authenticated user_id or render auth UI and stop execution.

    Call at the top of every page render function:
        user_id = require_auth(db)
    """
    if _st().session_state.get("user_id"):
        return _st().session_state["user_id"]
    _render_auth(db)
    _st().stop()


def get_current_user_id() -> Optional[str]:
    """Return user_id from session without stopping execution."""
    return _st().session_state.get("user_id")


def sign_out():
    """Clear the session and rerun."""
    for key in ["user_id", "user_name", "user_email", "api_key", "_onboarding_done"]:
        _st().session_state.pop(key, None)
    _st().rerun()


def _load_session_from_db(db, user_id: str) -> None:
    """After login, load the user's API key into session state."""
    try:
        enc = db.get_user_pref(user_id, "anthropic_key_enc", default="")
        if enc:
            key = decrypt_api_key(enc)
            if key:
                _st().session_state["api_key"] = key
    except Exception as e:
        logger.warning("Could not load API key from DB: %s", e)


def _render_auth(db) -> None:
    """Render the sign-in / sign-up UI."""
    _st().markdown(
        "<h1 style='text-align:center;margin-bottom:0'>HirePilot</h1>",
        unsafe_allow_html=True,
    )
    _st().markdown(
        "<p style='text-align:center;color:#7a7d9c;margin-bottom:2rem'>"
        "Your job application copilot</p>",
        unsafe_allow_html=True,
    )

    col = _st().columns([1, 2, 1])[1]
    with col:
        tab_in, tab_up = _st().tabs(["Sign in", "Create account"])

        with tab_in:
            email_in = _st().text_input("Email", key="auth_login_email")
            pw_in = _st().text_input("Password", type="password", key="auth_login_pw")
            if _st().button("Sign in", use_container_width=True, type="primary"):
                _handle_login(db, email_in.strip().lower(), pw_in)

        with tab_up:
            name_up = _st().text_input("Your name", key="auth_su_name")
            email_up = _st().text_input("Email", key="auth_su_email")
            pw_up = _st().text_input(
                "Password (min 8 characters)", type="password", key="auth_su_pw"
            )
            if _st().button("Create account", use_container_width=True, type="primary"):
                _handle_signup(db, name_up.strip(), email_up.strip().lower(), pw_up)


def _handle_login(db, email: str, password: str) -> None:
    if not email or not password:
        _st().error("Please enter your email and password.")
        return
    user = db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        _st().error("Incorrect email or password.")
        logger.info("Failed login attempt for email=%s", email)
        return
    _st().session_state["user_id"] = user["id"]
    _st().session_state["user_name"] = user["name"]
    _st().session_state["user_email"] = user["email"]
    db.update_last_login(user["id"])
    _load_session_from_db(db, user["id"])
    logger.info("User %s signed in", user["id"])
    _st().rerun()


def _handle_signup(db, name: str, email: str, password: str) -> None:
    if not name:
        _st().error("Please enter your name.")
        return
    if not email or "@" not in email:
        _st().error("Please enter a valid email address.")
        return
    if len(password) < 8:
        _st().error("Password must be at least 8 characters.")
        return
    if db.get_user_by_email(email):
        _st().error("An account with this email already exists. Sign in instead.")
        return
    user_id = db.create_user(email, hash_password(password), name)
    _st().session_state["user_id"] = user_id
    _st().session_state["user_name"] = name
    _st().session_state["user_email"] = email
    _st().session_state["_onboarding_done"] = False
    logger.info("New user created: %s", user_id)
    _st().rerun()
