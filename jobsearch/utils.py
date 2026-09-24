"""Text, salary and date helpers used by sources and the grader."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """Strip HTML to readable plain text without needing a parser library."""
    if not raw:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", raw)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").lower()).strip()


def contains_keyword(text: str, keyword: str) -> bool:
    """Whole-word, case-insensitive match ("intern" must not match "international")."""
    if not text or not keyword:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(keyword.strip().lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def matching_keywords(text: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if contains_keyword(text, k)]


_NUM = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK])?")


def parse_salary_text(text: str) -> tuple[Optional[float], Optional[float], str]:
    """Parse strings like "$4,000 - $6,000 per month" or "SGD 60k-80k p.a.".

    Returns (min, max, period). Period is one of monthly/annual/hourly/daily/
    weekly or "" when it cannot be told.
    """
    if not text:
        return None, None, ""
    t = text.lower()
    period = ""
    if re.search(r"per\s*month|/\s*month|monthly|\bmth\b|\bp\.?m\.?\b|\bpcm\b", t):
        period = "monthly"
    elif re.search(r"per\s*(annum|year)|/\s*(year|yr)|annual|yearly|\bp\.?a\.?\b", t):
        period = "annual"
    elif re.search(r"per\s*hour|/\s*h(ou)?r|hourly", t):
        period = "hourly"
    elif re.search(r"per\s*day|/\s*day|daily", t):
        period = "daily"
    elif re.search(r"per\s*week|/\s*week|weekly", t):
        period = "weekly"

    values: list[float] = []
    for num, k in _NUM.findall(t):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if k:
            v *= 1000
        values.append(v)
    # Ignore stray small numbers (e.g. "5 days", "2 years experience").
    values = [v for v in values if v >= 100]
    if not values:
        return None, None, period
    lo, hi = min(values), max(values)
    if not period:
        # Guess from magnitude: SG monthly salaries are rarely above 30k.
        period = "annual" if hi > 30000 else "monthly"
    return lo, hi, period


def parse_datetime(value) -> Optional[datetime]:
    """Best-effort ISO/relative date parsing returning an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    iso = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%d/%m/%Y"):
        try:
            dt = datetime.fromisoformat(iso) if fmt is None else datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", s.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
            "month": timedelta(days=30 * n),
        }[unit]
        return datetime.now(timezone.utc) - delta
    return None


def infer_work_arrangement(*texts: str) -> str:
    """Return on-site / hybrid / remote / "" from free text."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return ""
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\b(fully\s+)?remote\b|work\s*from\s*home|\bwfh\b", blob):
        return "remote"
    if re.search(r"\bon[-\s]?site\b|\bin[-\s]?office\b", blob):
        return "on-site"
    return ""


# Canonical seniority levels, most senior first.
SENIORITY_LEVELS = [
    "senior management",
    "director",
    "senior manager",
    "manager",
    "senior executive",
    "executive",
    "junior executive",
    "fresh",
    "intern",
    "non-executive",
    "professional",
]

# Explicit level words in the title beat whatever the board reports.
_TITLE_SENIORITY_STRONG = [
    (r"\b(chief|cxo|ceo|coo|cfo|cro|cmo|vice[-\s]?president|vp|svp|evp|head of|general manager|managing director|country manager|president)\b", "senior management"),
    (r"\b(director)\b", "director"),
    (r"\b(senior manager|sr\.? manager|assistant vice president|avp)\b", "senior manager"),
    (r"\b(manager|mgr|team lead|team leader|lead)\b", "manager"),
    (r"\b(intern|internship|trainee)\b", "intern"),
    (r"\b(senior executive|sr\.? executive|senior .{0,25}executive|senior specialist|senior consultant|senior associate)\b", "senior executive"),
    (r"\b(junior|jr\.?|entry[-\s]level|fresh grad(uate)?s?|graduate)\b", "junior executive"),
    (r"\b(executive)\b", "executive"),
]
# Generic job words only used when the board says nothing about level.
_TITLE_SENIORITY_WEAK = [
    (r"\b(specialist|consultant|representative|rep|associate|officer|coordinator)\b", "executive"),
]

# Source-reported level -> canonical level.
_SOURCE_SENIORITY = {
    "senior management": "senior management",
    "middle management": "senior manager",
    "manager": "manager",
    "professional": "professional",
    "senior executive": "senior executive",
    "executive": "executive",
    "junior executive": "junior executive",
    "fresh/entry level": "fresh",
    "fresh / entry level": "fresh",
    "entry level": "junior executive",
    "non-executive": "non-executive",
    "internship": "intern",
    "director": "director",
    "associate": "executive",
    # LinkedIn's "Executive" means C-suite, not the SG "executive" grade.
    "linkedin:executive": "senior management",
    "linkedin:mid-senior level": "",
}


def infer_seniority(title: str, reported: str = "", source: str = "") -> str:
    """Canonical seniority: explicit title words, then the board's label, then generic title words."""
    t = (title or "").lower()
    for pattern, level in _TITLE_SENIORITY_STRONG:
        if re.search(pattern, t):
            return level
    r = (reported or "").strip().lower()
    if r:
        keyed = _SOURCE_SENIORITY.get(f"{source}:{r}")
        if keyed is not None:
            if keyed:
                return keyed
        elif r in _SOURCE_SENIORITY:
            return _SOURCE_SENIORITY[r]
        else:
            for level in SENIORITY_LEVELS:
                if level in r:
                    return level
    for pattern, level in _TITLE_SENIORITY_WEAK:
        if re.search(pattern, t):
            return level
    return ""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
