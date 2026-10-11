import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import pytest

from agent_doorbell.access_simulation import AccessError, AccessSimulation
from agent_doorbell.app import create_app
from agent_doorbell.bridge_client import allowed_request, prepare, send
from agent_doorbell.config import Config
from agent_doorbell.enrollment_bridge import EnrollmentBridge, BridgeAuth, fixture_references, signature
from agent_doorbell.store import Store


class Clock:
    def __init__(self): self.value=datetime(2026,10,7,15,tzinfo=timezone.utc)
    def __call__(self): return self.value
    def advance(self,**kwargs):self.value+=timedelta(**kwargs)


class Vision:
    def __init__(self):self.calls=[];self.fail_at=None;self.structured=False
    def enroll(self,raw):
        self.calls.append(raw)
        if len(self.calls)==self.fail_at:
            if self.structured:raise AccessError('PRIVATE MODEL BODY')
            raise RuntimeError('PRIVATE MODEL BODY')
        return raw,[.6,.8],{}


@pytest.fixture
def bridge(tmp_path):
    clock=Clock();store=Store(tmp_path);vision=Vision();access=AccessSimulation(store,clock=clock)
    return EnrollmentBridge(store,access,vision,tmp_path/'absent-policy',clock),clock,vision


def envelope(clock,**values):
    return {'schema_version':1,'request_id':values.get('request_id',uuid.uuid4().hex),
            'visitor_name':values.get('name','Avery visitor'),'purpose':'temporary_visitor_pass',
            'valid_from':clock().isoformat(),'valid_until':(clock()+timedelta(hours=1)).isoformat(),
            'references':fixture_references()}


def ingest(bridge,**values):
    svc,clock,vision=bridge
    return svc.ingest(json.dumps(envelope(clock,**values)).encode(),fixture=values.get('fixture',True))


def approve(svc,item,**values):
    return svc.decide(item['id'],json.dumps({'payload_digest':values.get('digest',item['digest']),
        'decision':values.get('decision','approve')}).encode(),
        fixture_only=values.get('fixture_only',True))


def test_five_photo_review_enrolls_only_temporary_fixture_and_one_advance_pass(bridge):
    svc,clock,vision=bridge;item=ingest(bridge)
    assert item['status']=='pending' and len(item['payload']['references'])==5
    assert not svc.access.snapshot()['passes'] and not vision.calls
    before=svc.store.profiles()
    approved=approve(svc,item)
    assert approved['status']=='approved' and approved['result']['pass_simulation_only']
    assert not approved['result']['real_actuation'] and not approved['result']['arrival_owner_approval_required']
    assert svc.store.profiles()==before and not svc.store.face_samples() and not vision.calls
    with svc.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==5
    assert approve(svc,item)==approved and len(svc.access.snapshot()['passes'])==1
    assert 'data_base64' not in json.dumps(approved) and 'embedding' not in json.dumps(approved)


@pytest.mark.parametrize('fault',['digest','count','duplicate','extra','window','encoding','checksum','simulation_photo','duplicate_key'])
def test_untrusted_bundle_fields_or_review_cannot_issue_a_pass(bridge,fault):
    svc,clock,vision=bridge
    if fault == 'digest':
        item=ingest(bridge)
        with pytest.raises(AccessError):approve(svc,item,digest='0'*64)
    else:
        body=envelope(clock)
        if fault=='count':body['references'].pop()
        if fault=='duplicate':body['references'][1]=body['references'][0]
        if fault=='extra':body['unlock_now']=True
        if fault=='window':body['valid_until']=(clock()+timedelta(hours=25)).isoformat()
        if fault=='encoding':body['references'][0]['data_base64']='$$PRIVATE$$'
        if fault=='checksum':body['references'][0]['sha256']='0'*64
        if fault=='simulation_photo':
            raw=b'not an owned generated photo';body['references'][0]={'sha256':hashlib.sha256(raw).hexdigest(),'data_base64':base64.b64encode(raw).decode()}
        raw=json.dumps(body).encode()
        if fault=='duplicate_key':raw=raw[:-1]+b',"visitor_name":"Different visitor"}'
        with pytest.raises(AccessError):svc.ingest(raw,fixture=True)
    assert not svc.access.snapshot()['passes'] and not svc.store.profiles() and not vision.calls


def test_payload_replay_cannot_replace_photos_or_name(bridge):
    svc,clock,vision=bridge;identifier=uuid.uuid4().hex
    first=ingest(bridge,request_id=identifier)
    assert ingest(bridge,request_id=identifier)==first
    with pytest.raises(AccessError,match='replay_payload_changed'):ingest(bridge,request_id=identifier,name='Another visitor')
    assert not svc.access.snapshot()['passes']


