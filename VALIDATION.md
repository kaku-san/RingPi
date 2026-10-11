# Validation status

## Version 0.3.9 caption request and text follow-up

Source **0.3.9** fixes the pinned llama.cpp request that returned ` 1`, and adds a same-event unverified caption follow-up through the existing bot queue. The smoke check rejects degenerate text and does not claim scene accuracy. A local generated-shape run through the app provider returned ` The image shows a blue square and a red circle.` in 23.62 s and again in 64.262 s. A later confirmation on the same pinned binary returned ` 1` in 21.849 s for the old request and that same sentence in 70.155 s for the corrected provider. The corrected warm request did not return within 120 s. Sampled peak RSS on the 64.262 s run was 431,476,736 bytes via `ps`. Thermal sensors were not available on that machine. The previous generated-shape device trial (11.220 s / 10.269 s, both ` 1`, 48.0°C → 57.3°C, RSS 546,734,080 bytes) used the old request. After `scripts/update-pi.sh`, one camera ring on 0.3.9 captured 3 frames (first frame 13,480 bytes) and stored `The image is a photograph of a plain white background.` in 11,372.9 ms. Ring, analysis, and caption deliveries were accepted, meaning the routine started, not that the owning chat finished. No corrected warm measurement was taken. Local checks: 90 caption, bot, and doorbell cases passed. The full backend run on this machine passed 333 cases with 2 skips, and 2 update-helper cases failed only because the default `python3` was 3.9 and has no `tomllib`. Those 5 update-helper cases passed when `python3` was 3.13, which is the CI interpreter. Details are in [HANDOFF.md](HANDOFF.md).

The current 8 October 2026 state is recorded in [HANDOFF.md](HANDOFF.md). Read-only status at **08:44 UTC** confirms healthy **0.3.8**, captions enabled and the bridge disabled. The completed owner-local receipt verifies managed pinned runtime/weights, unchanged app version and two actual generated-image inferences: **11.220 s cold**, **10.269 s warm**, sampled peak RSS **546,734,080 bytes**, temperature **48.0°C → 57.3°C**. Both responses were only ` 1`: runtime completion is proven, useful description and scene accuracy are not. This supersedes the intermediate absent-receipt/unavailable-API observations. The existing Grok text pairing/local face/object models were available at the last detailed check. On that 0.3.8 source, model captions stayed out of Bot payloads. The 0.3.9 camera ring recorded above is the later measurement. The 08:44 check itself included no camera capture, enrollment, Bot send, actuation, or security change. The handoff implementation passed 325 local backend cases with 2 skips, and exact CI at `21c858e` passed 322 backend cases with 5 skips, 19 browser cases and native restricted SSH. Current deployed source equality with GitHub HEAD is not claimed.

The sections below record historical checks and their limits. A successful local suite, HTTP acceptance or a routine marked succeeded does not establish image interpretation.

## Version 0.3.8 restricted proposer

The new proposal transport uses a dedicated SSH identity and root-owned fixed gate, then a bounded Unix socket whose Linux kernel peer UID must match the proposer. It has no owner signing key or owner route. Restricted mode keeps only the Pi-resident owner-review key and disables the legacy HTTP proposer. The private Mac inbox validates owned regular files, journals before transfer and never automatically retries ambiguous delivery. Default configuration remains off; source deployment creates no account, credential, SSH grant, service or folder authorization.

Local validation passed **302 backend tests**, with two skips for absent optional official-model weights and Linux-only kernel credentials on the Mac. **19 Chromium browser tests** passed; the latest synthetic Mac ring reached its panel in 183 ms. This does not measure Pi or iPhone/Safari performance. The disposable Linux `restricted_ssh` CI harness tests actual SSH authentication, proposal staging, kernel peer identity, command/SFTP/owner-file denial, PTY/forwarding refusal and isolated Python execution. Its exact-commit CI result must pass before release activation is proposed; Mac mocks alone do not prove that boundary. Actual Grok original-attachment export remains blocked on exact source references and destination scope, and no real visitor photos were imported.

See [restricted-role design, requirements and activation plan](docs/RESTRICTED_PROPOSER.md). No live Pi SSH/OS settings, credentials, camera, Bot sharing, caption enablement or physical control were changed for this work.

## Version 0.3.7 reviewed five-photo bridge

