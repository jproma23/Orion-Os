#!/usr/bin/env bash
set -e
ruff check src tests
pytest tests/unit -q
echo "OK"
