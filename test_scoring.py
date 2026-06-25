"""Tests for scoring.py — ATS scoring, hard rejects, and role detection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scoring import score_resume_jd

SAMPLE_RESUME = """
Alex | alex@test.com | Chicago, IL

SUMMARY
Data analyst with 2 years experience in SQL, Excel, and data reporting.

EXPERIENCE
Data Analyst Intern — TechCorp (2022-2024)
- Wrote SQL queries to pull and analyse data from PostgreSQL
- Built Excel dashboards and Tableau visualisations for stakeholders
- Automated reporting workflows reducing manual effort by 30%

EDUCATION
BSc Business Administration — State University, 2022

SKILLS
SQL, Excel, Tableau, PowerPoint, Python (beginner), Jira
"""

SAMPLE_JD_ANALYST = """
Data Analyst — DataFlow Inc
Requirements: SQL, Excel, Tableau or Power BI, data visualisation,
reporting, stakeholder management. 1-3 years experience.
Nice to have: Python, Looker. Full-time position.
"""

SAMPLE_JD_CLEARANCE = """
Senior Analyst — Defense Contractor
Must be US citizen with active security clearance (TS/SCI).
No visa sponsorship available.
"""

SAMPLE_JD_SENIOR = """
Staff Engineering Manager — TechCo
10+ years of engineering experience required.
Must have led teams of 20+ engineers.
Principal-level technical leadership.
"""


# ── Basic scoring ──────────────────────────────────────────────────────

def test_score_returns_expected_keys():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    for key in ["score", "matched_keywords", "missing_keywords",
                "hard_rejects", "role_type", "quality_check", "is_preferred_role"]:
        assert key in r, f"Missing key: {key}"


def test_score_range():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert 0 <= r["score"] <= 100


def test_analyst_role_detected():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert r["role_type"] in ("Analyst", "Mixed")


def test_analyst_is_preferred():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert r["is_preferred_role"] is True


def test_matched_keywords_are_strings():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert all(isinstance(k, str) for k in r["matched_keywords"])


def test_missing_keywords_not_in_resume():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    resume_lower = SAMPLE_RESUME.lower()
    for kw in r["missing_keywords"]:
        assert kw.lower() not in resume_lower, f"'{kw}' is in resume but listed as missing"


# ── Hard rejects ───────────────────────────────────────────────────────

def test_clearance_hard_reject():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_CLEARANCE)
    assert len(r["hard_rejects"]) > 0, "Clearance JD should produce hard reject"
    assert any("clearance" in m.lower() or "citizen" in m.lower()
               for m in r["hard_rejects"])


def test_seniority_hard_reject():
    entry_level_resume = "Recent graduate. 1 year experience. Entry level position."
    r = score_resume_jd(entry_level_resume, SAMPLE_JD_SENIOR)
    assert len(r["hard_rejects"]) > 0, "Senior 10yr JD with entry-level resume should reject"


def test_no_reject_for_normal_jd():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert r["hard_rejects"] == [], "Normal JD should have no hard rejects"


def test_no_reject_for_opt_friendly_jd():
    opt_jd = "Data analyst role. Will consider OPT and H1B candidates. SQL required."
    r = score_resume_jd(SAMPLE_RESUME, opt_jd)
    assert r["hard_rejects"] == [], "OPT-friendly JD must not hard reject"


# ── Quality check ──────────────────────────────────────────────────────

def test_quality_check_structure():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    qc = r["quality_check"]
    assert "grade" in qc
    assert "passes" in qc
    assert "warns" in qc
    assert "fails" in qc
    assert qc["grade"] in "ABCDF"


# ── Recommended changes ────────────────────────────────────────────────

def test_recommended_changes_is_list():
    r = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert isinstance(r["recommended_changes"], list)


# ── Edge cases ─────────────────────────────────────────────────────────

def test_empty_jd_does_not_crash():
    r = score_resume_jd(SAMPLE_RESUME, "")
    assert isinstance(r["score"], int)


def test_empty_resume_does_not_crash():
    r = score_resume_jd("", SAMPLE_JD_ANALYST)
    assert isinstance(r["score"], int)
    assert r["score"] == 0


def test_score_is_deterministic():
    """Same inputs produce same score."""
    r1 = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    r2 = score_resume_jd(SAMPLE_RESUME, SAMPLE_JD_ANALYST)
    assert r1["score"] == r2["score"]


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
