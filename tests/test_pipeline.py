from datetime import datetime, timedelta, timezone

from jobsearch.models import Job
from jobsearch.pipeline import deduplicate, run
from jobsearch.sources.base import BaseSource
from jobsearch.storage import SeenStore


def _job(source, sid, title, company="Acme Payments", **kw):
    base = dict(
        location="Singapore",
        description="Fintech payments for SME merchants",
        salary_min=4500, salary_max=6000, salary_period="monthly",
        employment_type="Full Time",
        posted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    base.update(kw)
    return Job(source=source, source_id=sid, title=title, company=company, url=f"https://x/{source}/{sid}", **base)


class FakeSource(BaseSource):
    name = "fake"
    display_name = "Fake"

    def __init__(self, jobs, fail=False):
        super().__init__({}, {}, session=None)
        self._jobs = jobs
        self._fail = fail

    def search(self, query):
        if self._fail:
            raise RuntimeError("board down")
        return list(self._jobs)


def test_deduplicate_across_boards():
    jobs = [
        _job("a", "1", "Sales Manager"),
        _job("b", "9", "sales manager", company="ACME PAYMENTS"),
        _job("a", "1", "Sales Manager"),
        _job("b", "10", "Sales Manager", company="Other Co"),
    ]
    unique, dropped = deduplicate(jobs)
    assert [j.uid for j in unique] == ["a:1", "b:10"] and dropped == 2


def test_run_end_to_end_without_network(tmp_path, config, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config["search"]["queries"] = ["q"]
    config["storage"]["seen_file"] = str(tmp_path / "seen.json")
    config["delivery"]["reports_dir"] = str(tmp_path / "reports")
    jobs = [
        _job("a", "1", "Business Development Manager"),
        _job("a", "2", "Sales Executive", salary_min=None, salary_max=None, description="office furniture",
             employment_type="Contract"),
        _job("a", "3", "Sales Intern"),
        _job("a", "4", "Regional Sales Director", description="widgets", salary_min=None, salary_max=None,
             employment_type="Contract"),
    ]
    sources = [FakeSource(jobs), FakeSource([], fail=True)]

    result = run(config, sources=sources, send=False)
    tiers = {g.job.source_id: g.tier for g in result.reported}
    # 1 = 10/10 A; 2 = 7/10 B (no industry, salary, employment); 3 excluded (intern); 4 excluded (title)
    assert tiers == {"1": "A", "2": "B"}
    assert result.stats["excluded"] == 2
    assert result.stats["fetched"] == 4
    assert result.stats["per_source"] == {"Fake": 4}
    assert (tmp_path / "reports" / "latest.md").exists()
    assert len(result.report_paths) == 3

    # second run: same postings are now "seen" and not reported again
    result2 = run(config, sources=sources, send=False)
    assert result2.reported == [] and result2.stats["seen"] == 2

    # unless explicitly included
    result3 = run(config, sources=sources, send=False, include_seen=True)
    assert len(result3.reported) == 2


def test_run_director_below_tier(tmp_path, config):
    config["search"]["queries"] = ["q"]
    config["storage"]["seen_file"] = str(tmp_path / "seen.json")
    job = _job("a", "4", "Regional Business Development Director", description="widgets", salary_min=None,
               salary_max=None, employment_type="Contract")
    result = run(config, sources=[FakeSource([job])], send=False, write_reports=False, update_seen=False)
    # title ok, industry no, excluded-terms ok, salary no, location ok, arrangement ok(unknown),
    # seniority no (director), employment no, freshness ok, company ok -> 6/10 = 60% -> Tier B
    assert [(g.tier, g.met_count) for g in result.reported] == [("B", 6)]
    assert result.stats["below_tier"] == 0
