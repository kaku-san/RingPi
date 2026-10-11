from io import BytesIO
import asyncio
import os
from pathlib import Path
import json

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from agent_doorbell.app import create_app
from agent_doorbell.config import Config
from agent_doorbell.local_vision import LocalVision, assess_face_gate, clean_image, FaceError, MODELS, FACE_MODEL


def photo():
    output=BytesIO();Image.new("RGB",(320,240),"black").save(output,"JPEG")
    return output.getvalue()


def test_face_gate_accepts_a_clear_quarter_frame_and_rejects_a_soft_or_tiny_face():
    # 25% and 30% of a 960-wide frame, with the measured clear-face sharpness.
    assert assess_face_gate(240, 37.61, 960) == "usable"
    assert assess_face_gate(288, 37.61, 960) == "usable"
    assert assess_face_gate(222.6, 37.61, 960) == "usable"
    assert assess_face_gate(219.3, 19.91, 960) == "low_quality"
    assert assess_face_gate(60, 80, 960) == "low_quality"
    assert assess_face_gate(240, 29.9, 960) == "low_quality"


def test_match_conservative_threshold_ambiguity_and_model_version(tmp_path):
    np=pytest.importorskip("numpy")
    vision=LocalVision(tmp_path);vision.np=np
    vector=np.array([1.,0.])
    def sample(profile,score,model=MODELS[FACE_MODEL][1]):
        return {"profile_id":profile,"embedding":[score,(1-score*score)**.5],"model":model}
    assert vision.match(vector,[sample("a",.7)])["status"]=="probable_match"
    assert vision.match(vector,[sample("a",.7),sample("b",.65)])["status"]=="uncertain"
    assert vision.match(vector,[sample("a",.4)])["status"]=="uncertain"
    assert vision.match(vector,[sample("a",.1)])["status"]=="unknown"
    assert vision.match(vector,[sample("a",.99,"different-model")])["status"]=="not_enrolled"


def test_enrollment_requires_consent_stays_local_and_deletes_biometrics(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera="synthetic",background=False))
    class TestVision:
        detail="Test fixture"
        status={"available":True}
        def enroll(self,raw):return clean_image(raw),[.2,.8],{"fixture":0}
    app.state.doorbell.local_vision=TestVision()
    with TestClient(app) as client:
        profile=client.post("/api/profiles",json={"name":"Avery"}).json()
        path=f'/api/profiles/{profile["id"]}/enroll'
        assert client.post(path,content=b"not an image").status_code==422
        assert client.post(path,content=photo()).status_code==201
        store=app.state.doorbell.store
        assert len(store.face_samples())==1
        profiles=client.get("/api/profiles").json()
        assert profiles["profiles"][0]["face_samples"]==1 and "embedding" not in json.dumps(profiles)
        files=list((tmp_path/"faces"/profile["id"]).glob("*.jpg"))
        assert len(files)==1 and files[0].stat().st_mode & 0o777 == 0o600
        event=store.add_event("ring","physical")
        analysis=json.dumps({"status":"probable_match","profile_id":profile["id"],"similarity":.9,"objects":[]})
        store.update_event(event["id"],person_id=profile["id"],person_status="probable_match",face_status="probable_match",frames=1,local_analysis=analysis)
        assert store.event(event["id"])["description"] == "A person was detected. Probably Avery (0.90)"
        manual=store.add_event("ring","physical")
        store.update_event(manual["id"],person_id=profile["id"],person_status="manual",local_analysis=analysis)
        assert client.delete(f'/api/profiles/{profile["id"]}/enrollment').status_code==204
        assert not store.face_samples() and not files[0].exists()
        assert store.event(event["id"])["person_id"] is None
        assert store.event(event["id"])["local_analysis"]["profile_id"] is None
        assert store.event(event["id"])["description"] is None
        assert store.event(event["id"])["description_status"] == "no_confident_observations"
        assert store.event(manual["id"])["person_id"]==profile["id"] and store.event(manual["id"])["person_status"]=="manual"
        assert store.event(manual["id"])["local_analysis"]["profile_id"] is None


def test_photo_limits_and_invalid_files():
    with pytest.raises(FaceError):clean_image(b"x"*8_000_001)
    with pytest.raises(FaceError):clean_image(b"unsupported")


def test_edit_profile_preserves_enrollment_and_updates_local_labels(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera="none",background=False))
    with TestClient(app) as client:
        profile=client.post('/api/profiles',json={'name':'Avery','note':'Family'}).json()
        store=app.state.doorbell.store
        store.add_face_sample(profile['id'],photo(),[.2,.8],MODELS[FACE_MODEL][1])
        event=store.add_event('test','browser')
        store.update_event(event['id'],person_id=profile['id'],person_status='manual')
        updated=client.put('/api/profiles/'+profile['id'],json={'name':' Avery local ','note':' New note '})
        assert updated.status_code==200
        assert updated.json()['name']=='Avery local' and updated.json()['face_samples']==1
        assert store.event(event['id'])['person_name']=='Avery local'
        assert len(store.face_samples())==1
        assert client.put('/api/profiles/'+profile['id'],json={'name':' '}).status_code==422
        assert client.put('/api/profiles/missing',json={'name':'Nobody'}).status_code==404


