# Local vision: models, enrollment and limits

Face/object analysis executes locally using OpenCV's CPU backend with two threads. Enrollment images and embeddings never enter a Bot payload. Optional camera sharing is separate and defaults off. Motion stays paused by default. The optional local caption stage is documented in [Local captions](LOCAL_CAPTIONS.md).

| Function | Official weight | Size | License |
| --- | --- | --- | --- |
| Face detection | `face_detection_yunet_2023mar.onnx` | 232,589 bytes | MIT |
| Face embeddings | `face_recognition_sface_2021dec.onnx` | 38,696,353 bytes | Apache 2.0 |
| Object detections | `object_detection_nanodet_2022nov.onnx` | 3,800,954 bytes | Apache 2.0 |

Source: [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models), pinned revision `47534e27c9851bb1128ccc0102f1145e27f23f98`. Exact SHA-256 values and byte limits are in `agent_doorbell/local_vision.py`, taken from the official Git LFS pointers. The installer downloads only these weights, verifies them and keeps copies of their licenses beside them. Inference code for NanoDet is vendored from that revision under its Apache license. Weights, photographs, vectors and databases are excluded from Git.

Install explicitly on the Pi, after installing `.[person]`:

```bash
.venv/bin/python scripts/install-local-models.py
systemctl --user restart agent-doorbell
```

Alternatively the one-login deployment supports `--models` as its third argument. Models live under the private data directory's `models` folder. Startup verifies weights locally and never downloads anything. If dependencies or weights are missing, rings and history work while recognition reports unavailable.

## Enrollment and probable matching

### Add people on your phone

1. Open the Pi dashboard and choose **Overview → Add people**, or **Familiar people → Add a person**.
2. Enter a name and optional note, then choose **Save & choose photos**. A name alone is a manual label; recognition needs reference photos.
3. Choose one clear photo from your phone. On iPhone, use **Photo Library** in the file picker. Only JPEG/PNG under 8 MB are accepted. Export HEIC as JPEG/PNG, or use a cropped screenshot of the consenting person's face. No library connection or automatic photo import is configured.
4. Confirm the person's permission and choose **Enroll locally**. The preview is local. Quality feedback explains missing, multiple, small or blurry faces; rejected photos are not enrolled. Accepted photos remain saved if you close the flow. Add further photos one at a time, up to five per person, with varied lighting/angles.
5. Choose **Test recognition**, select a different photo you did not enroll, and press **Run local test**. Opening the test or choosing a file does not run it. The test photo is processed in memory and not saved; no camera capture, event or Bot message occurs. A probable match is shown as `Probably {name} ({score})`. No match stays `unknown`.
6. Use **Edit name**, **Enroll photo**, **Delete enrolled photos**, or **Remove** on the person's card to manage their local profile. Back from photos returns to the same name, without creating another person. Cancelling before submission uploads nothing. Deleting enrollment removes reference photos/vectors and clears prior automatic match labels.

Names, reference photos and embeddings stay local through this flow. It never enables Bot name sharing or photo sharing. No real people have been enrolled during development. This photo test does not establish accuracy at the door camera's angle and lighting; any later real-camera test must be explicitly initiated by the owner.

Create a named profile, choose **Enroll photo**, confirm permission, and select one JPEG/PNG under 8 MB/20 megapixels. Images are resized, normalized to JPEG and stripped of metadata. Enrollment requires exactly one detected face, at least 70 pixels across and a Laplacian variance of at least 30. Blurry, small, missing and multiple-face inputs are rejected with specific messages. Up to five photos per profile and 100 total are allowed.

Photos and normalized SFace embeddings stay under the private data directory (files mode 600, directories 700). Embeddings are never returned by the API. Delete enrolled photos to remove both photos/vectors and clear prior automatic identity labels; deleting the profile also removes its enrollment. A human manual correction takes priority over a late model result.

Every saved ring, test or motion snapshot is submitted for asynchronous local analysis, normally all three frames. YuNet finds faces; SFace aligns and embeds one usable face. Cosine similarity compares enrolled samples from the same model version. A probable match requires similarity >= **0.50** and a margin >= **0.10** over a different person's best score on each accepted frame. Scores from 0.35 below acceptance or ambiguous candidates are uncertain; lower scores are unknown. These conservative initial thresholds are **not calibrated for this door**. A probable match is not treated as proven by the lock. The [OpenCV tutorial](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html) gives dataset-specific thresholds, not an accuracy promise for this camera.

