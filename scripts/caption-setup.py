"""Owner-run caption readiness/install/enable stages; never redeploy application source."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

from agent_doorbell.caption_model import MODEL_FILES, MODEL_REVISION, RUNTIME_REVISION
from agent_doorbell.captions import CaptionError, LlamaCaptionProvider


def private_lines(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Service environment must be an owner-only regular file; no credential was printed.')
        return stream.read().splitlines()


def paths(lines):
    values = {}
    for line in lines:
        key, separator, value = line.partition('=')
        if separator and key in {'DOORBELL_DATA_DIR', 'DOORBELL_LLAMA_SERVER'}:
            parsed = shlex.split(value, comments=True)
            if len(parsed) != 1:
                raise ValueError('Invalid nonsecret caption path setting; no credential was printed.')
            values[key] = parsed[0]
    directory = Path(values.get('DOORBELL_DATA_DIR', '~/.local/share/agent-doorbell')).expanduser() / 'captions'
    runtime = Path(values['DOORBELL_LLAMA_SERVER']).expanduser() if values.get('DOORBELL_LLAMA_SERVER') else None
    return directory, runtime


def status():
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with http.open('http://127.0.0.1:8877/api/status', timeout=5) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError('Status metadata exceeds the bounded read.')
        return json.loads(raw)


def verified(provider):
    try:
        provider.verify_files()
    except CaptionError as error:
        return False, str(error)
    except (ValueError, OSError, AttributeError, TypeError, IndexError):
        return False, 'Pinned caption files or provenance could not be verified.'
    return True, None


def file_info(path):
    try:
        info = path.stat()
        present = stat.S_ISREG(info.st_mode)
        return {'present': present, 'actual_bytes': info.st_size if present else None,
                'inspection_issue': None}
    except FileNotFoundError:
        return {'present': False, 'actual_bytes': None, 'inspection_issue': None}
    except OSError:
        return {'present': None, 'actual_bytes': None,
                'inspection_issue': 'File metadata could not be read.'}


def file_status(directory, runtime, provider):
    managed = directory / 'runtime/build/bin/llama-server'
    selected = runtime if runtime is not None else managed
    custom = os.path.abspath(selected) != os.path.abspath(managed)
    binary = file_info(selected)
    binary['executable'] = binary['present'] is True and os.access(selected, os.X_OK)
    weights = {}
    for name, (size, _) in MODEL_FILES.items():
        info = file_info(directory / name)
        weights[name] = info | {'expected_bytes': size,
                                'size_matches': info['present'] is True and info['actual_bytes'] == size}
    # Match the existing provider's provenance location without assuming a new Pi package.
    provenance = (file_info(selected.parents[2] / 'provenance.json') if len(selected.parents) > 2 else
                  {'present': None, 'actual_bytes': None,
                   'inspection_issue': 'Runtime path has no expected provenance location.'})
    components = {'runtime': binary, 'provenance': provenance}
    components.update({f'weights:{name}': info for name, info in weights.items()})
    ready, issue = verified(provider)
    return {'runtime_selection': 'custom' if custom else 'managed',
            'install_supported': not custom,
            'runtime': binary, 'weights': weights, 'provenance': provenance,
            'missing_components': [name for name, info in components.items() if info['present'] is False],
            'unreadable_components': [name for name, info in components.items() if info['inspection_issue']],
            'files_present': binary['present'] is True and all(info['present'] is True for info in weights.values()),
            'files_available': binary['executable'] and all(info['size_matches'] for info in weights.values()),
            'pinned_files_verified': ready, 'verification_issue': issue}


def configure(environment):
    # Re-read before writing so other owner settings, including credentials, survive.
    lines = private_lines(environment)
    lines = [line for line in lines if not line.startswith(('DOORBELL_LOCAL_CAPTIONS=', 'DOORBELL_CAPTION_DEADLINE_SECONDS='))]
    lines += ['DOORBELL_LOCAL_CAPTIONS=1', 'DOORBELL_CAPTION_DEADLINE_SECONDS=30']
    environment.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=environment.parent, delete=False) as stream:
            temporary = Path(stream.name)
            temporary.chmod(0o600)
            stream.write('\n'.join(lines) + '\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, environment)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def setup(app_dir, environment, mode='check'):
    if mode not in {'check', 'install', 'enable'}:
        raise ValueError('Choose check, install or enable.')
    if not (app_dir / '.agent-doorbell-managed').is_file():
        raise ValueError('A managed existing application is required; no source was transferred.')
    directory, runtime = paths(private_lines(environment))
    provider = LlamaCaptionProvider(directory, runtime)
    current = status()
    version = current.get('version')
    if not isinstance(version, str):
        raise ValueError('Running application version is unavailable; no change was made.')
    files = file_status(directory, runtime, provider)
    tools = {name: bool(shutil.which(name)) for name in ('cmake', 'git', 'c++', 'make')}
    disk_path = directory
    while not disk_path.exists():
        disk_path = disk_path.parent
    result = {'app_version_before': version, 'app_version_after': version, 'mode': mode,
              'tools': tools, 'free_disk_bytes': shutil.disk_usage(disk_path).free,
              'download_bytes': sum(size for size, _ in MODEL_FILES.values()),
              'model_revision': MODEL_REVISION, 'runtime_revision': RUNTIME_REVISION,
              **files,
              'caption_service_enabled': current.get('local_captions', {}).get('enabled'),
              'application_source_changed': False, 'application_package_installed': False,
              'capture': False, 'inference': False, 'bot_test': False, 'system_packages_changed': False}
    if mode == 'install':
        if not files['install_supported']:
            raise ValueError('DOORBELL_LLAMA_SERVER selects a custom runtime. --install only builds the managed default; '
                             'provision the reviewed custom runtime separately or explicitly select the managed default '
                             'in the private service environment. No files or configuration changed.')
        if not all(tools.values()):
            raise ValueError('Missing build tools: ' + ', '.join(name for name, present in tools.items() if not present) + '. No model installed or source changed.')
        if result['free_disk_bytes'] < 1_073_741_824:
            raise ValueError('At least 1 GiB free is required; no installation was started.')
        installer = app_dir / 'scripts/install-local-captions.py'
        if not installer.is_file() or installer.is_symlink():
            raise ValueError('The existing reviewed caption installer is unavailable; no app release was redeployed.')
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if key in os.environ}
        subprocess.run([sys.executable, str(installer), '--install', '--data-dir', str(directory.parent)],
                       cwd=app_dir, env=env, check=True)
        files = file_status(directory, runtime, provider)
        if not files['pinned_files_verified']:
            raise ValueError('Caption files did not pass pinned verification. Configuration and app source are unchanged.')
        result.update(files)
        # Install does not enable or restart the app, and never runs model inference.
    elif mode == 'enable':
        if not files['pinned_files_verified']:
            raise ValueError('Verify the pinned runtime and model files before enabling captions; no configuration changed.')
        configure(environment)
        subprocess.run(['systemctl', '--user', 'restart', 'agent-doorbell.service'], check=True)
        for attempt in range(15):
            try:
                after = status()
                if after.get('version') == version and after.get('local_captions', {}).get('enabled'):
                    result['caption_service_enabled'] = True
                    break
                if after.get('version') not in {None, version}:
                    raise ValueError('App version unexpectedly changed outside this helper. No app source was transferred.')
            except OSError:
                pass
            time.sleep(1)
        else:
            raise ValueError('Caption configuration was enabled but service health needs review; no test or inference was triggered.')
    after = status()
    result['app_version_after'] = after.get('version')
    if result['app_version_after'] != version:
        raise ValueError('App version unexpectedly changed outside this helper. No app release was transferred.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='read-only prerequisites, pinned-file and service check (default)')
    modes.add_argument('--install', action='store_true', help='explicitly build/download only reviewed caption files; do not enable')
    modes.add_argument('--enable', action='store_true', help='enable already verified files in private config and restart only Doorbell')
    parser.add_argument('--app-dir', type=Path, default=Path('~/agent-doorbell').expanduser())
    args = parser.parse_args()
    mode = 'install' if args.install else 'enable' if args.enable else 'check'
    try:
        result = setup(args.app_dir, Path.home() / '.config/agent-doorbell/environment', mode)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (OSError, subprocess.CalledProcessError):
        # Do not dump command/environment/error bodies containing owner credentials.
        raise SystemExit('Caption stage did not complete. Run --check for prerequisites and pinned-file status; app source was not changed.') from None
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