The bridge implementation includes separate proposal/owner signature scopes, SSH stdin transport to Pi loopback, bounded five-photo parsing/metadata stripping, immutable name/window/photo-digest review, temporary references isolated from household enrollment, and one advance-approved simulated visitor pass. Default policy is absent/disabled, live LAN callers are rejected, and owner review is separately required. No public port, tunnel, firewall or SSH setting is configured. Purpose keys stay on the Pi and are independent of notification credentials.

The hardware-free demo completes five owned generated images → pending review → mock consent/approval → temporary reference set → one valid simulated arrival → reference deletion. Focused tests cover stale/wrong/replayed signatures, scope separation, remote-client refusal, private/no-follow policy and reference paths, malformed/duplicate payloads, consent/hash/photo-quality failures, replay, concurrent controllers, expiry during model work, partial/restart failure, cleanup retry after I/O failure, and secret/image exclusion from results. The native enrollment adapter is mocked for these tests; generated images contain no people. Actual real-photo enrollment, SSH signing on the Pi, Grok attachment export and accuracy remain unverified.

Portable setup includes a disabled example policy, nonsecret environment example, user-relative paths, a fork guide, an adaptable Grok instruction and exact per-batch client commands. Owner setup has a read-only default and requires a separate explicit approval flag before key creation/restart. Setup tests use fixed fake keys; no real credential was generated. Official Grok documentation supports registered-computer local execution with per-command approval. A blocked photo export remains blocked; there is no assumed attachment API or alternate copy route.

Final local validation passed **274 backend tests**, with one optional official-model smoke test skipped because its weight directory was not configured for this run. This includes all **43 bridge cases** and the latest setup-wrapper/boundary checks. The unchanged application/UI passed **19 Chromium browser tests**; the latest synthetic Mac ring reached its panel in 215 ms, which does not measure Pi latency or actual iPhone/Safari behavior. The hardware-free demo returned `hardware_free_flow_passed`. The 0.3.7 wheel contains both bridge modules and matches the final packaged source, without private photos, databases, policies, credentials or model weights. JavaScript/Bash syntax and Git whitespace checks passed. Exact-commit CI is checked separately after the private push.

Browser review covers five-photo pending state, one exact advance approval, one-use arrival without a repeated owner prompt and disabled live bridge status. API regressions reject proposal-key owner approval and refuse real imports through mock decision routes. Existing ring, household enrollment, pairing and caption behavior remain covered. Both keys are read only by the Pi-resident helper; the shared owner SSH account itself is not role-restricted, so the prototype requires separate exact per-command owner approval. Unattended agent isolation, physical control, real visitor factor verification and actual door-camera matching against temporary references are unconfigured.

## Version 0.3.6 access simulation and caption setup

The full development suite passed **232 backend tests**, including existing official pinned OpenCV models on a generated blank image, and **18 Chromium browser tests**. Forty simulation cases cover exact advance approval, bounded one-time windows, expiry during owner/visitor verification, revocation, required mock face/factor evidence, duplicate and concurrent requests, restart recovery, unknown state and invalid acknowledgments. Model-worker regressions confirm arbitrary captions and recognition results cannot issue commands or passes. No real person, photo, visitor credential, Grok inbound command or lock action was involved.

Browser fixtures verify exact command review, unsupported opening distinct from unlocking, one advance pass approval without a repeated arrival prompt, missing-factor retry, single use, revocation, literal hostile names and 16px/44px controls at a 390×844 viewport. This is Chromium on the Mac, not actual iPhone/Safari. The latest synthetic Mac ring reached its panel in 229 ms; it does not measure Pi latency. A 0.3.6 wheel built successfully and contains the simulation code/UI with no private data, credentials, enrollment media or model weights. JavaScript/Bash syntax and Git whitespace checks passed.

Eight caption-setup tests plus five existing installer tests verify independent check/install/enable stages, preservation of a newer app version and private settings, prerequisite failures and credential exclusion from installer subprocesses/results. Only mocks ran: no actual caption runtime build, model download, service enablement or inference. The previous 0.3.4 helper assigned a shell flag without exporting it before Python read the environment; the defect is reproduced locally, but its effect on the Pi is not established without owner-terminal output. The new helper sends only setup Python and never transfers an app archive or downgrades application source.

Read-only Pi checks at 13:27 UTC confirm **0.3.5**, local model readiness, captions disabled, retained verified/confirmed Grok text pairing with names/images off, and the local camera-share API READY. Camera status after restart remains unverified. No capture, enrollment, Bot send, private photo/embedding read or physical command was triggered. The new access workflow remains simulation only; hardware, owner authentication, visitor credentials, temporary photo ingestion and secure Grok return transport are unconfigured. Exact-commit CI is checked separately after the private push.