### Recognition across the snapshot sequence

The ring alert and text notification still precede camera work. Saved frames and quick positive object observations appear during capture. The size and sharpness gate runs on the decoded frame. A capture whose long edge is already 960 pixels is not scaled down before that measurement. A face must be at least 70 pixels on its shorter side, so a clear frontal face about a quarter of a 960-wide frame passes the size check. Sharpness is the Laplacian variance of that face crop, with a cutoff of 30. The clearest face that passes is the one that counts. Other usable faces must still agree on the same enrolled profile. No-face and low-quality frames do not veto it. When several passing frames agree, the displayed score is that clearest frame's score. A probable match is not a certain identity and does not prove who is there.

Different accepted profiles, an unknown/uncertain usable face alongside a match, or any frame containing multiple faces prevents a single-person assignment. More than one positive person detection in a frame also prevents assignment, even if only one face is usable. This is conservative burst evidence, not tracking or proof that an undetected person is absent. No-face or low-quality frames cannot establish who was present. Ordinary face matching has no verified resistance to photographs or other spoofing and never authorizes entry.

One shared worker runs one local frame pass at a time, with at most one additional waiting frame. Burst length is limited to one through five frames. Under overload, a frame explicitly records `analysis_busy`; failures/interrupted work record unavailable analysis, and incomplete evidence cannot produce an automatic identity. Cancelling a capture does not free a still-running native OpenCV call or start a second call alongside it. Native model execution has no forced thread-kill deadline; bounded inputs, two OpenCV threads and the small queue bound its work and backlog. This differs from the isolated caption process, which has a separately enforced timeout.

Each local event retains frame index, face-quality/result, accepted local profile ID, similarity/margin, positive objects, person count and model/camera/queue timings. Embeddings, reference photos, raw exceptions and enrolled names are not part of these diagnostics. Event details explain disagreements and per-frame quality. Deleting enrollment scrubs nested match evidence as well as the overall match; human corrections retain priority. The Bot receives one final analysis stage for the original event ID after capture completes or fails. Its existing whitelist excludes local profile IDs, scores and per-frame metadata; name/image sharing settings are not changed.

Multiple faces are ambiguous; small/blurry faces are low quality; no face and no enrollment are explicit states. Predictions never authorize entry or prove identity. Evaluate consenting known people and unknown visitors at the actual camera angle and lighting before relying on recognition. No real user enrollment has been performed or accuracy verified during development.

## Positive object observations and timing

Local detection results also produce a saved factual sentence in the event API
and dashboard, labeled **Local observations**. This updates after the first frame,
before the remaining capture sequence and independently of any Bot response.
Observations are refreshed across later frames while retaining positive detections.
For example, a positive person detection with a low-quality face yields
“A person was detected. The face isn't clear enough to identify them.” Empty
results mean no confident local observations, not an empty scene. These summaries
do not contain an enrolled name or a full vision-language caption. Retained
events receive the same summary on migration; deletion removes it with the event.

NanoDet uses aspect-preserving letterboxing to 416x416 and reports up to ten positive outputs with confidence at least 0.50, using the 80-class ordering in the [pinned official demo](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/demo.py). These are detections, not guaranteed scene facts. The app retains general object labels rather than a special person/animal checklist; empty outputs do not prove absence. Bot descriptions mention useful positive observations and face-match uncertainty, without claiming clothing, activity, occupation, parcel possession or ownership. NanoDet has no parcel class and does not produce a full scene caption. The earlier HOG pedestrian detector remains a fallback when NanoDet weights are absent.

Run a bounded local computation benchmark without camera or biometric data:

```bash
.venv/bin/python scripts/benchmark-local-vision.py
```

The app records camera-acquisition and per-model analysis timings in event metadata. Ring delivery is separate from these tasks; enriched local events and opt-in bot analysis updates retain the original event ID. Optional local VLM captions have a separate queue and deadline. End-to-end latency includes camera startup and analysis, plus any separately enabled Bot processing. Current caption installation/trial evidence is tracked in [the handoff](../HANDOFF.md); availability and real-scene accuracy require separate verification. Zero-latency semantic recognition is not promised.
