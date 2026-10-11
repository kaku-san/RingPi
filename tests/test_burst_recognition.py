import asyncio
import json
import time
from threading import Event, Lock

import pytest

from agent_doorbell.app import Doorbell
from agent_doorbell.bots import safe_event
from agent_doorbell.burst_recognition import BurstRecognition, FrameAnalysisQueue, best_frame_index
from agent_doorbell.camera import Camera, CameraUnavailable
from agent_doorbell.config import Config
from agent_doorbell.local_vision import FACE_MODEL, MODELS


def fixture_bell(tmp_path, results, fail_capture=None):
    class Frames(Camera):
        def __init__(self):
            super().__init__('synthetic')
            self.calls = 0
        async def capture(self, max_age=0):
            index = self.calls
            self.calls += 1
            if index == fail_capture:
                raise CameraUnavailable('offline', 'Synthetic camera failure')
            self.last_capture_ms = 4
            return str(index).encode()
    bell = Doorbell(Config(data_dir=tmp_path, camera='synthetic', background=False,
                           burst_frames=3, burst_interval=0), camera=Frames())
    profiles = {}
    for name in ('Avery', 'Blair'):
        profile = bell.store.add_profile(name, '')
        bell.store.add_face_sample(profile['id'], b'synthetic reference', [.2, .8], MODELS[FACE_MODEL][1])
        profiles[name] = profile['id']
    class Vision:
        objects = True
        status = {'available': True, 'objects_available': True}
        calls = []
        def analyze(self, raw, samples):
            index = int(raw)
            self.calls.append(index)
            result = results[index % len(results)]
            if isinstance(result, Exception):
                raise result
            if callable(result):
                return result(bell, profiles)
            status, name = (result, None) if isinstance(result, str) else result
            return {'status': status, 'profile_id': profiles.get(name),
                    'similarity': .8 if name else None, 'margin': .3 if name else None,
                    'objects': [] if status == 'no_face' else [{'label': 'person', 'confidence': .8}],
                    'timings_ms': {'yunet': 2, 'sface': 3 if name else 0, 'analysis_total': 5},
                    'embedding': ['MUST NOT BE STORED'], 'name': 'MUST NOT BE STORED'}
    bell.local_vision = Vision()
    return bell, profiles


def record(bell):
    async def run():
        event = bell.store.add_event('test', 'browser', simulated=True)
        await bell.record(event['id'])
        result = bell.store.event(event['id'])
        await bell.close()
        return result
    return asyncio.run(run())


@pytest.mark.parametrize('results,expected,reason', [
    (['no_face', ('probable_match', 'Avery'), 'low_quality'], 'probable_match', 'usable_faces_agree'),
    ([('probable_match', 'Avery'), 'no_face', ('probable_match', 'Avery')], 'probable_match', 'usable_faces_agree'),
    ([('probable_match', 'Avery'), ('probable_match', 'Blair'), 'no_face'], 'uncertain', 'identity_disagreement'),
    ([('probable_match', 'Avery'), 'unknown', 'low_quality'], 'uncertain', 'conflicting_face_evidence'),
    (['uncertain', ('probable_match', 'Avery'), 'no_face'], 'uncertain', 'conflicting_face_evidence'),
    (['multiple_faces', ('probable_match', 'Avery'), 'no_face'], 'multiple_faces', 'multiple_faces'),
    (['no_face', 'no_face', 'no_face'], 'no_face', 'no_accepted_match'),
    (['low_quality', 'no_face', 'low_quality'], 'low_quality', 'no_accepted_match'),
    ([RuntimeError('PRIVATE ERROR'), ('probable_match', 'Avery'), 'no_face'], 'uncertain', 'analysis_incomplete'),
    (['no_face', RuntimeError('PRIVATE ERROR'), 'low_quality'], 'unavailable', 'analysis_incomplete'),
])
def test_every_frame_is_checked_and_identity_evidence_is_conservative(tmp_path, results, expected, reason):
    bell, profiles = fixture_bell(tmp_path, results)
    event = record(bell)
    assert bell.local_vision.calls == [0, 1, 2]
    assert event['frames'] == 3 and event['capture_status'] == 'complete'
    assert event['face_status'] == expected
    assert event['person_id'] == (profiles['Avery'] if expected == 'probable_match' else None)
    assert event['local_analysis']['aggregation']['reason'] == reason
    assert event['local_analysis']['complete']
    assert len(event['local_analysis']['frames']) == 3
    assert event['local_analysis']['timings_ms']['camera_acquisition'] == 12
    text = json.dumps(event)
    assert 'embedding' not in text and 'MUST NOT BE STORED' not in text and 'PRIVATE ERROR' not in text
    payload = json.dumps(safe_event(event))
    assert 'Avery' not in payload and 'Blair' not in payload and profiles['Avery'] not in payload
    assert 'local_analysis' not in payload and 'similarity' not in payload
    assert not bell.access.snapshot()['commands'] and not bell.access.snapshot()['passes']


