"""
cover_letter.py — Personalised cover letter generator with retry and sanitization.
"""
from __future__ import annotations

import logging
from typing import Optional

import anthropic

from sanitize import sanitize_for_prompt, wrap_for_prompt

logger = logging.getLogger("hirepilot.cover_letter")

COVER_LETTER_SYSTEM = """You are an expert cover letter writer for early-career and mid-level professionals.

RULES:
1. NEVER invent experience, skills, companies, or achievements not in the candidate's summary.
2. NEVER use the opening phrase "I am writing to apply for" or "I would like to apply".
3. NEVER use hollow phrases: "passionate", "excited to leverage", "dynamic", "fast-paced".
4. DO open with a compelling one-sentence hook tied to the company or role.
5. DO connect 2-3 specific pieces of the candidate's real experience to the JD's key needs.
6. DO keep it to 3 short paragraphs, under 220 words total.
7. DO end with a single confident sentence requesting next steps.
8. Match the tone requested exactly: Professional | Enthusiastic | Concise | Creative.
9. Output ONLY the cover letter text (including salutation and sign-off). No preamble."""


def _get_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=45.0)


def generate_cover_letter(
    company: str,
    role: str,
    jd_text: str,
    candidate_name: str,
    hiring_manager: str = "",
    resume_summary: str = "",
    tone: str = "Professional",
    api_key: str = "",
    scoring_result: Optional[dict] = None,
) -> str:
    """Generate a short personalised cover letter. Returns the letter text."""
    # Sanitize all user inputs
    safe_company  = sanitize_for_prompt(company,        200,   "company")
    safe_role     = sanitize_for_prompt(role,           200,   "role")
    safe_jd       = sanitize_for_prompt(jd_text,        8_000, "job description")
    safe_name     = sanitize_for_prompt(candidate_name, 100,   "name")
    safe_manager  = sanitize_for_prompt(hiring_manager, 100,   "hiring manager")
    safe_summary  = sanitize_for_prompt(resume_summary, 2_000, "resume summary")
    safe_tone     = tone if tone in ("Professional", "Enthusiastic", "Concise", "Creative") else "Professional"

    salutation = f"Dear {safe_manager}," if safe_manager else "Dear Hiring Manager,"
    sign_off   = f"Best regards,\n{safe_name}"

    scoring_note = ""
    if scoring_result:
        matched = scoring_result.get("matched_keywords", [])[:6]
        if matched:
            scoring_note = f"\nHighlight these matched skills: {', '.join(matched)}."

    summary_block = (
        wrap_for_prompt("candidate_experience", safe_summary)
        if safe_summary
        else "(No experience summary provided — work only from the JD context.)"
    )

    user_prompt = (
        f"COMPANY: {safe_company}\n"
        f"ROLE: {safe_role}\n"
        f"TONE: {safe_tone}\n"
        f"SALUTATION: {salutation}\n"
        f"SIGN-OFF: {sign_off}\n"
        f"{scoring_note}\n\n"
        f"{summary_block}\n\n"
        f"{wrap_for_prompt('job_description', safe_jd)}\n\n"
        f"Write the cover letter. Under 220 words. {safe_tone} tone."
    )

    try:
        client = _get_client(api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            system=COVER_LETTER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return resp.content[0].text.strip()
    except anthropic.AuthenticationError:
        raise ValueError("Invalid API key. Check your key in Profile settings.")
    except anthropic.RateLimitError:
        raise ValueError("Rate limit reached. Wait 60 seconds and try again.")
    except anthropic.APITimeoutError:
        raise ValueError("Request timed out. Please retry.")
    except anthropic.APIStatusError as e:
        raise ValueError(f"AI service error ({e.status_code}). Please retry.")
