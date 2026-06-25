"""
scoring.py — Production ATS scoring engine for JobAutoPilot.

Public API
----------
score_resume_jd(resume_text, jd_text) -> ScoringResult dict

Internal pipeline
-----------------
1.  Hard-reject gate  (clearance, seniority, sponsorship)
2.  Weighted keyword match  (catalog phrases + freeform JD tokens)
3.  Role-type classification  (Analyst / Software / Operations / Mixed)
4.  Preferred-role check
5.  Soft-fit warnings
6.  Concrete recommended_changes
7.  Quality-check on the resume itself
8.  Summary tips
"""

import re
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

STOP_WORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "shall","can","not","no","nor","so","yet","both","either","whether",
    "as","if","while","since","than","then","that","this","these","those",
    "it","its","we","you","he","she","they","them","their","our","your","my",
    "more","most","such","each","every","all","any","few","some","also",
    "just","about","up","out","use","used","using","work","working","role",
    "position","candidate","company","team","experience","ability","strong",
    "excellent","looking","seeking","join","help","please","must","able",
    "will","good","great","etc","well","per","new","high","low","day","time",
}

# (phrase, weight 1-3, category)
KEYWORD_CATALOG: List[Tuple[str, int, str]] = [
    # ── Data / Analyst / BI ───────────────────────────────────────────
    ("sql",                          3, "data"),
    ("mysql",                        2, "data"),
    ("postgresql",                   2, "data"),
    ("excel",                        3, "data"),
    ("google sheets",                2, "data"),
    ("pivot tables",                 2, "data"),
    ("vlookup",                      2, "data"),
    ("index match",                  2, "data"),
    ("tableau",                      3, "data"),
    ("power bi",                     3, "data"),
    ("looker",                       2, "data"),
    ("looker studio",                2, "data"),
    ("qlik",                         2, "data"),
    ("data analysis",                3, "data"),
    ("data analytics",               3, "data"),
    ("business intelligence",        3, "data"),
    ("reporting",                    3, "data"),
    ("dashboards",                   2, "data"),
    ("dashboard",                    2, "data"),
    ("kpi",                          2, "data"),
    ("metrics",                      2, "data"),
    ("data visualization",           2, "data"),
    ("etl",                          2, "data"),
    ("data pipeline",                2, "data"),
    ("data warehouse",               2, "data"),
    ("snowflake",                    2, "data"),
    ("bigquery",                     2, "data"),
    ("redshift",                     2, "data"),
    ("pandas",                       2, "data"),
    ("numpy",                        2, "data"),
    ("python",                       2, "data"),
    ("r programming",                2, "data"),
    ("statistics",                   2, "data"),
    ("statistical analysis",         2, "data"),
    ("forecasting",                  2, "data"),
    ("trend analysis",               2, "data"),
    ("ad hoc",                       2, "data"),
    ("ad hoc analysis",              2, "data"),
    ("stakeholder",                  2, "data"),
    ("requirements gathering",       2, "data"),
    ("business requirements",        2, "data"),
    ("data quality",                 2, "data"),
    ("data governance",              2, "data"),
    ("data modeling",                2, "data"),
    ("a/b testing",                  2, "data"),
    ("ab testing",                   2, "data"),
    ("cohort analysis",              2, "data"),
    ("funnel analysis",              2, "data"),
    ("user analytics",               2, "data"),
    ("product analytics",            2, "data"),
    ("financial analysis",           3, "data"),
    ("variance analysis",            2, "data"),
    ("budget analysis",              2, "data"),
    ("p&l",                          2, "data"),

    # ── Operations / Business Analyst ─────────────────────────────────
    ("process improvement",          3, "operations"),
    ("process optimization",         3, "operations"),
    ("operations",                   3, "operations"),
    ("project management",           3, "operations"),
    ("program management",           2, "operations"),
    ("workflow",                     2, "operations"),
    ("workflow automation",          2, "operations"),
    ("sla",                          2, "operations"),
    ("kpi tracking",                 2, "operations"),
    ("cross-functional",             2, "operations"),
    ("cross functional",             2, "operations"),
    ("coordination",                 2, "operations"),
    ("documentation",                2, "operations"),
    ("standard operating procedure", 2, "operations"),
    ("sop",                          2, "operations"),
    ("jira",                         2, "operations"),
    ("confluence",                   2, "operations"),
    ("asana",                        2, "operations"),
    ("monday.com",                   1, "operations"),
    ("trello",                       1, "operations"),
    ("agile",                        2, "operations"),
    ("scrum",                        2, "operations"),
    ("kanban",                       2, "operations"),
    ("vendor management",            2, "operations"),
    ("vendor",                       1, "operations"),
    ("budget",                       2, "operations"),
    ("cost reduction",               2, "operations"),
    ("efficiency",                   2, "operations"),
    ("root cause analysis",          2, "operations"),
    ("quality assurance",            2, "operations"),
    ("qa",                           2, "operations"),
    ("compliance",                   2, "operations"),
    ("risk management",              2, "operations"),
    ("change management",            2, "operations"),
    ("salesforce",                   2, "operations"),
    ("crm",                          2, "operations"),
    ("erp",                          2, "operations"),
    ("sap",                          2, "operations"),
    ("microsoft office",             2, "operations"),
    ("ms office",                    2, "operations"),
    ("powerpoint",                   2, "operations"),
    ("communication",                2, "operations"),
    ("presentation",                 2, "operations"),
    ("problem solving",              2, "operations"),
    ("supply chain",                 2, "operations"),
    ("procurement",                  2, "operations"),
    ("onboarding",                   2, "operations"),
    ("training",                     2, "operations"),
    ("reporting structure",          2, "operations"),
    ("capacity planning",            2, "operations"),

    # ── Software / Technical Support ──────────────────────────────────
    ("javascript",                   3, "software"),
    ("typescript",                   3, "software"),
    ("react",                        3, "software"),
    ("vue",                          3, "software"),
    ("angular",                      3, "software"),
    ("node.js",                      3, "software"),
    ("java",                         3, "software"),
    ("c#",                           3, "software"),
    ("c++",                          3, "software"),
    ("golang",                       3, "software"),
    ("rust",                         3, "software"),
    ("swift",                        3, "software"),
    ("kotlin",                       3, "software"),
    ("php",                          2, "software"),
    ("html",                         2, "software"),
    ("css",                          2, "software"),
    ("rest api",                     2, "software"),
    ("rest apis",                    2, "software"),
    ("graphql",                      2, "software"),
    ("git",                          2, "software"),
    ("github",                       2, "software"),
    ("version control",              2, "software"),
    ("docker",                       2, "software"),
    ("kubernetes",                   2, "software"),
    ("aws",                          2, "software"),
    ("azure",                        2, "software"),
    ("gcp",                          2, "software"),
    ("google cloud",                 2, "software"),
    ("mongodb",                      2, "software"),
    ("redis",                        2, "software"),
    ("microservices",                2, "software"),
    ("ci/cd",                        2, "software"),
    ("devops",                       2, "software"),
    ("debugging",                    2, "software"),
    ("troubleshooting",              2, "software"),
    ("technical support",            3, "software"),
    ("it support",                   3, "software"),
    ("help desk",                    3, "software"),
    ("ticketing",                    2, "software"),
    ("servicenow",                   2, "software"),
    ("zendesk",                      2, "software"),
    ("linux",                        2, "software"),
    ("bash",                         2, "software"),
    ("shell scripting",              2, "software"),
    ("api integration",              2, "software"),
    ("unit testing",                 2, "software"),
    ("code review",                  2, "software"),
    ("system design",                2, "software"),
    ("object oriented",              2, "software"),
    ("oop",                          2, "software"),
    ("data structures",              2, "software"),
    ("algorithms",                   2, "software"),
    ("networking",                   2, "software"),
    ("vpn",                          1, "software"),
    ("active directory",             2, "software"),
    ("office 365",                   2, "software"),
]

