from datetime import datetime, timedelta, timezone

from jobsearch.grader import apply_ai_blend, grade_job, sort_key, tier_for
from jobsearch.models import Job


def _req_ids(graded):
    return {c.id: c for c in graded.checks}


def test_good_job_is_tier_a(config, good_job):
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert g.tier == "A"
    assert not g.excluded
    assert g.met_count == g.total_count == len(config["requirements"])
    assert g.summary == f"{g.total_count} of {g.total_count} requirements met (100%)"


def test_title_must_have_excludes(config, good_job):
    good_job.title = "Marketing Coordinator"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert g.must_have_failed and g.excluded and g.tier == "D"
    assert _req_ids(g)["title"].detail == "title is not a target role"


def test_intern_excluded_but_international_ok(config, good_job):
    good_job.title = "Business Development Intern"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert g.excluded
    good_job.title = "International Business Development Manager"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert not g.excluded


def test_cap_c_policy(config, good_job):
    good_job.title = "Business Development Intern"
    grading = dict(config["grading"], must_have_failure="cap_c")
    g = grade_job(good_job, config["requirements"], grading)
    assert not g.excluded and g.tier == "C"


def test_salary_unknown_is_unmet_but_not_fatal(config, good_job):
    good_job.salary_min = good_job.salary_max = None
    g = grade_job(good_job, config["requirements"], config["grading"])
    c = _req_ids(g)["salary"]
    assert not c.met and c.detail == "salary not stated"
    assert g.tier == "A"  # 9 of 10 = 90%
    assert g.met_count == g.total_count - 1


def test_salary_annual_converted_to_monthly(config, good_job):
    good_job.salary_min, good_job.salary_max, good_job.salary_period = 54000, 72000, "annual"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert _req_ids(g)["salary"].met  # 72000/12 = 6000 >= 4000
    good_job.salary_min, good_job.salary_max = 30000, 42000
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert not _req_ids(g)["salary"].met  # 3500/mo
    assert "below 4,000/mo" in _req_ids(g)["salary"].detail


def test_salary_compare_max_vs_min(config, good_job):
    good_job.salary_min, good_job.salary_max = 3500, 5000
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert _req_ids(g)["salary"].met
    reqs = [dict(r, compare="min") if r["id"] == "salary" else r for r in config["requirements"]]
    g = grade_job(good_job, reqs, config["grading"])
    assert not _req_ids(g)["salary"].met


def test_seniority_director_fails(config, good_job):
    good_job.title = "Director, Business Development"
    good_job.seniority = ""
    g = grade_job(good_job, config["requirements"], config["grading"])
    c = _req_ids(g)["seniority"]
    assert not c.met and c.detail == "level is director"
    assert g.tier == "A"  # 9/10 still A, not excluded


def test_seniority_unknown_counts_as_met(config, good_job):
    good_job.title = "Business Development Ninja"
    good_job.seniority = ""
    g = grade_job(good_job, config["requirements"], config["grading"])
    c = _req_ids(g)["seniority"]
    assert c.met and c.detail == "level not stated"


def test_industry_keywords_missing_lowers_score(config, good_job):
    good_job.description = "Sell office furniture to corporate clients."
    g = grade_job(good_job, config["requirements"], config["grading"])
    c = _req_ids(g)["industry"]
    assert not c.met
    assert g.score_pct == 90


def test_location_must_have(config, good_job):
    good_job.location = "Kuala Lumpur, Malaysia"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert g.excluded


def test_company_exclusion(config, good_job):
    reqs = [dict(r, values=["Acme"]) if r["id"] == "company" else r for r in config["requirements"]]
    g = grade_job(good_job, reqs, config["grading"])
    assert g.excluded and "excluded" in _req_ids(g)["company"].detail


def test_freshness(config, good_job):
    good_job.posted_at = datetime.now(timezone.utc) - timedelta(days=12)
    g = grade_job(good_job, config["requirements"], config["grading"])
    c = _req_ids(g)["freshness"]
    assert not c.met and c.detail == "posted 12d ago"


def test_work_arrangement_inferred(config, good_job):
    good_job.description = "Fully remote role selling payments to merchants"
    g = grade_job(good_job, config["requirements"], config["grading"])
    assert _req_ids(g)["arrangement"].detail == "remote"


def test_weights_change_percent(config, good_job):
    good_job.salary_min = good_job.salary_max = None
    reqs = [dict(r, weight=3) if r["id"] == "salary" else r for r in config["requirements"]]
    g = grade_job(good_job, reqs, config["grading"])
    # 9 met with weight 1 each; salary weight 3 unmet -> 9/12 = 75%
    assert g.score_pct == 75 and g.tier == "B"
    assert g.met_count == 9 and g.total_count == 10


def test_tier_thresholds():
    tiers = {"A": 80, "B": 60, "C": 40}
    assert tier_for(80, tiers) == "A"
    assert tier_for(79.9, tiers) == "B"
    assert tier_for(60, tiers) == "B"
    assert tier_for(40, tiers) == "C"
    assert tier_for(39, tiers) == "D"


def test_ai_blend_and_sort(config, good_job):
    g = grade_job(good_job, config["requirements"], config["grading"])
    g.ai_score = 50
    apply_ai_blend(g, 0.3, config["grading"]["tiers"], affects_tier=False)
    assert g.final_score == 85 and g.tier == "A"
    apply_ai_blend(g, 0.5, config["grading"]["tiers"], affects_tier=True)
    assert g.final_score == 75 and g.tier == "B"

    fresh = grade_job(good_job, config["requirements"], config["grading"])  # A, 100%
    other = grade_job(
        Job(source="t", source_id="2", title="Sales Executive", company="X", url="", location="Singapore"),
        config["requirements"], config["grading"],
    )
    assert other.tier == "A" and other.score_pct == 80
    ordered = sorted([other, g, fresh], key=sort_key)
    assert [x.final_score for x in ordered] == [100, 80, 75]  # A before B, higher score first
