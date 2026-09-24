"""Orchestrates one run: search -> de-duplicate -> grade -> filter -> report -> deliver."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .grader import apply_ai_blend, grade_all, sort_key, tier_at_least
from .models import GradedJob, Job
from .report import render_html, render_markdown, render_telegram
from .sources import build_sources
from .sources.base import BaseSource
from .storage import SeenStore
from .utils import normalize

log = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))


@dataclass
class RunResult:
    run_date: datetime
    reported: list[GradedJob]
    stats: dict
    markdown: str = ""
    html: str = ""
    report_paths: list[Path] = field(default_factory=list)
    email_sent: bool = False
    telegram_sent: bool = False


def collect(sources: list[BaseSource], queries: list[str]) -> tuple[list[Job], dict]:
    """Run every query against every source. Returns (jobs, per_source_counts)."""
    jobs: list[Job] = []
    per_source: Counter = Counter()
    for source in sources:
        for query in queries:
            found = source.safe_search(query)
            per_source[source.display_name] += len(found)
            jobs.extend(found)
    return jobs, dict(per_source)


def deduplicate(jobs: list[Job]) -> tuple[list[Job], int]:
    """Drop exact duplicates (same uid) and cross-board duplicates (same title + company)."""
    seen_uid: set[str] = set()
    seen_key: set[str] = set()
    out: list[Job] = []
    dropped = 0
    for j in jobs:
        key = f"{normalize(j.title)}|{normalize(j.company)}"
        if j.uid in seen_uid or (j.company.lower() != "undisclosed" and key in seen_key):
            dropped += 1
            continue
        seen_uid.add(j.uid)
        seen_key.add(key)
        out.append(j)
    return out, dropped


def run(config: dict, *, sources: list[BaseSource] | None = None, send: bool = True,
        include_seen: bool | None = None, write_reports: bool = True, update_seen: bool = True,
        store: SeenStore | None = None) -> RunResult:
    run_date = datetime.now(SGT)
    grading = config["grading"]
    delivery = config["delivery"]
    min_tier = grading.get("min_tier_to_report", "C")
    if include_seen is None:
        include_seen = bool(delivery.get("include_seen", False))

    sources = build_sources(config) if sources is None else sources
    if not sources:
        log.warning("No sources enabled in config")
    raw_jobs, per_source = collect(sources, config["search"]["queries"])
    jobs, duplicates = deduplicate(raw_jobs)
    log.info("Fetched %d postings, %d unique after de-duplication", len(raw_jobs), len(jobs))

    graded = grade_all(jobs, config["requirements"], grading)
    excluded = [g for g in graded if g.excluded]
    kept = [g for g in graded if not g.excluded and tier_at_least(g.tier, min_tier)]
    below = len(graded) - len(excluded) - len(kept)

    store = store or SeenStore(config["storage"]["seen_file"])
    already_seen = [g for g in kept if store.is_seen(g.job.uid)]
    if not include_seen:
        kept = [g for g in kept if not store.is_seen(g.job.uid)]

    ai_cfg = config.get("ai_grading") or {}
    if ai_cfg.get("enabled") and kept:
        from .ai_grader import grade_with_ai

        kept.sort(key=sort_key)
        grade_with_ai(kept, config)
        for g in kept:
            apply_ai_blend(g, ai_cfg.get("weight", 0.3), grading["tiers"], bool(ai_cfg.get("affects_tier")))
        kept = [g for g in kept if tier_at_least(g.tier, min_tier)]

    kept.sort(key=sort_key)
    stats = {
        "fetched": len(raw_jobs),
        "per_source": per_source,
        "duplicates": duplicates,
        "excluded": len(excluded),
        "below_tier": below,
        "seen": len(already_seen),
        "min_tier": min_tier,
        "tiers": dict(Counter(g.tier for g in kept)),
    }
    result = RunResult(run_date=run_date, reported=kept, stats=stats)
    result.markdown = render_markdown(kept, run_date, stats, grading["tiers"])
    result.html = render_html(kept, run_date, stats, grading["tiers"])

    if write_reports:
        result.report_paths = write_report_files(result, delivery.get("reports_dir", "reports"))

    if send:
        deliver(result, config)

    if update_seen and kept:
        for g in kept:
            store.mark(g)
        store.save()
        log.info("Seen store updated: %d postings remembered", len(store))
    return result


def write_report_files(result: RunResult, reports_dir: str) -> list[Path]:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = result.run_date.strftime("%Y-%m-%d")
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(result.markdown, encoding="utf-8")
    html_path.write_text(result.html, encoding="utf-8")
    latest = out_dir / "latest.md"
    latest.write_text(result.markdown, encoding="utf-8")
    log.info("Reports written: %s, %s", md_path, html_path)
    return [md_path, html_path, latest]


def deliver(result: RunResult, config: dict) -> None:
    delivery = config["delivery"]
    email_cfg = delivery.get("email") or {}
    kept = result.reported
    tier_counts = Counter(g.tier for g in kept)

    if email_cfg.get("enabled", True):
        if not kept and not email_cfg.get("send_when_empty", False):
            log.info("No new matches and send_when_empty is false; email skipped")
        else:
            from .notify.email import EmailConfigError, recipients, send_email

            subject = (email_cfg.get("subject") or "Job Digest {date}: {count} new matches").format(
                date=result.run_date.strftime("%d %b %Y"),
                count=len(kept),
                tier_a=tier_counts.get("A", 0),
                tier_b=tier_counts.get("B", 0),
                tier_c=tier_counts.get("C", 0),
            )
            try:
                send_email(
                    subject=subject,
                    html_body=result.html,
                    text_body=result.markdown,
                    to=recipients(email_cfg.get("to_env", "EMAIL_TO")),
                    attachments={f"job-digest-{result.run_date.strftime('%Y-%m-%d')}.md": result.markdown},
                )
                result.email_sent = True
            except EmailConfigError as exc:
                log.error("Email not sent: %s", exc)
            except Exception as exc:
                log.error("Email failed: %s", exc)

    tg_cfg = delivery.get("telegram") or {}
    if tg_cfg.get("enabled") and kept:
        from .notify.telegram import send_telegram

        text = render_telegram(kept, result.run_date, [str(t) for t in tg_cfg.get("tiers") or ["A"]])
        try:
            result.telegram_sent = send_telegram(text)
        except Exception as exc:
            log.error("Telegram failed: %s", exc)
