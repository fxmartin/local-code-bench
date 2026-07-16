#!/usr/bin/env python3
"""Backfill one or more legacy JSONL files with verified engine versions."""

from __future__ import annotations

import argparse
from pathlib import Path

from local_code_bench.backfill import backfill_jsonl
from local_code_bench.engine_provenance import EngineProvenance


def _version(value: str) -> tuple[str, str]:
    component, separator, version = value.partition("=")
    if not separator or not component.strip() or not version.strip():
        raise argparse.ArgumentTypeError("version must use NAME=VERSION")
    return component.strip(), version.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument(
        "--version",
        action="append",
        required=True,
        type=_version,
        metavar="NAME=VERSION",
    )
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    versions = dict(args.version)
    provenance = EngineProvenance(
        name=args.engine,
        versions=versions,
        capture_method="manual-backfill",
    )
    for path in args.paths:
        changed = backfill_jsonl(path, provenance, backup_dir=args.backup_dir)
        print(f"{path}: {changed} records updated")


if __name__ == "__main__":
    main()
