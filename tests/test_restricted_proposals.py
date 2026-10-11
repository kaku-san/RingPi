import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import uuid

import pytest

from agent_doorbell.access_simulation import AccessSimulation
from agent_doorbell.bridge_client import send_restricted
from agent_doorbell.enrollment_bridge import BridgeAuth, EnrollmentBridge, fixture_references, MAX_BODY
from agent_doorbell.proposal_gate import COMMAND, MAGIC, load_policy, forward
from agent_doorbell.restricted_proposals import RestrictedProposalServer, peer_uid
from agent_doorbell.restricted_setup import render_plan, audit_effective, ssh_requirements
from agent_doorbell.store import Store


def bundle():
    now=datetime.now(timezone.utc)
    return json.dumps({'schema_version':1,'request_id':uuid.uuid4().hex,'visitor_name':'Consenting fixture visitor',
      'purpose':'temporary_visitor_pass','valid_from':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat(),
      'references':fixture_references()}).encode()


async def exchange(path, raw, declared=None, magic=MAGIC):
    reader,writer=await asyncio.open_unix_connection(str(path))
    writer.write(magic+struct.pack('!I',len(raw) if declared is None else declared)+raw);await writer.drain()
    size=struct.unpack('!I',await reader.readexactly(4))[0]
    result=json.loads(await reader.readexactly(size))
    writer.close();await writer.wait_closed()
    return result


@pytest.fixture
def isolated():
    with tempfile.TemporaryDirectory(prefix='dbp-',dir='/tmp') as name:
        root=Path(name);directory=root/'socket';directory.mkdir();os.chown(directory,-1,os.getgid());directory.chmod(0o2710)
        store=Store(root/'data');access=AccessSimulation(store)
        class NoEnrollment:
            def enroll(self,raw):raise AssertionError('Proposal ingress cannot perform enrollment')
        svc=EnrollmentBridge(store,access,NoEnrollment(),root/'absent')
        policy={'uid':os.getuid()+1,'gid':os.getgid(),'socket':str(directory/'proposals.sock')}
        yield root,svc,policy


def test_unix_proposal_protocol_stages_without_signing_or_enrollment(isolated):
    root,svc,policy=isolated
    async def scenario():
        server=RestrictedProposalServer(svc,policy,peer=lambda connection:policy['uid'])
        await server.start();assert server.server and not server.issue
        try:
            raw=bundle();item=await exchange(server.path,raw)
            assert item['status']=='pending' and not item['fixture']
            assert len(item['payload']['references'])==5 and not svc.access.snapshot()['passes']
            assert svc.store.profiles()==[] and svc.store.face_samples()==[]
            serialized=json.dumps(item)
            assert 'data_base64' not in serialized and 'embedding' not in serialized and 'owner_key' not in serialized
            assert server.path.stat().st_mode&0o777==0o660
            # A decision or HTTP path is never routed to an owner operation.
            decision=json.dumps({'decision':'approve','payload_digest':item['digest'],
                         'path':'/api/enrollment/bridge/proposals/'+item['id']+'/decision'}).encode()
            rejected=await exchange(server.path,decision)
            assert rejected['detail']=='invalid_enrollment_bundle'
            assert svc.proposal(item['id'])['status']=='pending' and not svc.access.snapshot()['passes']
            duplicate=await exchange(server.path,raw)
            assert duplicate['id']==item['id']
        finally:await server.close()
        assert not server.path.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['wrong_uid','large','bad_magic','capacity'])
def test_unix_ingress_rejects_identity_protocol_and_capacity_faults(isolated,fault):
    root,svc,policy=isolated
    async def scenario():
        peer=policy['uid']+1 if fault=='wrong_uid' else policy['uid']
        server=RestrictedProposalServer(svc,policy,peer=lambda connection:peer)
        await server.start()
        try:
            if fault=='capacity':server.active=2
            raw=b'{}';declared=MAX_BODY+1 if fault=='large' else None
            result=await exchange(server.path,raw,declared,b'X'*len(MAGIC) if fault=='bad_magic' else MAGIC)
            expected={'wrong_uid':'restricted_proposer_identity_required','large':'enrollment_bundle_too_large',
                      'bad_magic':'proposal_protocol_required','capacity':'proposal_ingress_busy'}[fault]
            assert result['detail']==expected and not svc.access.snapshot()['passes']
            with svc.store.connection() as db:assert db.execute('SELECT COUNT(*) FROM enrollment_imports').fetchone()[0]==0
        finally:await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['directory_mode','directory_symlink','existing_regular_file'])
def test_listener_never_replaces_untrusted_paths(isolated,fault):
    root,svc,policy=isolated;directory=Path(policy['socket']).parent
    if fault=='directory_mode':directory.chmod(0o2777)
    if fault=='directory_symlink':
        moved=root/'moved';directory.rename(moved);directory.symlink_to(moved,target_is_directory=True)
    if fault=='existing_regular_file':Path(policy['socket']).write_text('retained')
    async def scenario():
        server=RestrictedProposalServer(svc,policy,peer=lambda connection:policy['uid'])
        await server.start();assert not server.server and server.issue
        if fault=='existing_regular_file':assert Path(policy['socket']).read_text()=='retained'
        await server.close()
    asyncio.run(scenario())


def test_native_linux_peer_uid_comes_from_kernel():
    if not hasattr(socket,'SO_PEERCRED'):pytest.skip('Linux-only kernel credential API; native SSH CI covers it')
    first,second=socket.socketpair()
    try:assert peer_uid(first)==os.getuid() and peer_uid(second)==os.getuid()
    finally:first.close();second.close()


