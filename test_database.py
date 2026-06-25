"""Tests for database.py — multi-user isolation and CRUD operations."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Database

def _make_db():
    """Create a fresh in-memory test database."""
    return Database(":memory:")


def _make_user(db, email="alice@test.com", name="Alice"):
    from auth import hash_password
    return db.create_user(email, hash_password("password123"), name)


# ── User management ────────────────────────────────────────────────────

def test_create_user_returns_id():
    db = _make_db()
    uid = _make_user(db)
    assert uid and len(uid) == 32


def test_get_user_by_email():
    db = _make_db()
    uid = _make_user(db, "test@example.com", "Test User")
    user = db.get_user_by_email("test@example.com")
    assert user is not None
    assert user["id"] == uid
    assert user["name"] == "Test User"


def test_get_user_by_email_case_insensitive():
    db = _make_db()
    _make_user(db, "Case@Example.COM", "Case User")
    user = db.get_user_by_email("case@example.com")
    assert user is not None


def test_get_user_by_email_not_found():
    db = _make_db()
    assert db.get_user_by_email("nobody@nowhere.com") is None


def test_duplicate_email_rejected():
    db = _make_db()
    _make_user(db, "dup@test.com")
    try:
        _make_user(db, "dup@test.com")
        assert False, "Should have raised"
    except Exception:
        pass  # expected


def test_delete_user_cascades():
    db = _make_db()
    uid = _make_user(db)
    db.add_job(uid, "Stripe", "Data Analyst")
    db.delete_user_and_all_data(uid)
    assert db.get_user(uid) is None
    assert db.get_all_jobs(uid) == []


# ── User isolation ─────────────────────────────────────────────────────

def test_user_isolation_jobs():
    """User A cannot see User B's jobs."""
    db = _make_db()
    uid_a = _make_user(db, "a@test.com", "Alice")
    uid_b = _make_user(db, "b@test.com", "Bob")
    db.add_job(uid_a, "Stripe", "Data Analyst")
    db.add_job(uid_b, "Google", "Software Engineer")

    jobs_a = db.get_all_jobs(uid_a)
    jobs_b = db.get_all_jobs(uid_b)

    assert len(jobs_a) == 1 and jobs_a[0]["company"] == "Stripe"
    assert len(jobs_b) == 1 and jobs_b[0]["company"] == "Google"


def test_user_isolation_delete():
    """User A cannot delete User B's job."""
    db = _make_db()
    uid_a = _make_user(db, "a@test.com", "Alice")
    uid_b = _make_user(db, "b@test.com", "Bob")
    jid = db.add_job(uid_b, "Netflix", "Engineer")

    # Alice tries to delete Bob's job
    db.delete_job(uid_a, jid)

    # Bob's job still exists
    assert db.get_job(uid_b, jid) is not None


def test_user_isolation_update():
    """User A cannot update User B's job."""
    db = _make_db()
    uid_a = _make_user(db, "a@test.com", "Alice")
    uid_b = _make_user(db, "b@test.com", "Bob")
    jid = db.add_job(uid_b, "Netflix", "Engineer", status="Found")

    db.update_status(uid_a, jid, "Rejected")  # Alice tries to reject Bob's job

    job = db.get_job(uid_b, jid)
    assert job["status"] == "Found", "Bob's job status must not change"


def test_user_isolation_documents():
    db = _make_db()
    uid_a = _make_user(db, "a@test.com", "Alice")
    uid_b = _make_user(db, "b@test.com", "Bob")
    db.save_document(uid_a, "Resume", "Stripe", "Analyst", "Alice's resume")
    db.save_document(uid_b, "Resume", "Google", "Engineer", "Bob's resume")

    docs_a = db.get_all_documents(uid_a)
    docs_b = db.get_all_documents(uid_b)
    assert len(docs_a) == 1 and "Alice" in docs_a[0]["content"]
    assert len(docs_b) == 1 and "Bob" in docs_b[0]["content"]


# ── Job CRUD ───────────────────────────────────────────────────────────

def test_add_and_get_job():
    db = _make_db()
    uid = _make_user(db)
    jid = db.add_job(uid, "Stripe", "Data Analyst",
                     location="Remote", status="Applied", match_score=84)
    job = db.get_job(uid, jid)
    assert job["company"] == "Stripe"
    assert job["role"] == "Data Analyst"
    assert job["match_score"] == 84
    assert job["status"] == "Applied"
    assert job["user_id"] == uid


def test_update_status():
    db = _make_db()
    uid = _make_user(db)
    jid = db.add_job(uid, "Test", "Role")
    db.update_status(uid, jid, "Offer")
    assert db.get_job(uid, jid)["status"] == "Offer"


def test_update_match_score():
    db = _make_db()
    uid = _make_user(db)
    jid = db.add_job(uid, "Test", "Role")
    db.update_match_score(uid, jid, 91)
    assert db.get_job(uid, jid)["match_score"] == 91


def test_delete_job():
    db = _make_db()
    uid = _make_user(db)
    jid = db.add_job(uid, "Test", "Role")
    db.delete_job(uid, jid)
    assert db.get_job(uid, jid) is None


def test_get_all_jobs_limit():
    db = _make_db()
    uid = _make_user(db)
    for i in range(5):
        db.add_job(uid, f"Co{i}", "Role")
    jobs = db.get_all_jobs(uid, limit=3)
    assert len(jobs) == 3


def test_pipeline_summary():
    db = _make_db()
    uid = _make_user(db)
    db.add_job(uid, "A", "R1", status="Applied")
    db.add_job(uid, "B", "R2", status="Offer")
    db.add_job(uid, "C", "R3", status="Rejected")
    s = db.get_pipeline_summary(uid)
    assert s["total"] == 3
    assert s["offers"] == 1
    assert s["rejected"] == 1
    assert s["active"] == 1


# ── User prefs ─────────────────────────────────────────────────────────

def test_user_prefs_roundtrip():
    db = _make_db()
    uid = _make_user(db)
    db.set_user_pref(uid, "my_key", {"nested": [1, 2, 3]})
    val = db.get_user_pref(uid, "my_key")
    assert val == {"nested": [1, 2, 3]}


def test_user_prefs_isolation():
    db = _make_db()
    uid_a = _make_user(db, "a@test.com", "Alice")
    uid_b = _make_user(db, "b@test.com", "Bob")
    db.set_user_pref(uid_a, "resume_text", "Alice resume")
    db.set_user_pref(uid_b, "resume_text", "Bob resume")
    assert db.get_user_pref(uid_a, "resume_text") == "Alice resume"
    assert db.get_user_pref(uid_b, "resume_text") == "Bob resume"


def test_user_prefs_default():
    db = _make_db()
    uid = _make_user(db)
    val = db.get_user_pref(uid, "nonexistent_key", default="fallback")
    assert val == "fallback"


# ── Documents ──────────────────────────────────────────────────────────

def test_save_and_delete_document():
    db = _make_db()
    uid = _make_user(db)
    did = db.save_document(uid, "Resume", "Stripe", "Analyst", "My resume content")
    docs = db.get_all_documents(uid)
    assert len(docs) == 1
    assert docs[0]["content"] == "My resume content"
    db.delete_document(uid, did)
    assert db.get_all_documents(uid) == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
