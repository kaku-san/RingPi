"""Run the real update shell flow against a temporary fake SSH/Git/Pi boundary."""
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


SHA = '0123456789abcdef0123456789abcdef01234567'
REPO = Path(__file__).resolve().parents[1]


def executable(path, code):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(code))
    path.chmod(0o700)


def boundary(tmp_path, managed=True):
    binary = tmp_path / 'bin'; binary.mkdir()
    app = tmp_path / 'remote-app'; app.mkdir()
    (app / '.venv/bin').mkdir(parents=True)
    if managed:
        (app / '.agent-doorbell-managed').touch()
    environment = tmp_path / 'private-environment'
    environment.write_text('DOORBELL_GROK_ROUTINE_KEY=FAKE_PRIVATE_TEST_KEY\nDOORBELL_LOCAL_CAPTIONS=0\n')
    reference = tmp_path / 'private-reference'; reference.write_bytes(b'PRIVATE_FAKE_REFERENCE')
    executable(binary / 'git', '''
        import io, os, sys, tarfile
        from pathlib import Path
        args = sys.argv[1:]
        sha = os.environ['TEST_RELEASE_SHA']
        if args == ['rev-parse', '--verify', 'HEAD']: print(sha)
        elif args == ['cat-file', '-e', sha + '^{commit}']: pass
        elif args == ['show', sha + ':pyproject.toml']:
            print('[project]\\nversion = "0.3.5"')
        elif args == ['archive', sha]:
            data = b'committed source only'
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
                info = tarfile.TarInfo('release.txt'); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        else: raise SystemExit(8)
    ''')
    executable(binary / 'ssh', '''
        import json, os, shlex, subprocess, sys
        from pathlib import Path
        args = sys.argv[1:]
        Path(os.environ['TEST_SSH_ARGS']).write_text(json.dumps(args))
        script = args[-1].replace('app_dir="$HOME/agent-doorbell"',
                   'app_dir=' + shlex.quote(os.environ['TEST_REMOTE_APP']))
        data = sys.stdin.buffer.read()
        result = subprocess.run(['bash', '-c', script], input=data)
        raise SystemExit(result.returncode)
    ''')
    executable(binary / 'systemctl', '''
        import os, stat, sys
        from pathlib import Path
        marker = Path(os.environ['TEST_REMOTE_APP']) / '.release-sha'
        text = marker.read_text()
        assert text == os.environ['TEST_RELEASE_SHA'] + '\\n'
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600
        Path(os.environ['TEST_RESTART_ARGS']).write_text(' '.join(sys.argv[1:]))
    ''')
    executable(app / '.venv/bin/python', '''
        import json, os, sys, urllib.request
        from pathlib import Path
        if sys.argv[1:3] == ['-m', 'pip']:
            assert '--no-deps' in sys.argv
            Path(os.environ['TEST_INSTALL_ARGS']).write_text(json.dumps(sys.argv[1:]))
        elif sys.argv[1:] == ['-']:
            class Response:
                def __init__(self, path): self.path = path
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, limit):
                    value = {'version': '0.3.5', 'local_vision': {'available': True},
                             'burst_recognition': {'frames': 3}, 'local_captions': {'enabled': False}}
                    if self.path.endswith('/api/bots'):
                        value = {'bots': [{'provider': 'grok', 'enabled': True, 'verified': True,
                           'confirmed': True, 'images': False, 'image_owner_allowed': False,
                           'config': {'share_names': False, 'secret': 'FAKE_PRIVATE_TEST_KEY'}}]}
                    return json.dumps(value).encode()
            class Http:
                def open(self, path, timeout):
                    assert path in ('http://127.0.0.1:8877/api/status', 'http://127.0.0.1:8877/api/bots')
                    return Response(path)
            urllib.request.build_opener = lambda *args: Http()
            exec(compile(sys.stdin.read(), '<remote read-only verifier>', 'exec'))
        else: raise SystemExit(7)
    ''')
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
               TEST_RELEASE_SHA=SHA, TEST_REMOTE_APP=str(app), TEST_SSH_ARGS=str(tmp_path/'ssh-args.json'),
               TEST_RESTART_ARGS=str(tmp_path/'restart.txt'), TEST_INSTALL_ARGS=str(tmp_path/'install.json'))
    env.pop('DOORBELL_SSH_IDENTITY', None)
    return app, environment, reference, env