@pytest.mark.parametrize('status', ['probable_match','unknown','uncertain','no_face','low_quality','multiple_faces','not_enrolled'])
def test_chosen_photo_recognition_is_explicit_ephemeral_and_local(tmp_path,status):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False))
    bell=app.state.doorbell
    profile=bell.store.add_profile('Avery local','')
    bell.store.add_face_sample(profile['id'],photo(),[.2,.8],MODELS[FACE_MODEL][1])
    calls=[]
    class TestVision:
        detail='Fixture ready'
        status={'available':True}
        def recognize_photo(self,raw,samples):
            clean_image(raw);calls.append(len(samples))
            # Even an accidental extra local field must never expose vectors.
            return {'status':status,'profile_id':profile['id'] if status=='probable_match' else None,
                    'similarity':.7 if status=='probable_match' else None,'quality':'usable',
                    'timings_ms':{'yunet':1,'sface':2},'embedding':[.2,.8]}
    bell.local_vision=TestVision()
    async def no_capture(*args,**kwargs):raise AssertionError('Recognition test must not use camera')
    bell.camera.capture=no_capture
    def no_bot(*args,**kwargs):raise AssertionError('Recognition test must not send to a Bot')
    bell.bots.enqueue_event=no_bot
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in bell.store.faces.rglob('*.jpg')}
    with TestClient(app) as client:
        response=client.post('/api/recognition/test',content=photo())
        assert response.status_code==200
        result=response.json()
        assert result['status']==status and result['identity_assurance']=='probable_enrolled_match_only'
        assert result['person_name']==('Avery local' if status=='probable_match' else None)
        assert not result['stored'] and not result['camera_used'] and not result['bot_sent']
        assert 'embedding' not in result and 'objects' not in result
        assert client.post('/api/recognition/test',content=b'invalid').status_code==422
        assert client.post('/api/recognition/test',content=b'x'*8_000_001).status_code==413
        assert client.post('/api/recognition/test',content=photo(),headers={'Origin':'https://unrelated.invalid'}).status_code==403
    assert not bell.store.events() and not list(bell.store.media.iterdir())
    assert before=={str(p.relative_to(tmp_path)):p.read_bytes() for p in bell.store.faces.rglob('*.jpg')}
    assert len(bell.store.face_samples())==1


def test_recognition_model_prerequisites_and_deleted_enrollment(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False))
    bell=app.state.doorbell
    class TestVision:
        detail='Fixture ready'
        status={'available':True}
        def recognize_photo(self,raw,samples):
            bell.store.delete_face_samples(samples[0]['profile_id'])
            return {'status':'probable_match','profile_id':samples[0]['profile_id'],'similarity':.9}
    with TestClient(app) as client:
        assert client.post('/api/recognition/test?consent=true',content=photo()).status_code==409
        bell.local_vision=TestVision()
        assert client.post('/api/recognition/test?consent=true',content=photo()).status_code==409
        profile=bell.store.add_profile('Avery local','')
        bell.store.add_face_sample(profile['id'],photo(),[.2,.8],MODELS[FACE_MODEL][1])
        result=client.post('/api/recognition/test?consent=true',content=photo()).json()
        assert result['status']=='enrollment_deleted' and result['person_name'] is None and result['profile_id'] is None
        assert result['similarity'] is None


@pytest.mark.parametrize('quality',['no_face','low_quality','multiple_faces'])
def test_enrollment_quality_rejection_has_no_saved_sample(tmp_path,quality):
    app=create_app(Config(data_dir=tmp_path,camera='none',background=False))
    class RejectedVision:
        detail='Fixture ready'
        status={'available':True}
        def enroll(self,raw):raise FaceError({'no_face':'No clear face found. Choose a front-facing photo.',
                                             'low_quality':'Face is too small or blurry. Choose a sharper, closer photo.',
                                             'multiple_faces':'Choose a photo containing exactly one person.'}[quality])
    app.state.doorbell.local_vision=RejectedVision()
    with TestClient(app) as client:
        profile=client.post('/api/profiles',json={'name':'Avery'}).json()
        response=client.post('/api/profiles/'+profile['id']+'/enroll?consent=true',content=photo())
        assert response.status_code==422 and response.json()['detail']
        assert not app.state.doorbell.store.face_samples()
        assert not list(app.state.doorbell.store.faces.iterdir())


