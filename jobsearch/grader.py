"""Rule-based grading: apply each configured requirement to a job and tier it.

Score = weighted requirements met / weighted total, displayed as
"7 of 10 requirements met (70%)". Tiers come from grading.tiers thresholds.
A failed must-have requirement either excludes the job or caps it at Tier C,
depending on grading.must_have_failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from .models import CheckResult, GradedJob, Job
from .utils import (
    contains_keyword,
    infer_seniority,
    infer_work_arrangement,
    matching_keywords,
    normalize,
)

log = logging.getLogger(__name__)

TIER_ORDER = ["A", "B", "C", "D"]


def _values(req: dict) -> list[str]:
    return [str(v) for v in (req.get("values") or []) if str(v).strip()]


def _unknown(req: dict, detail: str) -> tuple[bool, str]:
    return (req.get("unknown", "unmet") == "met"), detail


# --------------------------------------------------------------------------
# Individual checks. Each returns (met, detail).
# --------------------------------------------------------------------------

def check_title_keywords(job: Job, req: dict) -> tuple[bool, str]:
    hits = matching_keywords(job.title, _values(req))
    if hits:
        return True, f"title matches '{hits[0]}'"
    return False, "title is not a target role"


def check_description_keywords(job: Job, req: dict) -> tuple[bool, str]:
    text = f"{job.title}\n{job.description}"
    if not job.description.strip():
        return _unknown(req, "no description available")
    hits = matching_keywords(text, _values(req))
    need = int(req.get("min_matches", 1) or 1)
    if len(hits) >= need:
        return True, "mentions " + ", ".join(sorted(set(h.lower() for h in hits))[:5])
    return False, "no domain keywords found" if not hits else f"only {len(hits)} of {need} keywords"


def check_exclude_keywords(job: Job, req: dict) -> tuple[bool, str]:
    scope = req.get("scope", "title")
    text = job.title if scope == "title" else f"{job.title}\n{job.description}"
    hits = matching_keywords(text, _values(req))
    if hits:
        return False, f"contains excluded term '{hits[0]}'"
    return True, "no excluded terms"


def check_salary_min(job: Job, req: dict) -> tuple[bool, str]:
    lo, hi = job.monthly_salary()
    if lo is None and hi is None:
        return _unknown(req, "salary not stated")
    threshold = float(req.get("value", 0) or 0)
    compare = req.get("compare", "max")
    basis = (hi if hi is not None else lo) if compare == "max" else (lo if lo is not None else hi)
    shown = job.salary_display()
    if basis is not None and basis >= threshold:
        return True, f"{shown}"
    return False, f"{shown} is below {threshold:,.0f}/mo"


def check_location(job: Job, req: dict) -> tuple[bool, str]:
    loc = normalize(job.location)
    if not loc:
        return _unknown(req, "location not stated")
    for v in _values(req):
        if contains_keyword(loc, v):
            return True, job.location
    return False, f"location is {job.location}"


def check_work_arrangement(job: Job, req: dict) -> tuple[bool, str]:
    arrangement = job.work_arrangement or infer_work_arrangement(job.title, job.description)
    if not arrangement:
        return _unknown(req, "arrangement not stated")
    wanted = [normalize(v).replace("onsite", "on-site").replace("on site", "on-site") for v in _values(req)]
    if arrangement in wanted:
        return True, arrangement
    return False, f"arrangement is {arrangement}"


def check_seniority(job: Job, req: dict) -> tuple[bool, str]:
    level = infer_seniority(job.title, job.seniority, job.source)
    if not level:
        return _unknown(req, "level not stated")
    wanted = {normalize(v) for v in _values(req)}
    if level in wanted:
        return True, level
    # "manager" in the wanted list also accepts "senior manager" and vice versa
    # is deliberately NOT done: the user chose the exact levels.
    return False, f"level is {level}"


def check_employment_type(job: Job, req: dict) -> tuple[bool, str]:
    et = normalize(job.employment_type)
    if not et:
        return _unknown(req, "employment type not stated")
    for v in _values(req):
        if normalize(v).replace("-", " ") in et.replace("-", " "):
            return True, job.employment_type
    return False, f"type is {job.employment_type}"


def check_posted_within_days(job: Job, req: dict) -> tuple[bool, str]:
    if job.posted_at is None:
        return _unknown(req, "posting date not stated")
    days = int(req.get("value", 7) or 7)
    age = (datetime.now(timezone.utc) - job.posted_at).total_seconds() / 86400.0
    if age <= days + 0.999:
        return True, f"posted {max(int(age), 0)}d ago"
    return False, f"posted {int(age)}d ago"


def check_company_exclude(job: Job, req: dict) -> tuple[bool, str]:
    company = normalize(job.company)
    for v in _values(req):
        if normalize(v) and normalize(v) in company:
            return False, f"company '{job.company}' is excluded"
    return True, "company ok"


CHECKS: dict[str, Callable[[Job, dict], tuple[bool, str]]] = {
    "title_keywords": check_title_keywords,
    "description_keywords": check_description_keywords,
    "exclude_keywords": check_exclude_keywords,
    "salary_min": check_salary_min,
    "location": check_location,
    "work_arrangement": check_work_arrangement,
    "seniority": check_seniority,
    "employment_type": check_employment_type,
    "posted_within_days": check_posted_within_days,
    "company_exclude": check_company_exclude,
}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def tier_for(score_pct: float, tiers: dict) -> str:
    if score_pct >= float(tiers.get("A", 80)):
        return "A"
    if score_pct >= float(tiers.get("B", 60)):
        return "B"
    if score_pct >= float(tiers.get("C", 40)):
        return "C"
    return "D"


def tier_at_least(tier: str, minimum: str) -> bool:
    return TIER_ORDER.index(tier) <= TIER_ORDER.index(minimum)


def grade_job(job: Job, requirements: list[dict], grading: dict) -> GradedJob:
    checks: list[CheckResult] = []
    for req in requirements:
        fn = CHECKS.get(req.get("type", ""))
        if fn is None:
            log.warning("Skipping requirement %s: unknown type %s", req.get("id"), req.get("type"))
            continue
        try:
            met, detail = fn(job, req)
        except Exception as exc:  # a broken check must never kill the run
            log.exception("Requirement %s failed on %s: %s", req.get("id"), job.uid, exc)
            met, detail = False, f"check error: {exc}"
        checks.append(
            CheckResult(
                id=str(req.get("id") or req.get("type")),
                description=str(req.get("description") or req.get("id") or req.get("type")),
                met=bool(met),
                weight=float(req.get("weight", 1) or 0),
                must_have=bool(req.get("must_have", False)),
                detail=detail,
            )
        )

    total_w = sum(c.weight for c in checks)
    met_w = sum(c.weight for c in checks if c.met)
    score = (met_w / total_w * 100.0) if total_w else 0.0
    must_have_failed = any(c.must_have and not c.met for c in checks)

    tier = tier_for(score, grading.get("tiers", {}))
    excluded = False
    if must_have_failed:
        policy = grading.get("must_have_failure", "exclude")
        if policy == "exclude":
            excluded = True
            tier = "D"
        else:  # cap_c
            if tier in ("A", "B"):
                tier = "C"

    return GradedJob(
        job=job,
        checks=checks,
        met_count=sum(1 for c in checks if c.met),
        total_count=len(checks),
        score_pct=round(score, 1),
        tier=tier,
        must_have_failed=must_have_failed,
        excluded=excluded,
        final_score=round(score, 1),
    )


def grade_all(jobs: list[Job], requirements: list[dict], grading: dict) -> list[GradedJob]:
    return [grade_job(j, requirements, grading) for j in jobs]


def apply_ai_blend(graded: GradedJob, weight: float, tiers: dict, affects_tier: bool) -> None:
    """Blend an AI 0-100 fit score into final_score (and optionally the tier)."""
    if graded.ai_score is None:
        graded.final_score = graded.score_pct
        return
    w = max(0.0, min(1.0, float(weight)))
    graded.final_score = round((1 - w) * graded.score_pct + w * float(graded.ai_score), 1)
    if affects_tier and not graded.excluded:
        new_tier = tier_for(graded.final_score, tiers)
        if graded.must_have_failed and new_tier in ("A", "B"):
            new_tier = "C"
        graded.tier = new_tier


def sort_key(g: GradedJob):
    posted = g.job.posted_at.timestamp() if g.job.posted_at else 0.0
    return (TIER_ORDER.index(g.tier), -g.final_score, -posted, g.job.title.lower())
