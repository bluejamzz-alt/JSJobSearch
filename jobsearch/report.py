"""Render the digest as Markdown (for files / Telegram) and HTML (for email)."""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime
from typing import Iterable

from .grader import TIER_ORDER
from .models import GradedJob

TIER_LABEL = {
    "A": "Tier A – strong match",
    "B": "Tier B – good match",
    "C": "Tier C – partial match",
    "D": "Tier D – weak match",
}
TIER_COLOR = {"A": "#1a7f37", "B": "#0969da", "C": "#bf8700", "D": "#6e7781"}


def _by_tier(jobs: Iterable[GradedJob]) -> dict[str, list[GradedJob]]:
    out: dict[str, list[GradedJob]] = {t: [] for t in TIER_ORDER}
    for g in jobs:
        out.setdefault(g.tier, []).append(g)
    return out


def _posted(g: GradedJob) -> str:
    return g.job.posted_at.strftime("%d %b") if g.job.posted_at else "-"


def _level(g: GradedJob) -> str:
    for c in g.checks:
        if c.id == "seniority" and c.met and c.detail and "not stated" not in c.detail:
            return c.detail
    return g.job.seniority or "-"


def tier_thresholds_text(tiers: dict) -> str:
    return (f"Tier A ≥ {tiers.get('A', 80):.0f}%, Tier B ≥ {tiers.get('B', 60):.0f}%, "
            f"Tier C ≥ {tiers.get('C', 40):.0f}%")