def test_multiple_people_veto_identity_even_if_only_one_face_is_usable(tmp_path):
    def crowded(bell, profiles):
        return {'status': 'no_face', 'person_count': 2, 'objects': [{'label': 'person', 'confidence': .8}], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, [('probable_match', 'Avery'), crowded, 'no_face'])
    event = record(bell)
    assert event['face_status'] == 'uncertain' and event['person_id'] is None
    assert event['local_analysis']['aggregation']['reason'] == 'multiple_people'
    assert event['local_analysis']['frames'][1]['person_count'] == 2


def test_partial_capture_retains_good_frame_without_assigning_single_identity(tmp_path):
    bell, profiles = fixture_bell(tmp_path, [('probable_match', 'Avery'), 'no_face', 'no_face'], fail_capture=1)
    event = record(bell)
    assert event['capture_status'] == 'partial' and event['frames'] == 1
    assert event['face_status'] == 'uncertain' and event['person_id'] is None
    assert not event['local_analysis']['complete']
    assert event['local_analysis']['frames'][0]['status'] == 'probable_match'
    assert event['local_analysis']['aggregation']['reason'] == 'analysis_incomplete'


def test_positive_object_observations_survive_later_empty_frames(tmp_path):
    def positive(bell, profiles):
        return {'status': 'no_face', 'objects': [{'label': 'dog', 'confidence': .7}], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, [positive, 'no_face', 'no_face'])
    event = record(bell)
    assert event['description'] == 'A dog was detected.'
    assert event['local_analysis']['objects'] == [{'label': 'dog', 'confidence': .7}]


def test_first_frame_is_pending_until_remaining_frames_finish_and_bot_gets_final_result(tmp_path):
    entered, release = Event(), Event()
    def late_conflict(bell, profiles):
        entered.set()
        assert release.wait(3)
        return {'status': 'unknown', 'objects': [{'label': 'person', 'confidence': .8}], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, [('probable_match', 'Avery'), late_conflict, 'no_face'])
    published, deliveries = [], []
    bell.bus.publish = lambda name, data: published.append((name, data))
    def bot_spy(event, stage='ring', **kwargs):
        deliveries.append((stage, safe_event(event)))
        return []
    bell.bots.enqueue_event = bot_spy
    async def run():
        start = time.monotonic()
        event, duplicate = await bell.ring('browser')
        assert time.monotonic() - start < .2 and event['capture_status'] == 'pending'
        assert published[0][0] == 'ring' and not duplicate
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            pending = bell.store.event(event['id'])
            assert pending['face_status'] == 'pending' and pending['person_id'] is None
            assert pending['description'].startswith('A person was detected.')
            assert deliveries == []
            # A second ring still returns immediately while the model worker is occupied.
            second_start = time.monotonic()
            second, _ = await bell.ring('physical')
            assert time.monotonic() - second_start < .2
            await asyncio.sleep(0)
            assert second['capture_status'] == 'pending'
        finally:
            release.set()
        await asyncio.gather(*tuple(bell.tasks))
        completed = bell.store.event(event['id'])
        assert completed['face_status'] == 'uncertain' and completed['person_id'] is None
        analysis = [p for stage, p in deliveries if stage == 'visit' and p['id'] == event['id']]
        assert len(analysis) == 1 and analysis[0]['face_analysis'] == 'uncertain'
        assert all('enrolled_name_untrusted' not in p for _, p in deliveries)
        await bell.close()
    asyncio.run(run())


