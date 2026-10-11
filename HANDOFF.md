# Agent Doorbell handoff — 8 October 2026

The product goal remains **doorbell press → camera snapshot → local scene description → the existing Grok text notification**, with familiar-person enrollment through the dashboard. Version **0.3.9** fixes the degenerate local caption request and sends a usable caption onward as unverified text. That package is deployed. One real camera ring captured three frames. The app marked the ring, analysis, and caption deliveries accepted, which means the existing routine started. It does not prove the owning chat finished.

## Code and deployment

The application/package version is **0.3.9**. `scripts/update-pi.sh` deployed the caption follow-up; the service then reported version **0.3.9**, local captions enabled, and the existing pairing still enabled, verified, and confirmed. Image sharing and name sharing stayed off. The camera ring below ran on code commit `ee7f1f8d24bf4ce21b164f2d14af7d003f21e9ae`. The earlier 0.3.8 rollout was `9627d5d1d94d2d65d8250bbb33539f0b8e4992c6`.

0.3.9 changes the owned llama.cpp request and adds a caption follow-up through `BotService.enqueue` / `enqueue_event`. Ring and detector stages are unchanged and still run before the caption. The follow-up keeps the original event ID and the pairing revision captured at ring time. It is deduplicated, limited to 400 characters and 120 seconds of event age, and dropped when the event is deleted, the saved caption no longer matches, the revision changed, or the credential fingerprint changed. The payload is labeled `unverified_local_model_caption`, carries an uncertainty sentence, and includes no names, images, embeddings, or notes. Caption text is untrusted data in that payload, not a command. No routine, key, account, or bridge is created.

## Why both device trials returned ` 1`

Pinned llama.cpp `42b021b4dc42be573f1e1463528532fc8294c650` rewrites an OpenAI `image_url` part into a random `<__media_…__>` marker and then, because the part is media, stringifies the message before Jinja. The SmolVLM template emits the literal `<image>` only when a content part's type is exactly `image`. That branch never runs. The previous request put text first, so the marker was appended after the prompt. The server still spent image tokens (about 205 prompt tokens on a local rerun) and returned the nonempty string ` 1`, which the old smoke check accepted.

The app now sets `LLAMA_MEDIA_MARKER=<image>` on the owned server and sends the image part before the text. The model's own template then renders the marker at the front of the user turn. The weights and the pinned revision are unchanged. A separate smoke check requires at least 8 collapsed characters and two alphabetic words of three or more letters. ` 1` fails it and is not stored or forwarded. `caption_accuracy_verified` stays false: the smoke check does not judge whether the sentence matches the scene.

Local generated-shape rerun on the development machine, same pinned weights, CPU-only server, two threads, temperature 0, 48 tokens, blue square and red circle, no camera and no Bot send:

| Request | Raw text | Time |
| --- | --- | --- |
| Previous: text first, random marker | ` 1` | 28.410 s cold, 83.853 s warm, after the server was already listening |
| Corrected, through the app provider: image first, marker `<image>` | ` The image shows a blue square and a red circle.` | 23.62 s whole cold caption, including checksum, startup, and inference |

A second corrected request on that same server did not return within 180 s. An earlier corrected warm attempt was still inside prompt evaluation when it was stopped at 120 s. Warm reuse is not established on the development machine. A second cold app-provider run of the same corrected request took 64.262 s and returned the same sentence. A later confirmation on the same pinned binary returned ` 1` in 21.849 s for the old text-first request, and the same blue-square sentence in 70.155 s for the corrected app provider, including checksum and startup. That corrected warm request did not return within 120 s. Sampled peak RSS on the 64.262 s run, via `ps` because `/proc` is absent, was 431,476,736 bytes. There is no `/sys/class/thermal` reading on that machine. The spread means the 30-second product deadline is not a stable budget there. One matching sentence on one generated image is not scene-accuracy verification. The device camera caption is a separate measurement below.

The repository must remain private. Do not commit runtime receipts, personal diagnostics, account addresses, routine URLs/keys, SSH credentials, photos, embeddings, databases or model weights. [Fork setup](docs/FORK_SETUP.md) describes installation without the original owner's paths or computer.

## Latest device evidence

Read-only API status at **08:44 UTC on 8 October 2026**, together with the completed owner-local receipt, confirms the app is healthy on **0.3.8** and **captions are installed, pinned-file verified and enabled**. The previously unavailable API and absent receipt were intermediate observations; they are superseded by this completed result. Recheck before treating this snapshot as current.

