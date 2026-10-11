#!/usr/bin/env bash
# One SSH login. Only tracked application source is transferred; no media or credentials.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "Usage: bash scripts/deploy-pi.sh user@pi-host [existing-host-key-alias] [--models]" >&2
  exit 2
fi
target=$1
case "$target" in -*) echo 'Invalid SSH destination' >&2; exit 2;; esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$script_dir/.."
git rev-parse --verify HEAD >/dev/null
ssh_options=(-o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ForwardAgent=no -o ClearAllForwardings=yes -o RequestTTY=no -o ConnectTimeout=10)
if [ "$#" -ge 2 ]; then ssh_options+=(-o "HostKeyAlias=$2"); fi
install_models=0
if [ "$#" -eq 3 ]; then
  if [ "$3" != '--models' ]; then echo 'Only --models is supported as the third argument' >&2; exit 2; fi
  install_models=1
fi
remote_script=$(cat <<'REMOTE'
set -eu
app_dir="$HOME/agent-doorbell"
if [ -e "$app_dir" ] && [ ! -f "$app_dir/.agent-doorbell-managed" ]; then
  echo 'Refusing to overwrite an existing unmanaged ~/agent-doorbell directory.' >&2
  exit 3
fi
umask 077
mkdir -p "$app_dir"
tar -xf - -C "$app_dir"
touch "$app_dir/.agent-doorbell-managed"
python3 -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install --disable-pip-version-check "$app_dir"
if "$app_dir/.venv/bin/python" -m pip install --disable-pip-version-check "${app_dir}[person]"; then
  echo 'Optional CPU person detector installed.'
else
  echo 'Optional person detector unavailable; the local core remains installed.'
fi
if [ "${DOORBELL_INSTALL_LOCAL_MODELS:-0}" = 1 ]; then
  "$app_dir/.venv/bin/python" "$app_dir/scripts/install-local-models.py"
fi
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/agent-doorbell"
install -m 600 "$app_dir/deploy/agent-doorbell.service" "$HOME/.config/systemd/user/agent-doorbell.service"
systemctl --user daemon-reload
systemctl --user enable agent-doorbell.service
systemctl --user restart agent-doorbell.service
"$app_dir/.venv/bin/python" "$app_dir/scripts/hardware_smoke.py" --capture
echo 'Agent Doorbell is installed in its own directory, environment, port, and user service.'
echo 'Open http://<Pi LAN address>:8877 on your local network.'
echo 'Service: systemctl --user status agent-doorbell'
echo 'No SSH keys, other local service settings, firewall rules, or boot-login policies were changed.'
REMOTE
)
echo "Deploying the committed app to $target. Enter SSH authentication only in your Terminal."
git archive HEAD | ssh "${ssh_options[@]}" "$target" "DOORBELL_INSTALL_LOCAL_MODELS=$install_models
$remote_script"
