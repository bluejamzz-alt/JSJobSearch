"""MyCareersFuture: portal filters, date-window paging, detail enrichment, portal links."""

from datetime import datetime, timedelta, timezone

from conftest import load_fixture

from jobsearch.models import Job
from jobsearch.pipeline import run
from jobsearch.sources.mycareersfuture import MyCareersFutureSource, apply_detail


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    """Records requests; serves search pages from a list and details from a dict."""

    def __init__(self, pages=None, details=None):
        self.pages = list(pages or [])
        self.details = details or {}
        self.posts = []
        self.gets = []

    def post(self, url, params=None, json=None, headers=None):
        self.posts.append({"url": url, "params": params, "json": json})
        page = (params or {}).get("page", 0)
        return FakeResponse(self.pages[page] if page < len(self.pages) else {"results": []})

    def get(self, url, headers=None):
        self.gets.append(url)
        uuid = url.rstrip("/").rsplit("/", 1)[-1]
        if uuid in self.details:
            return FakeResponse(self.details[uuid])
        return FakeResponse({}, status=404)


def _item(uuid, days_old, title="Business Development Manager", **extra):
    date = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y-%m-%d")
    item = {
        "uuid": uuid,
        "title": title,
        "postedCompany": {"name": f"Co {uuid}"},
        "salary": {"minimum": 4500, "maximum": 6500, "type": {"salaryType": "Monthly"}},
        "employmentTypes": [{"employmentType": "Full Time"}],
        "positionLevels": [{"position": "Manager"}],
        "categories": [{"category": "Sales / Retail"}],
        "flexibleWorkArrangements": [],
        "metadata": {"newPostingDate": date, "isHideSalary": False},
    }
    item.update(extra)
    return item


def _source(config, session, **overrides):
    settings = dict(config["sources"]["mycareersfuture"], delay_seconds=0, **overrides)
    return MyCareersFutureSource(settings, config["search"], session=session)


def test_search_body_uses_portal_filters(config):
    src = _source(config, FakeSession())
    body = src.search_body("account executive")
    assert body == {
        "search": "account executive",
        "sortBy": ["new_posting_date"],
        "salary": 4000,
        "positionLevels": ["Manager", "Senior Executive", "Executive", "Junior Executive"],
    }
    src = _source(config, FakeSession(), filters={"employment_types": ["Full Time"]})
    assert src.search_body("x") == {"search": "x", "sortBy": ["new_posting_date"], "employmentTypes": ["Full Time"]}


def test_search_pages_until_date_window(config, monkeypatch):
    monkeypatch.setattr(MyCareersFutureSource, "sleep", lambda self, s=None: None)
    page0 = {"results": [_item(f"a{i}", 1) for i in range(3)]}
    page1 = {"results": [_item("b0", 2), _item("b1", 20), _item("b2", 25)]}
    page2 = {"results": [_item("c0", 1)]}  # must never be requested
    session = FakeSession(pages=[page0, page1, page2])
    jobs = _source(config, session, page_size=3).search("q")
    assert [j.source_id for j in jobs] == ["a0", "a1", "a2", "b0"]
    assert [p["params"]["page"] for p in session.posts] == [0, 1]


def test_search_respects_max_results(config, monkeypatch):
    monkeypatch.setattr(MyCareersFutureSource, "sleep", lambda self, s=None: None)
    pages = [{"results": [_item(f"p{p}-{i}", 1) for i in range(3)]} for p in range(5)]
    jobs = _source(config, FakeSession(pages=pages), page_size=3, max_results=5).search("q")
    assert len(jobs) == 5


def test_live_shaped_search_and_detail(config):
    search = load_fixture("mcf_search_live.json")
    detail = load_fixture("mcf_detail_live.json")
    jobs = MyCareersFutureSource.parse(search)
    assert len(jobs) == 2
    job = jobs[0]
    assert job.description == ""  # the search API does not return descriptions
    assert job.url.startswith("https://www.mycareersfuture.gov.sg/job/")
    assert job.salary_min and job.seniority
    assert apply_detail(job, detail)
    assert len(job.description) > 200 and "<" not in job.description


def test_enrich_fills_descriptions_with_cap(config):
    jobs = [Job(source="mycareersfuture", source_id=f"u{i}", title="BD Manager", company="c", url="")
            for i in range(4)]
    jobs.append(Job(source="jobstreet", source_id="x", title="BD Manager", company="c", url=""))
    details = {f"u{i}": {"description": "<p>Fintech payments for <b>SME</b> merchants. Hybrid.</p>"}
               for i in range(4)}
    session = FakeSession(details=details)
    filled = _source(config, session, max_details=3, detail_workers=2).enrich(jobs)
    assert filled == 3 and len(session.gets) == 3
    assert jobs[0].description == "Fintech payments for SME merchants. Hybrid."
    assert jobs[0].work_arrangement == "hybrid"
    assert jobs[3].description == "" and jobs[4].description == ""


def test_enrich_disabled_and_failures(config):
    job = Job(source="mycareersfuture", source_id="missing", title="t", company="c", url="")
    assert _source(config, FakeSession(), fetch_details=False).enrich([job]) == 0
    assert _source(config, FakeSession()).enrich([job]) == 0  # 404 is skipped, not raised
    assert not apply_detail(job, {"description": ""})


def test_hidden_salary_and_telecommuting():
    item = _item("h", 1, flexibleWorkArrangements=[{"id": 2, "flexibleWorkArrangement": "Telecommuting"}])
    item["metadata"]["isHideSalary"] = True
    job = MyCareersFutureSource.parse({"results": [item]})[0]
    assert job.salary_min is None and job.salary_max is None
    assert job.salary_display() == "not stated"
    assert job.work_arrangement == "hybrid"


def test_portal_url(config):
    src = _source(config, FakeSession())
    assert src.portal_url("sales manager") == (
        "https://www.mycareersfuture.gov.sg/search?search=sales+manager&sortBy=new_posting_date&page=0&salary=4000"
    )


def test_pipeline_enriches_only_new_surviving_postings(tmp_path, config, monkeypatch):
    monkeypatch.setattr(MyCareersFutureSource, "sleep", lambda self, s=None: None)
    config["search"]["queries"] = ["q"]
    config["storage"]["seen_file"] = str(tmp_path / "seen.json")
    page = {"results": [_item("good", 1), _item("intern", 1, title="Business Development Intern")]}
    details = {"good": {"description": "Fintech payments company selling to SME merchants."},
               "intern": {"description": "Fintech internship"}}
    session = FakeSession(pages=[page], details=details)
    src = _source(config, session, page_size=100)

    result = run(config, sources=[src], send=False, write_reports=False)
    assert session.gets == ["https://api.mycareersfuture.gov.sg/v2/jobs/good"]  # intern excluded first
    [g] = result.reported
    assert g.tier == "A" and g.met_count == g.total_count  # industry keywords now found
    assert result.stats["portal_links"][0][0] == "MyCareersFuture: q"

    session.gets.clear()
    session.posts.clear()
    result = run(config, sources=[src], send=False, write_reports=False)
    assert result.reported == [] and session.gets == []  # already sent: no detail fetch
    assert result.stats["seen"] == 1