# Preferred target role phrases (substring match on JD)
PREFERRED_ROLES = [
    "analyst", "data analyst", "business analyst", "bi analyst",
    "reporting analyst", "operations analyst", "financial analyst",
    "junior analyst", "associate analyst", "analytics analyst",
    "junior software developer", "junior developer", "junior engineer",
    "associate software engineer", "software developer",
    "technical support", "it support", "help desk", "support specialist",
    "support analyst", "desktop support", "application support",
    "operations coordinator", "operations specialist", "operations associate",
    "business operations", "sales operations", "revenue operations",
    "data entry", "reporting specialist",
]

# Patterns that signal the JD is too senior
SENIOR_SIGNALS = [
    r"\b(8|9|10|11|12|13|14|15|\d{2})\+?\s*years?\b",
    r"\b[6-9]\+\s*years?\b",
    r"\bstaff\s+(?:engineer|developer|scientist|swe)\b",
    r"\bprincipal\s+(?:engineer|developer|scientist)\b",
    r"\bdirector\s+of\b",
    r"\bvp\s+of\b",
    r"\bvice\s+president\b",
    r"\bhead\s+of\b",
    r"\blead\s+(?:engineer|developer|architect|scientist)\b",
    r"\barchitect\b",
    r"\bengineering\s+manager\b",
    r"\bsenior\s+(?:staff|principal)\b",
]

