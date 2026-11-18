"""Command line entry points for local development."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benefitiq.db import init_db, wait_for_db
from benefitiq.ingestion.loaders import IngestionError
from benefitiq.ingestion.service import ingest_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benefitiq")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("wait-db", help="Wait until Postgres accepts connections")
    sub.add_parser("init-db", help="Create extensions and tables")

    ingest = sub.add_parser("ingest", help="Ingest a manifest of local source files")
    ingest.add_argument("--manifest", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "wait-db":
            wait_for_db()
        elif args.command == "init-db":
            init_db()
        elif args.command == "ingest":
            counts = ingest_manifest(args.manifest)
            print(f"inserted={counts['inserted']} skipped={counts['skipped']}")
        else:
            parser.error(f"unknown command {args.command}")
    except IngestionError as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
