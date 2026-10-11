#!/usr/bin/env bash
# Send only the reviewed setup script to the existing Pi Python; no source redeploy.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo 'Usage: bash scripts/captions-pi.sh user@pi-host [trusted-host-key-alias] [--check|--install|--enable]' >&2
  exit 2
fi
target=$1
case "$target" in -*) exit 2;; esac
mode=--check
if [ "$#" -eq 3 ]; then mode=$3; fi
case "$mode" in --check|--install|--enable) ;; *) echo 'Invalid caption stage' >&2; exit 2;; esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ssh_options=(-o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ForwardAgent=no -o ClearAllForwardings=yes -o RequestTTY=no -o ConnectTimeout=10)
if [ "$#" -ge 2 ]; then ssh_options+=(-o "HostKeyAlias=$2"); fi
ssh "${ssh_options[@]}" "$target" 'set -eu
cd "$HOME/agent-doorbell"
exec "$HOME/agent-doorbell/.venv/bin/python" - '"$mode" < "$script_dir/caption-setup.py"