# Entry-level signals on the resume
ENTRY_SIGNALS = [
    r"\b(0|1|2|3)\+?\s*years?\s*(?:of\s*)?(?:experience|exp)\b",
    r"\bjunior\b",
    r"\bentry[\s\-]level\b",
    r"\bassociate\b",
    r"\bfresh(?:er|graduate|man)?\b",
    r"\brecent\s+graduate\b",
    r"\bintern(?:ship)?\b",
    r"\bgraduate\s+student\b",
    r"\bbootcamp\b",
    r"\bself[\s\-]taught\b",
]

# Hard-reject citizenship/clearance patterns
CLEARANCE_SIGNALS = [
    r"\bus\s+citizen(?:ship)?\b",
    r"\bsecurity\s+clearance\b",
    r"\bts\s*/\s*sci\b",
    r"\bsecret\s+clearance\b",
    r"\btop\s+secret\b",
    r"\bclearance\s+required\b",
    r"\bmust\s+be\s+(?:a\s+)?us\s+citizen\b",
    r"\bonly\s+us\s+citizens?\b",
    r"\bno\s+(?:visa\s+)?sponsorship\b",
    r"\bcannot\s+(?:provide|offer|support)\s+(?:visa\s+)?sponsorship\b",
    r"\bwill\s+not\s+(?:provide|offer|sponsor)\s+(?:a\s+)?visa\b",
    r"\bcitizenship\s+required\b",
    r"\bauthorized\s+to\s+work.*without\s+sponsorship\b",
    r"\bwork\s+authorization.*no\s+sponsorship\b",
]

YEARS_RE = re.compile(
    r"(\d+)\+?\s*(?:to\s*\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp|work\s*experience)",
    re.IGNORECASE,
)

# Weak action verbs that ATS and recruiters penalise
WEAK_VERBS = [
    "helped", "assisted", "worked on", "was responsible for",
    "duties included", "responsible for", "tasked with",
    "participated in", "involved in", "contributed to",
]

# Niche domains that need domain-specific experience
NICHE_DOMAINS = [
    ("healthcare", "healthcare / medical"),
    ("medical", "healthcare / medical"),
    ("pharmaceutical", "pharma"),
    ("clinical", "clinical / healthcare"),
    ("legal", "legal / law"),
    ("law firm", "legal / law"),
    ("hedge fund", "hedge fund / finance"),
    ("investment banking", "investment banking"),
    ("defense", "defense / government"),
    ("military", "defense / military"),
    ("oil and gas", "oil & gas"),
    ("insurance", "insurance"),
    ("real estate", "real estate"),
]


# ══════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════

def score_resume_jd(resume_text: str, jd_text: str) -> Dict:
    """
    Full ATS analysis.

    Returns
    -------
    dict with keys:
      score               int 0-100
      keyword_hits        int
      total_keywords      int
      matched_keywords    list[str]
      missing_keywords    list[str]   top 25
      recommended_changes list[str]  concrete edits
      role_type           str  Analyst | Software | Operations | Mixed | Unknown
      is_preferred_role   bool
      fit_warnings        list[str]  soft warnings
      hard_rejects        list[str]  if non-empty → do not apply
      quality_check       dict       resume quality analysis
      tips                list[str]
    """
    jd_lower     = jd_text.lower()
    resume_lower = resume_text.lower()

    hard_rejects           = _check_hard_rejects(jd_lower, resume_lower)
    matched, missing, score = _keyword_score(jd_lower, resume_lower)
    role_type, is_preferred = _classify_role(jd_lower)
    fit_warnings           = _fit_warnings(jd_lower, resume_lower, role_type, is_preferred)
    changes                = _recommend_changes(matched, missing, jd_lower, resume_lower, role_type)
    quality                = _quality_check(resume_text, resume_lower)
    tips                   = _tips(score, is_preferred, hard_rejects)

    return {
        "score":               score,
        "keyword_hits":        len(matched),
        "total_keywords":      len(matched) + len(missing),
        "matched_keywords":    sorted(matched),
        "missing_keywords":    sorted(missing[:25]),
        "recommended_changes": changes,
        "role_type":           role_type,
        "is_preferred_role":   is_preferred,
        "fit_warnings":        fit_warnings,
        "hard_rejects":        hard_rejects,
        "quality_check":       quality,
        "tips":                tips,
    }


