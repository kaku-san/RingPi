"""Complete five-photo proposal → approval → one-use arrival using owned fixtures.

Install .[test] first. No hardware, SSH, live Bot, real person or credentials.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import uuid

from fastapi.testclient import TestClient
from agent_doorbell.app import create_app
from agent_doorbell.config import Config


def run_demo(directory):
    app = create_app(Config(data_dir=directory, camera='none', background=False,
                            enrollment_bridge_policy=directory/'absent-policy.json'))
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        proposal = client.post('/api/enrollment/simulation/proposals', json={
            'simulation_only': True, 'request_id': uuid.uuid4().hex, 'visitor_name': 'Avery demo visitor',
            'valid_from': now.isoformat(), 'valid_until': (now+timedelta(hours=1)).isoformat()}).json()
        assert proposal['status']=='pending' and len(proposal['payload']['references'])==5
        assert client.get('/api/access/simulation').json()['passes']==[]
        approved = client.post('/api/enrollment/simulation/proposals/'+proposal['id']+'/decision', json={
            'simulation_only':True, 'payload_digest':proposal['digest'], 'confirm_mock_owner':True,
            'decision':'approve'}).json()
        assert approved['status']=='approved'
        result = approved['result']
        arrival = {'simulation_only':True, 'request_id':uuid.uuid4().hex, 'face_status':'probable_match',
                   'face_visitor_id':result['visitor_id'], 'second_factor':'verified_mock'}
        route = '/api/access/simulation/passes/'+result['pass_id']+'/arrival'
        first = client.post(route, json=arrival).json()
        assert first['status']=='simulated_completed' and not first['real_actuation']
        arrival['request_id']=uuid.uuid4().hex
        assert client.post(route, json=arrival).json()['status']=='denied'
        app.state.doorbell.enrollment.cleanup()
        assert not app.state.doorbell.store.profiles() and not app.state.doorbell.store.face_samples()
        assert not list((directory/'temporary-references'/proposal['id']).glob('*.jpg'))
        return {'status':'hardware_free_flow_passed','generated_reference_images':5,
                'advance_approval_count':1,'repeat_arrival_owner_approval':False,'uses':1,
                'temporary_references_removed_after_use':True,'permanent_profiles_created':0,
                'real_photos_or_credentials':False,'ssh_or_live_bot':False,'real_actuation':False}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='doorbell-bridge-demo-') as directory:
        print(json.dumps(run_demo(Path(directory)), indent=2))