| Capability | What is established | What remains |
| --- | --- | --- |
| App/dashboard | Service reported **0.3.9** after `scripts/update-pi.sh` | Exact file equality with a later documentation commit is settled only by that updater |
| Camera | One ring: `rpicam` online, capture `complete`, 3 frames, first frame 13,480-byte JPEG | Scene content and later lighting are not established by that one frame |
| Local detection | Face/recognition and object model availability both true; this ring reported `not_detected` | Accuracy at this camera angle, lighting and distance |
| Grok text | Existing pairing stayed enabled, verified, and confirmed. Ring and analysis deliveries were **accepted** | Accepted means the routine started, not that the owning chat finished |
| Local VLM runtime | Managed runtime/weights stayed pinned-file verified. Captions stayed enabled. This ring's caption was **ready** in 11,372.9 ms | Corrected-request warm time was not measured; only one ring was sent |
| Caption text to Grok | Same event, text only, images off. Delivery **accepted** with the routine-started detail | The owning chat was not independently read. The caption is an unverified observation |
| Grok images | Image sharing and owner image allowance both off | Native reading/useful interpretation of original visitor images not proven |
| Household enrollment | Dashboard Add people → consented local photos → local photo test implemented/tested | No real enrollment or recognition accuracy established in this handoff |
| Burst recognition | Every captured frame analyzed; conservative disagreement/incomplete-evidence gates tested | Real burst-camera timing and recognition at the door |
| Enrollment bridge | Disabled; restricted configuration/listener false | Parked; interrupted transaction must be reconciled before any future activation |
| Lock/access | Simulation code only; `real_actuation: false` | Actual lock hardware, wiring, power, actuator protocol and independent visitor verification unknown |
| Physical button | Optional adapter exists | Wiring/GPIO behavior not verified |

After that one camera caption, the owned provider reported `ready: true`. The raw caption text was `The image is a photograph of a plain white background.` Smoke acceptance of that sentence is not scene-accuracy verification. No second ring was sent, so this device has no corrected-request warm time. The earlier generated-shape warm time of 10.269 s used the old request and returned ` 1`.

The owner-run command completed **real local inference on a generated 512×384 JPEG containing a blue square and red circle**, with no camera, people, enrollment, Bot send or actuation:

| Measurement | Result |
| --- | --- |
| Cold whole-caption time | 11.220 s |
| Warm whole-caption time | 10.269 s |
| Deadline | 30 s per trial; both completed |
| Sampled runtime peak RSS | 546,734,080 bytes (about 521 MiB) |
| Pi temperature before/after | 48.0°C → 57.3°C |
| Generated output | Both returned the nonempty string ` 1` |
| Owned trial runtime | Stopped after the trials |
| Application/package | 0.3.8 before and after; neither source nor package reinstalled |
| Configuration | Captions enabled; only Doorbell restarted; no OS/security settings changed |

The receipt's `captions_enabled_synthetic_verified` label establishes **pinned files, runtime completion and enablement**, not useful image description. Those two device trials returned ` 1` because the smoke check then accepted any nonempty text. The 0.3.9 check rejects that text. The later camera ring is the corrected-request measurement: 11,372.9 ms, three captured frames, and the raw sentence above. It does not measure warm reuse, throttling, or concurrent camera-share performance. Do not reinstall caption files that already verify.

The completed caption installer required no sudo. Preserve the existing routine, private settings and security policy. Further updates use `scripts/update-pi.sh` for one tested commit. Do not use `deploy-pi.sh` on this existing install.

## Architecture and model roles

[Architecture](docs/ARCHITECTURE.md) and [local model details](docs/LOCAL_MODELS.md) are the implementation references.

- One FastAPI/Uvicorn process serves the dashboard/API and SSE ring alerts; SQLite and event media remain in the private data directory outside the checkout.
- Ring persistence and the immediate Bot text stage precede camera/model work. Camera capture yields to the existing camera-share guard. Three-frame bursts produce a later deduplicated local-analysis stage with the same event ID.
- **YuNet** finds faces, **SFace** compares locally enrolled embeddings, and **NanoDet** reports positive object labels. Their factual observation sentence is already sent through the approved Grok text flow. A probable face match never proves identity or authorizes entry.
- **SmolVLM-256M-Instruct Q8_0** plus its projector supplies a richer, fallible caption of the first saved event frame. The pinned CPU-only **llama.cpp** server is an app-owned loopback process with two threads, one slot, at most 48 output tokens and owned-process timeout/cleanup. It receives no Bot key, enrolled names, embeddings or conversation history. Pins/checksums, 278,824,384-byte weight total and license records are in [local captions](docs/LOCAL_CAPTIONS.md).
- A usable caption can be sent later as unverified text on the same event. It does not enter identity decisions, tools, approval, or access logic. Enablement alone did not deliver captions; 0.3.9 adds that follow-up, and one camera ring accepted the routine start.
- Grok uses the **existing authenticated webhook routine**, not a model API credential or generic remote executor. HTTP 200 means a routine started; it is not a chat-completion receipt. Names, images and embeddings remain excluded by current configuration.

