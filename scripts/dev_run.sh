#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
exec organizer dry-run --guild 0 --vault ./tests/fixtures/sample_vault