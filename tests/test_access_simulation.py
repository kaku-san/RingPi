from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier
import uuid

from fastapi.testclient import TestClient
import pytest

from agent_doorbell.access_simulation import AccessError, AccessSimulation, SimulationLock, UnconfiguredHardware
from agent_doorbell.app import create_app
from agent_doorbell.config import Config
from agent_doorbell.store import Store


def request_id(): return uuid.uuid4().hex


class Clock:
    def __init__(self): self.value=datetime(2026,10,7,12,tzinfo=timezone.utc)
    def __call__(self): return self.value
    def advance(self, **kwargs): self.value+=timedelta(**kwargs)


class CountLock(SimulationLock):
    def __init__(self): super().__init__(); self.calls=[]
    def execute(self,action,request):
        self.calls.append((action,request))
        return super().execute(action,request)


@pytest.fixture
def service(tmp_path):
    clock=Clock(); lock=CountLock(); store=Store(tmp_path)
    return AccessSimulation(store,lock,clock),lock,clock


def new_pass(service, **values):
    return service.pass_request(values.get('request_id',request_id()),values.get('name','Avery visitor'),
        values.get('references',5),values.get('start',service.clock()),
        values.get('end',service.clock()+timedelta(hours=1)))


def approve(service,item):
    return service.decide_pass(item['id'],item['digest'],True,'approve')


def arrive(service,item,**values):
    return service.arrival(item['id'],values.get('request_id',request_id()),values.get('face','probable_match'),
        values.get('visitor',item['visitor_id']),values.get('factor','verified_mock'))


def test_advance_approval_allows_one_arrival_without_repeat_owner_approval(service):
    svc,lock,clock=service; item=new_pass(svc)
    assert arrive(svc,item)['code']=='pass_not_active' and not lock.calls
    approve(svc,item)
    def no_repeat(*args):raise AssertionError('Arrival must use the existing approval')
    svc.owner.verify=no_repeat
    result=arrive(svc,item)
    assert result['status']=='simulated_completed' and not result['arrival_owner_approval_required']
    assert not result['hardware_confirmed'] and not result['real_actuation']
    assert result['used_at'] and len(lock.calls)==1 and lock.calls[0][0]=='unlock'
    assert arrive(svc,item)['code']=='pass_not_active' and len(lock.calls)==1
    row=svc.snapshot()['passes'][0]
    assert row['status']=='used' and row['payload']['max_uses']==1
    assert not svc.store.profiles() and not svc.store.face_samples() and not svc.store.events()


@pytest.mark.parametrize('confirmed,digest',[(False,None),(True,'0'*64)])
def test_owner_decision_is_explicit_and_bound_to_exact_pass(service,confirmed,digest):
    svc,lock,clock=service; item=new_pass(svc)
    with pytest.raises(AccessError,match='mock_owner_confirmation_required'):
        svc.decide_pass(item['id'],digest or item['digest'],confirmed,'approve')
    assert svc.snapshot()['passes'][0]['status']=='pending' and not lock.calls
    assert svc.snapshot()['audit'][0]['code']=='mock_owner_confirmation_required'


@pytest.mark.parametrize('face',['unknown','uncertain','no_face','low_quality','multiple_faces'])
def test_unusable_or_conflicting_face_never_grants_entry(service,face):
    svc,lock,clock=service; item=new_pass(svc);approve(svc,item)
    assert arrive(svc,item,face=face)['code']=='visitor_face_not_accepted'
    assert not lock.calls and svc.snapshot()['passes'][0]['status']=='approved'


@pytest.mark.parametrize('factor',['missing','rejected'])
def test_face_alone_is_insufficient_without_mock_visitor_factor(service,factor):
    svc,lock,clock=service; item=new_pass(svc);approve(svc,item)
    assert arrive(svc,item,factor=factor)['code']=='visitor_second_factor_required'
    assert not lock.calls
    assert arrive(svc,item)['status']=='simulated_completed' and len(lock.calls)==1


def test_different_visitor_cannot_use_the_pass(service):
    svc,lock,clock=service; item=new_pass(svc);approve(svc,item)
    assert arrive(svc,item,visitor=request_id())['code']=='visitor_face_not_accepted'
    assert not lock.calls


def test_future_pass_is_approved_now_and_valid_only_inside_window(service):
    svc,lock,clock=service
    item=new_pass(svc,start=clock()+timedelta(hours=2),end=clock()+timedelta(hours=3));approve(svc,item)
    assert arrive(svc,item)['code']=='pass_not_yet_valid'
    clock.advance(hours=2)
    assert arrive(svc,item)['status']=='simulated_completed' and len(lock.calls)==1


