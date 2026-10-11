import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def setup_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('caption_setup', Path(__file__).parents[1]/'scripts/caption-setup.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = tmp_path/'app'; (app/'scripts').mkdir(parents=True)
    (app/'.agent-doorbell-managed').touch()
    (app/'scripts/install-local-captions.py').write_text('reviewed synthetic installer sentinel')
    data = tmp_path/'private data'; data.mkdir()
    environment = tmp_path/'environment'
    environment.write_text(f'DOORBELL_DATA_DIR="{data}"\nDOORBELL_GROK_ROUTINE_KEY=FAKE_PRIVATE_KEY\nDOORBELL_LOCAL_CAPTIONS=0\n')
    environment.chmod(0o600)
    class Provider:
        ready = False
        def __init__(self, directory, runtime): pass
        def verify_files(self):
            if not self.ready: raise module.CaptionError('Pinned files unavailable')
    calls = []
    state = {'version':'0.3.5','local_captions':{'enabled':False}}
    monkeypatch.setattr(module, 'LlamaCaptionProvider', Provider)
    monkeypatch.setattr(module, 'status', lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(module.shutil, 'which', lambda name:'/fixture/'+name)
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if '--install' in args: Provider.ready = True
        if args[0]=='systemctl': state['local_captions']['enabled']=True
    monkeypatch.setattr(module.subprocess, 'run', run)
    return module, app, environment, Provider, calls, state


def test_check_is_read_only_and_preserves_newer_running_app(setup_fixture):
    module, app, environment, provider, calls, state = setup_fixture
    before = environment.read_bytes()
    result = module.setup(app, environment)
    assert result['mode']=='check' and not result['pinned_files_verified']
    assert result['app_version_before']==result['app_version_after']=='0.3.5'
    assert not calls and environment.read_bytes()==before
    assert not result['application_source_changed'] and not result['inference']
    assert 'FAKE_PRIVATE_KEY' not in json.dumps(result)


def test_runtime_install_does_not_redeploy_enable_restart_or_forward_credentials(setup_fixture):
    module, app, environment, provider, calls, state = setup_fixture
    before=environment.read_bytes()
    result=module.setup(app,environment,'install')
    assert result['pinned_files_verified'] and result['app_version_after']=='0.3.5'
    assert environment.read_bytes()==before and not state['local_captions']['enabled']
    assert len(calls)==1 and '--install' in calls[0][0] and '--data-dir' in calls[0][0]
    assert not any('pip' in str(args) or 'git archive' in str(args) or 'systemctl' in args for args,_ in calls)
    assert all(key not in calls[0][1]['env'] for key in ('DOORBELL_GROK_ROUTINE_KEY','DOORBELL_HERMES_API_KEY'))


def test_enable_requires_verified_files_and_preserves_private_settings(setup_fixture):
    module, app, environment, provider, calls, state = setup_fixture
    before=environment.read_bytes()
    with pytest.raises(ValueError,match='Verify the pinned'):module.setup(app,environment,'enable')
    assert not calls and environment.read_bytes()==before
    provider.ready=True
    result=module.setup(app,environment,'enable')
    assert result['app_version_after']=='0.3.5' and result['caption_service_enabled']
    assert calls[0][0]==['systemctl','--user','restart','agent-doorbell.service']
    content=environment.read_text()
    assert 'FAKE_PRIVATE_KEY' in content and 'DOORBELL_LOCAL_CAPTIONS=1' in content
    assert 'DOORBELL_CAPTION_DEADLINE_SECONDS=30' in content and environment.stat().st_mode&0o777==0o600
    assert 'FAKE_PRIVATE_KEY' not in json.dumps(result)


def test_missing_tools_or_failed_install_never_changes_configuration(setup_fixture,monkeypatch):
    module, app, environment, provider, calls, state=setup_fixture
    before=environment.read_bytes()
    monkeypatch.setattr(module.shutil,'which',lambda name:None if name=='cmake' else '/fixture/'+name)
    with pytest.raises(ValueError,match='cmake'):module.setup(app,environment,'install')
    assert not calls and environment.read_bytes()==before
    monkeypatch.setattr(module.shutil,'which',lambda name:'/fixture/'+name)
    def failed(*args,**kwargs):raise subprocess.CalledProcessError(2,['synthetic installer'])
    monkeypatch.setattr(module.subprocess,'run',failed)
    with pytest.raises(subprocess.CalledProcessError):module.setup(app,environment,'install')
    assert environment.read_bytes()==before and state['version']=='0.3.5'


@pytest.mark.parametrize('problem',['permissions','symlink','unmanaged'])
def test_unsafe_inputs_are_refused_without_mutation(setup_fixture,problem):
    module, app, environment, provider, calls, state=setup_fixture
    original=environment.read_bytes()
    if problem=='permissions':environment.chmod(0o644)
    elif problem=='symlink':
        target=environment.with_name('owner-file');environment.rename(target);environment.symlink_to(target)
    else:(app/'.agent-doorbell-managed').unlink()
    with pytest.raises((ValueError,OSError)):module.setup(app,environment,'enable')
    assert not calls and environment.read_bytes()==original


def test_shell_helper_sends_only_python_and_stage_without_app_archive(tmp_path):
    script=Path(__file__).parents[1]/'scripts/captions-pi.sh'
    text=script.read_text()
    assert 'git archive' not in text and 'pip install' not in text
    assert 'StrictHostKeyChecking=yes' in text and 'UpdateHostKeys=no' in text
    assert 'exec "$HOME/agent-doorbell/.venv/bin/python" - ' in text
    subprocess.run(['bash','-n',str(script)],check=True)


def test_check_lists_all_missing_components_without_creating_files(setup_fixture):
    module, app, environment, provider, calls, state = setup_fixture
    directory, runtime = module.paths(module.private_lines(environment))
    result = module.setup(app, environment)
    assert result['runtime_selection'] == 'managed' and result['install_supported']
    assert not result['files_present'] and not result['files_available']
    assert set(result['missing_components']) == {'runtime', 'provenance'} | {
        f'weights:{name}' for name in module.MODEL_FILES}
    assert not result['unreadable_components']
    assert not result['runtime']['present'] and not result['runtime']['executable']
    assert all(info['actual_bytes'] is None for info in result['weights'].values())
    assert not calls and not directory.exists()


@pytest.fixture
def pinned_setup(setup_fixture, monkeypatch):
    from agent_doorbell import captions
    module, app, environment, provider, calls, state = setup_fixture
    directory, _ = module.paths(module.private_lines(environment))
    models = {name: (len(data), hashlib.sha256(data).hexdigest())
              for name, data in [('model.gguf', b'model'), ('projector.gguf', b'projector')]}
    monkeypatch.setattr(module, 'MODEL_FILES', models)
    monkeypatch.setattr(captions, 'MODEL_FILES', models)
    monkeypatch.setattr(module, 'LlamaCaptionProvider', captions.LlamaCaptionProvider)
    binary = directory / 'runtime/build/bin/llama-server'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic runtime; never executed')
    binary.chmod(0o700)
    for name in models:
        (directory / name).write_bytes(name.split('.')[0].encode())
    provenance = binary.parents[2] / 'provenance.json'
    provenance.write_text(json.dumps({'revision': module.RUNTIME_REVISION,
                                     'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
    return module, app, environment, calls, state, directory, binary, provenance


def test_check_distinguishes_verified_files_from_service_enablement(pinned_setup):
    module, app, environment, calls, state, directory, binary, provenance = pinned_setup
    result = module.setup(app, environment)
    assert result['files_present'] and result['files_available'] and result['pinned_files_verified']
    assert not result['missing_components'] and not result['unreadable_components']
    assert all(info['size_matches'] for info in result['weights'].values())
    assert not result['caption_service_enabled'] and not result['inference']
    assert not calls


@pytest.mark.parametrize('problem', ['missing_weight', 'wrong_size', 'same_size_corruption',
                                     'not_executable', 'missing_provenance', 'malformed_provenance'])
def test_check_reports_partial_or_corrupt_install_in_one_result(pinned_setup, problem):
    module, app, environment, calls, state, directory, binary, provenance = pinned_setup
    model = directory / 'model.gguf'
    if problem == 'missing_weight': model.unlink()
    elif problem == 'wrong_size': model.write_bytes(b'bad')
    elif problem == 'same_size_corruption': model.write_bytes(b'wrong')
    elif problem == 'not_executable': binary.chmod(0o600)
    elif problem == 'missing_provenance': provenance.unlink()
    else: provenance.write_text('[]')
    result = module.setup(app, environment)
    assert not result['pinned_files_verified'] and result['verification_issue']
    assert result['files_present'] is (problem != 'missing_weight')
    assert result['files_available'] is (problem not in {'missing_weight', 'wrong_size', 'not_executable'})
    assert ('weights:model.gguf' in result['missing_components']) is (problem == 'missing_weight')
    assert ('provenance' in result['missing_components']) is (problem == 'missing_provenance')
    if problem == 'wrong_size':
        assert result['weights']['model.gguf']['actual_bytes'] == 3
        assert result['weights']['model.gguf']['expected_bytes'] == 5
    assert not calls and not result['inference']


def test_check_reports_unreadable_metadata_without_calling_it_missing(pinned_setup, monkeypatch):
    module, app, environment, calls, state, directory, binary, provenance = pinned_setup
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        if path == directory / 'model.gguf': raise PermissionError('PRIVATE_PATH_NOT_FOR_OUTPUT')
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    result = module.setup(app, environment)
    assert result['unreadable_components'] == ['weights:model.gguf']
    assert 'weights:model.gguf' not in result['missing_components']
    assert result['weights']['model.gguf']['present'] is None
    assert not result['pinned_files_verified']
    assert 'PRIVATE_PATH_NOT_FOR_OUTPUT' not in json.dumps(result)
    assert not calls


@pytest.mark.parametrize('ready', [False, True])
def test_custom_runtime_install_stops_before_changes_even_if_verified(pinned_setup, ready):
    module, app, environment, calls, state, directory, binary, provenance = pinned_setup
    custom_root = directory.parent / 'custom-runtime'
    if ready: (directory / 'runtime').rename(custom_root)
    custom = custom_root / 'build/bin/llama-server'
    environment.write_text(environment.read_text() + f'DOORBELL_LLAMA_SERVER="{custom}"\n')
    before = environment.read_bytes()
    check = module.setup(app, environment)
    assert check['runtime_selection'] == 'custom' and not check['install_supported']
    assert check['pinned_files_verified'] is ready
    with pytest.raises(ValueError, match='custom runtime'):
        module.setup(app, environment, 'install')
    assert environment.read_bytes() == before and not calls
    if ready:
        enabled = module.setup(app, environment, 'enable')
        assert enabled['caption_service_enabled'] and enabled['pinned_files_verified']
        assert f'DOORBELL_LLAMA_SERVER="{custom}"' in environment.read_text()
        assert calls[0][0] == ['systemctl', '--user', 'restart', 'agent-doorbell.service']


def test_explicit_managed_runtime_override_can_install_and_refresh_metadata(pinned_setup, monkeypatch):
    module, app, environment, calls, state, directory, binary, provenance = pinned_setup
    environment.write_text(environment.read_text() + f'DOORBELL_LLAMA_SERVER="{binary}"\n')
    model = directory / 'model.gguf'
    model.unlink()
    def install(args, **kwargs):
        calls.append((args, kwargs))
        model.write_bytes(b'model')
    monkeypatch.setattr(module.subprocess, 'run', install)
    result = module.setup(app, environment, 'install')
    assert result['install_supported'] and result['runtime_selection'] == 'managed'
    assert result['pinned_files_verified'] and result['files_present']
    assert not result['missing_components'] and result['weights']['model.gguf']['actual_bytes'] == 5
    assert len(calls) == 1 and '--install' in calls[0][0]
