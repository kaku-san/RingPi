#!/usr/bin/env bash
# Local fork checks. Same pytest and browser commands as CI, without the
# disposable-runner SSH job and without any Pi or secret.
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-all}

leak() {
  python3 scripts/leak-check.py
}

backend() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    python -m pip install '.[test]'
    python -m pytest -q
  else
    if [ ! -x .venv/bin/python ]; then
      python3 -m venv .venv
    fi
    .venv/bin/python -m pip install '.[test]'
    .venv/bin/python -m pytest -q
  fi
}

browser() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    python -m venv .venv
    .venv/bin/python -m pip install -e .
    npm ci --ignore-scripts
    npx playwright install --with-deps chromium
    npm run test:browser
  else
    if [ ! -x .venv/bin/python ]; then
      python3 -m venv .venv
    fi
    .venv/bin/python -m pip install -e .
    npm ci --ignore-scripts
    npx playwright install chromium
    npm run test:browser
  fi
}

case "$mode" in
  all) leak; backend; browser ;;
  leak) leak ;;
  backend) backend ;;
  browser) browser ;;
  *) echo "Usage: bash scripts/test.sh [all|leak|backend|browser]" >&2; exit 2 ;;
esac