## Completed fixes and known limits

The version-safe caption helper replaced an older release-specific shell path whose unexported environment variable could abort the caption stage after an app update. Do not rerun that older updater: it can install an older app archive. `scripts/captions-pi.sh` performs separate check/install/enable stages without application redeployment.

The latest tested caption fix validates the **selected** runtime, refuses an unsupported custom-runtime installation before mutation, and distinguishes file presence, executable/expected-size availability, pinned hashes/provenance, missing components and unreadable components. A disabled caption service or null provider says nothing about whether files were installed. File presence alone is not pinned verification; verified files alone are not a measured inference result.

Restricted setup previously recorded success but current owner/root diagnostics showed partial cleanup: role, public-key registration and owner policy absent while a recorded runtime socket remained. The exact trigger is not established. A historical success receipt is not live readiness. Transaction helper checks were corrected for root `sudo -l -U` returning zero with an explicit denial, restartable partial cleanup, empty-inbox readiness and output failure after committed success. Those transaction-specific controllers were tested on the owner workstation, **not applied as a successful recovery**, and are not a generic production installer in this repository.

**Do not blindly rerun the original restricted setup or generate a replacement credential after partial credential creation.** Preserve existing private receipts/backups and reconcile only the same approved transaction, with identity/collision, owner SSH-policy and recorded socket checks. An active/changed socket or reused UID/GID must not be deleted. Leave the bridge parked for the core handoff. [Restricted proposer](docs/RESTRICTED_PROPOSER.md) records the boundary and recovery requirements; its plan command has no apply mode.

