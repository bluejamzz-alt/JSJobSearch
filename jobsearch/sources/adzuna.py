"""Adzuna job search API (free key from https://developer.adzuna.com/)."""

from __future__ import annotations

import logging
import os

from ..models import Job
from ..utils import html_to_text, infer_work_arrangement, parse_datetime
from .base import BaseSource

log = logging.getLogger(__name__)

ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaSource(BaseSource):
    name = "adzuna"
    display_name = "Adzuna"

    def search(self, query: str) -> list[Job]:
        app_id = os.environ.get(self.settings.get("app_id_env", "ADZUNA_APP_ID"), "")
        app_key = os.environ.get(self.settings.get("app_key_env", "ADZUNA_APP_KEY"), "")
        if not app_id or not app_key:
            log.warning("Adzuna enabled but ADZUNA_APP_ID / ADZUNA_APP_KEY not set; skipping")
            return []
        country = self.settings.get("country", "sg")
        page_size = int(self.settings.get("page_size", 50) or 50)
        max_pages = int(self.settings.get("max_pages", 1) or 1)
        jobs: list[Job] = []
        for page in range(1, max_pages + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what": query,
                "where": self.location,
                "results_per_page": page_size,
                "max_days_old": self.posted_within_days,
                "sort_by": "date",
                "content-type": "application/json",
            }
            resp = self.session.get(ENDPOINT.format(country=country, page=page), params=params)
            if resp.status_code != 200:
                log.warning("Adzuna HTTP %s for %r", resp.status_code, query)
                break
            batch = self.parse(resp.json())
            jobs.extend(batch)
            if len(batch) < page_size or len(jobs) >= self.max_results:
                break
        return self.dedupe(jobs)[: self.max_results]

    @staticmethod
    def parse(payload: dict) -> list[Job]:
        jobs: list[Job] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "")
            title = (item.get("title") or "").strip()
            if not jid or not title:
                continue
            description = html_to_text(item.get("description") or "")
            ct_time = (item.get("contract_time") or "").replace("_", " ")
            ct_type = item.get("contract_type") or ""
            emp = ", ".join(x for x in (ct_time, ct_type) if x)
            jobs.append(
                Job(
                    source="adzuna",
                    source_id=jid,
                    title=title,
                    company=((item.get("company") or {}).get("display_name") or "Undisclosed").strip(),
                    url=item.get("redirect_url") or "",
                    location=((item.get("location") or {}).get("display_name") or "Singapore"),
                    description=description,
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    salary_currency="SGD",
                    salary_period="annual",
                    employment_type=emp,
                    work_arrangement=infer_work_arrangement(title, description),
                    posted_at=parse_datetime(item.get("created")),
                    raw={"salary_is_predicted": item.get("salary_is_predicted")},
                )
            )
        return jobs
