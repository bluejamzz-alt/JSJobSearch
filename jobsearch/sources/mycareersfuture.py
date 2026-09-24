"""MyCareersFuture (Singapore government job portal) via its public search API."""

from __future__ import annotations

import logging

from ..models import Job
from ..utils import html_to_text, infer_work_arrangement, parse_datetime, slugify
from .base import BaseSource

log = logging.getLogger(__name__)

SITE = "https://www.mycareersfuture.gov.sg"


class MyCareersFutureSource(BaseSource):
    name = "mycareersfuture"
    display_name = "MyCareersFuture"

    def search(self, query: str) -> list[Job]:
        endpoint = self.settings.get("endpoint", "https://api.mycareersfuture.gov.sg/v2/search")
        page_size = int(self.settings.get("page_size", 100) or 100)
        max_pages = int(self.settings.get("max_pages", 2) or 2)
        body = {"search": query, "sortBy": ["new_posting_date"]}
        if self.settings.get("min_salary"):
            body["salary"] = int(self.settings["min_salary"])

        jobs: list[Job] = []
        for page in range(max_pages):
            resp = self.session.post(
                endpoint,
                params={"limit": page_size, "page": page},
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if resp.status_code != 200:
                log.warning("MyCareersFuture HTTP %s for %r", resp.status_code, query)
                break
            batch = self.parse(resp.json())
            jobs.extend(batch)
            if len(batch) < page_size or len(jobs) >= self.max_results:
                break
            self.sleep(0.5)
        return self.dedupe(jobs)[: self.max_results]

    @staticmethod
    def parse(payload: dict) -> list[Job]:
        results = payload.get("results") or payload.get("jobs") or []
        jobs: list[Job] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            uuid = str(item.get("uuid") or item.get("id") or "")
            title = (item.get("title") or "").strip()
            if not uuid or not title:
                continue
            company = ((item.get("postedCompany") or {}).get("name")
                       or (item.get("hiringCompany") or {}).get("name") or "").strip()
            meta = item.get("metadata") or {}
            url = meta.get("jobDetailsUrl") or _build_url(item, uuid, title, company)
            salary = item.get("salary") or {}
            salary_type = ((salary.get("type") or {}).get("salaryType") or "Monthly").lower()
            period = "annual" if "ann" in salary_type or "year" in salary_type else "monthly"
            emp_types = [e.get("employmentType", "") for e in item.get("employmentTypes") or [] if isinstance(e, dict)]
            levels = [p.get("position", "") for p in item.get("positionLevels") or [] if isinstance(p, dict)]
            description = html_to_text(item.get("description") or "")
            posted = parse_datetime(meta.get("newPostingDate") or meta.get("originalPostingDate"))
            jobs.append(
                Job(
                    source="mycareersfuture",
                    source_id=uuid,
                    title=title,
                    company=company or "Undisclosed",
                    url=url,
                    location="Singapore",
                    description=description,
                    salary_min=_num(salary.get("minimum")),
                    salary_max=_num(salary.get("maximum")),
                    salary_currency="SGD",
                    salary_period=period,
                    employment_type=", ".join(t for t in emp_types if t),
                    seniority=", ".join(l for l in levels if l),
                    work_arrangement=infer_work_arrangement(title, description),
                    posted_at=posted,
                    raw={"jobPostId": meta.get("jobPostId"), "categories": item.get("categories")},
                )
            )
        return jobs


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _build_url(item: dict, uuid: str, title: str, company: str) -> str:
    cats = item.get("categories") or []
    cat = (cats[0].get("category") if cats and isinstance(cats[0], dict) else "") or "job"
    return f"{SITE}/job/{slugify(cat)}/{slugify(title)}-{slugify(company)}-{uuid}"