@pytest.mark.parametrize('action',['dismiss','revoke','expire'])
def test_reference_cleanup_for_rejected_revoked_and_expired_proposals(bridge,action):
    svc,clock,vision=bridge;item=ingest(bridge)
    if action=='expire':clock.advance(minutes=5);svc.cleanup()
    else:approve(svc,item,decision=action)
    assert not (svc.directory/item['id']).exists()
    with pytest.raises(AccessError):approve(svc,item)
    assert not svc.access.snapshot()['passes']


def test_consumed_pass_erases_reference_bytes_and_vectors_without_repeat_approval(bridge):
    svc,clock,vision=bridge;item=approve(svc,ingest(bridge));result=item['result']
    svc.access.owner.verify=lambda *args: (_ for _ in ()).throw(AssertionError('No repeated arrival approval'))
    arrival=svc.access.arrival(result['pass_id'],uuid.uuid4().hex,'probable_match',result['visitor_id'],'verified_mock')
    assert arrival['status']=='simulated_completed'
    svc.cleanup()
    assert not (svc.directory/item['id']).exists()
    with svc.store.connection() as db:assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==0
    assert svc.access.arrival(result['pass_id'],uuid.uuid4().hex,'probable_match',result['visitor_id'],'verified_mock')['status']=='denied'


def test_real_reference_adapter_runs_only_after_exact_owner_review_and_keeps_vectors_private(bridge):
    svc,clock,vision=bridge;item=ingest(bridge,fixture=False)
    assert not vision.calls
    with pytest.raises(AccessError,match='not_found'):approve(svc,item)
    approved=approve(svc,item,fixture_only=False)
    assert len(vision.calls)==5 and approved['result']['enrollment']=='local_temporary_references'
    assert 'embedding' not in json.dumps(approved) and '[0.6, 0.8]' not in json.dumps(approved)
    assert not svc.store.profiles() and not svc.store.face_samples()
    with svc.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==5


@pytest.mark.parametrize('structured',[False,True])
def test_partial_model_failure_cannot_leave_grant_or_references(bridge,structured):
    svc,clock,vision=bridge;item=ingest(bridge,fixture=False);vision.fail_at=3
    vision.structured=structured
    with pytest.raises(AccessError,match='temporary_enrollment_failed'):approve(svc,item,fixture_only=False)
    assert not svc.access.snapshot()['passes'] and not (svc.directory/item['id']).exists()
    assert 'PRIVATE MODEL BODY' not in json.dumps(svc.proposal(item['id']))
    with svc.store.connection() as db:assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==0


def test_changed_reference_on_disk_cannot_be_enrolled(bridge):
    svc,clock,vision=bridge;item=ingest(bridge)
    (svc.directory/item['id']/'0.jpg').write_bytes(b'CHANGED')
    with pytest.raises(AccessError,match='reference_changed_after_review'):approve(svc,item)
    assert not svc.access.snapshot()['passes'] and not (svc.directory/item['id']).exists()


def test_private_permissions_and_no_follow_policy(bridge,tmp_path):
    svc,clock,vision=bridge;item=ingest(bridge)
    assert svc.directory.stat().st_mode&0o077==0
    for photo in (svc.directory/item['id']).iterdir():assert photo.stat().st_mode&0o077==0
    policy=tmp_path/'policy';policy.write_text(json.dumps({'enabled':True,'proposal_key':'a'*64,'owner_key':'b'*64}));policy.chmod(0o600)
    assert BridgeAuth.from_file(svc.store,clock,policy).keys
    policy.chmod(0o644);assert not BridgeAuth.from_file(svc.store,clock,policy).keys
    alias=tmp_path/'alias';alias.symlink_to(policy)
    assert not BridgeAuth.from_file(svc.store,clock,alias).keys


@pytest.mark.parametrize('fault',['disabled','remote_client','wrong_key','wrong_scope','stale','changed_body','changed_path','replay'])
def test_loopback_signature_scopes_and_freshness_fail_closed(bridge,fault):
    svc,clock,vision=bridge;auth=svc.auth;auth.keys={'proposal':'a'*64,'owner':'b'*64}
    route='/api/enrollment/bridge/proposals';body=b'owned fixture';timestamp=str(int(clock().timestamp()));nonce=uuid.uuid4().hex
    headers={'x-doorbell-time':timestamp,'x-doorbell-nonce':nonce,'x-doorbell-signature':signature('a'*64,'POST',route,timestamp,nonce,body)}
    scope,host='proposal','127.0.0.1'
    if fault=='disabled':auth.keys={}
    if fault=='remote_client':host='192.0.2.20'
    if fault=='wrong_key':headers['x-doorbell-signature']='0'*64
    if fault=='wrong_scope':scope='owner'
    if fault=='stale':clock.advance(seconds=61)
    if fault=='changed_body':body=b'altered'
    if fault=='changed_path':route+='/decision'
    if fault=='replay':auth.verify(scope,'POST',route,headers,body,host)
    with pytest.raises(AccessError):auth.verify(scope,'POST',route,headers,body,host)
    assert not svc.access.snapshot()['passes']


