"""Base class and HTTP session shared by all job sources."""

from __future__ import annotations

import logging
import time
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import Job

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def make_session(timeout: float = 30.0) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-SG,en;q=0.9"})
    session.request = _with_timeout(session.request, timeout)  # type: ignore[method-assign]
    return session


def _with_timeout(request_fn, timeout: float):
    def wrapped(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request_fn(method, url, **kwargs)

    return wrapped


class BaseSource:
    """A job board. Subclasses implement ``search`` and a pure ``parse``.

    ``settings`` is this source's block from config.yaml; ``search_cfg`` is
    the top-level ``search`` block (location, posted_within_days, ...).
    """

    name = "base"
    display_name = "Base"

    def __init__(self, settings: dict, search_cfg: dict, session: requests.Session | None = None):
        self.settings = settings or {}
        self.search_cfg = search_cfg or {}
        self.session = session or make_session()

    # -- to implement -------------------------------------------------------
    def search(self, query: str) -> list[Job]:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------
    @property
    def max_results(self) -> int:
        return int(self.search_cfg.get("max_results_per_query", 60) or 60)

    @property
    def location(self) -> str:
        return str(self.search_cfg.get("location", "Singapore") or "Singapore")

    @property
    def posted_within_days(self) -> int:
        return int(self.search_cfg.get("posted_within_days", 7) or 7)

    def safe_search(self, query: str) -> list[Job]:
        """Run ``search`` and never raise: a broken board must not stop the run."""
        try:
            jobs = self.search(query)
            log.info("%s: %d results for %r", self.display_name, len(jobs), query)
            return jobs
        except Exception as exc:
            log.error("%s: search for %r failed: %s", self.display_name, query, exc)
            return []

    def sleep(self, seconds: float | None = None) -> None:
        delay = float(self.settings.get("delay_seconds", 1.0) if seconds is None else seconds)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def dedupe(jobs: Iterable[Job]) -> list[Job]:
        seen: set[str] = set()
        out: list[Job] = []
        for j in jobs:
            if j.uid in seen:
                continue
            seen.add(j.uid)
            out.append(j)
        return out
