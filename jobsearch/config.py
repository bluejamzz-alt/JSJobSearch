"""Configuration loading: config.yaml plus environment variables (.env supported)."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.yaml"

_DEFAULTS: dict[str, Any] = {
    "profile": {"name": "", "timezone": "Asia/Singapore"},
    "search": {
        "queries": [],
        "location": "Singapore",
        "posted_within_days": 7,
        "max_results_per_query": 60,
    },
    "sources": {},
    "requirements": [],
    "grading": {
        "tiers": {"A": 80, "B": 60, "C": 40},
        "min_tier_to_report": "C",
        "must_have_failure": "exclude",
    },
    "ai_grading": {
        "enabled": False,
        "model": "claude-opus-5",
        "effort": "low",
        "weight": 0.3,
        "affects_tier": False,
        "max_jobs": 40,
    },
    "delivery": {
        "reports_dir": "reports",
        "include_seen": False,
        "email": {
            "enabled": True,
            "to_env": "EMAIL_TO",
            "subject": "Job Digest {date}: {count} new matches ({tier_a} Tier A)",
            "send_when_empty": False,
        },
        "telegram": {"enabled": False, "tiers": ["A"]},
    },
    "storage": {"seen_file": "data/seen_jobs.json"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader; never overrides variables already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    load_dotenv()
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Config file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    cfg = _merge(_DEFAULTS, user_cfg)
    validate_config(cfg)
    return cfg


VALID_REQUIREMENT_TYPES = {
    "title_keywords",
    "description_keywords",
    "exclude_keywords",
    "salary_min",
    "location",
    "work_arrangement",
    "seniority",
    "employment_type",
    "posted_within_days",
    "company_exclude",
}


def validate_config(cfg: dict) -> None:
    problems: list[str] = []
    if not cfg["search"].get("queries"):
        problems.append("search.queries must list at least one query")
    if not cfg.get("requirements"):
        problems.append("requirements must list at least one requirement")
    seen_ids: set[str] = set()
    for i, req in enumerate(cfg.get("requirements") or []):
        rid = req.get("id") or f"#{i}"
        if req.get("type") not in VALID_REQUIREMENT_TYPES:
            problems.append(f"requirement {rid}: unknown type {req.get('type')!r}")
        if rid in seen_ids:
            problems.append(f"requirement id {rid!r} is duplicated")
        seen_ids.add(rid)
        if req.get("unknown", "unmet") not in ("met", "unmet"):
            problems.append(f"requirement {rid}: unknown must be 'met' or 'unmet'")
    tiers = cfg["grading"].get("tiers", {})
    if not all(k in tiers for k in ("A", "B", "C")):
        problems.append("grading.tiers must define A, B and C thresholds")
    elif not (tiers["A"] >= tiers["B"] >= tiers["C"]):
        problems.append("grading.tiers must satisfy A >= B >= C")
    if cfg["grading"].get("must_have_failure") not in ("exclude", "cap_c"):
        problems.append("grading.must_have_failure must be 'exclude' or 'cap_c'")
    if cfg["grading"].get("min_tier_to_report") not in ("A", "B", "C", "D"):
        problems.append("grading.min_tier_to_report must be A, B, C or D")
    if problems:
        raise ValueError("Invalid config:\n  - " + "\n  - ".join(problems))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default