@pytest.mark.parametrize('correction', ['manual', 'unknown', 'uncertain'])
def test_correction_during_later_frame_remains_authoritative(tmp_path, correction):
    entered, release = Event(), Event()
    def later(bell, profiles):
        entered.set()
        assert release.wait(3)
        return {'status': 'probable_match', 'profile_id': profiles['Avery'], 'similarity': .8,
                'margin': .3, 'objects': [], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, ['no_face', later, 'no_face'])
    async def run():
        event = bell.store.add_event('test', 'browser')
        task = asyncio.create_task(bell.record(event['id']))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            bell.store.update_event(event['id'], person_status=correction,
                                    person_id=profiles['Blair'] if correction == 'manual' else None)
        finally:
            release.set()
        await task
        result = bell.store.event(event['id'])
        assert result['person_status'] == correction
        assert result['person_id'] == (profiles['Blair'] if correction == 'manual' else None)
        assert result['face_status'] == 'probable_match'
        await bell.close()
    asyncio.run(run())


def test_enrollment_deleted_between_frames_cannot_reappear_and_scrubs_nested_evidence(tmp_path):
    def deleting(bell, profiles):
        bell.store.delete_face_samples(profiles['Avery'])
        return {'status': 'probable_match', 'profile_id': profiles['Avery'], 'similarity': .9,
                'margin': .4, 'objects': [], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, [('probable_match', 'Avery'), deleting, 'no_face'])
    result = record(bell)
    assert result['face_status'] == 'enrollment_deleted' and result['person_id'] is None
    assert all(frame['profile_id'] is None for frame in result['local_analysis']['frames'])
    assert all(frame['similarity'] is None for frame in result['local_analysis']['frames'])
    # Deleting enrollment after a completed burst also clears every nested match.
    bell, profiles = fixture_bell(tmp_path / 'after', [('probable_match', 'Avery')] * 3)
    result = record(bell)
    bell.store.update_event(result['id'], person_status='manual', person_id=profiles['Blair'])
    bell.store.delete_face_samples(profiles['Avery'])
    cleared = bell.store.event(result['id'])
    assert cleared['person_status'] == 'manual' and cleared['person_id'] == profiles['Blair']
    assert cleared['face_status'] == 'enrollment_deleted'
    assert all(frame['profile_id'] is None and frame['similarity'] is None for frame in cleared['local_analysis']['frames'])


def test_queue_is_bounded_and_cancellation_cannot_start_overlapping_native_calls():
    entered, release = Event(), Event()
    lock, calls, active, peak = Lock(), [], 0, 0
    def analyze(frame):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(frame)
        try:
            if frame == b'held':
                entered.set()
                assert release.wait(3)
            return {'status': 'no_face', 'objects': [], 'timings_ms': {}}
        finally:
            with lock:
                active -= 1
    async def run():
        queue = FrameAnalysisQueue(analyze)
        first = asyncio.create_task(queue.submit(b'held'))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            waiting = asyncio.create_task(queue.submit(b'waiting'))
            await asyncio.sleep(0)
            assert queue.outstanding == 2
            start = time.monotonic()
            rejected = await queue.submit(b'overflow')
            assert time.monotonic() - start < .1 and rejected['error'] == 'analysis_busy'
            first.cancel(); waiting.cancel()
            await asyncio.gather(first, waiting, return_exceptions=True)
            assert queue.outstanding == 2  # Native work still owns its slot.
            assert (await queue.submit(b'no replacement'))['error'] == 'analysis_busy'
        finally:
            release.set()
        await queue.task
        assert queue.outstanding == 0 and calls == [b'held'] and peak == 1
        assert (await queue.submit(b'fresh'))['status'] == 'no_face'
        assert calls == [b'held', b'fresh'] and peak == 1
        await queue.close()
    asyncio.run(run())


