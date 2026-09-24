"""Command line entry point: ``python -m jobsearch <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import __version__
from .config import DEFAULT_CONFIG_PATH, load_config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run
    from .storage import SeenStore

    config = load_config(args.config)
    store = SeenStore(config["storage"]["seen_file"])
    if args.reset_seen:
        store.reset()
        store.save()
        logging.getLogger(__name__).info("Seen store reset")
    dry = args.dry_run
    result = run(
        config,
        send=not (dry or args.no_email),
        include_seen=True if args.include_seen else None,
        write_reports=not dry,
        update_seen=not dry,
        store=store,
    )
    if args.print or dry:
        print(result.markdown)
    counts = result.stats.get("tiers", {})
    print(
        f"{len(result.reported)} new matches (A {counts.get('A', 0)}, B {counts.get('B', 0)}, "
        f"C {counts.get('C', 0)}); email {'sent' if result.email_sent else 'not sent'}",
        file=sys.stderr,
    )
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from .sources import build_sources

    print(f"Config OK: {args.config}")
    print(f"Queries ({len(config['search']['queries'])}): " + "; ".join(config["search"]["queries"]))
    print("Enabled sources: " + ", ".join(s.display_name for s in build_sources(config)))
    print(f"Requirements ({len(config['requirements'])}):")
    for r in config["requirements"]:
        flags = []
        if r.get("must_have"):
            flags.append("must-have")
        if r.get("unknown", "unmet") == "met":
            flags.append("unknown=met")
        print(f"  - {r.get('id')}: {r.get('description')}" + (f"  [{', '.join(flags)}]" if flags else ""))
    t = config["grading"]["tiers"]
    print(f"Tiers: A >= {t['A']}%, B >= {t['B']}%, C >= {t['C']}%; reporting Tier "
          f"{config['grading']['min_tier_to_report']} and above")
    return 0


def cmd_test_email(args: argparse.Namespace) -> int:
    from .notify.email import recipients, send_email

    config = load_config(args.config)
    to = recipients(config["delivery"]["email"].get("to_env", "EMAIL_TO"))
    now = datetime.now().strftime("%d %b %Y %H:%M")
    send_email(
        subject=f"JSJobSearch test email ({now})",
        html_body="<p>If you can read this, email delivery is configured correctly.</p>",
        text_body="If you can read this, email delivery is configured correctly.",
        to=to,
    )
    print(f"Test email sent to {', '.join(to)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobsearch", description="Automated job search with requirement grading")
    p.add_argument("--version", action="version", version=f"jobsearch {__version__}")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH, help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="search, grade and deliver the digest")
    r.add_argument("--dry-run", action="store_true",
                   help="search and grade only: print the digest, no email, no report files, no seen update")
    r.add_argument("--no-email", action="store_true", help="write reports but do not send")
    r.add_argument("--include-seen", action="store_true", help="include postings sent in earlier digests")
    r.add_argument("--reset-seen", action="store_true", help="forget all previously sent postings first")
    r.add_argument("--print", action="store_true", help="also print the markdown digest to stdout")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("check-config", help="validate config.yaml and show what will run")
    c.set_defaults(func=cmd_check_config)

    t = sub.add_parser("test-email", help="send a test email using the SMTP settings")
    t.set_defaults(func=cmd_test_email)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