## Version 0.3.5 burst recognition

The full development suite passed **184 backend tests**, including the official pinned OpenCV model smoke on a generated blank image, and **15 Chromium browser tests**. The 28 new burst regressions and five mocked updater tests passed. The latest synthetic Mac browser ring reached its panel in 102 ms; this does not measure Pi latency. JavaScript/Python/shell syntax and Git whitespace checks passed. Exact-commit CI and owner-authenticated Pi deployment are checked separately.

Every saved frame is submitted for local analysis. One worker bounds native calls to one running and one waiting frame; overload/failure remains explicit unavailable evidence. Quick local observations arrive during capture, while an automatic identity and the single final Bot analysis stage wait until the sequence ends. All usable faces must agree under unchanged per-frame gates; competing identities, uncertain/unknown usable faces, multiple-face/person evidence, or incomplete work prevents a single-person assignment. Weakest accepted scores are retained; rejected evidence is not rescued by averaging or a maximum score.

Mocked regressions cover a missing first face followed by a clear match, consistent later matches, competing people, unknown/uncertain evidence, multiple faces/bodies, entirely missing/poor-quality faces, individual model errors, partial capture, earlier/later enrollment deletion, human corrections, deletion during native inference and bounded queue/cancellation behavior. Per-frame diagnostics are whitelisted and exclude vectors, names and raw exceptions. Existing Bot/image regressions retain the immediate ring, final analysis deduplication, name sharing disabled and at most the separately consented first image. Browser QA explains each frame and disagreements without displaying candidate identity data.

The generic `scripts/update-pi.sh` helper is tested across a fake SSH/Git/Pi boundary: pinned archives, real shell-to-Python environment propagation, unmanaged-directory refusal, private state retained, no dependency/model download and no hardware/worker test. It preserves host-key checking and verifies only local status after restart. It has not been executed on the Pi for this release. Successful real-camera matching, Pi burst latency, spoof resistance and actual iPhone/Safari behavior remain unverified.

## Version 0.3.4 people flow and local-caption support

The Add people flow connects name creation/editing, owner-chosen reference photos, consent, local preview, per-photo quality feedback, enrollment management and an explicit test with another chosen photo. The test is ephemeral: no saved test photo, event, camera capture or Bot delivery. Backend fixtures verify edit persistence, consent/size/format rejection, quality failures, probable/uncertain/unknown/unusable-face outcomes, reference deletion during testing and exclusion of embeddings from responses. No real person has been enrolled or tested during development.

The full backend suite passed **151 tests**, including the explicitly installed official OpenCV model smoke test. The installer suite also passed after adding the explicit Make prerequisite. Script compilation, JavaScript syntax and Git whitespace checks passed.

All **14 browser tests** passed on the development Mac. New QA covers back without duplicate people, cancel without upload, repeated submit while busy, actionable quality errors, mobile preview/HEIC guidance, 16px file-picker text, at least 44px controls, deletion confirmation and safe rendering of model output. The 390x844 review is a Chromium viewport test, not an actual iPhone/Safari verification. The latest synthetic browser ring reached its panel in 133 ms; this is not a Pi performance measurement.

The caption tests use tiny owned HTTP subprocesses, not SmolVLM. They verify one active/one pending job, queued-job replacement, deduplication, observations preceding a later caption, termination during stalled inference or cold startup, restart on a later event, camera-share priority, deletion/shutdown/cancel handling, checksum/provenance gates and no action/identity changes from arbitrary model text. The original 0.7-second mock startup allowance was increased to 2 seconds to avoid terminating the fixture interpreter before reaching the phase under test. Production remains a separately configured monotonic deadline (30 seconds by default), including queue wait/startup/inference and forced termination of the owned process group.

The read-only installer check found Git/compiler/Make on the Mac and CMake absent. No SmolVLM weights were downloaded, no real llama.cpp build or model inference occurred, and no Pi model latency/RAM/thermal or concurrent camera-share result is claimed. The reviewed installer requires explicit invocation, preserves unmanaged files, verifies pinned sources/weights and keeps license notices. Private deployment/authentication and a later approved hardware trial remain required.

## Version 0.3.3 local observations

Local validation passed **120 backend tests**, including the installed official model smoke test, and **10 browser tests**. Event descriptions now persist factual local object/face observations independently of Bot availability. The first-frame regression verifies the description arrives before the remaining capture burst finishes. Tests cover low-quality/no-face uncertainty, positive objects, empty detections without absence claims, offline capture, persistence across restart, retained-event migration, and removal of stale enrolled-match wording after enrollment deletion. Browser tests verify the sentence appears in both activity and event details.