def test_api_default_disabled_and_fixture_flow_has_no_camera_bot_or_live_photo(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False,enrollment_bridge_policy=tmp_path/'absent'))
    with TestClient(app) as client:
        assert client.get('/api/enrollment/bridge').json()['enabled'] is False
        assert client.post('/api/enrollment/bridge/proposals',content=b'PRIVATE PHOTO').status_code==503
        now=datetime.now(timezone.utc);body={'simulation_only':True,'request_id':uuid.uuid4().hex,'visitor_name':'<script>visitor</script>',
              'valid_from':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat()}
        item=client.post('/api/enrollment/simulation/proposals',json=body).json()
        response=client.post('/api/enrollment/simulation/proposals/'+item['id']+'/decision',json={
            'simulation_only':True,'payload_digest':item['digest'],'confirm_mock_owner':True,'decision':'approve'})
        assert response.json()['status']=='approved'
        assert not app.state.doorbell.store.events() and not app.state.doorbell.store.profiles()
        assert not app.state.doorbell.bots.deliveries() and not list(app.state.doorbell.store.faces.iterdir())
        assert client.post('/api/enrollment/simulation/proposals',content=b'x'*9000).status_code==413
        rejected=client.post('/api/enrollment/simulation/proposals',json=body|{'photos':['PRIVATE IMAGE BODY']})
        assert rejected.status_code==422 and 'PRIVATE IMAGE BODY' not in rejected.text
        assert client.post('/api/enrollment/simulation/proposals',json=body,headers={'Origin':'https://elsewhere.invalid'}).status_code==403


def test_signed_loopback_api_proposes_reviews_and_enrolls_mocked_images(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False,enrollment_bridge_policy=tmp_path/'absent'))
    svc=app.state.doorbell.enrollment;svc.auth.keys={'proposal':'a'*64,'owner':'b'*64};svc.vision=Vision()
    def headers(scope,method,route,body):
        timestamp=str(int(datetime.now(timezone.utc).timestamp()));nonce=uuid.uuid4().hex
        return {'X-Doorbell-Time':timestamp,'X-Doorbell-Nonce':nonce,'X-Doorbell-Signature':signature(svc.auth.keys[scope],method,route,timestamp,nonce,body)}
    with TestClient(app,client=('127.0.0.1',50000)) as client:
        raw=json.dumps(envelope(lambda:datetime.now(timezone.utc))).encode();route='/api/enrollment/bridge/proposals'
        response=client.post(route,content=raw,headers=headers('proposal','POST',route,raw))
        assert response.status_code==201;item=response.json();review=route+'/'+item['id']
        assert client.get(review,headers=headers('proposal','GET',review,b'')).status_code==403
        assert client.get(review,headers=headers('owner','GET',review,b'')).json()['digest']==item['digest']
        raw=json.dumps({'payload_digest':item['digest'],'decision':'approve'}).encode();route=review+'/decision'
        assert client.post(route,content=raw,headers=headers('proposal','POST',route,raw)).status_code==403
        fixture_route='/api/enrollment/simulation/proposals/'+item['id']+'/decision'
        fixture_decision={'simulation_only':True,'payload_digest':item['digest'],'confirm_mock_owner':True,
                          'decision':'approve'}
        assert client.post(fixture_route,json=fixture_decision).status_code==404
        assert svc.proposal(item['id'])['status']=='pending' and not svc.vision.calls
        assert not svc.access.snapshot()['passes']
        response=client.post(route,content=raw,headers=headers('owner','POST',route,raw))
        assert response.json()['status']=='approved' and len(svc.vision.calls)==5
        assert not app.state.doorbell.store.profiles() and not app.state.doorbell.store.face_samples()


