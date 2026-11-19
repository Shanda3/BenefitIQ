"""Command line entry points for local development."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benefitiq.db import init_db, wait_for_db
from benefitiq.ingestion.fetch import fetch_manifest
from benefitiq.ingestion.payer_fetch import fetch_payer_sources
from benefitiq.ingestion.loaders import IngestionError, get_loader
from benefitiq.ingestion.service import ingest_manifest, load_manifest, spec_from_entry


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

    count = sub.add_parser("count-public", help="Count rows in the public manifest without writing the database")
    count.add_argument("--manifest", type=Path, default=Path("corpus/public/manifest.yaml"))

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
        elif args.command == "count-public":
            _print_public_counts(args.manifest)
        else:
            parser.error(f"unknown command {args.command}")
    except IngestionError as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
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
