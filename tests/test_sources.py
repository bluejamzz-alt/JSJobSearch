from datetime import datetime, timezone

from conftest import load_fixture

from jobsearch.grader import grade_job
from jobsearch.models import Job
from jobsearch.sources.adzuna import AdzunaSource
from jobsearch.sources.jobstreet import JobStreetSource, apply_detail as js_apply_detail
from jobsearch.sources.jooble import JoobleSource
from jobsearch.sources.linkedin import LinkedInSource, apply_detail_html
from jobsearch.sources.mycareersfuture import MyCareersFutureSource


def test_mcf_parse(config):
    jobs = MyCareersFutureSource.parse(load_fixture("mcf.json"))
    assert len(jobs) == 2
    j = jobs[0]
    assert j.uid == "mycareersfuture:0f1a2b3c4d5e6f708192a3b4c5d6e7f8"
    assert j.company == "ACME PAYMENTS PTE. LTD."
    assert j.url.startswith("https://www.mycareersfuture.gov.sg/job/sales/")
    assert (j.salary_min, j.salary_max, j.salary_period) == (4500, 7000, "monthly")
    assert j.employment_type == "Full Time, Permanent"
    assert j.seniority == "Manager"
    assert j.work_arrangement == "hybrid"
    assert "fintech" in j.description and "<" not in j.description
    assert j.posted_at == datetime(2026, 9, 20, tzinfo=timezone.utc)
    # fallback URL when jobDetailsUrl is absent
    assert jobs[1].url == ("https://www.mycareersfuture.gov.sg/job/sales-retail/"
                           "sales-intern-some-startup-ffffffffffffffffffffffffffffffff")
    g = grade_job(j, config["requirements"], config["grading"])
    assert g.tier == "A"
    assert grade_job(jobs[1], config["requirements"], config["grading"]).excluded


def test_jobstreet_parse(config):
    jobs = JobStreetSource.parse(load_fixture("jobstreet.json"))
    assert len(jobs) == 1
    j = jobs[0]
    assert j.uid == "jobstreet:80123456"
    assert j.url == "https://sg.jobstreet.com/job/80123456"
    assert j.company == "Global Pay Singapore"
    assert j.location == "Central Region, Singapore"
    assert (j.salary_min, j.salary_max, j.salary_period) == (4000, 6500, "monthly")
    assert j.work_arrangement == "hybrid"
    assert j.employment_type == "Full time"
    assert "F&B" in j.description and "Uncapped commission" in j.description
    g = grade_job(j, config["requirements"], config["grading"])
    assert g.tier == "A" and not g.excluded


def test_jobstreet_graphql_detail(config):
    job = JobStreetSource.parse(load_fixture("jobstreet.json"))[0]
    payload = load_fixture("jobstreet_detail_live.json")
    assert js_apply_detail(job, payload)
    assert job.raw["full_description"] is True
    assert len(job.description) > 200 and "<" not in job.description
    assert not js_apply_detail(job, {"data": {"jobDetails": None}})
    assert not js_apply_detail(job, {"errors": [{"message": "bad"}]})


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class _GraphQLSession:
    def __init__(self, contents):
        self.contents, self.posts = contents, []

    def post(self, url, json=None, headers=None, **kw):
        self.posts.append((url, json))
        job_id = json["variables"]["jobId"]
        if job_id not in self.contents:
            return _Resp({}, status=500)
        return _Resp({"data": {"jobDetails": {"job": {"id": job_id, "content": self.contents[job_id]}}}})


