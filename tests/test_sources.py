from datetime import datetime, timezone

from conftest import load_fixture

from jobsearch.grader import grade_job
from jobsearch.models import Job
from jobsearch.sources.adzuna import AdzunaSource
from jobsearch.sources.jobstreet import JobStreetSource, parse_detail_html
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


def test_jobstreet_detail_html():
    html = "<html><body><div data-automation='jobAdDetails'><p>Full <b>description</b></p></div></body></html>"
    assert parse_detail_html(html) == "Full description"
    assert parse_detail_html("<html></html>") == ""


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
