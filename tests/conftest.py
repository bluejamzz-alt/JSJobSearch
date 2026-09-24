import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobsearch.config import load_config
from jobsearch.models import Job

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def load_fixture(name: str):
    p = FIXTURES / name
    if name.endswith(".json"):
        return json.loads(p.read_text(encoding="utf-8"))
    return p.read_text(encoding="utf-8")


@pytest.fixture
def config():
    return load_config(ROOT / "config.yaml")


@pytest.fixture
def good_job():
    return Job(
        source="test",
        source_id="1",
        title="Business Development Manager",
        company="Acme Payments",
        url="https://example.com/1",
        location="Singapore",
        description="Fintech company selling payments to SME merchants. Hybrid arrangement.",
        salary_min=4500,
        salary_max=6000,
        salary_period="monthly",
        employment_type="Full Time",
        seniority="Manager",
        posted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
