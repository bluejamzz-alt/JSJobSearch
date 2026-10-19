"""JobStreet Singapore via the JSON search endpoint used by its own website."""

from __future__ import annotations

import logging

from ..models import Job
from ..utils import html_to_text, infer_work_arrangement, parse_datetime, parse_salary_text
from .base import BaseSource

log = logging.getLogger(__name__)

SITE = "https://sg.jobstreet.com"
GRAPHQL_ENDPOINT = "https://sg.jobstreet.com/graphql"
# Job pages sit behind a Cloudflare challenge; the GraphQL endpoint the site itself uses does not.
DETAIL_QUERY = "query jobDetails($jobId: ID!) { jobDetails(id: $jobId) { job { id content(platform: WEB) } } }"


class JobStreetSource(BaseSource):
    name = "jobstreet"
    display_name = "JobStreet"

    def search(self, query: str) -> list[Job]:
        endpoint = self.settings.get("endpoint", f"{SITE}/api/jobsearch/v5/search")
        page_size = int(self.settings.get("page_size", 30) or 30)
        max_pages = int(self.settings.get("max_pages", 2) or 2)
        jobs: list[Job] = []
        for page in range(1, max_pages + 1):
            params = {
                "siteKey": "SG-Main",
                "sourcesystem": "houston",
                "keywords": query,
                "page": page,
                "pageSize": page_size,
                "locale": "en-SG",
                "sortmode": "ListedDate",
                "daterange": self.posted_within_days,
            }
            resp = self.session.get(endpoint, params=params, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                log.warning("JobStreet HTTP %s for %r", resp.status_code, query)
                break
            batch = self.parse(resp.json())
            jobs.extend(batch)
            if len(batch) < page_size or len(jobs) >= self.max_results:
                break
            self.sleep(0.5)
        return self.dedupe(jobs)[: self.max_results]

    def needs_detail(self, job: Job) -> bool:
        return not job.raw.get("full_description")

    def fill_detail(self, job: Job) -> bool:
        endpoint = self.settings.get("detail_endpoint", GRAPHQL_ENDPOINT)
        resp = self.session.post(
            endpoint,
            json={"operationName": "jobDetails", "query": DETAIL_QUERY, "variables": {"jobId": job.source_id}},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            log.debug("JobStreet detail HTTP %s for %s", resp.status_code, job.uid)
            return False
        return apply_detail(job, resp.json())

    @staticmethod
    def parse(payload: dict) -> list[Job]:
        items = payload.get("data") or payload.get("jobs") or []
        jobs: list[Job] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "")
            title = (item.get("title") or "").strip()
            if not jid or not title:
                continue
            adv = item.get("advertiser") or {}
            company = (adv.get("description") or item.get("companyName") or "").strip()
            locs = item.get("locations") or []
            location = ", ".join(l.get("label", "") for l in locs if isinstance(l, dict) and l.get("label"))
            location = location or item.get("location") or item.get("suburb") or "Singapore"
            if "singapore" not in location.lower():
                location = f"{location}, Singapore"
            salary_text = item.get("salary") or item.get("salaryLabel") or ""
            lo, hi, period = parse_salary_text(salary_text)
            work_types = item.get("workTypes") or ([item["workType"]] if item.get("workType") else [])
            arrangements = ((item.get("workArrangements") or {}).get("data") or [])
            arrangement = ""
            for a in arrangements:
                label = a.get("label") if isinstance(a, dict) else None
                text = (label.get("text") if isinstance(label, dict) else label) or ""
                text = text.lower()
                if "hybrid" in text:
                    arrangement = "hybrid"
                elif "remote" in text:
                    arrangement = "remote"
                elif "on-site" in text or "on site" in text or "office" in text:
                    arrangement = "on-site"
                if arrangement:
                    break
            bullets = [b for b in item.get("bulletPoints") or [] if isinstance(b, str)]
            description = "\n".join(filter(None, [html_to_text(item.get("teaser") or ""), *bullets]))
            url = item.get("jobUrl") or f"{SITE}/job/{jid}"
            if url.startswith("/"):
                url = SITE + url
            jobs.append(
                Job(
                    source="jobstreet",
                    source_id=jid,
                    title=title,
                    company=company or "Undisclosed",
                    url=url,
                    location=location,
                    description=description,
                    salary_min=lo,
                    salary_max=hi,
                    salary_currency="SGD",
                    salary_period=period,
                    employment_type=", ".join(w for w in work_types if isinstance(w, str)),
                    seniority="",
                    work_arrangement=arrangement or infer_work_arrangement(title, description),
                    posted_at=parse_datetime(item.get("listingDate")),
                    raw={"salary": salary_text},
                )
            )
        return jobs


def apply_detail(job: Job, payload: dict) -> bool:
    """Replace the search teaser with the full ad text from a GraphQL jobDetails response."""
    try:
        content = payload["data"]["jobDetails"]["job"]["content"]
    except (KeyError, TypeError):
        return False
    text = html_to_text(content or "")
    if not text:
        return False
    job.description = text
    job.raw["full_description"] = True
    job.work_arrangement = job.work_arrangement or infer_work_arrangement(job.title, text)
    return True