These checks use fixtures or explicitly installed model weights. They do not capture a new camera image, send a Bot request, establish real-scene accuracy, or verify the 0.3.3 Pi deployment. Optional richer Grok image description remains a separate later stage.

## Automated/local checks

Verified for release 0.2.3 on the development Mac on 2026-10-07 with Python 3.13.7, Node 26.5.0 and Playwright Chromium. These results do not establish Raspberry Pi performance.

| Check | Result |
| --- | --- |
| Backend suite | **64 passed**, including explicitly installed official local-model smoke test |
| Browser suite | **9 passed** (`npm run test:browser`) |
| Ring delivery in browser fixture | **146 ms** from Playwright click request to visible ring alert in the latest local run; synthetic camera, Mac localhost |
| Slow-camera behavior | Ring API returns in under 300 ms while the test camera takes 600 ms; event bus publishes the ring before capture |
| Optional CPU detector | Installed OpenCV HOG executes on a blank test frame without downloading a model; this is not a real-scene accuracy test |
| Packaging | Python wheel builds and includes server modules, HTML, CSS, JS and icon |
| Script syntax | Deployment Bash syntax and browser JavaScript syntax pass |
| Visual review | Desktop (1280 px) and mobile (390 px) checked; working navigation, no horizontal overflow, clear offline and synthetic states |

The suites cover persistence, three-frame sequences, duplicate suppression, offline camera behavior, camera-share busy/timeout guards, motion zones and cooldown, retention/media deletion, profiles and manual corrections (including corrections during detection), daily totals beyond one history page, reply approval state, cross-origin write rejection and safe rendering of user text.

A browser regression check stalls audio initialization deliberately: a test ring still arrives without trying to start audio. Chime is enabled through the separate sound control. Browser startup originally delayed the ring path; the final implementation removes that dependency.

One non-failing upstream warning remains: Starlette's test client deprecates its current `httpx` integration. No production API failure was observed.

## Initial Pi deployment check

Verified on 2026-10-06 by read-only API checks after user-local deployment authentication:

- The Pi serves Agent Doorbell version 0.1.0.
- Camera status is online using the real `rpicam` adapter, with synthetic mode disabled.
- The installer test-ring event completed with three saved frames and no camera error.
- OpenCV HOG is available and returned `not_detected` for that event. No images were retrieved for this check, so the result does not establish detector accuracy or image quality.
- The camera-share health API concurrently reports ready and the Pi online.
- Motion monitoring remains off; local retention is seven days.

This confirms app startup, real camera capture, event metadata persistence and detector execution. It does not verify simultaneous camera requests, physical GPIO wiring, unattended startup after reboot or visitor-side audio delivery.

## Version 0.2 local checks

The actual pinned YuNet, SFace and NanoDet weights load and execute locally. A bounded five-run benchmark on this development Mac (Darwin, x86_64 Python runtime, two OpenCV threads, blank synthetic arrays) measured:

| Computation | Median | Maximum |
| --- | --- | --- |
| YuNet, 960x540 blank image | 20.4 ms | 22.5 ms |
| SFace, 112x112 aligned blank input | 18.7 ms | 28.1 ms |
| NanoDet, 416x416 blank input | 48.0 ms | 49.2 ms |

These measurements establish model execution cost on the Mac, not ARM64 Pi latency or recognition accuracy. No real enrollment or biometric image was used. Camera startup, network acceptance and actual Bot notification were not part of this benchmark.

Added tests cover conservative match thresholds, ambiguity and model-version filtering; consented upload limits, storage permissions and deletion; inert text handling; endpoint restrictions and DNS rebinding; synthetic-only pairing; destination confirmation; credential rotation; setup cancellation; bounded retries; ambiguous/interrupted Grok sends; same-ID two-stage deduplication; existing Hermes sessions and exact approvals. Browser enrollment and Bot results use labelled local fixtures; they do not prove a real personal Bot connected or face-match accuracy.

Read-only Pi checks on 2026-10-06 verified version 0.2.1, YuNet/SFace/NanoDet available and motion off. Real camera capture was online before the latest service restart; after restart it is unverified until the next local capture. The previously recorded camera/model timings are from one existing event, not a performance or accuracy benchmark. A Grok Doorbell webhook routine was created in the user-selected Bot with explicit approval. The user entered its key locally. Exactly one approved synthetic test was accepted and visibly acknowledged in that owning Bot chat; no duplicate was sent after the browser interaction timed out. Destination confirmation and ongoing text sharing were then enabled under explicit user approval, with names off and images/embeddings excluded. No real visitor or paired camera-analysis event has been sent during this work. The subsequent user-local 0.2.2 update is verified below.

