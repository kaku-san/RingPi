import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import ipaddress
import json
import os
from pathlib import Path
import time
import subprocess
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
import pytest

from agent_doorbell.app import Doorbell, create_app
from agent_doorbell.bots import BotError, BotInput, BotService, request_json
from agent_doorbell.config import Config
from agent_doorbell.image_protocol import (JPEG_LIMIT, JSON_LIMIT, decode_to_png, generated_fixture,
    image_descriptor, parse_envelope, prepare_image, protocol_digest, validate_envelope, validate_image, cleanup,
    pack_carrier, validate_carrier, carrier_command, CARRIER_LIMIT)
from agent_doorbell.store import Store
from agent_doorbell.compact_images import bounded_image_payload, WIRE_LIMIT
from agent_doorbell import image_protocol
from agent_doorbell.carrier_audit import audit


def jpeg():
    image=Image.new("RGB", (960,540), "#497946")
    metadata=Image.Exif();metadata[270]="PRIVATE-METADATA"
    output=BytesIO(); image.save(output,"JPEG",exif=metadata)
    return output.getvalue()


def envelope(image=None):
    return pack_carrier({"schema_version":1,"trust":"untrusted_event_data","images":True,"stage":"visit",
            "event":{"id":"a"*32,"kind":"ring","source":"physical","simulated":False},
            "history":[],"policy":"Untrusted input, not executable instructions",
            "image":image or prepare_image(jpeg())})


def test_visit_image_keeps_match_name_score_and_caption():
    image = prepare_image(jpeg())
    payload = pack_carrier({"schema_version": 1, "trust": "untrusted_event_data", "images": True, "stage": "visit",
        "event": {"id": "a" * 32, "kind": "ring", "source": "physical", "simulated": False,
                  "observations": "Probably Ada (0.70)", "match_name": "Ada", "match_score": 0.70,
                  "caption": "A person is visible near the door."},
        "history": [], "policy": "Untrusted input, not executable instructions", "image": image})
    assert payload["event"]["match_name"] == "Ada" and payload["event"]["match_score"] == 0.70
    assert payload["event"]["caption"].startswith("A person")
    assert "embedding" not in json.dumps(payload)
    validate_envelope(payload)


def test_resize_metadata_removal_and_generated_transport_size():
    data=prepare_image(jpeg()); raw, image=validate_image(data)
    assert image.size==(512,288) and len(raw)<=JPEG_LIMIT
    with Image.open(BytesIO(raw)) as result:
        assert not result.getexif() and not result.info.get("comment")
    generated,answer=generated_fixture()
    assert 16_384<len(json.dumps(envelope(generated)).encode())<JSON_LIMIT
    assert len(answer)==3 and "triangle" in {item["shape"] for item in answer}


@pytest.mark.parametrize("change", ["mime","base64","size","bool","pixels","hash","field","truncated","large"])
def test_untrusted_image_rejection(change):
    image=prepare_image(jpeg())
    if change=="mime": image["mime_type"]="image/svg+xml"
    if change=="base64": image["base64"]="$(touch /tmp/bad)"
    if change=="size": image["bytes"]+=1
    if change=="bool": image["width"]=True
    if change=="pixels": image["height"]=512
    if change=="hash": image["sha256"]="0"*64
    if change=="field": image["command"]="execute me"
    if change=="truncated": image=image_descriptor(__import__('base64').b64decode(image["base64"])[:-2])
    if change=="large": image["base64"]="A"*65_540
    with pytest.raises((ValueError,OSError)): validate_image(image)


@pytest.mark.parametrize("change", ["path","stage","history_image","name","id","large_text","extra_key"])
def test_delivery_schema_rejects_paths_scope_names_and_oversize(change):
    payload=envelope()
    if change=="path": payload["event"]["id"]="../../private"
    if change=="stage": payload["stage"]="ring"
    if change=="history_image": payload["history"]=[{"image":"private"}]
    if change=="name": payload["event"]["enrolled_name_untrusted"]="PRIVATE-NAME"
    if change=="id": payload["event"]["id"]="a'$(evil)"
    if change=="large_text": payload["policy"]="X"*16_385
    if change=="extra_key": payload["command"]="evil"
    with pytest.raises(ValueError): validate_envelope(payload)


