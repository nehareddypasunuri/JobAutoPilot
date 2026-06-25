"""
sanitize.py — Input validation and prompt sanitization for HirePilot.

Every piece of user text that reaches a Claude prompt passes through here.
Prevents prompt injection, enforces length limits, and validates structured inputs.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("hirepilot.sanitize")

# ── Length limits ──────────────────────────────────────────────────────
MAX_RESUME_CHARS    = 12_000   # ~4,000 tokens — enough for any resume
MAX_JD_CHARS        = 8_000    # ~2,700 tokens
MAX_SUMMARY_CHARS   = 2_000
MAX_COMPANY_CHARS   = 200
MAX_ROLE_CHARS      = 200
MAX_NOTES_CHARS     = 2_000
MAX_URL_CHARS       = 2_000
MAX_NAME_CHARS      = 100
MAX_API_KEY_CHARS   = 200

# ── Prompt injection patterns ──────────────────────────────────────────
# Common injection attempts in job descriptions or resume fields
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"you\s+are\s+now\s+(a\s+)?(?:an?\s+)?(?:different|new|unrestricted)",
    r"disregard\s+(all\s+)?(?:previous|prior)\s+",
    r"new\s+instruction[s]?:",
    r"system\s+prompt\s*:",
    r"</?(system|user|assistant|human)>",
    r"\[INST\]|\[/INST\]",
    r"<\|(?:im_start|im_end|endoftext)\|>",
    r"act\s+as\s+(?:dan|jailbreak|evil|uncensored)",
    r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|uncensored|evil|jailbroken)",
]
_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.DOTALL
)


class ValidationError(ValueError):
    """Raised when user input fails validation. Message is safe to show users."""


def clean_text(text: str, max_chars: int, field_name: str = "input") -> str:
    """
    Strip, truncate, and check for injection patterns.
    Returns cleaned text. Raises ValidationError on injection detection.
    """
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return text
    # Truncate silently (very long input = bad actor or paste error)
    if len(text) > max_chars:
        logger.warning("Truncating %s from %d to %d chars", field_name, len(text), max_chars)
        text = text[:max_chars]
    # Check for injection attempts
    if _INJECTION_RE.search(text):
        logger.warning("Injection pattern detected in %s", field_name)
        raise ValidationError(
            f"The {field_name} contains content that looks like a prompt injection attempt "
            "and cannot be processed. Please review and resubmit."
        )
    return text


def sanitize_for_prompt(text: str, max_chars: int, field_name: str = "input") -> str:
    """
    Clean text AND wrap in XML delimiters so it cannot escape its role in the prompt.
    Use this for any user content that goes directly into a Claude message.
    """
    cleaned = clean_text(text, max_chars, field_name)
    return cleaned


def wrap_for_prompt(tag: str, content: str) -> str:
    """
    Wrap sanitized content in XML tags.

    Example:
        wrap_for_prompt("resume", resume_text)
        → "<resume>\\n...text...\\n</resume>"

    Claude's context uses these as structural delimiters that user text
    cannot break out of (the closing tag is not present in any real resume).
    """
    # Escape any accidental closing tag in content
    safe = content.replace(f"</{tag}>", f"[/{tag}]")
    return f"<{tag}>\n{safe}\n</{tag}>"


# ── Domain-specific validators ─────────────────────────────────────────

def validate_resume(text: str) -> str:
    """Validate and clean resume text."""
    cleaned = clean_text(text, MAX_RESUME_CHARS, "resume")
    if cleaned and len(cleaned.split()) < 20:
        raise ValidationError(
            "Resume text is too short. Please paste your full resume (plain text)."
        )
    return cleaned


def validate_jd(text: str) -> str:
    """Validate and clean job description text."""
    cleaned = clean_text(text, MAX_JD_CHARS, "job description")
    if cleaned and len(cleaned.split()) < 10:
        raise ValidationError(
            "Job description is too short. Please paste the full job posting."
        )
    return cleaned


def validate_company(text: str) -> str:
    """Validate company name."""
    cleaned = clean_text(text, MAX_COMPANY_CHARS, "company name")
    if not cleaned:
        raise ValidationError("Company name is required.")
    return cleaned


def validate_role(text: str) -> str:
    """Validate role / job title."""
    cleaned = clean_text(text, MAX_ROLE_CHARS, "role")
    if not cleaned:
        raise ValidationError("Role / job title is required.")
    return cleaned


def validate_url(url: str) -> str:
    """Validate and clean a URL."""
    url = url.strip()[:MAX_URL_CHARS]
    if url and not re.match(r"^https?://", url):
        raise ValidationError(
            "Job URL must start with http:// or https://"
        )
    return url


def validate_api_key(key: str) -> str:
    """Validate Anthropic API key format."""
    key = key.strip()[:MAX_API_KEY_CHARS]
    if not key:
        return ""
    if not re.match(r"^sk-ant-", key):
        raise ValidationError(
            "That doesn't look like an Anthropic API key. "
            "Keys start with 'sk-ant-'. Get yours at console.anthropic.com."
        )
    return key


def validate_email(email: str) -> str:
    """Validate email address format."""
    email = email.strip()[:MAX_NAME_CHARS]
    if not email:
        raise ValidationError("Email address is required.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Please enter a valid email address.")
    return email.lower()


def validate_name(name: str) -> str:
    """Validate a person's name."""
    name = clean_text(name, MAX_NAME_CHARS, "name").strip()
    if not name:
        raise ValidationError("Name is required.")
    return name


def validate_work_auth(value: str, valid_options: list) -> str:
    """Validate work authorization is from the allowed set."""
    if value not in valid_options:
        raise ValidationError(f"Work authorization must be one of: {', '.join(valid_options)}")
    return value