@pytest.mark.parametrize('terminal',['revoke','expiry'])
def test_revoked_and_expired_passes_cannot_be_reapproved_or_used(service,terminal):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item)
    if terminal=='revoke':svc.decide_pass(item['id'],item['digest'],True,'revoke')
    else:clock.advance(hours=1)
    assert arrive(svc,item)['code']=='pass_not_active' and not lock.calls
    with pytest.raises(AccessError,match='pass_not_pending'):approve(svc,item)
    assert svc.snapshot()['passes'][0]['status']==('revoked' if terminal=='revoke' else 'expired')


@pytest.mark.parametrize('start,end',[(8,9),(0,25),(1,1),(0,-1)])
def test_pass_windows_are_bounded(service,start,end):
    svc,lock,clock=service
    start_time=clock()+timedelta(days=start) if start==8 else clock()+timedelta(hours=start)
    with pytest.raises(AccessError,match='invalid_pass_window'):
        new_pass(svc,start=start_time,end=clock()+timedelta(hours=end))
    assert not svc.snapshot()['passes'] and not lock.calls


def test_pending_approval_expires_and_payload_replay_cannot_change_scope(service):
    svc,lock,clock=service; identifier=request_id();item=new_pass(svc,request_id=identifier)
    assert new_pass(svc,request_id=identifier)['id']==item['id']
    with pytest.raises(AccessError,match='replay_payload_changed'):
        new_pass(svc,request_id=identifier,name='Different visitor')
    clock.advance(minutes=5)
    with pytest.raises(AccessError,match='pass_not_pending'):approve(svc,item)
    assert svc.snapshot()['passes'][0]['status']=='expired' and not lock.calls


def test_arrival_duplicate_is_frozen_and_changed_payload_cannot_replay(service):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item);identifier=request_id()
    denied=arrive(svc,item,request_id=identifier,factor='missing')
    assert arrive(svc,item,request_id=identifier,factor='missing')==denied
    with pytest.raises(AccessError,match='replay_payload_changed'):
        arrive(svc,item,request_id=identifier)
    good_id=request_id(); result=arrive(svc,item,request_id=good_id)
    assert arrive(svc,item,request_id=good_id)==result and len(lock.calls)==1


def test_lock_unknown_and_no_confirmation_never_claim_actuation(service):
    svc,lock,clock=service; item=new_pass(svc);approve(svc,item)
    lock.state='unknown'
    assert arrive(svc,item)['code']=='lock_state_unknown' and not lock.calls
    lock.state='locked'
    original=lock.execute
    def unconfirmed(*args):
        result=original(*args);result['simulation_confirmed']=False;return result
    lock.execute=unconfirmed
    result=arrive(svc,item)
    assert result['status']=='uncertain' and result['used_at'] is None and not result['hardware_confirmed']
    assert svc.snapshot()['passes'][0]['status']=='uncertain' and lock.state=='unknown'
    assert arrive(svc,item)['code']=='pass_not_active' and len(lock.calls)==1


@pytest.mark.parametrize('failure',['exception','structured_exception','hardware_claim','wrong_request','wrong_action','wrong_state'])
def test_bad_adapter_results_are_inert_uncertain_data(service,failure):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item)
    def bad(*args):
        if failure=='exception':raise RuntimeError('PRIVATE TOOL BODY')
        if failure=='structured_exception':raise AccessError('PRIVATE TOOL BODY')
        result={'simulation_confirmed':True,'hardware_confirmed':False,'simulated_state':'unlocked',
                'request_id':args[1],'action':args[0]}
        if failure=='hardware_claim':result['hardware_confirmed']=True
        if failure=='wrong_request':result['request_id']=request_id()
        if failure=='wrong_action':result['action']='lock'
        if failure=='wrong_state':result['simulated_state']='locked'
        return result
    lock.execute=bad
    result=arrive(svc,item)
    assert result['status']=='uncertain' and not result['hardware_confirmed'] and not result['real_actuation']
    assert 'PRIVATE TOOL BODY' not in json.dumps(svc.snapshot())


def test_commands_require_review_are_deduplicated_and_open_is_not_unlock(service):
    svc,lock,clock=service;identifier=request_id();command=svc.command(identifier,'unlock')
    assert not lock.calls
    with pytest.raises(AccessError,match='mock_owner_confirmation_required'):
        svc.decide_command(identifier,command['digest'],False,'approve')
    result=svc.decide_command(identifier,command['digest'],True,'approve')
    assert result['status']=='simulated_completed' and lock.state=='unlocked' and lock.door_position=='unknown'
    assert svc.decide_command(identifier,command['digest'],True,'approve')==result and len(lock.calls)==1
    with pytest.raises(AccessError,match='replay_payload_changed'):svc.command(identifier,'lock')
    opening=svc.command(request_id(),'open')
    opened=svc.decide_command(opening['id'],opening['digest'],True,'approve')
    assert opened['result']['code']=='unsupported_capability' and opened['status']=='failed'
    assert len(lock.calls)==1 and lock.door_position=='unknown'
    locking=svc.command(request_id(),'lock')
    assert svc.decide_command(locking['id'],locking['digest'],True,'approve')['status']=='simulated_completed'
    assert lock.state=='locked'


