"""
resume_tailor.py — AI-powered resume tailoring with retry and sanitization.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, Optional

import anthropic

from sanitize import sanitize_for_prompt, wrap_for_prompt

logger = logging.getLogger("hirepilot.resume_tailor")

TAILOR_SYSTEM = """You are a professional resume writer specialising in ATS optimisation.
You help candidates at the analyst / junior software / operations level land more interviews.

STRICT RULES — never break these:
1. NEVER invent experience, roles, companies, projects, metrics, or skills not in the original resume.
2. NEVER change job titles, employers, dates, or educational institutions.
3. NEVER add technologies or tools the candidate has not mentioned.
4. DO rewrite bullet points to mirror the JD's language more closely.
5. DO reorder bullets to lead with the most relevant points.
6. DO strengthen verb choices (Led / Built / Delivered / Analysed / Designed).
7. DO add a professional Summary section at the top (2-3 sentences) using JD keywords.
8. DO add or update a Skills section listing tools mentioned in BOTH resume and JD.
9. Output ONLY the tailored resume text — no preamble, no commentary.
10. Use plain text with clear section headers (ALL CAPS) and bullet dashes (-)."""

QC_SYSTEM = """You are a strict resume reviewer. Give a concise quality assessment.

Return a JSON object with this exact shape:
{
  "overall_grade": "A" | "B" | "C" | "D",
  "summary": "2-3 sentence plain-English verdict",
  "strengths": ["strength 1", "strength 2", "strength 3"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "ats_risks": ["risk 1", "risk 2"],
  "final_verdict": "STRONG APPLY" | "APPLY WITH EDITS" | "NEEDS WORK" | "DO NOT APPLY"
}

Be direct and specific. No filler. Output ONLY the JSON object."""


def _get_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)


def _call(client: anthropic.Anthropic, system: str, messages: list, max_tokens: int) -> str:
    """Make a Claude API call with standardised error handling."""
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return resp.content[0].text.strip()
    except anthropic.AuthenticationError:
        raise ValueError("Invalid API key. Check your key in Profile settings.")
    except anthropic.RateLimitError:
        raise ValueError("Rate limit reached. Wait 60 seconds and try again.")
    except anthropic.APITimeoutError:
        raise ValueError("Request timed out. The AI service is slow — please retry.")
    except anthropic.APIStatusError as e:
        raise ValueError(f"AI service error ({e.status_code}). Please retry.")


def tailor_resume(
    resume_text: str,
    jd_text: str,
    target_role: str,
    api_key: str,
    scoring_result: Optional[Dict] = None,
) -> Dict:
    """
    Tailor the resume to the JD using Claude.
    Returns {"tailored_resume": str, "notes": str}
    """
    # Sanitize all user inputs before they enter the prompt
    safe_resume = sanitize_for_prompt(resume_text, 12_000, "resume")
    safe_jd     = sanitize_for_prompt(jd_text, 8_000, "job description")
    safe_role   = sanitize_for_prompt(target_role, 200, "target role")

    client = _get_client(api_key)

    scoring_context = ""
    if scoring_result:
        missing  = ", ".join(scoring_result.get("missing_keywords", [])[:12])
        matched  = ", ".join(scoring_result.get("matched_keywords",  [])[:8])
        scoring_context = (
            f"\nATS PRE-ANALYSIS:\n"
            f"- Match score before tailoring: {scoring_result.get('score', '?')}%\n"
            f"- Already in resume: {matched}\n"
            f"- High-priority missing keywords to weave in: {missing}\n"
        )

    user_prompt = (
        f"TARGET ROLE: {safe_role}\n"
        f"{scoring_context}\n"
        f"{wrap_for_prompt('job_description', safe_jd)}\n\n"
        f"{wrap_for_prompt('resume', safe_resume)}\n\n"
        "Tailor the resume above. Follow all rules exactly. "
        "Do NOT invent anything. Mirror JD language where the candidate already has that experience."
    )

    tailored = _call(client, TAILOR_SYSTEM, [{"role": "user", "content": user_prompt}], 2500)

    # Second call: changelog
    try:
        notes = _call(
            client,
            "You are a resume editor assistant.",
            [
                {"role": "user",      "content": user_prompt},
                {"role": "assistant", "content": tailored},
                {"role": "user",      "content":
                    "List the TOP 5 changes you made as short bullet points. "
                    "Be specific. Output only the 5 bullets, no intro."},
            ],
            300,
        )
    except Exception:
        notes = "Resume tailored successfully."

    return {"tailored_resume": tailored, "notes": notes}


def quality_check(resume_text: str, jd_text: str, api_key: str) -> Dict:
    """Run a quality check on a resume vs JD. Returns parsed QC dict."""
    safe_resume = sanitize_for_prompt(resume_text, 12_000, "resume")
    safe_jd     = sanitize_for_prompt(jd_text, 8_000, "job description")

    client = _get_client(api_key)
    user_prompt = (
        f"{wrap_for_prompt('job_description', safe_jd)}\n\n"
        f"{wrap_for_prompt('resume', safe_resume)}\n\n"
        "Evaluate the resume against the JD. Return JSON only."
    )

    raw = _call(client, QC_SYSTEM, [{"role": "user", "content": user_prompt}], 800)
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "overall_grade": "?", "summary": raw[:300],
            "strengths": [], "weaknesses": [], "ats_risks": [],
            "final_verdict": "PARSE ERROR — see summary",
        }
