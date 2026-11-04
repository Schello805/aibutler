#!/bin/sh
# Shared local/CI regression gate. No personal settings or paid model calls.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ -n "${BUTLER_TEST_PYTHON:-}" ]; then
    python_bin=$BUTLER_TEST_PYTHON
elif [ -x .venv/bin/python ]; then
    python_bin=.venv/bin/python
else
    python_bin=python3
fi
command -v node >/dev/null 2>&1 || { echo 'Node.js wird für die Frontend-Regressionstests benötigt.' >&2; exit 1; }
"$python_bin" -m pytest -q "$@"
node --test tests/*.cjs
"$python_bin" scripts/evaluate_quality.py