# ══════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════

def _check_hard_rejects(jd_lower: str, resume_lower: str) -> List[str]:
    rejects = []

    # Citizenship / clearance
    for pat in CLEARANCE_SIGNALS:
        if re.search(pat, jd_lower, re.IGNORECASE):
            rejects.append(
                "🚫 HARD REJECT — Role requires US citizenship, security clearance, or no visa "
                "sponsorship. Do not apply."
            )
            break

    # Seniority: check explicit years first
    jd_is_senior = any(re.search(p, jd_lower, re.IGNORECASE) for p in SENIOR_SIGNALS)
    res_is_entry  = any(re.search(p, resume_lower, re.IGNORECASE) for p in ENTRY_SIGNALS)

    years_match = YEARS_RE.search(jd_lower)
    if years_match:
        yrs = int(years_match.group(1))
        if yrs >= 5 and res_is_entry:
            rejects.append(
                f"🚫 HARD REJECT — Role requires {yrs}+ years of experience. "
                f"Your resume signals entry-level / early-career. Do not apply."
            )
    elif jd_is_senior and res_is_entry:
        rejects.append(
            "🚫 HARD REJECT — Role language is senior-level (Staff / Lead / Principal / Director). "
            "Your resume signals entry-level. Do not apply."
        )

    return rejects


def _keyword_score(
    jd_lower: str, resume_lower: str
) -> Tuple[List[str], List[str], int]:
    matched_w, total_w = 0, 0
    matched_kws, missing_kws = [], []
    seen: set = set()

    for phrase, weight, _ in KEYWORD_CATALOG:
        if phrase in seen:
            continue
        seen.add(phrase)
        if phrase not in jd_lower:
            continue
        total_w += weight
        if phrase in resume_lower:
            matched_w += weight
            matched_kws.append(phrase)
        else:
            missing_kws.append(phrase)

    # Freeform JD tokens not in catalog
    for tok in _freeform_tokens(jd_lower, seen):
        total_w += 1
        if tok in resume_lower:
            matched_w += 1
            matched_kws.append(tok)
        else:
            missing_kws.append(tok)

    score = round(matched_w / total_w * 100) if total_w else 0
    score = min(score, 98)
    return matched_kws, missing_kws, score


def _freeform_tokens(jd_lower: str, already_seen: set) -> List[str]:
    tokens = re.sub(r"[^\w\s]", " ", jd_lower).split()
    result = []
    for tok in tokens:
        if len(tok) < 4:
            continue
        if tok in STOP_WORDS or tok in already_seen or tok.isdigit():
            continue
        result.append(tok)
    return list(dict.fromkeys(result))[:30]


def _classify_role(jd_lower: str) -> Tuple[str, bool]:
    scores = {"Analyst": 0, "Software": 0, "Operations": 0}
    for phrase, weight, category in KEYWORD_CATALOG:
        if phrase in jd_lower:
            if category == "data":
                scores["Analyst"]    += weight
            elif category == "software":
                scores["Software"]   += weight
            elif category == "operations":
                scores["Operations"] += weight

    top = max(scores.values())
    if top == 0:
        role_type = "Unknown"
    else:
        leaders = [k for k, v in scores.items() if v == top]
        role_type = leaders[0] if len(leaders) == 1 else "Mixed"

    is_preferred = any(r in jd_lower for r in PREFERRED_ROLES)
    return role_type, is_preferred


