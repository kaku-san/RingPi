#!/usr/bin/env bash
# Update an existing managed Pi app from one committed release. No hardware test.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo 'Usage: bash scripts/update-pi.sh user@pi-host [trusted-host-key-alias] [40-character-commit]' >&2
  exit 2
fi
target=$1
case "$target" in -*) echo 'Invalid SSH destination' >&2; exit 2;; esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$script_dir/.."
release=$(git rev-parse --verify HEAD)
if [ "$#" -eq 3 ]; then release=$3; fi
if ! [[ "$release" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'The release must be a full committed SHA.' >&2
  exit 2
fi
git cat-file -e "$release^{commit}"
version=$(git show "$release:pyproject.toml" | python3 -c 'import sys,tomllib; print(tomllib.loads(sys.stdin.read())["project"]["version"])')
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then exit 2; fi
ssh_options=(-o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ForwardAgent=no -o ClearAllForwardings=yes -o RequestTTY=no -o ConnectTimeout=10)
if [ "$#" -ge 2 ]; then ssh_options+=(-o "HostKeyAlias=$2"); fi
if [ -n "${DOORBELL_SSH_IDENTITY:-}" ]; then
  case "$DOORBELL_SSH_IDENTITY" in
    *[!A-Za-z0-9_./~-]*) echo 'Invalid SSH identity path' >&2; exit 2;;
  esac
  ssh_options+=(-o BatchMode=yes -o IdentitiesOnly=yes -i "$DOORBELL_SSH_IDENTITY")
fi
remote_script=$(cat <<'REMOTE_UPDATE'
set -eu
umask 077
app_dir="$HOME/agent-doorbell"
if [ ! -f "$app_dir/.agent-doorbell-managed" ]; then
  echo 'Refusing to update an unmanaged application.' >&2
  exit 3
fi
tar -xf - -C "$app_dir"
"$app_dir/.venv/bin/python" -m pip install --disable-pip-version-check --no-deps "$app_dir"
release_sha=${DOORBELL_RELEASE_SHA:-}
if ! [[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'The release must be a full committed SHA.' >&2
  exit 2
fi
printf '%s\n' "$release_sha" > "$app_dir/.release-sha"
chmod 0600 "$app_dir/.release-sha"
systemctl --user restart agent-doorbell.service
"$app_dir/.venv/bin/python" - <<'READ_ONLY_VERIFY'
import json, os, time, urllib.request
http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with http.open('http://127.0.0.1:8877' + path, timeout=3) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise SystemExit('Status metadata exceeded the bounded read.')
        return json.loads(raw)
for attempt in range(15):
    try:
        status = get('/api/status')
        if status.get('version') == os.environ['DOORBELL_UPDATE_VERSION']:
            break
    except OSError:
        pass
    time.sleep(1)
else:
    raise SystemExit('Updated service did not report the expected version; no hardware test was triggered.')
print('UPDATE_READY', json.dumps({'version': status['version'],
      'local_models_available': status.get('local_vision', {}).get('available'),
      'burst_recognition': status.get('burst_recognition'),
      'local_captions_enabled': status.get('local_captions', {}).get('enabled')}), flush=True)
grok = next((bot for bot in get('/api/bots')['bots'] if bot['provider'] == 'grok'), None)
if grok:
    print('PAIRING_RETAINED', json.dumps({key: grok.get(key) for key in
          ('enabled', 'verified', 'confirmed', 'images', 'image_owner_allowed')}), flush=True)
print('NO_EXPLICIT_CAPTURE_ENROLLMENT_OR_BOT_TEST_TRIGGERED', True, flush=True)
READ_ONLY_VERIFY
REMOTE_UPDATE
)
echo "Updating committed release $release. Authenticate only in your Terminal."
git archive "$release" | ssh "${ssh_options[@]}" "$target" "export DOORBELL_UPDATE_VERSION=$version DOORBELL_RELEASE_SHA=$release
$remote_script"