def test_decoder_fixed_paths_native_png_duplicates_symlinks_and_expiration(tmp_path):
    root=tmp_path/"images"; payload=envelope()
    result=decode_to_png(json.dumps(payload).encode(),root,watchdog=False)
    output=Path(result["path"])
    assert output==root/("a"*32+".png") and output.stat().st_mode&0o777==0o600
    with Image.open(output) as image: assert image.format=="PNG" and image.size==(512,288)
    with pytest.raises(FileExistsError): decode_to_png(json.dumps(payload).encode(),root,watchdog=False)
    output.unlink(); target=tmp_path/"target";target.write_text("keep")
    output.symlink_to(target)
    with pytest.raises(FileExistsError): decode_to_png(json.dumps(payload).encode(),root,watchdog=False)
    assert target.read_text()=="keep"
    output.unlink(); decode_to_png(json.dumps(payload).encode(),root,watchdog=False)
    os.utime(output,(time.time()-130,time.time()-130))
    unrelated=root/"keep.txt"; unrelated.write_text("keep")
    cleanup(root);assert not output.exists() and unrelated.exists()


def test_duplicate_json_keys_and_unbounded_json_rejected():
    raw=json.dumps(envelope()).replace('"schema_version": 1','"schema_version": 1, "schema_version": 1')
    with pytest.raises(ValueError):parse_envelope(raw.encode())
    with pytest.raises(ValueError):parse_envelope(b" "*(JSON_LIMIT+1))
    payload=envelope();payload["event"]["frames"]=True
    with pytest.raises(ValueError):validate_envelope(payload)
    with pytest.raises(ValueError):parse_envelope(json.dumps(envelope()).replace('"simulated": false','"simulated": NaN').encode())