def render_markdown(jobs: list[GradedJob], run_date: datetime, stats: dict, tiers: dict) -> str:
    counts = Counter(g.tier for g in jobs)
    lines = [f"# Job Digest – {run_date.strftime('%a %d %b %Y')} (SGT)", ""]
    lines.append(
        f"**{len(jobs)} new matches** · Tier A: {counts.get('A', 0)} · Tier B: {counts.get('B', 0)}"
        f" · Tier C: {counts.get('C', 0)}"
    )
    src = ", ".join(f"{k} {v}" for k, v in (stats.get("per_source") or {}).items())
    lines.append(
        f"Fetched {stats.get('fetched', 0)} postings ({src or 'no sources'}); "
        f"{stats.get('duplicates', 0)} duplicates, {stats.get('excluded', 0)} excluded by must-haves, "
        f"{stats.get('below_tier', 0)} below Tier {stats.get('min_tier', 'C')}, "
        f"{stats.get('seen', 0)} already sent earlier."
    )
    lines.append("")
    lines.append(f"Score = requirements met ÷ total. {tier_thresholds_text(tiers)}.")
    lines.append("")
    if not jobs:
        lines.append("_No new postings met the bar today._")
        return "\n".join(lines) + "\n"

    grouped = _by_tier(jobs)
    n = 0
    for tier in TIER_ORDER:
        group = grouped.get(tier) or []
        if not group:
            continue
        lines.append(f"## {TIER_LABEL[tier]} ({len(group)})")
        lines.append("")
        lines.append("| # | Score | Title | Company | Salary | Level | Source | Posted | Gaps |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for g in group:
            n += 1
            j = g.job
            title = f"[{_md(j.title)}]({j.url})" if j.url else _md(j.title)
            score = f"{g.met_count}/{g.total_count} ({g.score_pct:.0f}%)"
            if g.ai_score is not None:
                score += f" · AI {g.ai_score}"
            lines.append(
                f"| {n} | {score} | {title} | {_md(j.company)} | {_md(j.salary_display())} | "
                f"{_md(_level(g))} | {_md(source_label(j.source))} | {_posted(g)} | {_md(g.gaps())} |"
            )
        lines.append("")
    if any(g.ai_reason for g in jobs):
        lines.append("## AI notes")
        lines.append("")
        for g in jobs:
            if g.ai_reason:
                lines.append(f"- **{_md(g.job.title)}** ({_md(g.job.company)}): {_md(g.ai_reason)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_html(jobs: list[GradedJob], run_date: datetime, stats: dict, tiers: dict) -> str:
    counts = Counter(g.tier for g in jobs)
    e = html.escape
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Job Digest {e(run_date.strftime('%d %b %Y'))}</title>",
        "<style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2328;"
        "margin:0;padding:16px;background:#fff}h1{font-size:20px;margin:0 0 8px}h2{font-size:16px;"
        "margin:24px 0 8px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid"
        " #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f6f8fa}"
        ".badge{display:inline-block;padding:2px 8px;border-radius:12px;color:#fff;font-weight:600;font-size:12px}"
        ".muted{color:#57606a;font-size:13px}.gaps{color:#9a3412;font-size:12px}a{color:#0969da}"
        ".score{white-space:nowrap}</style></head><body>",
        f"<h1>Job Digest – {e(run_date.strftime('%a %d %b %Y'))} (SGT)</h1>",
        f"<p><strong>{len(jobs)} new matches</strong> · "
        f"<span class='badge' style='background:{TIER_COLOR['A']}'>A {counts.get('A', 0)}</span> "
        f"<span class='badge' style='background:{TIER_COLOR['B']}'>B {counts.get('B', 0)}</span> "
        f"<span class='badge' style='background:{TIER_COLOR['C']}'>C {counts.get('C', 0)}</span></p>",
    ]
    src = ", ".join(f"{e(k)} {v}" for k, v in (stats.get("per_source") or {}).items())
    parts.append(
        f"<p class='muted'>Fetched {stats.get('fetched', 0)} postings ({src or 'no sources'}); "
        f"{stats.get('duplicates', 0)} duplicates, {stats.get('excluded', 0)} excluded by must-haves, "
        f"{stats.get('below_tier', 0)} below Tier {e(str(stats.get('min_tier', 'C')))}, "
        f"{stats.get('seen', 0)} already sent earlier.<br>"
        f"Score = requirements met ÷ total. {e(tier_thresholds_text(tiers))}.</p>"
    )
    if not jobs:
        parts.append("<p><em>No new postings met the bar today.</em></p></body></html>")
        return "".join(parts)

    grouped = _by_tier(jobs)
    n = 0
    for tier in TIER_ORDER:
        group = grouped.get(tier) or []
        if not group:
            continue
        parts.append(
            f"<h2><span class='badge' style='background:{TIER_COLOR[tier]}'>{tier}</span> "
            f"{e(TIER_LABEL[tier])} ({len(group)})</h2>"
        )
        parts.append(
            "<table><thead><tr><th>#</th><th>Score</th><th>Title</th><th>Company</th><th>Salary</th>"
            "<th>Level</th><th>Source</th><th>Posted</th><th>Gaps</th></tr></thead><tbody>"
        )
        for g in group:
            n += 1
            j = g.job
            title = f"<a href='{e(j.url)}'>{e(j.title)}</a>" if j.url else e(j.title)
            score = f"{g.met_count}/{g.total_count} ({g.score_pct:.0f}%)"
            if g.ai_score is not None:
                score += f"<br><span class='muted'>AI {g.ai_score}</span>"
            ai = f"<br><span class='muted'>{e(g.ai_reason)}</span>" if g.ai_reason else ""
            parts.append(
                f"<tr><td>{n}</td><td class='score'>{score}</td><td>{title}{ai}</td><td>{e(j.company)}</td>"
                f"<td>{e(j.salary_display())}</td><td>{e(_level(g))}</td><td>{e(source_label(j.source))}</td>"
                f"<td>{e(_posted(g))}</td><td class='gaps'>{e(g.gaps())}</td></tr>"
            )
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts)


def render_telegram(jobs: list[GradedJob], run_date: datetime, tiers_wanted: list[str]) -> str:
    picked = [g for g in jobs if g.tier in tiers_wanted]
    if not picked:
        return ""
    lines = [f"Job Digest {run_date.strftime('%d %b')}: {len(picked)} new {'/'.join(tiers_wanted)} matches"]
    for g in picked[:20]:
        j = g.job
        lines.append(f"• [{g.tier}] {j.title} @ {j.company} ({g.met_count}/{g.total_count}) {j.url}")
    if len(picked) > 20:
        lines.append(f"…and {len(picked) - 20} more in the email digest.")
    return "\n".join(lines)


def source_label(name: str) -> str:
    return {
        "mycareersfuture": "MyCareersFuture",
        "jobstreet": "JobStreet",
        "linkedin": "LinkedIn",
        "adzuna": "Adzuna",
        "jooble": "Jooble",
    }.get(name, name)


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")