def test_command_expiry_and_dismissal_do_not_run_adapter(service):
    svc,lock,clock=service;item=svc.command(request_id(),'unlock');clock.advance(minutes=5)
    with pytest.raises(AccessError,match='proposal_expired'):
        svc.decide_command(item['id'],item['digest'],True,'approve')
    dismissed=svc.command(request_id(),'lock')
    assert svc.decide_command(dismissed['id'],dismissed['digest'],True,'dismiss')['status']=='dismissed'
    assert not lock.calls


def test_restart_preserves_used_pass_and_blocks_interrupted_action(service):
    svc,lock,clock=service;used=new_pass(svc);approve(svc,used);arrive(svc,used)
    interrupted=new_pass(svc);approve(svc,interrupted)
    command=svc.command(request_id(),'unlock')
    with svc.store.connection() as db:
        db.execute("UPDATE access_passes SET status='executing' WHERE id=?",(interrupted['id'],))
        db.execute("UPDATE access_commands SET status='executing' WHERE id=?",(command['id'],))
    restarted=AccessSimulation(svc.store,CountLock(),clock)
    assert arrive(restarted,used)['code']=='pass_not_active'
    assert arrive(restarted,interrupted)['code']=='pass_not_active'
    assert restarted.decide_command(command['id'],command['digest'],True,'approve')['status']=='uncertain'
    assert not restarted.simulator.calls


def test_atomic_pass_claim_allows_only_one_of_two_concurrent_arrivals(service):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item)
    other=AccessSimulation(svc.store,CountLock(),clock);barrier=Barrier(2)
    for controller in (svc,other):
        original=controller.visitor.verify
        def held(*args,original=original):original(*args);barrier.wait(timeout=3)
        controller.visitor.verify=held
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(arrive,controller,item) for controller in (svc,other)]
        results=[future.result(timeout=5) for future in futures]
    assert sum(result['status']=='simulated_completed' for result in results)==1
    assert len(lock.calls)+len(other.simulator.calls)==1
    assert svc.snapshot()['passes'][0]['status']=='used'


def test_duplicate_concurrent_arrival_is_reserved_before_any_action(service):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item)
    other=AccessSimulation(svc.store,CountLock(),clock);identifier=request_id()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda controller:arrive(controller,item,request_id=identifier),(svc,other)))
    assert len(lock.calls)+len(other.simulator.calls)==1
    assert arrive(other,item,request_id=identifier)['status']=='simulated_completed'
    assert all(result['status'] in {'executing','simulated_completed'} for result in results)


def test_simulation_cannot_connect_real_hardware_or_grok_transport(service):
    svc,lock,clock=service
    with pytest.raises(ValueError,match='Only a SimulationLock'):AccessSimulation(svc.store,UnconfiguredHardware())
    with pytest.raises(AccessError,match='hardware_unconfigured'):svc.hardware.execute('unlock',request_id())
    with pytest.raises(AccessError,match='grok_return_transport_unconfigured'):
        svc.transport.receive({'text':'unlock now','photos':['untrusted image']})
    assert not lock.calls and svc.status()['hardware']=='unconfigured'


def test_simulation_requests_cannot_upload_enroll_or_touch_camera_bots(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False))
    bell=app.state.doorbell
    async def no_capture(*args,**kwargs):raise AssertionError('Simulation must not capture')
    def no_bot(*args,**kwargs):raise AssertionError('Simulation must not send')
    bell.camera.capture=no_capture;bell.bots.enqueue_event=no_bot
    with TestClient(app) as client:
        before=client.get('/api/bots').json()
        body={'simulation_only':True,'request_id':request_id(),'action':'unlock'}
        for extra in ({'source':'caption'},{'photos':['BASE64_PRIVATE']},{'hardware':True},{'embedding':[.2,.8]}):
            assert client.post('/api/access/simulation/commands',json=body|extra).status_code==422
        for flag in (False,'true',1):
            assert client.post('/api/access/simulation/commands',json=body|{'simulation_only':flag}).status_code==422
        assert client.post('/api/access/simulation/commands',json=body,headers={'Origin':'https://unrelated.invalid'}).status_code==403
        response=client.post('/api/access/simulation/commands',json=body)
        assert response.status_code==201 and response.json()['status']=='pending'
        item=response.json()
        approved=client.post('/api/access/simulation/commands/'+item['id']+'/decision',json={
            'simulation_only':True,'payload_digest':item['digest'],'confirm_mock_owner':True,'decision':'approve'})
        assert approved.json()['result']['real_actuation'] is False
        assert client.get('/api/bots').json()==before
        assert not bell.store.profiles() and not bell.store.face_samples() and not bell.store.events()
        assert not list(bell.store.faces.iterdir()) and not list(bell.store.media.iterdir())
        assert app.state.doorbell.status()['access_simulation']['grok_return_transport']=='unconfigured'