def test_event_deleted_during_analysis_is_not_restored_or_delivered(tmp_path):
    entered, release = Event(), Event()
    def held(bell, profiles):
        entered.set()
        assert release.wait(3)
        return {'status': 'probable_match', 'profile_id': profiles['Avery'], 'similarity': .8,
                'margin': .3, 'objects': [], 'timings_ms': {}}
    bell, profiles = fixture_bell(tmp_path, [held, 'no_face', 'no_face'])
    deliveries = []
    bell.bots.enqueue_event = lambda *args, **kwargs: deliveries.append(kwargs.get('stage'))
    async def run():
        event = bell.store.add_event('test', 'browser')
        task = asyncio.create_task(bell.record(event['id']))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert bell.store.delete_event(event['id'])
        finally:
            release.set()
        await task
        assert bell.store.event(event['id']) is None
        assert not (bell.store.media / event['id']).exists()
        assert bell.local_vision.calls == [0] and not deliveries
        await bell.close()
    asyncio.run(run())


@pytest.mark.parametrize('score,margin', [(float('nan'), .3), (1.1, .3), (.8, .09), (.8, None)])
def test_invalid_or_rejected_per_frame_score_cannot_become_an_identity(score, margin):
    burst = BurstRecognition(1)
    burst.add(0, {'status': 'probable_match', 'profile_id': 'fixture', 'similarity': score,
                  'margin': margin, 'objects': [], 'timings_ms': {}}, 1)
    result = burst.result({'fixture'}, complete=True)
    assert result['status'] == 'uncertain' and result['profile_id'] is None


def test_best_face_in_the_burst_counts_and_stays_unproven():
    burst = BurstRecognition(3)
    burst.add(0, {"status": "no_face", "objects": [], "timings_ms": {}}, 1)
    burst.add(1, {"status": "low_quality", "face_side": 219.3, "laplacian": 19.91,
                  "detector_score": 0.95, "measured_width": 960, "objects": [], "timings_ms": {}}, 1)
    burst.add(2, {"status": "probable_match", "profile_id": "fixture", "similarity": 0.703,
                  "margin": 0.2, "face_side": 222.6, "laplacian": 37.61, "detector_score": 0.95,
                  "measured_width": 960, "objects": [], "timings_ms": {}}, 1)
    result = burst.result({"fixture"}, complete=True)
    assert result["status"] == "probable_match"
    assert result["profile_id"] == "fixture"
    assert result["similarity"] == 0.703
    assert result["aggregation"]["chosen_frame"] == 2
    assert result["aggregation"]["reason"] == "usable_faces_agree"
    assert result["identity_proven"] is False
    assert best_frame_index(result["frames"]) == 2
    softer = BurstRecognition(2)
    softer.add(0, {"status": "probable_match", "profile_id": "fixture", "similarity": 0.9,
                   "margin": 0.2, "face_side": 200, "laplacian": 31, "objects": [], "timings_ms": {}}, 1)
    softer.add(1, {"status": "probable_match", "profile_id": "fixture", "similarity": 0.61,
                   "margin": 0.2, "face_side": 240, "laplacian": 50, "objects": [], "timings_ms": {}}, 1)
    clearest = softer.result({"fixture"}, complete=True)
    assert clearest["similarity"] == 0.61 and clearest["aggregation"]["chosen_frame"] == 1
    assert clearest["identity_proven"] is False


def test_aggregation_uses_weakest_accepted_score_without_relaxing_thresholds():
    burst = BurstRecognition(3)
    for index, score in enumerate((.9, .6, .7)):
        burst.add(index, {'status': 'probable_match', 'profile_id': 'fixture', 'similarity': score,
                          'margin': .2, 'objects': [], 'timings_ms': {}}, 1)
    assert burst.result({'fixture'})['status'] == 'pending'
    result = burst.result({'fixture'}, complete=True)
    assert result['similarity'] == .6 and result['margin'] == .2
    # A frame claiming a match below the original acceptance gate must veto it.
    burst = BurstRecognition(3)
    for index, score in enumerate((.9, .49, .7)):
        burst.add(index, {'status': 'probable_match', 'profile_id': 'fixture', 'similarity': score,
                          'margin': .2, 'objects': [], 'timings_ms': {}}, 1)
    assert burst.result({'fixture'}, complete=True)['status'] == 'uncertain'


@pytest.mark.parametrize('count', [0, 6, 1000])
def test_unbounded_burst_configuration_is_rejected(tmp_path, count):
    with pytest.raises(ValueError, match='one and five'):
        Doorbell(Config(data_dir=tmp_path, camera='none', background=False, burst_frames=count))
    assert not list(tmp_path.iterdir())
