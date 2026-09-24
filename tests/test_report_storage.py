from datetime import datetime

from jobsearch.grader import grade_job
from jobsearch.report import render_html, render_markdown, render_telegram
from jobsearch.storage import SeenStore


def _graded(config, good_job):
    return grade_job(good_job, config["requirements"], config["grading"])


def test_markdown_report_has_tiers_links_and_score(config, good_job):
    g = _graded(config, good_job)
    stats = {"fetched": 10, "per_source": {"MyCareersFuture": 10}, "duplicates": 1, "excluded": 2,
             "below_tier": 3, "seen": 0, "min_tier": "C"}
    md = render_markdown([g], datetime(2026, 9, 24, 8, 0), stats, config["grading"]["tiers"])
    assert "# Job Digest – Thu 24 Sep 2026" in md
    assert "**1 new matches** · Tier A: 1" in md
    assert "## Tier A – strong match (1)" in md
    assert "[Business Development Manager](https://example.com/1)" in md
    assert "| 10/10 (100%) |" in md
    assert "SGD 4,500-6,000/mo" in md


def test_html_report_escapes_and_lists_gaps(config, good_job):
    good_job.title = "BD Manager <Payments> & Co"
    good_job.salary_min = good_job.salary_max = None
    g = _graded(config, good_job)
    html = render_html([g], datetime(2026, 9, 24), {}, config["grading"]["tiers"])
    assert "BD Manager &lt;Payments&gt; &amp; Co" in html
    assert "Salary at least SGD 4,000 / month" in html  # the gap is named
    assert "href='https://example.com/1'" in html


def test_empty_report(config):
    md = render_markdown([], datetime(2026, 9, 24), {}, config["grading"]["tiers"])
    assert "No new postings met the bar today" in md
    html = render_html([], datetime(2026, 9, 24), {}, config["grading"]["tiers"])
    assert "No new postings met the bar today" in html


def test_telegram_text(config, good_job):
    g = _graded(config, good_job)
    text = render_telegram([g], datetime(2026, 9, 24), ["A"])
    assert text.startswith("Job Digest 24 Sep: 1 new A matches")
    assert "https://example.com/1" in text
    assert render_telegram([g], datetime(2026, 9, 24), ["B"]) == ""


def test_seen_store_roundtrip(tmp_path, config, good_job):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    assert not store.is_seen(good_job.uid)
    store.mark(_graded(config, good_job))
    store.save()
    again = SeenStore(path)
    assert again.is_seen(good_job.uid) and len(again) == 1
    again.reset()
    assert len(again) == 0


def test_seen_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json", encoding="utf-8")
    store = SeenStore(path)
    assert len(store) == 0
