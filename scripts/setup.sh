#!/usr/bin/env bash
# Local checkout setup, or the existing first Pi install (scripts/deploy-pi.sh).
# Later updates stay on scripts/update-pi.sh. This file stores no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."

# Timeouts already used by the wrapped path:
#   SSH ConnectTimeout=10 in scripts/deploy-pi.sh and scripts/update-pi.sh
#   button API timeout=3 in scripts/gpio_button.py
#   caption deadline DOORBELL_CAPTION_DEADLINE_SECONDS (default 30), including cold start
# Local pip uses PIP_DEFAULT_TIMEOUT, default 120, for this checkout only.

if [ "$#" -gt 0 ]; then
  echo "First Pi install via scripts/deploy-pi.sh. SSH ConnectTimeout is 10 seconds."
  echo "Enter SSH authentication only in your terminal. This wrapper does not read a key."
  exec bash scripts/deploy-pi.sh "$@"
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -e '.[test]'
cat <<'EOF'
Local checkout is ready.
  .venv/bin/python -m agent_doorbell
  bash scripts/test.sh

First install on a Pi (existing installer, includes the camera smoke check):
  bash scripts/setup.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS

Models on that same installer:
  bash scripts/deploy-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --models

Later source-only updates:
  bash scripts/update-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS FULL_COMMIT_SHA

Optional updater identity path, never the key text:
  DOORBELL_SSH_IDENTITY=OWNER_SSH_KEY bash scripts/update-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS FULL_COMMIT_SHA
EOF