An owner-approved Grok UI test succeeded via conversation details → Library → media → Download media, saving a diagnostic screenshot locally. A file hash matched its suggested filename, but no independent original digest was obtained. This establishes **manual UI save behavior**. Automated original-photo export, exact original byte identity, permitted visitor source references and an exact destination grant remain unproven. A blocked copy must not be worked around by reencoding, wrapping or moving it through another route. See [image flow](docs/GROK_IMAGES.md) and [restricted export limits](docs/RESTRICTED_PROPOSER.md#what-is-established-about-grok-attachment-export).

A notification credential was previously posted in chat; the owner declined rotation. Never reproduce it or create a replacement routine without authorization. Existing private pairing/settings must be preserved.

## Verification evidence

For 0.3.9, 90 caption, bot, and doorbell cases passed locally. The full backend run passed 333 cases with 2 skips. Two update-helper cases failed on this machine's default Python 3.9, which has no `tomllib`, and passed when `python3` was 3.13. That local suite does not replace exact-head CI, and it does not verify the Pi.

For handoff implementation commit `21c858e0e09b5382fb203c4efad82b539cfc06e1`, [CI run 37751140001](https://github.com/kaku-san/RingPi/actions/runs/37751140001) passed **322 backend cases with 5 skips**, **19 Chromium browser cases**, and the actual disposable-Linux restricted-SSH job. The subsequent handoff correction changes documentation only. Earlier caption-fix baseline `24d9b81fdf1f4ebd2604e6a427480ebf1b48d0be` also passed [its exact CI](https://github.com/kaku-san/RingPi/actions/runs/37742534091). Skips reflect absent optional NumPy/OpenCV/official weights; the Linux native kernel check runs there. Native SSH CI creates only ephemeral fixtures and is not production-role readiness or biometric accuracy evidence.

The handoff's full Mac backend check passed **325 cases with 2 skips** (unconfigured official-model weights and the Linux-only kernel credential API). The benchmark/setup/installer focused check passed **36 cases**. Benchmark fixtures establish generated input, cold/warm provider reuse, error/deadline reporting and owned-provider cleanup; they run **no model**. Shell syntax and benchmark CLI help passed. Current handoff CI must be checked against its exact head SHA, not inferred from that older green run. See the repository [Actions workflow](https://github.com/kaku-san/RingPi/actions/workflows/test.yml).

Two earlier full Mac runs hit different unchanged timing fixtures, each with 320 other cases passing; the five affected timing cases passed in isolation, and the complete exact Linux baseline CI passed. Assertions were not weakened. Prior transaction-helper checks passed 42 cases including real temporary local sockets; that does not establish successful Pi recovery.

One hardware capture and its paired text deliveries are recorded above. No enrollment, lock action, or security-setting change was performed. The generated-shape trial and its limits remain above; mocked test counts must not be substituted for that device evidence. An accepted delivery is not live Bot-chat completion, and one caption is not scene-accuracy proof.

## Next steps, in order

1. The degenerate ` 1` response is explained and the request shape is fixed. Do not replace the pinned model. The smoke check is separate from scene accuracy.
2. `scripts/update-pi.sh` deployed 0.3.9. Do not use `deploy-pi.sh` to update this existing install, and do not reinstall caption files that already verify.
3. The caption follow-up is implemented through the existing enqueue path. Focused tests cover fast and slow completion, deletion, pairing and credential changes, failure, queue and age bounds, and duplicates.
4. One camera ring is recorded above: three frames, cold caption 11,372.9 ms, raw unverified text, and accepted routine starts for ring, analysis, and caption. Corrected warm time and the owning chat were not observed. A probable face match never proves identity.
5. Use dashboard **Add people** for separately consented enrollment. Leave Grok photo transfer, restricted provisioning and simulated lock extensions parked until the core works and the owner requests them. Real lock work needs actual hardware information and separate authorization.

## Portable commands

Run from an authorized repository checkout. Replace `OWNER@PI_IP` and `TRUSTED_PI_ALIAS` with the existing account and saved trusted host-key alias; preserve host-key checking. An unexpected changed key blocks connection until the owner verifies it. Owner SSH passwords stay in local Terminal. New setup accounts, sudo/security changes, model substitutions, external sends or real-camera tests are not implied by reading this handoff.

For a later unavailable API or failed service, inspect existing user-service metadata before attempting repair:

```bash
ssh -o StrictHostKeyChecking=yes -o UpdateHostKeys=no \
  -o HostKeyAlias=TRUSTED_PI_ALIAS -o ForwardAgent=no -o ClearAllForwardings=yes \
  OWNER@PI_IP \
  'systemctl --user show agent-doorbell.service -p ActiveState -p SubState -p Result -p ExecMainStatus'
```

If needed, inspect `journalctl --user -u agent-doorbell.service -n 40 --no-pager` locally in the authenticated Pi Terminal. Share only the relevant redacted error, not the environment file or whole private log. This inspection requires no sudo.

Read-only readiness (also reports the existing running app version):

```bash
bash scripts/captions-pi.sh OWNER@PI_IP TRUSTED_PI_ALIAS --check
```

Only if a later check proves verified files missing and no installation is running; the current installation already verifies:

```bash
bash scripts/captions-pi.sh OWNER@PI_IP TRUSTED_PI_ALIAS --install
```

After installation, the following sends only this repository's generic benchmark source to the existing Pi Python over SSH. It loads the deployed setup helper's private path reader from its existing scripts directory. It installs nothing, changes no configuration and uses locally generated blue-square/red-circle JPEG input. The two trials each have a 30-second whole-caption deadline; only the app-owned trial runtime is stopped afterward.

```bash
ssh -o StrictHostKeyChecking=yes -o UpdateHostKeys=no \
  -o HostKeyAlias=TRUSTED_PI_ALIAS -o ForwardAgent=no -o ClearAllForwardings=yes \
  -o RequestTTY=no -o ConnectTimeout=10 OWNER@PI_IP \
  'cd "$HOME/agent-doorbell/scripts"; exec "$HOME/agent-doorbell/.venv/bin/python" -' \
  < scripts/benchmark-local-captions.py
```

It prints cold/warm elapsed time, sampled runtime peak RSS and temperatures when readable. It reports failure rather than silently extending the deadline. It does not verify caption accuracy, benchmark the real camera or contact Grok. It can also run directly on the Pi from an updated checkout as `.venv/bin/python scripts/benchmark-local-captions.py`.

Captions are already enabled on the current Pi. For a later separately requested enablement, after reviewing pinned verification and useful trial output:

```bash
bash scripts/captions-pi.sh OWNER@PI_IP TRUSTED_PI_ALIAS --enable
```

This preserves unrelated private settings and restarts only Doorbell, keeping the application version unchanged. Enabling allows captions on future eligible owner-triggered events; it triggers no capture or Bot test itself. It still does **not** implement caption-text forwarding.

Local validation and exact CI inspection:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
npm ci --ignore-scripts
npx playwright install chromium
npm run test:browser
git rev-parse HEAD
gh repo view --json visibility
gh run list --workflow test.yml --commit FULL_COMMIT_SHA
gh run view RUN_ID --json headSha,status,conclusion,jobs
```

The official-weight smoke test requires separately installed pinned test weights via `DOORBELL_TEST_MODELS_DIR`; absent optional numerical/vision packages can cause further skips. The native restricted-SSH harness must run only in its disposable CI environment, never on the Pi/workstation. [Validation history](VALIDATION.md) preserves earlier results and limits.

A later **owner-requested source update** uses the tested full commit, without a camera or Bot test:

```bash
bash scripts/update-pi.sh OWNER@PI_IP TRUSTED_PI_ALIAS FULL_TESTED_COMMIT_SHA
```

Do not use initial `deploy-pi.sh` to update this existing installation: that installer performs its documented hardware check. New-fork setup and initial deployment are separate from this handoff. No unattended-start guarantee, push notification guarantee or physical lock integration is established.