def _fit_warnings(
    jd_lower: str, resume_lower: str, role_type: str, is_preferred: bool
) -> List[str]:
    warnings = []

    if not is_preferred:
        warnings.append(
            "⚠️ Role title doesn't align with preferred targets (analyst / operations / "
            "junior developer / technical support). Consider whether this is a strategic fit."
        )

    # Soft seniority warning (not hard-reject level)
    soft_senior = [
        r"\bsenior\b", r"\b5\+\s*years?\b", r"\b4\+\s*years?\b",
    ]
    if any(re.search(p, jd_lower, re.IGNORECASE) for p in soft_senior):
        warnings.append(
            "⚠️ JD uses 'Senior' or 4-5+ year language. Confirm you meet the experience bar "
            "before investing time tailoring materials."
        )

    if "mba" in jd_lower:
        warnings.append("⚠️ JD mentions MBA — verify whether it's required or 'preferred'.")
    if re.search(r"\bph\.?d\b", jd_lower):
        warnings.append("⚠️ JD mentions PhD — verify whether a graduate degree is strictly required.")

    for domain_kw, domain_label in NICHE_DOMAINS:
        if domain_kw in jd_lower and domain_kw not in resume_lower:
            warnings.append(
                f"⚠️ Role is in the {domain_label} domain, which isn't reflected on your resume. "
                f"You may need to explicitly address this in a cover letter."
            )
            break

    # Relocation
    if re.search(r"\brelocation\b|\bon[\s-]?site\b|\bin[\s-]?office\b", jd_lower):
        warnings.append(
            "⚠️ Role may require on-site presence or relocation — confirm your availability."
        )

    return warnings


def _recommend_changes(
    matched: List[str], missing: List[str],
    jd_lower: str, resume_lower: str, role_type: str,
) -> List[str]:
    changes = []
    missing_set = set(missing)

    # 1. Summary / profile section
    changes.append(
        "📝 Summary — Write a 2-3 sentence professional summary at the top of the resume. "
        "It must include the exact job title from the JD and 2-3 top skills the JD emphasises. "
        "Do not write 'Looking for an opportunity…' — state your value directly."
    )

    # 2. High-priority missing keywords (weight ≥ 2)
    priority_missing = [
        phrase for phrase, weight, _ in KEYWORD_CATALOG
        if phrase in missing_set and weight >= 2
    ][:7]
    if priority_missing:
        changes.append(
            f"🔑 Missing high-priority keywords — add these naturally into existing bullets "
            f"(only where truthful): {', '.join(priority_missing)}. "
            f"Do not add skills you do not actually have."
        )

    # 3. Quantification
    num_re = re.compile(r"\b\d[\d,]*\s*(%|percent|million|k\b|thousand|hours?|days?|weeks?|users?|records?|reports?|clients?)")
    hits = len(num_re.findall(resume_lower))
    if hits < 3:
        changes.append(
            "📊 Quantify achievements — fewer than 3 numbers found on your resume. "
            "Add metrics to at least 3 bullets. Examples: "
            "'reduced processing time by 35%', 'analysed datasets of 50k+ rows', "
            "'automated 4 weekly reports saving 6 hours/week'."
        )

    # 4. Weak verbs
    found_weak = [v for v in WEAK_VERBS if v in resume_lower]
    if found_weak:
        changes.append(
            f"✏️ Replace weak phrases ({', '.join(found_weak[:3])}) with strong action verbs: "
            f"Analysed / Built / Designed / Delivered / Implemented / Automated / Optimised / Led."
        )

    # 5. Role-specific advice
    if role_type == "Analyst":
        if "sql" in missing_set:
            changes.append(
                "💾 SQL is critical for this role — add any SQL usage, even SELECT/JOIN "
                "queries from coursework, self-study, or personal projects with a note."
            )
        if "excel" in missing_set and "google sheets" not in matched:
            changes.append(
                "📋 Mention Excel or Google Sheets explicitly (with specific features: "
                "pivot tables, VLOOKUP, charts) if you have used either for data work."
            )
        if ("tableau" in missing_set or "power bi" in missing_set) and "dashboard" in jd_lower:
            changes.append(
                "📈 JD mentions dashboards — if you've built any (even in Google Looker Studio "
                "or Excel), add it with a one-line description of what it showed."
            )

    elif role_type == "Software":
        if "git" in missing_set and "github" in missing_set:
            changes.append(
                "🔧 Git/GitHub is expected for every dev role. Add it to your skills even "
                "if you only use it for personal projects."
            )
        if "rest api" in missing_set and "api" in jd_lower:
            changes.append(
                "🔌 Mention any REST API work — consuming an API in a project, building "
                "a simple endpoint — even if it's from a course or personal project."
            )
        github_re = re.compile(r"github\.com/\w+")
        if not github_re.search(resume_lower):
            changes.append(
                "🔗 Add your GitHub profile URL so reviewers can verify your code. "
                "Make sure your pinned repos are public and have READMEs."
            )

    elif role_type == "Operations":
        if "process improvement" in missing_set:
            changes.append(
                "⚙️ Rephrase any workflow or efficiency win as 'process improvement' — "
                "e.g., 'Identified and resolved bottleneck in X, improving Y by Z%'."
            )
        if "cross-functional" in missing_set or "cross functional" in missing_set:
            changes.append(
                "🤝 Reframe any coordination with other teams as 'cross-functional collaboration' "
                "or 'stakeholder management'."
            )
        if "jira" in missing_set and "project management" in jd_lower:
            changes.append(
                "📋 If you've used any task tracking tool (Jira, Asana, Trello, even Notion), "
                "list it by name in your Skills section."
            )

    # 6. Skills section completeness
    skills_re = re.compile(r"(skills|technical skills|core competencies|tools)", re.IGNORECASE)
    if not skills_re.search(resume_lower):
        changes.append(
            "🛠 Add a dedicated Skills section. List tools exactly as written in the JD — "
            "ATS systems do exact-match. E.g., 'SQL' not 'database queries'."
        )
    else:
        changes.append(
            "🛠 Update your Skills section to include any matched JD tools verbatim — "
            "spelling and casing matter for ATS parsers."
        )

    # 7. Contact / header
    if not re.search(r"linkedin\.com/in/", resume_lower):
        changes.append(
            "🔗 Add your LinkedIn URL to the header (linkedin.com/in/yourname). "
            "Recruiters check LinkedIn immediately after reading a resume."
        )

    return changes


