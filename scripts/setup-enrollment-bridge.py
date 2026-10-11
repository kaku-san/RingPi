"""Owner-run bridge check or separately approved private-key setup on the Pi.

Never redeploys source, exports keys, opens a port, grants hardware or installs models.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.request

from agent_doorbell import __version__
from agent_doorbell.config import Config
from agent_doorbell.enrollment_bridge import BridgeAuth


def status():
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with http.open('http://127.0.0.1:8877/api/status',timeout=3) as response:raw=response.read(131073)
    if len(raw)>131072:raise ValueError('Bounded status read exceeded')
    return json.loads(raw)


def setup(app_dir,policy,enable=False,owner_approval=False):
    if not (app_dir/'.agent-doorbell-managed').is_file():raise ValueError('A managed existing Pi application is required')
    before=status()
    if before.get('version')!=__version__ or 'enrollment_bridge' not in before:
        raise ValueError('Deploy the matching application version before bridge setup')
    auth=BridgeAuth.from_file(None,lambda:datetime.now(timezone.utc),policy)
    if auth.issue:raise ValueError('Existing policy needs owner review; no key was changed')
    result={'mode':'enable' if enable else 'check','app_version':before['version'],
            'private_policy_ready':bool(auth.keys),'bridge_enabled':before['enrollment_bridge']['enabled'],
            'credentials_created':False,'secrets_printed':False,'public_port_opened':False,
            'network_or_ssh_settings_changed':False,'models_installed_or_enabled':False,
            'capture_or_enrollment_or_bot_test':False,'real_actuation':False}
    if not enable:return result
    if owner_approval is not True:raise ValueError('Separate owner approval for proposal and owner-review scopes is required')
    if auth.keys and result['bridge_enabled']:return result
    if not auth.keys:
        policy.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if policy.parent.is_symlink():raise ValueError('Private policy directory cannot be a symlink')
        payload={'enabled':True,'proposal_key':secrets.token_hex(32),'owner_key':secrets.token_hex(32)}
        temporary=None
        try:
            with tempfile.NamedTemporaryFile('w',dir=policy.parent,delete=False) as stream:
                temporary=Path(stream.name);temporary.chmod(0o600)
                stream.write(json.dumps(payload)+'\n');stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,policy)
        finally:
            if temporary and temporary.exists():temporary.unlink()
        result.update(credentials_created=True,private_policy_ready=True)
    subprocess.run(['systemctl','--user','restart','agent-doorbell.service'],stdin=subprocess.DEVNULL,check=True)
    for _ in range(15):
        try:
            after=status()
            if after.get('version')==before['version'] and after.get('enrollment_bridge',{}).get('enabled'):
                result['bridge_enabled']=True;return result
        except OSError:pass
        time.sleep(1)
    raise ValueError('Private policy was saved; service enablement needs review. No test or photo transfer was triggered')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true');modes.add_argument('--enable',action='store_true')
    parser.add_argument('--owner-approves-proposal-and-review-scopes',action='store_true')
    parser.add_argument('--app-dir',type=Path,default=Path('~/agent-doorbell').expanduser())
    parser.add_argument('--policy',type=Path,default=Config().enrollment_bridge_policy)
    args=parser.parse_args()
    try:
        result=setup(args.app_dir,args.policy,args.enable,args.owner_approves_proposal_and_review_scopes)
    except ValueError as error:raise SystemExit(str(error)) from None
    except Exception:raise SystemExit('Bridge setup did not complete; inspect owner-only policy and service health. No secret was printed.') from None
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
