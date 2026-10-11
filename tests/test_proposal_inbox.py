import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_doorbell.app import create_app
from agent_doorbell.config import Config
from agent_doorbell.proposal_inbox import ProposalInbox, load_config
from test_restricted_proposals import bundle


@pytest.fixture
def incoming(tmp_path):
    config_dir=tmp_path/'config';config_dir.mkdir();config_dir.chmod(0o700)
    directory=tmp_path/'inbox';directory.mkdir();directory.chmod(0o700)
    path=config_dir/'proposal-inbox.json'
    value={'enabled':True,'inbox':str(directory),'ssh_target':'doorbell-proposer@pi.example',
           'host_key_alias':'pi.example','identity_file':str(tmp_path/'not-a-real-credential')}
    path.write_text(json.dumps(value));path.chmod(0o600)
    raw=bundle();source=directory/(json.loads(raw)['request_id']+'.json');source.write_bytes(raw);source.chmod(0o600)
    return path,value,source,raw


def test_inbox_stages_once_and_journals_only_bounded_metadata(incoming):
    path,value,source,raw=incoming;calls=[]
    def sender(target,alias,key,body):
        calls.append((target,alias,key,body))
        return {'status':'pending','id':json.loads(body)['request_id'],'digest':'a'*64}
    inbox=ProposalInbox(path,sender)
    result=inbox.once();assert result['transfers']==1 and not result['owner_decisions']
    assert inbox.once()['transfers']==0 and len(calls)==1
    assert ProposalInbox(path,sender).once()['transfers']==0 and len(calls)==1
    journal=json.loads((inbox.state/source.name).read_text())
    assert journal['status']=='pending_owner_review' and journal['bundle_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'data_base64' not in json.dumps(journal) and 'identity_file' not in json.dumps(journal)
    assert (inbox.state/source.name).stat().st_mode&0o077==0 and source.exists()


def test_ambiguous_failure_or_interrupted_send_never_retries(incoming):
    path,value,source,raw=incoming;calls=[]
    def sender(*args):calls.append(args);raise TimeoutError('private simulated reply')
    inbox=ProposalInbox(path,sender);assert inbox.once()['transfers']==0
    state=inbox.state/source.name;journal=json.loads(state.read_text())
    assert journal['status']=='needs_owner_review' and 'private' not in state.read_text()
    journal['status']='sending';state.write_text(json.dumps(journal))
    assert ProposalInbox(path,sender).once()['transfers']==0 and len(calls)==1


@pytest.mark.parametrize('change',[{'ssh_target':'owner@pi.example'},{'command':'approve'},
                                  {'inbox':'/tmp/../private'},{'host_key_alias':'pi;id'}])
def test_inbox_cannot_select_owner_command_or_destination_from_input(incoming,change):
    path,value,source,raw=incoming;path.write_text(json.dumps(value|change))
    with pytest.raises(ValueError):ProposalInbox(path,lambda *args:pytest.fail('No network permitted'))


def test_inbox_rejects_symlinks_mismatch_partial_and_untrusted_permissions(incoming):
    path,value,source,raw=incoming;calls=[]
    inbox=ProposalInbox(path,lambda *args:calls.append(args))
    outside=source.parent.parent/'outside';source.rename(outside);source.symlink_to(outside)
    assert inbox.once()['transfers']==0 and not calls
    source.unlink();source.write_text('{"incomplete":');source.chmod(0o600)
    assert inbox.once()['transfers']==0 and not calls
    source.write_bytes(bundle())
    assert inbox.once()['transfers']==0 and not calls
    source.write_bytes(raw);source.chmod(0o666)
    assert inbox.once()['transfers']==0 and not calls
    source.chmod(0o600);path.chmod(0o644)
    with pytest.raises(ValueError):load_config(path)


def test_native_save_readable_file_is_private_inside_owner_only_inbox(incoming):
    path,value,source,raw=incoming;source.chmod(0o644)
    def sender(*args):return {'status':'pending','id':json.loads(raw)['request_id'],'digest':'a'*64}
    assert ProposalInbox(path,sender).once()['transfers']==1


def test_disabled_inbox_reads_no_inputs_creates_no_state(incoming):
    path,value,source,raw=incoming;path.write_text(json.dumps(value|{'enabled':False}))
    inbox=ProposalInbox(path,lambda *args:pytest.fail('No network permitted'))
    assert inbox.once()=={'enabled':False,'transfers':0} and not inbox.state.exists()


def test_restricted_policy_disables_legacy_http_proposer(tmp_path):
    root=tmp_path
    path=root/'restricted.json'
    path.write_text(json.dumps({'enabled':True,'owner_key':'b'*64,'proposer_uid':os.getuid()+1,
       'proposer_gid':os.getgid()+1,'proposal_socket':'/run/agent-doorbell/proposals.sock'}));path.chmod(0o600)
    app=create_app(Config(data_dir=root/'other',camera='none',background=False,enrollment_bridge_policy=path))
    with TestClient(app,client=('127.0.0.1',50000)) as client:
        assert app.state.doorbell.enrollment.auth.keys=={'owner':'b'*64}
        assert client.post('/api/enrollment/bridge/proposals',content=bundle()).status_code==503
        assert not app.state.doorbell.proposal_ingress.server
