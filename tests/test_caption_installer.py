import hashlib
import importlib.util
import json
from io import BytesIO
from pathlib import Path
import sys

import pytest


@pytest.fixture
def installer():
    spec=importlib.util.spec_from_file_location('doorbell_caption_installer',Path(__file__).parents[1]/'scripts/install-local-captions.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('problem',['oversized','checksum'])
def test_bad_model_download_is_not_installed_or_left_on_disk(tmp_path,monkeypatch,installer,problem):
    data=b'explicit synthetic weight fixture'
    monkeypatch.setattr(installer.urllib.request,'urlopen',lambda *args,**kwargs:BytesIO(data))
    size=len(data)-1 if problem=='oversized' else len(data)
    with pytest.raises(ValueError):installer.download_model('https://huggingface.co/official-fixture',tmp_path/'fixture.gguf',size,'0'*64)
    assert not list(tmp_path.iterdir())


def test_verified_model_download_is_atomic_private_and_idempotent(tmp_path,monkeypatch,installer):
    data=b'explicit synthetic weight fixture';digest=hashlib.sha256(data).hexdigest();calls=[]
    def fixture(*args,**kwargs):calls.append(args);return BytesIO(data)
    monkeypatch.setattr(installer.urllib.request,'urlopen',fixture)
    target=tmp_path/'fixture.gguf'
    installer.download_model('https://huggingface.co/official-fixture',target,len(data),digest)
    installer.download_model('https://huggingface.co/official-fixture',target,len(data),digest)
    assert target.read_bytes()==data and target.stat().st_mode&0o777==0o600 and len(calls)==1


def test_missing_build_tools_do_not_download_or_create_directories(tmp_path,monkeypatch,installer):
    monkeypatch.setattr(installer.shutil,'which',lambda name:None)
    directory=tmp_path/'captions'
    with pytest.raises(ValueError,match='Missing build tools'):installer.install(directory)
    assert not directory.exists()


def test_unmanaged_caption_directory_is_never_replaced(tmp_path,monkeypatch,installer):
    monkeypatch.setattr(installer.shutil,'which',lambda name:'/explicit-fixture/'+name)
    marker=tmp_path/'owner-file';marker.write_text('keep this local fixture')
    with pytest.raises(ValueError,match='unmanaged'):installer.install(tmp_path)
    assert marker.read_text()=='keep this local fixture'


@pytest.fixture
def pinned_installation(tmp_path, monkeypatch, installer):
    from agent_doorbell import captions
    models = {'model.gguf': (5, hashlib.sha256(b'model').hexdigest())}
    monkeypatch.setattr(installer, 'MODEL_FILES', models)
    monkeypatch.setattr(captions, 'MODEL_FILES', models)
    directory = tmp_path / 'captions'
    binary = directory / 'runtime/build/bin/llama-server'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic runtime; never executed')
    binary.chmod(0o700)
    (directory / 'model.gguf').write_bytes(b'model')
    provenance = binary.parents[2] / 'provenance.json'
    provenance.write_text(json.dumps({'revision': installer.RUNTIME_REVISION,
                                     'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
    return directory, binary, provenance


@pytest.mark.parametrize('problem', [None, 'checksum', 'provenance'])
def test_installer_status_does_not_confuse_presence_with_verification(installer, pinned_installation, problem):
    directory, binary, provenance = pinned_installation
    if problem == 'checksum': (directory / 'model.gguf').write_bytes(b'wrong')
    if problem == 'provenance': provenance.write_text('null')
    result = installer.installation_status(directory)
    assert result['files_present'] and result['files_available']
    assert result['pinned_files_verified'] is (problem is None)
    assert 'installed' not in result


def test_custom_runtime_is_checked_and_never_installed(installer, pinned_installation):
    directory, binary, provenance = pinned_installation
    custom_root = directory.parent / 'custom-runtime'
    (directory / 'runtime').rename(custom_root)
    custom = custom_root / 'build/bin/llama-server'
    result = installer.installation_status(directory, custom)
    assert result['pinned_files_verified'] and result['runtime_selection'] == 'custom'
    assert not result['install_supported']
    with pytest.raises(ValueError, match='custom runtime'):
        installer.install(directory, custom)
    assert not (directory / 'runtime').exists()
    assert custom.read_bytes() == b'synthetic runtime; never executed'


def test_explicit_runtime_argument_overrides_process_environment(installer, pinned_installation, monkeypatch, capsys):
    directory, binary, provenance = pinned_installation
    monkeypatch.setenv('DOORBELL_LLAMA_SERVER', str(directory / 'missing-runtime'))
    monkeypatch.setattr(sys, 'argv', ['install-local-captions.py', '--data-dir', str(directory.parent),
                                     '--runtime', str(binary)])
    installer.main()
    result = json.loads(capsys.readouterr().out)
    assert result['pinned_files_verified'] and result['runtime_selection'] == 'managed'


@pytest.mark.parametrize('selection', ['environment', 'argument'])
def test_installer_cli_honors_custom_selection_and_refuses_before_install(tmp_path, monkeypatch, installer, capsys, selection):
    custom = tmp_path / 'custom/build/bin/llama-server'
    args = ['install-local-captions.py', '--data-dir', str(tmp_path / 'data'), '--install']
    if selection == 'environment': monkeypatch.setenv('DOORBELL_LLAMA_SERVER', str(custom))
    else:
        monkeypatch.setenv('DOORBELL_LLAMA_SERVER', str(tmp_path / 'ignored-runtime'))
        args += ['--runtime', str(custom)]
    monkeypatch.setattr(sys, 'argv', args)
    monkeypatch.setattr(installer.os, 'umask', lambda value: 0o077)
    with pytest.raises(SystemExit, match='custom runtime'): installer.main()
    result = json.loads(capsys.readouterr().out)
    assert result['runtime_selection'] == 'custom' and not result['install_supported']
    assert not result['files_present'] and not result['pinned_files_verified']
    assert 'installed' not in result
    assert not list(tmp_path.iterdir())
