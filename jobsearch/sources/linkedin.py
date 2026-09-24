"""LinkedIn Jobs via the public (logged-out) job listing endpoints.

The list endpoint returns HTML fragments of 25 cards per page. Details
(description, seniority, employment type) need one extra request per job,
so they are capped by ``max_details`` and spaced by ``delay_seconds``.
"""

from __future__ import annotations

import logging
import re

from ..models import Job
from ..utils import infer_work_arrangement, parse_datetime, parse_salary_text
from .base import BaseSource

log = logging.getLogger(__name__)

LIST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


class LinkedInSource(BaseSource):
    name = "linkedin"
    display_name = "LinkedIn"

    def search(self, query: str) -> list[Job]:
        endpoint = self.settings.get("endpoint", LIST_ENDPOINT)
        max_pages = int(self.settings.get("max_pages", 2) or 2)
        jobs: list[Job] = []
        for page in range(max_pages):
            params = {
                "keywords": query,
                "location": self.location,
                "f_TPR": f"r{self.posted_within_days * 86400}",
                "sortBy": "DD",
                "start": page * 25,
            }
            resp = self.session.get(endpoint, params=params)
            if resp.status_code != 200:
                log.warning("LinkedIn HTTP %s for %r (page %d)", resp.status_code, query, page)
                break
            batch = self.parse_list(resp.text)
            if not batch:
                break
            jobs.extend(batch)
            if len(jobs) >= self.max_results:
                break
            self.sleep()
        jobs = self.dedupe(jobs)[: self.max_results]
        if self.settings.get("fetch_details", True):
            self._fill_details(jobs)
        return jobs

    def _fill_details(self, jobs: list[Job]) -> None:
        cap = int(self.settings.get("max_details", 40) or 0)
        for i, job in enumerate(jobs):
            if cap and i >= cap:
                break
            try:
                resp = self.session.get(DETAIL_ENDPOINT.format(id=job.source_id))
                if resp.status_code == 200:
                    apply_detail_html(job, resp.text)
                else:
                    log.debug("LinkedIn detail HTTP %s for %s", resp.status_code, job.uid)
            except Exception as exc:
                log.debug("LinkedIn detail fetch failed for %s: %s", job.uid, exc)
            self.sleep()

    @staticmethod
    def parse_list(page_html: str) -> list[Job]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page_html, "html.parser")
        jobs: list[Job] = []
        for card in soup.select("div.base-card, li > div[data-entity-urn]"):
            urn = card.get("data-entity-urn") or ""
            m = re.search(r"jobPosting:(\d+)", urn)
            link = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
            href = (link.get("href") if link else "") or ""
            if not m:
                m = re.search(r"/jobs/view/(?:.*?-)?(\d+)", href)
            if not m:
                continue
            jid = m.group(1)
            title_el = card.select_one(".base-search-card__title")
            company_el = card.select_one(".base-search-card__subtitle")
            loc_el = card.select_one(".job-search-card__location")
            time_el = card.select_one("time")
            salary_el = card.select_one(".job-search-card__salary-info")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                continue
            lo, hi, period = parse_salary_text(salary_el.get_text(" ", strip=True) if salary_el else "")
            posted = parse_datetime(time_el.get("datetime")) if time_el else None
            jobs.append(
                Job(
                    source="linkedin",
                    source_id=jid,
                    title=title,
                    company=company_el.get_text(" ", strip=True) if company_el else "Undisclosed",
                    url=href.split("?")[0] if href else f"https://www.linkedin.com/jobs/view/{jid}",
                    location=loc_el.get_text(" ", strip=True) if loc_el else "",
                    salary_min=lo,
                    salary_max=hi,
                    salary_period=period,
                    posted_at=posted,
                )
            )
        return jobs


def apply_detail_html(job: Job, page_html: str) -> None:
    """Fill description / seniority / employment type from a job detail fragment."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    if desc:
        job.description = desc.get_text("\n", strip=True)
    for li in soup.select(".description__job-criteria-item, li.description__job-criteria-item"):
        header = li.select_one(".description__job-criteria-subheader")
        value = li.select_one(".description__job-criteria-text")
        if not header or not value:
            continue
        h = header.get_text(" ", strip=True).lower()
        v = value.get_text(" ", strip=True)
        if "seniority" in h:
            job.seniority = v
        elif "employment" in h:
            job.employment_type = v
    salary_el = soup.select_one(".salary, .compensation__salary")
    if salary_el and job.salary_min is None and job.salary_max is None:
        job.salary_min, job.salary_max, job.salary_period = parse_salary_text(salary_el.get_text(" ", strip=True))
    if not job.work_arrangement:
        job.work_arrangement = infer_work_arrangement(job.title, job.description)