## Hardware and integration limits

- Pi 0.2.2 startup, real camera availability and local model readiness are verified. A repeatable ARM64 latency benchmark, physical GPIO ring, actual door-view object/identity accuracy and Pi ring-to-panel latency remain unverified. One low-quality face result does not establish usable face-image quality.
- The camera-share busy/timeout coordination paths have automated tests; simultaneous real camera ownership still needs hardware testing.
- Local face/object analysis is ready on the Pi; an actual enrolled-face match is unverified. Synthetic Grok receipt and the user's real-camera paired test analysis are verified. Physical ring delivery, visitor-side speaker and push notification behavior remain unverified.
- Browser speech approval is implemented; physical audibility and visitor delivery are not established by browser automation.
- Synthetic fixtures are labelled in frames, events and the UI. They are not real camera or visitor evidence.
- SSH deployment requires the user to authenticate locally. No chat-supplied password is entered by the deployment workflow.

## Version 0.2.1 focused safety review

Concrete fixes: unknown profile deletion is checked before filesystem work, enrollment paths accept only generated profile IDs, and deletion clears stored automatic face references/scores while preserving manual labels. Newly created data/face directories use mode 700. Malformed nested Hermes capability/session/approval responses and deletion during a failed in-flight send now fail the affected delivery without stopping subsequent jobs. Known configured bearer echoes are redacted before output/approval truncation, including echoes of the other adapter's key.

Regression coverage includes percent-encoded dot profile IDs, private filesystem permissions, stale automatic metadata removal, malformed Hermes JSON, in-flight deletion, redaction at truncation boundaries, invalid ports/IPv6 URLs and actual localhost HTTP transport. The isolated transport test uses fake credentials: a redirect to a metadata address is rejected without being followed; proxy environment variables do not redirect the request; even an allowlisted metadata address remains blocked. No live bot or credentials were configured.

Code review confirmed bot payloads use an allowlist excluding notes, media URLs, photos and embeddings, including history; credential inputs are fixed process-environment variables, with no API for reading an arbitrary secret file. Grok HTTP 200 remains accepted, not completed. Browser speech is called only after that browser's explicit approval action; proposal/SSE updates and bot output cannot start playback or a Pi speaker. Ring publication and bot queueing precede asynchronous camera/model work.

This remains a trusted-LAN prototype without accounts: LAN clients can read records and change setup. Endpoint controls are not caller authentication. The review does not establish adversarial network safety, real recognition accuracy, physical speaker behavior or end-to-end personal Bot delivery. The existing Pi install command and explicit pinned-model installation are unchanged.

## Version 0.2.2 confirmed paired-bot ring test

Agent space adds a separate **Test ring with paired bot** action. The selected connection must be paired, destination-confirmed and enabled; unsaved setup changes disable the button. Its confirmation identifies the destination, test press, current-camera analysis, up to five recent text summaries and name-sharing scope. Cancel sends nothing. The Overview test remains local-only.

Four backend regressions verify two stages with one event ID only to the selected Bot, no accidental delivery from the local Overview test, rejection before capture for missing/disabled/stale credentials or missing confirmation, prevention of delayed analysis following a changed connection, and an offline-camera analysis update. The new browser fixture verifies exact destination/data wording, cancellation, the saved revision submitted after confirmation, and disabling on unsaved changes. These automated checks use synthetic cameras or an offline camera and fake credentials; they do not contact a live Bot. The separate, user-approved live pairing test is recorded above.

## Pi 0.2.2 update verification — 2026-10-07

After the user completed the existing deployment command, read-only checks of `/api/status`, `/api/bots` and the served HTML confirmed version **0.2.2**, real camera **online** with simulation off, local face/object models ready, and the **Test ring with paired bot** button present. The Grok connection remained verified, destination-confirmed and enabled, with credential presence reported, enrolled names off and images off. No credential value, camera image or face embedding was retrieved, and no additional capture or Bot event was triggered by these checks.

The browser tab may need a refresh to load the new button. At the time of this update check, paired camera-analysis delivery awaited the user's separate confirmed button press. The subsequent result is recorded below. The Overview test remains local-only; physical GPIO remains unverified.

## User paired-camera result and 0.2.3 description correction — 2026-10-07

