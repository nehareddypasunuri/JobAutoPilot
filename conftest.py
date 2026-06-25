"""
pytest configuration and shared fixtures for HirePilot tests.

The test suite runs without Streamlit — we mock it at import time.
All tests use in-memory SQLite so they are fast and leave no files.
"""
import os
import sys
from unittest.mock import MagicMock

# ── Must be set before any module import ──────────────────────────────────
os.environ.setdefault("HIREPILOT_SECRET_KEY", "test-secret-key-for-unit-tests-only-32x")
os.environ.setdefault("HIREPILOT_DB_PATH", ":memory:")

# ── Mock streamlit before any module loads it ─────────────────────────────
# This allows auth.py, app.py, and any UI module to be imported in tests
# without a running Streamlit server.
_st_mock = MagicMock()
_st_mock.session_state = {}
sys.modules["streamlit"] = _st_mock
sys.modules["anthropic"] = MagicMock()

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from database import Database
from auth import hash_password


@pytest.fixture
def db():
    """Fresh in-memory database for each test."""
    return Database(":memory:")


@pytest.fixture
def user_alice(db):
    """A registered user for testing."""
    uid = db.create_user("alice@test.com", hash_password("password123"), "Alice")
    return {"id": uid, "email": "alice@test.com", "name": "Alice"}


@pytest.fixture
def user_bob(db):
    """A second user — for isolation tests."""
    uid = db.create_user("bob@test.com", hash_password("password456"), "Bob")
    return {"id": uid, "email": "bob@test.com", "name": "Bob"}


@pytest.fixture
def alice_job(db, user_alice):
    """A job added by Alice."""
    job_id = db.add_job(
        user_id=user_alice["id"],
        company="Stripe",
        role="Data Analyst",
        job_description="SQL, Excel, Tableau required. Full-time.",
        status="Applied",
    )
    return {"id": job_id, "company": "Stripe", "role": "Data Analyst"}