def test_portable_client_uses_fixed_ssh_command_and_stdin_without_secrets(tmp_path,monkeypatch):
    raw=b'PRIVATE PHOTO BYTES';calls=[]
    class Result: returncode=0;stdout=b'{"status":"pending"}'
    def run(args,**kwargs):calls.append((args,kwargs));return Result()
    monkeypatch.setattr('agent_doorbell.bridge_client.subprocess.run',run)
    assert send('owner@pi.example','pi.example','proposal','POST','/api/enrollment/bridge/proposals',raw)['status']=='pending'
    args,kwargs=calls[0]
    assert kwargs['input']==raw and 'PRIVATE' not in ' '.join(args)
    assert 'StrictHostKeyChecking=yes' in args and 'ForwardAgent=no' in args
    assert not allowed_request('proposal','POST','/api/ring')
    with pytest.raises(ValueError):send('owner@pi.example;evil',None,'proposal','POST','/api/enrollment/bridge/proposals',raw)
    with pytest.raises(ValueError):send('owner@pi.example',None,'owner','POST','/api/settings',raw)
    assert len(calls)==1


def test_prepare_normalizes_owner_chosen_photos_without_network_or_enrollment(tmp_path):
    photos=[]
    for index,reference in enumerate(fixture_references()):
        path=tmp_path/f'{index}.jpg';path.write_bytes(base64.b64decode(reference['data_base64']));photos.append(path)
    now=datetime.now(timezone.utc);output=tmp_path/'.doorbell-bridge'/'bundle.json'
    result=prepare('Avery',now.isoformat(),(now+timedelta(hours=1)).isoformat(),photos,output)
    assert result['reference_count']==5 and not result['transferred'] and not result['enrolled']
    assert output.stat().st_mode&0o077==0 and len(json.loads(output.read_text())['references'])==5
    with pytest.raises(FileExistsError):prepare('Avery',now.isoformat(),(now+timedelta(hours=1)).isoformat(),photos,output)


def test_hardware_free_demo_is_complete_and_portable(tmp_path):
    path=Path(__file__).parents[1]/'scripts/bridge-demo.py'
    spec=importlib.util.spec_from_file_location('bridge_demo',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.run_demo(tmp_path)['status']=='hardware_free_flow_passed'


def test_concurrent_review_issues_one_pass_and_reference_set(bridge):
    svc,clock,vision=bridge;item=ingest(bridge)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:approve(svc,item),range(2)))
    assert results[0]==results[1] and len(svc.access.snapshot()['passes'])==1
    with svc.store.connection() as db:assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==5


def test_review_expiry_during_model_work_cannot_issue_pass(bridge):
    svc,clock,vision=bridge;item=ingest(bridge,fixture=False);original=vision.enroll
    def delayed(raw):
        result=original(raw)
        if len(vision.calls)==5:clock.advance(minutes=5)
        return result
    vision.enroll=delayed
    with pytest.raises(AccessError,match='enrollment_review_expired'):approve(svc,item,fixture_only=False)
    assert not svc.access.snapshot()['passes'] and not (svc.directory/item['id']).exists()


def test_restart_revokes_partial_grant_and_erases_reference_data(bridge):
    svc,clock,vision=bridge;item=ingest(bridge)
    visitor=svc.access.pass_request(item['id'],'Avery',5,clock(),clock()+timedelta(hours=1))
    svc.access.decide_pass(visitor['id'],visitor['digest'],True,'approve')
    with svc.store.connection() as db:db.execute("UPDATE enrollment_imports SET status='reviewing' WHERE id=?",(item['id'],))
    restarted=EnrollmentBridge(svc.store,svc.access,vision,svc.store.directory/'absent',clock)
    assert restarted.proposal(item['id'])['status']=='interrupted'
    assert svc.access.snapshot()['passes'][0]['status']=='revoked'
    assert not (svc.directory/item['id']).exists()


def test_reference_parent_symlink_never_deletes_other_directory(bridge,tmp_path):
    svc,clock,vision=bridge;item=ingest(bridge)
    retained=tmp_path/'retained';svc.directory.rename(retained);svc.directory.symlink_to(retained,target_is_directory=True)
    with pytest.raises(AccessError,match='private_reference_directory_invalid'):svc.erase_references(item['id'])
    assert (retained/item['id']/'0.jpg').is_file()


def test_malformed_policy_disables_bridge_without_crashing_app(bridge,tmp_path):
    svc,clock,vision=bridge;policy=tmp_path/'bad-policy';policy.write_text('[]');policy.chmod(0o600)
    auth=BridgeAuth.from_file(svc.store,clock,policy)
    assert not auth.keys and auth.issue