def test_recognize_photo_reuses_face_pipeline_without_objects_or_persistence(tmp_path):
    np=pytest.importorskip('numpy')
    vision=LocalVision(tmp_path);vision.cv=True;vision.np=np
    vision.decode=lambda jpeg:jpeg
    vision.extract=lambda image:(np.array([1.,0.]),'usable',{'yunet':1,'sface':2})
    samples=[{'profile_id':'fixture','embedding':[1.,0.],'model':MODELS[FACE_MODEL][1]}]
    result=vision.recognize_photo(photo(),samples)
    assert result['status']=='probable_match' and result['quality']=='usable'
    assert 'embedding' not in result and 'objects' not in result
    vision.extract=lambda image:(None,'low_quality',{'yunet':1})
    assert vision.recognize_photo(photo(),samples)['status']=='low_quality'
    assert not list(tmp_path.iterdir())


def test_enrollment_deleted_during_analysis_cannot_restore_identity(tmp_path):
    app=create_app(Config(data_dir=tmp_path,camera="synthetic",background=False,burst_frames=1))
    doorbell=app.state.doorbell
    profile=doorbell.store.add_profile("Avery", "")
    doorbell.store.add_face_sample(profile["id"],photo(),[.2,.8],MODELS[FACE_MODEL][1])
    class DeletingVision:
        objects=None
        status={"available":True}
        def analyze(self,raw,samples):
            assert samples
            doorbell.store.delete_face_samples(profile["id"])
            return {"status":"probable_match","profile_id":profile["id"],"similarity":.9,"objects":[],"timings_ms":{}}
    doorbell.local_vision=DeletingVision()
    event=doorbell.store.add_event("test","browser")
    asyncio.run(doorbell.record(event["id"]))
    result=doorbell.store.event(event["id"])
    assert result["face_status"]=="enrollment_deleted" and result["person_id"] is None
    assert result["local_analysis"]["profile_id"] is None and result["face_similarity"] is None
    assert not doorbell.store.face_samples()


def test_real_pinned_models_load_and_run_without_face_data():
    directory=os.environ.get("DOORBELL_TEST_MODELS_DIR")
    if not directory:pytest.skip("Official model weights are installed only for explicit local validation")
    vision=LocalVision(Path(directory))
    assert vision.status["available"] and vision.status["objects_available"]
    result=vision.analyze(photo(),[])
    assert result["status"]=="no_face" and isinstance(result["objects"],list)
    assert result["timings_ms"]["yunet"]>=0 and result["timings_ms"]["nanodet"]>=0


def test_object_analysis_keeps_general_detections_and_rejects_invalid_outputs(tmp_path):
    np=pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    vision=LocalVision(tmp_path)
    class ObjectFixture:
        def infer(self,image):
            assert image.shape==(416,416,3)
            # Official COCO indices: person, cat, backpack, dog. Invalid and
            # below-threshold predictions must not become scene observations.
            return np.array([[0,0,10,10,score,label] for score,label in
                             [(.9,0),(.8,15),(.7,24),(.6,16),(.49,2),(.9,-1),
                              (.9,80),(.9,float("nan")),(.9,15.5),(float("nan"),2),(1.1,2)]])
    vision.objects=ObjectFixture()
    result=vision.analyze(photo(),[])
    assert result["objects"]==[{"label":"person","confidence":.9},
                               {"label":"cat","confidence":.8},
                               {"label":"backpack","confidence":.7},
                               {"label":"dog","confidence":.6}]
    assert len(vision.status["object_classes"])==80
    assert "mailman" not in vision.status["object_classes"] and "package" not in vision.status["object_classes"]


@pytest.mark.parametrize("encoded_id", ["%2E", "%2E%2E"])
def test_unknown_profile_delete_cannot_remove_local_files(tmp_path, encoded_id):
    app=create_app(Config(data_dir=tmp_path,camera="none",background=False))
    with TestClient(app) as client:
        profile=client.post("/api/profiles",json={"name":"Avery"}).json()
        store=app.state.doorbell.store
        sample_id=store.add_face_sample(profile["id"],photo(),[.2,.8],MODELS[FACE_MODEL][1])
        marker=tmp_path/"keep-me.txt";marker.write_text("local fixture")
        assert client.delete('/api/profiles/'+encoded_id).status_code==404
        store.delete_face_samples("..")
        assert marker.read_text()=="local fixture"
        assert (tmp_path/"faces"/profile["id"]/(sample_id+".jpg")).is_file()
        assert store.profiles()[0]["face_samples"]==1


def test_new_private_storage_directories_and_files(tmp_path):
    app=create_app(Config(data_dir=tmp_path/"private-data",camera="none",background=False))
    store=app.state.doorbell.store
    profile=store.add_profile("Avery", "")
    sample=store.add_face_sample(profile["id"],photo(),[.2,.8],MODELS[FACE_MODEL][1])
    for directory in [store.directory,store.faces,store.media,store.faces/profile["id"]]:
        assert directory.stat().st_mode & 0o777 == 0o700
    for path in [store.path,store.faces/profile["id"]/(sample+".jpg")]:
        assert path.stat().st_mode & 0o777 == 0o600
