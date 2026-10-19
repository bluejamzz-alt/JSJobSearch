"""MyCareersFuture (Singapore government job portal) via its public API.

Search uses the same filters as the MCF website (minimum salary, position
levels, employment types), pages newest-first until postings fall outside
``search.posted_within_days``, then ``enrich`` opens each new posting's detail
record to get the full job description (the search API does not return it).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ..models import Job
from ..utils import html_to_text, infer_work_arrangement, parse_datetime, slugify
from .base import BaseSource

log = logging.getLogger(__name__)

SITE = "https://www.mycareersfuture.gov.sg"
SEARCH_ENDPOINT = "https://api.mycareersfuture.gov.sg/v2/search"
DETAIL_ENDPOINT = "https://api.mycareersfuture.gov.sg/v2/jobs/{uuid}"

# MCF "flexible work arrangement" labels that tell us where the work happens.
_FWA_ARRANGEMENT = {"telecommuting": "hybrid"}


class MyCareersFutureSource(BaseSource):
    name = "mycareersfuture"
    display_name = "MyCareersFuture"
    fetch_details_default = True  # search results never include descriptions

    # -- search -------------------------------------------------------------
    @property
    def filters(self) -> dict:
        return self.settings.get("filters") or {}

    def search_body(self, query: str) -> dict:
        body: dict = {"search": query, "sortBy": ["new_posting_date"]}
        f = self.filters
        salary = f.get("salary_min", self.settings.get("min_salary"))
        if salary:
            body["salary"] = int(salary)
        if f.get("position_levels"):
            body["positionLevels"] = [str(v) for v in f["position_levels"]]
        if f.get("employment_types"):
            body["employmentTypes"] = [str(v) for v in f["employment_types"]]
        return body

    def search(self, query: str) -> list[Job]:
        endpoint = self.settings.get("endpoint", SEARCH_ENDPOINT)
        page_size = int(self.settings.get("page_size", 100) or 100)
        body = self.search_body(query)
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.posted_within_days + 1)

        jobs: list[Job] = []
        page = 0
        while len(jobs) < self.max_results:
            resp = self.session.post(
                endpoint,
                params={"limit": page_size, "page": page},
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if resp.status_code != 200:
                log.warning("MyCareersFuture HTTP %s for %r (page %d)", resp.status_code, query, page)
                break
            batch = self.parse(resp.json())
            fresh = [j for j in batch if j.posted_at is None or j.posted_at >= cutoff]
            jobs.extend(fresh)
            # Results are newest first: stop once a page reaches past the date window.
            if len(batch) < page_size or len(fresh) < len(batch):
                break
            page += 1
            self.sleep(0.5)
        return self.dedupe(jobs)[: self.max_results]

    # -- details ------------------------------------------------------------
    def needs_detail(self, job: Job) -> bool:
        return not job.description.strip()

    def fill_detail(self, job: Job) -> bool:
        url = self.settings.get("detail_endpoint", DETAIL_ENDPOINT).format(uuid=job.source_id)
        resp = self.session.get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            log.debug("MyCareersFuture detail HTTP %s for %s", resp.status_code, job.uid)
            return False
        return apply_detail(job, resp.json())

    # -- portal link --------------------------------------------------------
    def portal_url(self, query: str) -> str:
        """The same search on the MyCareersFuture website, for opening by hand."""
        params = {"search": query, "sortBy": "new_posting_date", "page": 0}
        salary = self.filters.get("salary_min", self.settings.get("min_salary"))
        if salary:
            params["salary"] = int(salary)
        return f"{SITE}/search?{urlencode(params)}"

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def parse(payload: dict) -> list[Job]:
        results = payload.get("results") or payload.get("jobs") or []
        jobs: list[Job] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            job = _job_from_item(item)
            if job is not None:
                jobs.append(job)
        return jobs


def _job_from_item(item: dict) -> Job | None:
    uuid = str(item.get("uuid") or item.get("id") or "")
    title = (item.get("title") or "").strip()
    if not uuid or not title:
        return None
    company = ((item.get("postedCompany") or {}).get("name")
               or (item.get("hiringCompany") or {}).get("name") or "").strip()
    meta = item.get("metadata") or {}
    url = meta.get("jobDetailsUrl") or _build_url(item, uuid, title, company)
    salary = item.get("salary") or {}
    salary_type = ((salary.get("type") or {}).get("salaryType") or "Monthly").lower()
    period = "annual" if "ann" in salary_type or "year" in salary_type else "monthly"
    lo, hi = _num(salary.get("minimum")), _num(salary.get("maximum"))
    if meta.get("isHideSalary"):
        lo = hi = None
    emp_types = [e.get("employmentType", "") for e in item.get("employmentTypes") or [] if isinstance(e, dict)]
    levels = [p.get("position", "") for p in item.get("positionLevels") or [] if isinstance(p, dict)]
    description = html_to_text(item.get("description") or "")
    posted = parse_datetime(meta.get("newPostingDate") or meta.get("originalPostingDate"))
    return Job(
        source="mycareersfuture",
        source_id=uuid,
        title=title,
        company=company or "Undisclosed",
        url=url,
        location="Singapore",
        description=description,
        salary_min=lo,
        salary_max=hi,
        salary_currency="SGD",
        salary_period=period,
        employment_type=", ".join(t for t in emp_types if t),
        seniority=", ".join(l for l in levels if l),
        work_arrangement=infer_work_arrangement(title, description) or _fwa_arrangement(item),
        posted_at=posted,
        raw={"jobPostId": meta.get("jobPostId"), "categories": item.get("categories")},
    )


def apply_detail(job: Job, detail: dict) -> bool:
    """Copy the description (and anything else the search result lacked) onto ``job``."""
    if not isinstance(detail, dict):
        return False
    text = html_to_text(detail.get("description") or "")
    if not text:
        return False
    job.description = text
    job.work_arrangement = infer_work_arrangement(job.title, text) or job.work_arrangement or _fwa_arrangement(detail)
    if not job.employment_type:
        types = [e.get("employmentType", "") for e in detail.get("employmentTypes") or [] if isinstance(e, dict)]
        job.employment_type = ", ".join(t for t in types if t)
    return True


def _fwa_arrangement(item: dict) -> str:
    for f in item.get("flexibleWorkArrangements") or []:
        label = (f.get("flexibleWorkArrangement") if isinstance(f, dict) else str(f)) or ""
        mapped = _FWA_ARRANGEMENT.get(label.strip().lower())
        if mapped:
            return mapped
    return ""


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _build_url(item: dict, uuid: str, title: str, company: str) -> str:
    cats = item.get("categories") or []
    cat = (cats[0].get("category") if cats and isinstance(cats[0], dict) else "") or "job"
    return f"{SITE}/job/{slugify(cat)}/{slugify(title)}-{slugify(company)}-{uuid}"