def test_owner_setup_check_is_read_only_and_enable_is_separately_authorized(tmp_path,monkeypatch):
    path=Path(__file__).parents[1]/'scripts/setup-enrollment-bridge.py'
    spec=importlib.util.spec_from_file_location('setup_bridge',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    app=tmp_path/'app';app.mkdir();(app/'.agent-doorbell-managed').touch();policy=tmp_path/'policy'
    state={'version':module.__version__,'enrollment_bridge':{'enabled':False}}
    monkeypatch.setattr(module,'status',lambda:state)
    calls=[]
    def restarted(args,**kwargs):calls.append(args);state['enrollment_bridge']['enabled']=True
    monkeypatch.setattr(module.subprocess,'run',restarted)
    fake_keys=iter(['a'*64,'b'*64]);monkeypatch.setattr(module.secrets,'token_hex',lambda _:next(fake_keys))
    assert module.setup(app,policy)['credentials_created'] is False and not policy.exists() and not calls
    with pytest.raises(ValueError,match='owner approval'):module.setup(app,policy,True,False)
    assert not policy.exists() and not calls
    result=module.setup(app,policy,True,True)
    assert result['bridge_enabled'] and result['credentials_created'] and not result['secrets_printed']
    assert policy.stat().st_mode&0o077==0
    assert calls==[['systemctl','--user','restart','agent-doorbell.service']]
    assert 'a'*64 not in json.dumps(result) and 'b'*64 not in json.dumps(result)
    before=policy.read_bytes();again=module.setup(app,policy,True,True)
    assert not again['credentials_created'] and policy.read_bytes()==before and len(calls)==1
    state['enrollment_bridge']['enabled']=False
    reloaded=module.setup(app,policy,True,True)
    assert reloaded['bridge_enabled'] and not reloaded['credentials_created']
    assert policy.read_bytes()==before and len(calls)==2


def test_two_controllers_atomically_claim_one_owner_review(bridge):
    from threading import Barrier
    svc,clock,vision=bridge;item=ingest(bridge)
    other=EnrollmentBridge(svc.store,svc.access,vision,svc.store.directory/'absent',clock)
    gate=Barrier(2)
    for controller in (svc,other):
        original=controller.proposal
        def held(*args,original=original,**kwargs):
            row=original(*args,**kwargs)
            if row['status']=='pending':gate.wait(timeout=3)
            return row
        controller.proposal=held
    def decision(controller):
        try:return approve(controller,item)['status']
        except AccessError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(decision,(svc,other)))
    assert results.count('approved')==1 and len(svc.access.snapshot()['passes'])==1
    assert not vision.calls


def test_terminal_reference_cleanup_retries_after_io_failure(bridge):
    svc,clock,vision=bridge;item=approve(svc,ingest(bridge));clock.advance(hours=1)
    original=svc.erase_references
    def failed(identifier):raise PermissionError('synthetic private I/O failure')
    svc.erase_references=failed
    with pytest.raises(PermissionError):svc.cleanup()
    assert (svc.directory/item['id']).is_dir()
    svc.erase_references=original;svc.cleanup()
    assert not (svc.directory/item['id']).exists()
    with svc.store.connection() as db:assert db.execute('SELECT COUNT(*) FROM enrollment_reference_samples').fetchone()[0]==0


def test_setup_ssh_wrapper_streams_reviewed_source_and_requires_owner_flag(tmp_path):
    import subprocess
    root=Path(__file__).parents[1];script=root/'scripts/setup-enrollment-bridge-pi.sh'
    bindir=tmp_path/'bin';bindir.mkdir();receipt=tmp_path/'receipt.json'
    ssh=bindir/'ssh';ssh.write_text('#!/usr/bin/env python3\nimport hashlib,json,os,sys\nfrom pathlib import Path\nraw=sys.stdin.buffer.read()\nPath(os.environ["BRIDGE_FIXTURE_RECEIPT"]).write_text(json.dumps({"args":sys.argv[1:],"source_sha":hashlib.sha256(raw).hexdigest()}))\n');ssh.chmod(0o700)
    env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],BRIDGE_FIXTURE_RECEIPT=str(receipt))
    denied=subprocess.run(['bash',str(script),'owner@pi.example','pi.example','--enable'],env=env,capture_output=True)
    assert denied.returncode==2 and not receipt.exists()
    subprocess.run(['bash',str(script),'owner@pi.example','pi.example','--enable','--owner-approves-proposal-and-review-scopes'],env=env,check=True)
    value=json.loads(receipt.read_text())
    assert value['source_sha']==hashlib.sha256((root/'scripts/setup-enrollment-bridge.py').read_bytes()).hexdigest()
    assert 'StrictHostKeyChecking=yes' in value['args'] and 'ForwardAgent=no' in value['args']
    assert value['args'][-1].endswith('--enable --owner-approves-proposal-and-review-scopes')
    assert 'git archive' not in value['args'][-1] and 'pip' not in value['args'][-1]