def test_jobstreet_enrich_uses_graphql_and_skips_filled(config):
    jobs = [Job(source="jobstreet", source_id=str(i), title="Sales Executive", company="c", url="",
                description="teaser") for i in range(3)]
    jobs[2].raw["full_description"] = True  # already has the full text
    session = _GraphQLSession({"0": "<p>We sell <b>payment solutions</b> to SMEs.</p>"})
    src = JobStreetSource(dict(config["sources"]["jobstreet"]), config["search"], session=session)
    assert src.enrich(jobs) == 1  # job 1 fails (HTTP 500) and is skipped, not raised
    assert [p[1]["variables"]["jobId"] for p in session.posts] == ["0", "1"]
    assert session.posts[0][0] == "https://sg.jobstreet.com/graphql"
    assert jobs[0].description == "We sell payment solutions to SMEs."
    assert jobs[1].description == "teaser"
    g = grade_job(jobs[0], config["requirements"], config["grading"])
    assert {c.id: c for c in g.checks}["industry"].met


def test_jobstreet_enrich_off(config):
    job = Job(source="jobstreet", source_id="0", title="t", company="c", url="")
    settings = dict(config["sources"]["jobstreet"], fetch_details=False)
    assert JobStreetSource(settings, config["search"], session=_GraphQLSession({})).enrich([job]) == 0


def test_linkedin_list_and_detail(config):
    jobs = LinkedInSource.parse_list(load_fixture("linkedin_list.html"))
    assert [j.source_id for j in jobs] == ["4101234567", "4109999999"]
    j = jobs[0]
    assert j.title == "Sales Manager (Fintech)"
    assert j.company == "PayCo"
    assert j.url == "https://sg.linkedin.com/jobs/view/sales-manager-fintech-at-payco-4101234567"
    assert j.location == "Singapore, Singapore"
    assert j.posted_at == datetime(2026, 9, 21, tzinfo=timezone.utc)
    apply_detail_html(j, load_fixture("linkedin_detail.html"))
    assert "merchant acquiring" in j.description
    assert j.seniority == "Mid-Senior level"
    assert j.employment_type == "Full-time"
    assert j.work_arrangement == "hybrid"
    g = grade_job(j, config["requirements"], config["grading"])
    checks = {c.id: c for c in g.checks}
    assert checks["seniority"].met and checks["seniority"].detail == "manager"
    assert not checks["salary"].met  # LinkedIn rarely states salary
    assert g.tier == "A"
    director = grade_job(jobs[1], config["requirements"], config["grading"])
    assert {c.id: c for c in director.checks}["seniority"].detail == "level is director"


def test_adzuna_parse(config):
    jobs = AdzunaSource.parse(load_fixture("adzuna.json"))
    assert len(jobs) == 1
    j = jobs[0]
    assert j.uid == "adzuna:5001"
    assert j.salary_period == "annual" and j.monthly_salary() == (4500, 6000)
    assert j.employment_type == "full time, permanent"
    g = grade_job(j, config["requirements"], config["grading"])
    assert g.tier == "A"


def test_jooble_parse(config):
    jobs = JoobleSource.parse(load_fixture("jooble.json"))
    assert len(jobs) == 1
    j = jobs[0]
    assert j.uid == "jooble:9001"
    assert (j.salary_min, j.salary_max, j.salary_period) == (4500, 6000, "monthly")
    g = grade_job(j, config["requirements"], config["grading"])
    assert {c.id: c for c in g.checks}["seniority"].detail == "senior executive"
    assert g.tier == "A"


def test_parsers_ignore_garbage():
    assert MyCareersFutureSource.parse({"results": [None, {}, {"uuid": "x"}]}) == []
    assert JobStreetSource.parse({"data": ["bad", {"id": "1"}]}) == []
    assert AdzunaSource.parse({}) == []
    assert JoobleSource.parse({"jobs": [{"title": "no id"}]}) == []
    assert LinkedInSource.parse_list("<html></html>") == []


def test_salary_display():
    j = Job(source="t", source_id="1", title="t", company="c", url="", salary_min=4000, salary_max=6000,
            salary_period="monthly", salary_currency="SGD")
    assert j.salary_display() == "SGD 4,000-6,000/mo"
    j.salary_max = None
    assert j.salary_display() == "SGD 4,000/mo"
    j.salary_min = None
    assert j.salary_display() == "not stated"
