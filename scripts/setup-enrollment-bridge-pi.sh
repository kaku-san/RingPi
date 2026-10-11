#!/usr/bin/env bash
# Owner-reviewed setup only; no app archive, photo or secret leaves the Pi.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 4 ]; then
  echo 'Usage: bash scripts/setup-enrollment-bridge-pi.sh user@pi [trusted-alias] [--check|--enable] [--owner-approves-proposal-and-review-scopes]' >&2
  exit 2
fi
target=$1
case "$target" in -*) exit 2;; esac
mode=--check
if [ "$#" -ge 3 ]; then mode=$3; fi
approval=
case "$mode" in
  --check) if [ "$#" -eq 4 ]; then exit 2; fi;;
  --enable)
    if [ "$#" -ne 4 ] || [ "$4" != '--owner-approves-proposal-and-review-scopes' ]; then
      echo 'Enable requires the separately approved owner-scope flag.' >&2; exit 2
    fi
    approval=' --owner-approves-proposal-and-review-scopes';;
  *) exit 2;;
esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
options=(-o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ForwardAgent=no -o ClearAllForwardings=yes -o RequestTTY=no -o ConnectTimeout=10)
if [ "$#" -ge 2 ]; then options+=(-o "HostKeyAlias=$2"); fi
ssh "${options[@]}" "$target" 'exec "$HOME/agent-doorbell/.venv/bin/python" - '"$mode$approval" < "$script_dir/setup-enrollment-bridge.py"