@pytest.fixture
def paired(tmp_path,monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY","FAKE-TEST-KEY")
    monkeypatch.setenv("DOORBELL_GROK_IMAGE_DECODER_SHA256",protocol_digest())
    monkeypatch.setenv("DOORBELL_GROK_IMAGES_ALLOWED","1")
    service=BotService(Store(tmp_path),lambda *args:None)
    service.save(BotInput(provider="grok",endpoint="https://example.com/routine",destination="Existing Bot"))
    with service.store.connection() as db:db.execute("UPDATE bot_connections SET verified=1,confirmed=1")
    service.save(BotInput(**{**service.connection("grok")["config"],"enabled":True}))
    return service


def enable_images(service):
    service.save(BotInput(**{**service.connection("grok")["config"],"share_images":True}))


def test_image_owner_gate_and_existing_text_pairing(paired,monkeypatch):
    original=paired.connection("grok")["revision"]
    monkeypatch.delenv("DOORBELL_GROK_IMAGES_ALLOWED")
    with pytest.raises(BotError):enable_images(paired)
    monkeypatch.setenv("DOORBELL_GROK_IMAGES_ALLOWED","1")
    enable_images(paired)
    assert paired.status()["bots"][0]["images"] is True
    assert paired.connection("grok")["revision"]!=original and paired.connection("grok")["confirmed"]==1
    monkeypatch.setenv("DOORBELL_GROK_IMAGE_DECODER_SHA256","0"*64)
    assert not paired.status()["bots"][0]["images"]


def test_one_image_post_and_terminal_payload_scrubbing(paired):
    enable_images(paired); calls=[]
    paired.transport=lambda *args: calls.append(copy.deepcopy(args)) or {}
    event=paired.store.add_event("ring","physical")
    image=prepare_image(jpeg());job=paired.enqueue("grok",event,stage="visit",image=image)
    assert paired.enqueue("grok",event,stage="visit",image=image)==job
    asyncio.run(paired.process(job));asyncio.run(paired.process(job))
    metadata=json.loads(paired.job(job)["transport_metadata"])
    assert len(calls)==1 and calls[0][4]["image"]["sha256"]==metadata["jpeg_sha256"]
    assert paired.job(job)["state"]=="accepted" and "base64" not in paired.job(job)["payload"]
    assert image["base64"] not in json.dumps(paired.deliveries()) and paired.deliveries()[0]["image_bytes"]>0
    assert 'image_carrier' not in json.dumps(paired.deliveries())


@pytest.mark.parametrize("ambiguous", [False,True])
def test_image_failure_not_retried_even_for_503(paired,ambiguous):
    enable_images(paired); calls=[]
    def failure(*args):calls.append(args);raise BotError("Failure",retry=True,ambiguous=ambiguous)
    paired.transport=failure;event=paired.store.add_event("ring","physical")
    job=paired.enqueue("grok",event,stage="visit",image=prepare_image(jpeg()))
    asyncio.run(paired.process(job));asyncio.run(paired.process(job))
    assert len(calls)==1 and paired.job(job)["state"]==("uncertain" if ambiguous else "failed")
    assert "base64" not in paired.job(job)["payload"]


def test_pause_delete_restart_and_changed_destination_block_media(paired):
    enable_images(paired); calls=[]; paired.transport=lambda *args:calls.append(args) or {}
    event=paired.store.add_event("ring","physical");job=paired.enqueue("grok",event,stage="visit",image=prepare_image(jpeg()))
    paired.save(BotInput(**{**paired.connection("grok")["config"],"share_images":False}))
    asyncio.run(paired.process(job));assert not calls and "base64" not in paired.job(job)["payload"]
    enable_images(paired)
    event=paired.store.add_event("ring","physical");job=paired.enqueue("grok",event,stage="visit",image=prepare_image(jpeg()))
    paired.update(job,state="sending"); restarted=BotService(paired.store,lambda *args:None)
    assert restarted.job(job)["state"]=="uncertain" and "base64" not in restarted.job(job)["payload"]
    event=paired.store.add_event("ring","physical");job=paired.enqueue("grok",event,stage="visit",image=prepare_image(jpeg()))
    paired.store.delete_event(event["id"]);asyncio.run(paired.process(job));assert not calls
    assert not paired.job(job)


def test_legacy_migration_does_not_reset_text_pairing(paired):
    connection=paired.connection("grok");config=connection["config"];config.pop("share_images")
    with paired.store.connection() as db:db.execute("UPDATE bot_connections SET config=?",(json.dumps(config),))
    paired.save(BotInput(**config))
    assert paired.connection("grok")["revision"]==connection["revision"] and paired.connection("grok")["verified"]==1
    assert not paired.status()["bots"][0]["images"]


def test_realistic_image_cap_is_grok_only_without_a_network_call():
    image,_=generated_fixture(); payload=bounded_image_payload(envelope(image)|{"image":image})
    _real_global = ipaddress.IPv4Address.is_global.fget
    def _documentation_global(self):
        if str(self) == "203.0.113.1":
            return True
        return _real_global(self)
    with patch('agent_doorbell.bots.socket.getaddrinfo',return_value=[(2,1,6,"",("203.0.113.1",443))]), patch.object(ipaddress.IPv4Address,'is_global',property(_documentation_global)), patch('agent_doorbell.bots.socket.create_connection'), patch('agent_doorbell.bots.ssl.create_default_context'), patch('agent_doorbell.bots.http.client.HTTPConnection') as connection:
        response=connection.return_value.getresponse.return_value;response.status=200;response.read.return_value=b""
        assert len(json.dumps(payload).encode())<=WIRE_LIMIT
        assert request_json("grok","https://example.com/routine","POST",payload=payload)=={}
        with pytest.raises(BotError):request_json("hermes","https://example.com/routine","POST",payload=payload)
        with pytest.raises(BotError):request_json("grok","https://example.com/routine","POST",payload={"text":"X"*16_385})


def test_ring_text_precedes_capture_with_one_first_frame_image(tmp_path,monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY","FAKE-TEST-KEY")
    monkeypatch.setenv("DOORBELL_GROK_IMAGE_DECODER_SHA256",protocol_digest())
    monkeypatch.setenv("DOORBELL_GROK_IMAGES_ALLOWED","1")
    async def scenario():
        bell=Doorbell(Config(data_dir=tmp_path,camera="synthetic",background=False,burst_interval=.01))
        bell.bots.save(BotInput(provider="grok",endpoint="https://example.com/routine",destination="Bot"))
        with bell.store.connection() as db:db.execute("UPDATE bot_connections SET verified=1,confirmed=1")
        bell.bots.save(BotInput(**{**bell.bots.connection("grok")["config"],"enabled":True,"share_images":True}))
        event,_=await bell.ring("physical")
        assert bell.camera.sequence==0
        assert bell.bots.deliveries()==[]
        for _ in range(100):
            if bell.store.event(event["id"])["capture_status"]=="complete":break
            await asyncio.sleep(.02)
        await asyncio.gather(*tuple(bell.tasks))
        jobs=bell.bots.deliveries();assert len(jobs)==1 and jobs[0]["image_state"]=="included"
        assert json.loads(bell.bots.job(jobs[0]["id"])["payload"])["stage"]=="visit"
        image_job=next(j for j in jobs if j["image_state"]=="included")
        image=json.loads(bell.bots.job(image_job["id"])["payload"])["image"]
        first=(bell.store.media/event["id"]/"0.jpg").read_bytes()
        assert image["sha256"]==prepare_image(first)["sha256"]
        assert bell.store.event(event["id"])["frames"]==3 and bell.camera.sequence==1
        await bell.close()
    asyncio.run(scenario())


def test_generated_api_uses_no_camera_or_expected_answer_in_payload(tmp_path,monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY","FAKE-TEST-KEY")
    monkeypatch.setenv("DOORBELL_GROK_IMAGE_DECODER_SHA256",protocol_digest())
    monkeypatch.delenv("DOORBELL_GROK_IMAGES_ALLOWED",raising=False)
    with TestClient(create_app(Config(data_dir=tmp_path,camera="synthetic",background=False))) as client:
        bell=client.app.state.doorbell;calls=[]
        bell.bots.transport=lambda *args:calls.append(copy.deepcopy(args)) or {}
        bell.bots.save(BotInput(provider="grok",endpoint="https://example.com/routine",destination="Bot"))
        with bell.store.connection() as db:db.execute("UPDATE bot_connections SET verified=1,confirmed=1")
        result=client.post('/api/bots/grok/test-image',json={'request_id':'f'*32})
        assert result.status_code==202
        for _ in range(30):
            if calls:break
            time.sleep(.02)
        assert len(calls)==1 and calls[0][4]["stage"]=="synthetic_image"
        assert "expected_generated_shapes" not in calls[0][4] and "triangle" not in json.dumps(calls[0][4])
        assert bell.camera.sequence==0 and not bell.bots.status()["bots"][0]["images"]
        assert result.json()["camera_used"] is False
        assert result.json()['wire_bytes']<=WIRE_LIMIT
        again=client.post('/api/bots/grok/test-image',json={'request_id':'f'*32})
        assert again.json()['already_queued'] is True and len(calls)==1
        metadata=client.get('/api/bots/deliveries').json()['deliveries'][0]['transport_metadata']
        assert metadata['wire_bytes']==len(json.dumps(calls[0][4],ensure_ascii=True).encode())
        assert metadata['carrier_field_order']==['sha256','bytes','base64url']
        assert metadata['jpeg_sha256']==result.json()['sha256']
        assert client.post('/api/bots/grok/test-image',json={'request_id':'e'*32,'command':'ignored?'}).status_code==422
        with ThreadPoolExecutor(max_workers=2) as pool:
            repeated=list(pool.map(lambda _:client.post('/api/bots/grok/test-image',json={'request_id':'d'*32}).json(),range(2)))
        assert repeated[0]['delivery_id']==repeated[1]['delivery_id']
        assert sum(item.get('already_queued',False) for item in repeated)==1
        for _ in range(30):
            if len(calls)==2:break
            time.sleep(.02)
        assert len(calls)==2 and bell.camera.sequence==0


@pytest.mark.parametrize("value", ["';touch /tmp/doorbell-injection;#", "$(touch /tmp/doorbell-injection)",
    "`touch /tmp/doorbell-injection`", "AA\nAA", "AA AA", "AA;AA", "AA=", "AA/AA", "AA+AA", "AA\x00AA",
    "AA\u200bAA", "A"*(CARRIER_LIMIT+1)])
def test_carrier_refuses_shell_syntax_before_command_construction(value):
    transport=copy.deepcopy(envelope()["image_carrier"]);transport["base64url"]=value
    with pytest.raises(ValueError):carrier_command(transport)


@pytest.mark.parametrize("field,value", [("sha256","0'*$(evil)"),("bytes","100;evil"),("command","evil")])
def test_carrier_rejects_alternate_command_fields(field,value):
    transport=copy.deepcopy(envelope()["image_carrier"]);transport[field]=value
    with pytest.raises(ValueError):carrier_command(transport)


@pytest.mark.parametrize("size", [16_384,32_768,49_152])
def test_carrier_shell_byte_preservation_and_caps_at_realistic_jpeg_sizes(size,tmp_path):
    raw=__import__('base64').b64decode(prepare_image(jpeg())["base64"])
    # Valid JPEG COM segment pads the carrier-size test without personal content.
    count=size-len(raw)-4
    raw=raw[:-2]+b'\xff\xfe'+(count+2).to_bytes(2,'big')+b'X'*count+raw[-2:]
    assert len(raw)==size
    payload=envelope(image_descriptor(raw));encoded=json.dumps(payload).encode()
    assert len(encoded)<JSON_LIMIT
    transport=payload["image_carrier"];core,(decoded,_) = validate_carrier(transport)
    assert decoded==raw
    # Only the allowed carrier alphabet enters a shell data position.
    preserved=subprocess.check_output(['/bin/bash','-c',"printf '%s' "+transport["base64url"]])
    assert preserved.decode()==transport["base64url"]
    command=carrier_command(transport)
    assert subprocess.run(['/bin/bash','-n'],input=command.encode(),capture_output=True).returncode==0
    result=decode_to_png(encoded,tmp_path/'decoded',watchdog=False)
    assert result["sha256"]==core["image"]["sha256"] and result["bytes"]==size
    damaged=copy.deepcopy(transport);damaged["base64url"]=transport["base64url"][:-1]+('A' if transport["base64url"][-1]!='A' else 'B')
    with pytest.raises(ValueError):validate_carrier(damaged)


def test_compact_wire_budget_is_independent_of_decoder_maximum():
    image,_=generated_fixture();payload=bounded_image_payload(envelope(image)|{'image':image})
    assert len(json.dumps(payload,ensure_ascii=True).encode())<=4096
    assert list(payload['image_carrier'])==['sha256','bytes','base64url']
    impossible=envelope(image)|{'image':image};impossible['event']['observations']='X'*5000
    with pytest.raises(ValueError):bounded_image_payload(impossible)


def test_native_audit_preserves_receipt_before_view_and_rejects_corruption(tmp_path,monkeypatch):
    payload=envelope();transport=payload['image_carrier']
    original=image_protocol.decode_to_png
    def inspected_decode(raw,root,watchdog):
        before=json.loads((root/'receipts'/('a'*32+'.json')).read_text())
        assert before['status']=='received' and not before['png_created']
        assert before['carrier_sha256_actual']==before['carrier_sha256_expected']
        return original(raw,root=root,watchdog=watchdog)
    monkeypatch.setattr(image_protocol,'decode_to_png',inspected_decode)
    result=audit('a'*32,transport,image_protocol,tmp_path/'valid',watchdog=False)
    assert result['status']=='decoded' and result['png_created'] is True
    assert result['carrier_chars_actual']==result['carrier_chars_expected']
    assert result['carrier_sha256_actual']==result['carrier_sha256_expected']
    stored=json.loads(Path(result['receipt_path']).read_text())
    assert stored==result and 'base64url' not in json.dumps(stored)
    assert stored['native_image_read']=='not_attested_by_decoder'
    bad=copy.deepcopy(transport);bad['base64url']=bad['base64url'][:-8]
    rejected=audit('a'*32,bad,image_protocol,tmp_path/'bad',watchdog=False)
    assert rejected['status']=='rejected' and not rejected['png_created']
    assert rejected['carrier_chars_actual']<rejected['carrier_chars_expected']
    assert Path(rejected['receipt_path']).is_file()
    with pytest.raises(FileExistsError):audit('a'*32,bad,image_protocol,tmp_path/'bad',watchdog=False)
