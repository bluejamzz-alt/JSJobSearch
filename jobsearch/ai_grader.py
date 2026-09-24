"""Optional AI fit scoring with Claude.

Only runs when ``ai_grading.enabled`` is true and ANTHROPIC_API_KEY is set.
Each shortlisted posting is sent with the requirement list and comes back with
a 0-100 fit score and a one-line reason, which the pipeline blends into the
final ranking (see grader.apply_ai_blend).
"""

from __future__ import annotations

import json
import logging
import os

from .models import GradedJob
from .utils import truncate

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 12000

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fit_score", "reason", "concerns"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You screen job postings for a candidate and rate how well each one fits.

Candidate profile:
{profile}

Requirements (all of them matter; the first ones matter most):
{requirements}

Rate fit_score from 0 to 100: 90+ means apply today, 70-89 strong fit, 50-69 worth a look,
below 50 poor fit. Judge from what the posting actually says. Missing information is neutral,
not negative. Write reason as one plain sentence a busy person can scan, and list concrete
concerns (for example unclear salary, agency posting, quota-heavy, seniority mismatch)."""


def _client():
    try:
        import anthropic
    except ImportError:
        log.warning("ai_grading.enabled is true but the 'anthropic' package is not installed; skipping")
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ai_grading.enabled is true but ANTHROPIC_API_KEY is not set; skipping")
        return None
    return anthropic.Anthropic()


def _requirements_text(requirements: list[dict]) -> str:
    lines = []
    for r in requirements:
        desc = r.get("description") or r.get("id") or r.get("type")
        values = r.get("values")
        extra = f" ({', '.join(str(v) for v in values)})" if values else ""
        if r.get("value") is not None:
            extra = f" ({r['value']})"
        flag = " [must-have]" if r.get("must_have") else ""
        lines.append(f"- {desc}{extra}{flag}")
    return "\n".join(lines)


def grade_with_ai(graded: list[GradedJob], config: dict) -> int:
    """Populate ai_score / ai_reason on the given jobs. Returns how many were graded."""
    settings = config.get("ai_grading") or {}
    client = _client()
    if client is None:
        return 0
    import anthropic

    model = settings.get("model", "claude-opus-5")
    effort = settings.get("effort", "low")
    limit = int(settings.get("max_jobs", 40) or 0)
    profile = config.get("profile") or {}
    system = SYSTEM_PROMPT.format(
        profile=profile.get("summary") or f"Name: {profile.get('name', 'the candidate')}",
        requirements=_requirements_text(config.get("requirements") or []),
    )

    done = 0
    for g in graded:
        if limit and done >= limit:
            break
        j = g.job
        description = j.description or ""
        if len(description) > MAX_DESCRIPTION_CHARS:
            log.info("Description for %s is long (%d chars); sending the first %d",
                     j.uid, len(description), MAX_DESCRIPTION_CHARS)
            description = truncate(description, MAX_DESCRIPTION_CHARS)
        posting = {
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "salary": j.salary_display(),
            "employment_type": j.employment_type,
            "seniority": j.seniority,
            "work_arrangement": j.work_arrangement,
            "source": j.source,
            "rule_based_summary": g.summary,
            "unmet_requirements": [c.description for c in g.unmet],
            "description": description,
        }
        user_msg = "Rate this posting.\n\n" + json.dumps(posting, ensure_ascii=False, indent=2)
        try:
            data = _ask(client, model, effort, system, user_msg)
        except anthropic.RateLimitError as exc:
            log.warning("AI grading rate limited; stopping early (%s)", exc)
            break
        except anthropic.APIStatusError as exc:
            log.warning("AI grading API error for %s: %s", j.uid, exc)
            continue
        except anthropic.APIConnectionError as exc:
            log.warning("AI grading connection error; stopping early (%s)", exc)
            break
        except Exception as exc:
            log.warning("AI grading failed for %s: %s", j.uid, exc)
            continue
        if data is None:
            continue
        g.ai_score = int(max(0, min(100, data.get("fit_score", 0))))
        reason = str(data.get("reason", "")).strip()
        concerns = [str(c) for c in data.get("concerns") or [] if str(c).strip()]
        g.ai_reason = reason + (f" Concerns: {'; '.join(concerns)}." if concerns else "")
        done += 1
    log.info("AI graded %d postings with %s", done, model)
    return done


def _ask(client, model: str, effort: str, system: str, user_msg: str) -> dict | None:
    """One structured-output request. Uses server-side refusal fallbacks when the SDK supports them."""
    kwargs = dict(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": FIT_SCHEMA}},
    )
    try:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    except TypeError:  # older SDK without the fallbacks parameter
        response = client.messages.create(**kwargs)
    if getattr(response, "stop_reason", None) == "refusal":
        log.warning("AI grading request was declined by the model")
        return None
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("AI grading returned non-JSON output: %s", text[:120])
        return None