def test_gate_root_policy_refuses_writable_symlink_and_extra_scope(tmp_path):
    policy=tmp_path/'gate.json';value={'socket':'/run/agent-doorbell/proposals.sock','proposer_uid':900}
    policy.write_text(json.dumps(value));policy.chmod(0o644)
    assert load_policy(policy,trusted_uid=os.getuid())==value
    policy.chmod(0o666)
    with pytest.raises(ValueError):load_policy(policy,trusted_uid=os.getuid())
    policy.chmod(0o644);link=tmp_path/'link';link.symlink_to(policy)
    with pytest.raises(OSError):load_policy(link,trusted_uid=os.getuid())
    policy.write_text(json.dumps(value|{'owner_key':'PRIVATE FAKE KEY'}))
    with pytest.raises(ValueError):load_policy(policy,trusted_uid=os.getuid())


@pytest.mark.parametrize('command',['','id','sftp','doorbell-propose-v1;id','_forward owner POST /decision'])
def test_gate_has_no_shell_subsystem_or_owner_operation(command):
    with pytest.raises(ValueError):forward({'socket':'/no/socket','proposer_uid':os.getuid()},b'{}',command)


def test_gate_rejects_wrong_identity_and_tty():
    with pytest.raises(ValueError):forward({'socket':'/no/socket','proposer_uid':os.getuid()+1},b'{}',COMMAND)
    with pytest.raises(ValueError):forward({'socket':'/no/socket','proposer_uid':os.getuid()},b'{}',COMMAND,True)


def test_restricted_policy_contains_only_owner_signing_scope(tmp_path):
    store=Store(tmp_path/'data');path=tmp_path/'policy'
    uid,gid=os.getuid()+1,os.getgid()+1
    value={'enabled':True,'owner_key':'a'*64,'proposer_uid':uid,'proposer_gid':gid,'proposal_socket':'/run/agent-doorbell/proposals.sock'}
    path.write_text(json.dumps(value));path.chmod(0o600)
    clock=lambda:datetime.now(timezone.utc)
    auth=BridgeAuth.from_file(store,clock,path)
    assert auth.keys=={'owner':'a'*64} and auth.restricted['uid']==uid
    for change in ({'proposer_uid':os.getuid()},{'proposer_gid':os.getgid()},{'proposer_uid':0},{'proposal_socket':'/tmp/../owner'}):
        path.write_text(json.dumps(value|change))
        rejected=BridgeAuth.from_file(store,clock,path)
        assert not rejected.keys and not rejected.restricted and rejected.issue


def test_restricted_client_uses_only_dedicated_key_and_fixed_command(monkeypatch,tmp_path):
    calls=[]
    class Result:returncode=0;stdout=b'{"status":"pending"}'
    def run(args,**kwargs):calls.append((args,kwargs));return Result()
    monkeypatch.setattr('agent_doorbell.bridge_client.subprocess.run',run)
    raw=bundle();identity=tmp_path/'fake-credential';identity.write_text('owned test fixture, not an SSH key');identity.chmod(0o600)
    assert send_restricted('doorbell-proposer@pi.example','pi.example',identity,raw)['status']=='pending'
    args,kwargs=calls[0]
    assert args[-1]==COMMAND and kwargs['input']==raw
    assert 'IdentitiesOnly=yes' in args and 'ControlPath=none' in args and 'BatchMode=yes' in args
    assert '_forward' not in ' '.join(args) and 'owner_key' not in ' '.join(args)
    with pytest.raises(ValueError):send_restricted('owner@pi.example','pi.example',identity,raw)
    with pytest.raises(ValueError):send_restricted('doorbell-proposer@pi.example;id',None,identity,raw)
    assert len(calls)==1
    identity.unlink()
    with pytest.raises(OSError):send_restricted('doorbell-proposer@pi.example','pi.example',identity,raw)
    assert len(calls)==1


def test_plan_is_read_only_and_effective_ssh_audit_fails_closed():
    plan=render_plan('owner','/home/owner','192.0.2.8/32')
    assert render_plan('owner','/home/owner','198.51.100.8/32')['live_changes'] is False
    assert render_plan('owner','/home/owner','203.0.113.8/32')['live_changes'] is False
    assert not plan['live_changes'] and not plan['credentials_created'] and not plan['photos_transferred']
    assert not plan['network']['new_tcp_listener'] and not plan['physical_control']
    assert plan['account']['supplementary_groups']==[] and not plan['account']['sudo']
    config=next(item['content'] for item in plan['files'] if item['path'].endswith('.conf') and 'sshd_config' in item['path'])
    assert 'ForceCommand' in config and 'DisableForwarding yes' in config and 'PermitTTY no' in config
    assert 'PermitUserEnvironment' not in config and 'AcceptEnv LANG LC_*' in config
    lines='\n'.join(key+' '+value for key,value in ssh_requirements().items())
    assert audit_effective(lines)['restricted_proposer_ssh_verified']
    with pytest.raises(ValueError):audit_effective(lines.replace('disableforwarding yes','disableforwarding no'))
    assert audit_effective(lines+'\nacceptenv LANG\nacceptenv LC_*')['restricted_proposer_ssh_verified']
    with pytest.raises(ValueError):audit_effective(lines+'\nacceptenv LD_*')
    with pytest.raises(ValueError):audit_effective(lines+'\nsetenv LD_PRELOAD=/tmp/unsafe')
    for source in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'):
        with pytest.raises(ValueError):render_plan('owner','/home/owner', source)
    assert '192.168.' not in json.dumps(plan)
