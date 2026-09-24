"""Core data structures shared across sources, grader, report and delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Job:
    """One job posting, normalised across all sources.

    Salary is stored as reported plus a ``salary_period`` so the grader can
    convert to a monthly figure. Unknown fields stay empty / ``None``; the
    grader decides how to treat missing information per requirement.
    """

    source: str
    source_id: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = "SGD"
    salary_period: str = ""  # monthly | annual | hourly | daily | weekly | ""
    employment_type: str = ""
    seniority: str = ""  # as reported by the source, free text
    work_arrangement: str = ""  # on-site | hybrid | remote | ""
    posted_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def monthly_salary(self) -> tuple[Optional[float], Optional[float]]:
        """Return (min, max) converted to a monthly figure, or (None, None)."""
        factor = {
            "monthly": 1.0,
            "annual": 1.0 / 12.0,
            "yearly": 1.0 / 12.0,
            "weekly": 52.0 / 12.0,
            "daily": 22.0,
            "hourly": 22.0 * 8.0,
        }.get(self.salary_period or "monthly")
        if factor is None:
            return None, None
        lo = self.salary_min * factor if self.salary_min is not None else None
        hi = self.salary_max * factor if self.salary_max is not None else None
        return lo, hi

    def salary_display(self) -> str:
        if self.salary_min is None and self.salary_max is None:
            return "not stated"
        cur = self.salary_currency or ""
        per = {"monthly": "/mo", "annual": "/yr", "yearly": "/yr", "hourly": "/hr",
               "daily": "/day", "weekly": "/wk"}.get(self.salary_period, "")

        def fmt(v: Optional[float]) -> str:
            return f"{v:,.0f}" if v is not None else "?"

        if self.salary_min is not None and self.salary_max is not None and self.salary_min != self.salary_max:
            return f"{cur} {fmt(self.salary_min)}-{fmt(self.salary_max)}{per}".strip()
        return f"{cur} {fmt(self.salary_min if self.salary_min is not None else self.salary_max)}{per}".strip()


@dataclass
class CheckResult:
    """Outcome of one requirement applied to one job."""

    id: str
    description: str
    met: bool
    weight: float
    must_have: bool
    detail: str = ""


@dataclass
class GradedJob:
    job: Job
    checks: list[CheckResult]
    met_count: int
    total_count: int
    score_pct: float  # weighted, 0-100
    tier: str  # A | B | C | D
    must_have_failed: bool
    excluded: bool = False
    ai_score: Optional[int] = None
    ai_reason: str = ""
    final_score: float = 0.0

    @property
    def summary(self) -> str:
        return f"{self.met_count} of {self.total_count} requirements met ({self.score_pct:.0f}%)"

    @property
    def unmet(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.met]

    def gaps(self) -> str:
        return ", ".join(c.description for c in self.unmet) or "none"
