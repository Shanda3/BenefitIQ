"""Command line entry points for local development."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from benefitiq.db import init_db, wait_for_db
from benefitiq.ingestion.fetch import fetch_manifest
from benefitiq.ingestion.payer_fetch import fetch_payer_sources
from benefitiq.ingestion.loaders import IngestionError, get_loader
from benefitiq.ingestion.policies import approve_policy, ingest_policy_library
from benefitiq.ingestion.service import ingest_manifest, load_manifest, spec_from_entry
from benefitiq.ingestion.updates import check_updates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benefitiq")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("wait-db", help="Wait until Postgres accepts connections")
    sub.add_parser("init-db", help="Create extensions and tables")

    ingest = sub.add_parser("ingest", help="Ingest a manifest of local source files")
    ingest.add_argument("--manifest", required=True, type=Path)

    fetch = sub.add_parser("fetch-public", help="Download the public manifest into the gitignored cache")
    fetch.add_argument("--manifest", type=Path, default=Path("corpus/public/manifest.yaml"))

    sub.add_parser("fetch-payers", help="Download public Oklahoma payer documents into the gitignored cache")
    sub.add_parser("ingest-policies", help="Store eligible payer policies as versioned chunks")

    updates = sub.add_parser("check-updates", help="Re-fetch eligible policies and write a review report")
    updates.add_argument(
        "--report-only",
        action="store_true",
        help="Write the report without updating the database",
    )

    approve = sub.add_parser("approve-policy", help="Make a waiting policy version the current one")
    approve.add_argument("--source-id", required=True)

    count = sub.add_parser("count-public", help="Count rows in the public manifest without writing the database")
    count.add_argument("--manifest", type=Path, default=Path("corpus/public/manifest.yaml"))

    sub.add_parser(
        "load-pilot",
        help="Create tables and load the office, stub, and payer policy library",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "wait-db":
            wait_for_db()
        elif args.command == "init-db":
            init_db()
        elif args.command == "ingest":
            counts = ingest_manifest(args.manifest)
            print(f"inserted={counts['inserted']} skipped={counts['skipped']}")
        elif args.command == "fetch-public":
            outcomes = fetch_manifest(args.manifest)
            for source_id, outcome in outcomes.items():
                print(f"{source_id} {outcome}")
        elif args.command == "fetch-payers":
            outcomes = fetch_payer_sources()
            for source_id, outcome in outcomes.items():
                print(f"{source_id} {outcome}")
        elif args.command == "ingest-policies":
            totals = ingest_policy_library()
            print(
                "inserted={inserted} unchanged={unchanged} needs_review={needs_review} "
                "skipped={skipped} failed={failed} chunks={chunks}".format(**totals.as_dict())
            )
            for source_id, reason in totals.failures or []:
                print(f"failed {source_id} {reason}")
        elif args.command == "check-updates":
            rows = check_updates(write_db=not args.report_only)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row.result] = counts.get(row.result, 0) + 1
            print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
            print("report=reports/policy-updates.md")
        elif args.command == "approve-policy":
            approved = approve_policy(args.source_id)
            print(f"approved {approved}")
        elif args.command == "count-public":
            _print_public_counts(args.manifest)
        elif args.command == "load-pilot":
            return _load_pilot()
        else:
            parser.error(f"unknown command {args.command}")
    except IngestionError as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _load_pilot() -> int:
    """Migrations and embeddings for the pilot database. The URL is not printed."""
    if not os.environ.get("DATABASE_URL", "").strip():
        print("DATABASE_URL is required", file=sys.stderr)
        return 1
    from urllib.parse import urlparse

    from benefitiq.config import database_url

    parsed = urlparse(database_url().replace("postgresql+psycopg", "postgresql", 1))
    host = parsed.hostname or "unknown"
    name = parsed.path.lstrip("/") or "unknown"
    print(f"database={host}/{name}")
    wait_for_db()
    init_db()
    for manifest in (
        Path("corpus/stub/manifest.yaml"),
        Path("corpus/office/manifest.yaml"),
        Path("corpus/payers/manifest.yaml"),
    ):
        counts = ingest_manifest(manifest)
        print(f"{manifest} inserted={counts['inserted']} skipped={counts['skipped']}")
    totals = ingest_policy_library()
    print(
        "policies inserted={inserted} unchanged={unchanged} needs_review={needs_review} "
        "skipped={skipped} failed={failed} chunks={chunks}".format(**totals.as_dict())
    )
    for source_id, reason in totals.failures or []:
        print(f"failed {source_id} {reason}")
    return 0


def _print_public_counts(manifest_path: Path) -> None:
    fetch_manifest(manifest_path)
    base_dir, manifest = load_manifest(manifest_path)
    for entry in manifest.documents:
        file_path = (base_dir / entry.path).resolve()
        loaded = get_loader(entry.loader)(file_path, spec_from_entry(entry))
        print(
            f"{entry.source_id} rows={loaded.source_rows} "
            f"scanned={loaded.scanned_rows} chunks={len(loaded.chunks)} "
            f"codes={len(loaded.code_rows)}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