def test_update_transfers_only_pinned_source_preserves_private_state_and_does_no_capture(tmp_path):
    app, environment, reference, env = boundary(tmp_path)
    before = environment.read_bytes(), reference.read_bytes()
    result = subprocess.run(['bash', str(REPO/'scripts/update-pi.sh'), 'owner@pi.example', 'trusted.example', SHA],
                            cwd=REPO, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (app/'release.txt').read_bytes() == b'committed source only'
    assert (environment.read_bytes(), reference.read_bytes()) == before
    args = json.loads((tmp_path/'ssh-args.json').read_text())
    for option in ('StrictHostKeyChecking=yes', 'HostKeyAlias=trusted.example', 'UpdateHostKeys=no',
                   'ForwardAgent=no', 'ClearAllForwardings=yes', 'RequestTTY=no'):
        assert option in args
    assert (tmp_path/'restart.txt').read_text() == '--user restart agent-doorbell.service'
    assert 'UPDATE_READY' in result.stdout and 'PAIRING_RETAINED' in result.stdout
    assert 'FAKE_PRIVATE_TEST_KEY' not in result.stdout + result.stderr
    assert 'PRIVATE_FAKE_REFERENCE' not in result.stdout + result.stderr
    assert 'DOORBELL_UPDATE_VERSION' in args[-1] and args[-1].startswith('export ')


def test_update_can_pin_an_existing_identity_without_relaxing_host_keys(tmp_path):
    _app, _environment, _reference, env = boundary(tmp_path)
    identity = tmp_path / 'owner-key'
    identity.write_text('not a real key')
    env['DOORBELL_SSH_IDENTITY'] = str(identity)
    result = subprocess.run(['bash', str(REPO/'scripts/update-pi.sh'), 'owner@pi.example', 'trusted.example', SHA],
                            cwd=REPO, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    args = json.loads((tmp_path/'ssh-args.json').read_text())
    assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args and 'IdentitiesOnly=yes' in args
    assert 'HostKeyAlias=trusted.example' in args and args[args.index('-i') + 1] == str(identity)
    env['DOORBELL_SSH_IDENTITY'] = '/tmp/not allowed'
    refused = subprocess.run(['bash', str(REPO/'scripts/update-pi.sh'), 'owner@pi.example', 'trusted.example', SHA],
                             cwd=REPO, env=env, text=True, capture_output=True, timeout=10)
    assert refused.returncode == 2 and 'Invalid SSH identity path' in refused.stderr


def test_update_refuses_unmanaged_app_before_install_or_restart(tmp_path):
    app, environment, reference, env = boundary(tmp_path, managed=False)
    result = subprocess.run(['bash', str(REPO/'scripts/update-pi.sh'), 'owner@pi.example'],
                            cwd=REPO, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 3 and 'unmanaged' in result.stderr
    assert not (app/'release.txt').exists() and not (tmp_path/'restart.txt').exists()
    assert not (tmp_path/'install.json').exists()


@pytest.mark.parametrize('revision', ['HEAD', '-unsafe', '0'*39])
def test_update_rejects_unpinned_revision_without_ssh(tmp_path, revision):
    app, environment, reference, env = boundary(tmp_path)
    result = subprocess.run(['bash', str(REPO/'scripts/update-pi.sh'), 'owner@pi.example', 'trusted.example', revision],
                            cwd=REPO, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2 and not (tmp_path/'ssh-args.json').exists()
