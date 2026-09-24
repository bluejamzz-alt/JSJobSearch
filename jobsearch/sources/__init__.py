"""Source registry. Add a new board by subclassing BaseSource and registering it here."""

from __future__ import annotations

import logging

from .adzuna import AdzunaSource
from .base import BaseSource, make_session
from .jobstreet import JobStreetSource
from .jooble import JoobleSource
from .linkedin import LinkedInSource
from .mycareersfuture import MyCareersFutureSource

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[BaseSource]] = {
    MyCareersFutureSource.name: MyCareersFutureSource,
    JobStreetSource.name: JobStreetSource,
    LinkedInSource.name: LinkedInSource,
    AdzunaSource.name: AdzunaSource,
    JoobleSource.name: JoobleSource,
}


def build_sources(config: dict) -> list[BaseSource]:
    """Instantiate every enabled source, in config order (order = priority for de-duplication)."""
    session = make_session()
    sources: list[BaseSource] = []
    for name, settings in (config.get("sources") or {}).items():
        settings = settings or {}
        if not settings.get("enabled", False):
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            log.warning("Unknown source %r in config; skipping", name)
            continue
        sources.append(cls(settings, config.get("search", {}), session))
    return sources


__all__ = ["REGISTRY", "BaseSource", "build_sources"]
