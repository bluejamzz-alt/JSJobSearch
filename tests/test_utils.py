from datetime import datetime, timezone

import pytest

from jobsearch.utils import (
    contains_keyword,
    html_to_text,
    infer_seniority,
    infer_work_arrangement,
    parse_datetime,
    parse_salary_text,
)


def test_html_to_text_strips_tags_and_entities():
    assert html_to_text("<p>Hello &amp; <b>world</b></p><ul><li>one</li></ul>") == "Hello & world\none"


@pytest.mark.parametrize(
    "text,kw,expected",
    [
        ("International Sales Manager", "intern", False),
        ("Sales Intern", "intern", True),
        ("BD Manager", "bd manager", True),
        ("Business Development Executive", "business development", True),
        ("SMEs matter", "sme", False),
        ("SMEs matter", "smes", True),
    ],
)
def test_contains_keyword_whole_word(text, kw, expected):
    assert contains_keyword(text, kw) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$4,000 – $6,500 per month", (4000, 6500, "monthly")),
        ("SGD 4.5k - 6k per month", (4500, 6000, "monthly")),
        ("SGD 60,000 - 80,000 p.a.", (60000, 80000, "annual")),
        ("$5,000", (5000, 5000, "monthly")),
        ("$72,000", (72000, 72000, "annual")),
        ("Competitive", (None, None, "")),
        ("", (None, None, "")),
    ],
)
def test_parse_salary_text(text, expected):
    assert parse_salary_text(text) == expected


def test_parse_datetime_formats():
    assert parse_datetime("2026-09-20") == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert parse_datetime("2026-09-22T02:10:00Z") == datetime(2026, 9, 22, 2, 10, tzinfo=timezone.utc)
    ago = parse_datetime("3 days ago")
    assert ago is not None and (datetime.now(timezone.utc) - ago).days == 3
    assert parse_datetime("") is None
    assert parse_datetime("nonsense") is None


@pytest.mark.parametrize(
    "title,reported,source,expected",
    [
        ("Business Development Manager", "", "", "manager"),
        ("Senior Sales Executive", "", "", "senior executive"),
        ("Account Executive", "", "", "executive"),
        ("Junior Sales Executive", "", "", "junior executive"),
        ("Regional Sales Director", "", "", "director"),
        ("VP Sales", "", "", "senior management"),
        ("Head of Partnerships", "", "", "senior management"),
        ("Sales Intern", "", "", "intern"),
        ("Merchant Acquisition Lead", "", "", "manager"),
        ("Sales Representative", "Senior Executive", "mycareersfuture", "senior executive"),
        ("Sales Rep", "Mid-Senior level", "linkedin", "executive"),  # LinkedIn label is uninformative
        ("Merchant Hunter", "Mid-Senior level", "linkedin", ""),
        ("Sales Rep", "Executive", "linkedin", "senior management"),
        ("Sales Rep", "Entry level", "linkedin", "junior executive"),
        ("Sales Rep", "", "", "executive"),
        ("Merchant Hunter", "", "", ""),
    ],
)
def test_infer_seniority(title, reported, source, expected):
    assert infer_seniority(title, reported, source) == expected


def test_infer_work_arrangement():
    assert infer_work_arrangement("Sales Manager", "This is a hybrid role") == "hybrid"
    assert infer_work_arrangement("Remote Sales Executive", "") == "remote"
    assert infer_work_arrangement("", "Work from home allowed") == "remote"
    assert infer_work_arrangement("", "On-site at our CBD office") == "on-site"
    assert infer_work_arrangement("", "nothing said") == ""