def _quality_check(resume_text: str, resume_lower: str) -> Dict:
    """
    Objective quality checks on the resume itself.
    Returns a dict of checks with pass/warn/fail status and messages.
    """
    checks = []

    # Length: 400–900 words is ideal for early-career
    word_count = len(resume_text.split())
    if word_count < 200:
        checks.append({"status": "fail",  "label": "Length",
                       "message": f"Resume is very short ({word_count} words). Aim for 400-700 words for 1 page."})
    elif word_count < 400:
        checks.append({"status": "warn",  "label": "Length",
                       "message": f"Resume is thin ({word_count} words). Add more detail to projects and roles."})
    elif word_count > 1000:
        checks.append({"status": "warn",  "label": "Length",
                       "message": f"Resume is long ({word_count} words). Trim to 1 page / 600-800 words for early-career."})
    else:
        checks.append({"status": "pass",  "label": "Length",
                       "message": f"{word_count} words — good length."})

    # Quantified bullets
    num_re = re.compile(r"\b\d[\d,]*\s*(%|percent|million|k\b|thousand|hours?|days?|weeks?|users?|records?|reports?|clients?)")
    num_hits = len(num_re.findall(resume_lower))
    if num_hits == 0:
        checks.append({"status": "fail", "label": "Quantification",
                       "message": "No numbers found. Every position should have at least 1-2 quantified achievements."})
    elif num_hits < 3:
        checks.append({"status": "warn", "label": "Quantification",
                       "message": f"Only {num_hits} number(s) found. Add more metrics to strengthen impact."})
    else:
        checks.append({"status": "pass", "label": "Quantification",
                       "message": f"{num_hits} quantified points found — good."})

    # Action verbs
    strong_verbs = [
        "analysed","analyzed","built","created","designed","developed","delivered",
        "implemented","automated","optimised","optimized","reduced","increased",
        "led","managed","coordinated","launched","generated","improved","transformed",
        "established","streamlined","collaborated","presented","reported",
    ]
    verb_hits = sum(1 for v in strong_verbs if v in resume_lower)
    if verb_hits < 3:
        checks.append({"status": "warn", "label": "Action Verbs",
                       "message": "Fewer than 3 strong action verbs found. Lead every bullet with a power verb."})
    else:
        checks.append({"status": "pass", "label": "Action Verbs",
                       "message": f"{verb_hits} strong action verbs detected."})

    # Weak verbs check
    weak_found = [v for v in WEAK_VERBS if v in resume_lower]
    if weak_found:
        checks.append({"status": "warn", "label": "Weak Language",
                       "message": f"Weak phrases found: {', '.join(weak_found[:3])}. Replace with action verbs."})
    else:
        checks.append({"status": "pass", "label": "Weak Language",
                       "message": "No weak passive phrases detected."})

    # Contact info
    has_email   = bool(re.search(r"[\w.+-]+@[\w-]+\.\w+", resume_text))
    has_phone   = bool(re.search(r"[\+\(]?\d[\d\s\-\(\)]{7,}\d", resume_text))
    has_linkedin = "linkedin.com" in resume_lower
    has_github   = "github.com" in resume_lower

    if not has_email:
        checks.append({"status": "fail", "label": "Email", "message": "No email address found in resume."})
    else:
        checks.append({"status": "pass", "label": "Email", "message": "Email address present."})

    if not has_phone:
        checks.append({"status": "warn", "label": "Phone", "message": "No phone number found. Add one."})
    else:
        checks.append({"status": "pass", "label": "Phone", "message": "Phone number present."})

    if not has_linkedin:
        checks.append({"status": "warn", "label": "LinkedIn", "message": "No LinkedIn URL found. Add linkedin.com/in/yourname."})
    else:
        checks.append({"status": "pass", "label": "LinkedIn", "message": "LinkedIn URL present."})

    if not has_github:
        checks.append({"status": "warn", "label": "GitHub", "message": "No GitHub URL. Helpful for analyst/software roles."})
    else:
        checks.append({"status": "pass", "label": "GitHub", "message": "GitHub URL present."})

    # Education
    edu_re = re.compile(r"\b(bachelor|master|b\.?s\.?|m\.?s\.?|b\.?a\.?|m\.?b\.?a\.?|degree|university|college|graduated)\b", re.IGNORECASE)
    if not edu_re.search(resume_text):
        checks.append({"status": "warn", "label": "Education", "message": "No education section detected. Add it even if in progress."})
    else:
        checks.append({"status": "pass", "label": "Education", "message": "Education section detected."})

    # Summary/profile
    summary_re = re.compile(r"\b(summary|profile|objective|about me|professional summary)\b", re.IGNORECASE)
    if not summary_re.search(resume_text):
        checks.append({"status": "warn", "label": "Summary",
                       "message": "No Summary/Profile section. Add a 2-3 sentence pitch at the top — it's the first thing ATS and recruiters read."})
    else:
        checks.append({"status": "pass", "label": "Summary", "message": "Summary/Profile section present."})

    # Tally
    fails  = sum(1 for c in checks if c["status"] == "fail")
    warns  = sum(1 for c in checks if c["status"] == "warn")
    passes = sum(1 for c in checks if c["status"] == "pass")
    total  = len(checks)
    grade_score = round((passes + warns * 0.5) / total * 100) if total else 0

    return {
        "checks":      checks,
        "fails":       fails,
        "warns":       warns,
        "passes":      passes,
        "grade_score": grade_score,
        "grade":       _letter_grade(grade_score),
    }


def _letter_grade(score: int) -> str:
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"


def _tips(score: int, is_preferred: bool, hard_rejects: List[str]) -> List[str]:
    tips = []
    if hard_rejects:
        tips.append("🛑 This role has hard-reject flags. Do not apply until those are resolved.")
        return tips
    if score >= 70:
        tips.append("✅ Strong keyword match — focus your energy on quantifying achievements and tailoring your Summary.")
    elif score >= 45:
        tips.append("⚡ Moderate match — closing 4-5 keyword gaps will meaningfully improve ATS pass-through rate.")
    else:
        tips.append("🔴 Low keyword match — use the Resume Tailor before applying. Submitting as-is risks immediate rejection.")
    if is_preferred:
        tips.append("🌟 Role type aligns with your target profile — prioritise this application.")
    else:
        tips.append("🔎 Role is outside your primary target profile — apply only if the JD genuinely fits your background.")
    tips.append("📄 Filename: save as FirstLast_Resume_Company.pdf — not 'resume_final_v2.pdf'.")
    tips.append("🔗 Ensure your LinkedIn profile matches your resume dates and titles exactly.")
    return tips