The user supplied the owning Grok Bot's response to the **08:20 IST** test ring. Read-only local metadata for the same event confirms `source: browser_bot`, real-camera mode, three completed frames, `person_status: detected`, `face_status: low_quality` and one positive `person` detection at model confidence 0.814. The ring and analysis delivery jobs share that event ID and were each accepted once. The user-provided Bot response verifies analysis receipt in chat; HTTP acceptance alone would not do so. No images, embeddings, credential values, new captures or additional live Bot events were retrieved or sent for this investigation. This confirms real-camera analysis text delivery, not face recognition accuracy.

The previous saved routine asked for a fixed person/animal report, causing an irrelevant absent-animal sentence even though the event's object list contained only a person. Release 0.2.3 removes that product requirement, retains general canonical positive object detections, and adds bounded observation text with useful face uncertainty. UI details no longer list absent animals. The updated routine template and an existing-routine correction are in `docs/BOT_SETUP.md`; applying a source update cannot itself edit a saved cloud routine.

New regressions cover general COCO class mapping, invalid/below-threshold predictions, positive-only observation sentences, useful low-quality identity wording, permitted object labels in current/history payloads, exclusion of names/media/embeddings/arbitrary descriptions, and browser details with positive or empty detections. Tests use fixtures and fake credentials; they do not send another live test. Image sharing remains off: a supported authenticated attachment route for the existing Bot has not been verified.

Local release validation passed 64 backend and 9 browser checks, a Python wheel build, JavaScript/Bash syntax and Git whitespace checks. Non-interactive SSH to the known Pi still requires owner-local authentication; no remote install was performed. The available execution tools cannot operate the Grok routine editor, so its saved instruction remains unverified until the existing-routine edit is applied and reviewed.

## Version 0.3.1 carrier verification

The reviewed command builder rejects quotes, substitutions, backticks, whitespace, line breaks, non-ASCII, extra command fields and oversized carriers before constructing shell text. The cloud parser independently validates canonical encoding, lengths, digests and the strict inner event/JPEG schema. Offline 16/32/48 KiB valid JPEG carrier tests preserve exact bytes through a shell data argument and verified PNG normalization. No raw event text or executable/path is supplied by the carrier. These offline results do not establish live webhook or model transcription reliability.

The owner-local update deployed 0.3.1, configured the exact reviewed decoder and left camera sharing off. One generated 19,381-byte JPEG request was accepted once. The Grok worker summary reported incomplete carrier data and rejection before decoding; later inspection could not retrieve the original body or worker trace. No exact received count, truncation marker or raw-body digest was available. Actual network truncation, model-visible preview clipping and context/tool limits cannot be distinguished. No vendor payload ceiling was established, no PNG/read/expiry test occurred, and the request was not repeated.

## Version 0.3.2 diagnostic recovery

Local checks passed **112 backend tests** including the installed official model smoke test, and **10 browser tests**. The new tests cover generated request-ID deduplication, a complete diagnostic JSON budget of 4,096 bytes, persisted sender count/hash metadata, preserved real-camera JPEG quality, receipt persistence before viewing, rejection of shortened carrier arguments and browser confirmation/data submission. Tests use generated or fake data and do not send a live Bot request or capture a camera frame.

The 4 KiB budget applies only to the new generated geometry probe. It is an application diagnostic choice, not a documented provider limit or a production quality solution. Normal camera encoding retains the prior 512/384-pixel, quality 60/45 and 48 KiB JPEG limits. Camera sharing remains gated off. Even successful reading of the compact geometry probe would not prove that people, packages or activity can be described usefully in a real door scene.

Sender receipts retain whole serialized request byte count/hash, carrier byte/character count/hash and image dimensions/hash after encoded bytes are scrubbed. The separately reviewed cloud audit wrapper pins the unchanged decoder and fsyncs a private JSON receipt before carrier/image decoding. Its count/hash evidence applies to the worker-forwarded tool arguments, **not the original raw HTTP body**. It does not attest native image reading; that requires independent tool evidence. Receipt metadata is bounded to 200 files and best-effort 24-hour stale cleanup on later invocation. PNGs retain best-effort 120-second expiry. Neither cleanup erases provider chat, tool records or backups.

The next probe must use a distinct explicit marker and request ID, with native receipt inspection before scene interpretation. No old event may be resent. Larger image transfer, meaningful real-scene quality and hosted expiry remain unverified. An authenticated image-fetch route could be considered separately if needed; it is not implemented and would require an explicit decision about new reachability and credentials.
