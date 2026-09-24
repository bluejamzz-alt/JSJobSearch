"""Jooble job aggregator API (free key from https://jooble.org/api/about)."""

from __future__ import annotations

import logging
import os

from ..models import Job
from ..utils import html_to_text, infer_work_arrangement, parse_datetime, parse_salary_text
from .base import BaseSource

log = logging.getLogger(__name__)

ENDPOINT = "https://jooble.org/api/{key}"


class JoobleSource(BaseSource):
    name = "jooble"
    display_name = "Jooble"

    def search(self, query: str) -> list[Job]:
        key = os.environ.get(self.settings.get("api_key_env", "JOOBLE_API_KEY"), "")
        if not key:
            log.warning("Jooble enabled but JOOBLE_API_KEY not set; skipping")
            return []
        max_pages = int(self.settings.get("max_pages", 1) or 1)
        jobs: list[Job] = []
        for page in range(1, max_pages + 1):
            body = {"keywords": query, "location": self.location, "page": page}
            resp = self.session.post(ENDPOINT.format(key=key), json=body)
            if resp.status_code != 200:
                log.warning("Jooble HTTP %s for %r", resp.status_code, query)
                break
            batch = self.parse(resp.json())
            jobs.extend(batch)
            if not batch or len(jobs) >= self.max_results:
                break
        return self.dedupe(jobs)[: self.max_results]

    @staticmethod
    def parse(payload: dict) -> list[Job]:
        jobs: list[Job] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "")
            title = (item.get("title") or "").strip()
            if not jid or not title:
                continue
            description = html_to_text(item.get("snippet") or "")
            lo, hi, period = parse_salary_text(str(item.get("salary") or ""))
            jobs.append(
                Job(
                    source="jooble",
                    source_id=jid,
                    title=title,
                    company=(item.get("company") or "Undisclosed").strip(),
                    url=item.get("link") or "",
                    location=item.get("location") or "Singapore",
                    description=description,
                    salary_min=lo,
                    salary_max=hi,
                    salary_currency="SGD",
                    salary_period=period,
                    employment_type=item.get("type") or "",
                    work_arrangement=infer_work_arrangement(title, description),
                    posted_at=parse_datetime(item.get("updated")),
                )
            )
        return jobs
