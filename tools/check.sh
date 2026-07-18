#!/usr/bin/env bash
set -e
ruff check src motion_core tests
pytest tests/unit -q
echo "OK"