def test_local_pass_api_uses_separate_temporary_metadata_and_bounded_bodies(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False))
    with TestClient(app) as client:
        now=datetime.now(timezone.utc)
        body={'simulation_only':True,'request_id':request_id(),'visitor_name':'<img src=x> visitor',
              'mock_reference_count':5,'valid_from':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat()}
        response=client.post('/api/access/simulation/passes',json=body)
        assert response.status_code==201
        item=response.json()
        assert item['payload']['reference_source']=='simulation_fixture'
        assert client.get('/api/profiles').json()['profiles']==[]
        approved=client.post('/api/access/simulation/passes/'+item['id']+'/decision',json={
            'simulation_only':True,'payload_digest':item['digest'],'confirm_mock_owner':True,'decision':'approve'})
        assert approved.json()['status']=='approved'
        arrival={'simulation_only':True,'request_id':request_id(),'face_status':'probable_match',
                 'face_visitor_id':item['visitor_id'],'second_factor':'verified_mock'}
        result=client.post('/api/access/simulation/passes/'+item['id']+'/arrival',json=arrival)
        assert result.json()['status']=='simulated_completed' and result.json()['real_actuation'] is False
        assert client.post('/api/access/simulation/commands',content=(b'x'*5000 for _ in range(2))).status_code==413
        assert client.post('/api/access/simulation/passes',json=body|{'photos':['x'*9000]}).status_code==413
        assert client.post('/api/access/simulation/passes',json=body|{'photos':['pretend image']}).status_code==422
        assert not app.state.doorbell.store.face_samples()


def test_capacity_and_audit_are_bounded_without_issuing_extra_grants(service):
    svc,lock,clock=service
    for _ in range(200):svc.command(request_id(),'unlock')
    with pytest.raises(AccessError,match='simulation_history_limit'):svc.command(request_id(),'unlock')
    assert not lock.calls
    with svc.store.connection() as db:
        for _ in range(1100):svc.audit(db,'fixture',request_id(),'denied','mock_fixture')
        assert db.execute('SELECT COUNT(*) FROM access_audit').fetchone()[0]==1000
    assert all('visitor_name' not in row and 'photos' not in row for row in svc.snapshot()['audit'])


def test_exact_pass_creation_replay_returns_expired_receipt_without_reissuing(service):
    svc,lock,clock=service;identifier=request_id();start=clock();end=start+timedelta(hours=1)
    item=new_pass(svc,request_id=identifier,start=start,end=end);approve(svc,item)
    clock.advance(hours=2)
    old=new_pass(svc,request_id=identifier,start=start,end=end)
    assert old['id']==item['id'] and old['status']=='expired' and not lock.calls


@pytest.mark.parametrize('kind',['command','pass'])
def test_approval_expiring_during_verification_cannot_authorize(service,kind):
    svc,lock,clock=service
    item=svc.command(request_id(),'unlock') if kind=='command' else new_pass(svc)
    original=svc.owner.verify
    def delayed(*args):
        accepted=original(*args);clock.advance(minutes=5);return accepted
    svc.owner.verify=delayed
    with pytest.raises(AccessError,match='proposal_expired|pass_not_pending'):
        if kind=='command':svc.decide_command(item['id'],item['digest'],True,'approve')
        else:approve(svc,item)
    assert not lock.calls
    assert svc.snapshot()['commands' if kind=='command' else 'passes'][0]['status']=='expired'


def test_pass_expiring_during_visitor_verification_cannot_be_consumed(service):
    svc,lock,clock=service;item=new_pass(svc);approve(svc,item)
    original=svc.visitor.verify
    def delayed(*args):original(*args);clock.advance(hours=1)
    svc.visitor.verify=delayed
    result=arrive(svc,item)
    assert result['status']=='denied' and result['code']=='pass_not_active'
    assert not lock.calls and svc.snapshot()['passes'][0]['status']=='expired'
