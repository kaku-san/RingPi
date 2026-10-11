# Agent Doorbell

A local-first doorbell for a Raspberry Pi 5. The Pi keeps the camera, the faces, and the event record. A paired Grok Bot routine can receive text webhooks. It stays separate from other local apps: its own directory, environment, port, database, and user service. This is a prototype. Placeholders only: `OWNER@PI_HOST`, `TRUSTED_HOST_ALIAS`, `OWNER_SSH_KEY`.

## Quickstart

### Hardware

- Raspberry Pi 5, a Pi camera, and a private network
- Optional normally-open button between one free BCM GPIO and GND
- Do not use BCM 8, 10, 11, 17, 24, or 25; those pins are reserved by the existing screen adapter wiring

### Flash Pi OS

Flash 64-bit Raspberry Pi OS, boot it, and enable SSH. Put the login and host-key alias only in your own terminal. Do not commit them.

### Clone and setup

```bash
git clone <your-fork-url> agent-doorbell
cd agent-doorbell
bash scripts/setup.sh
```

`scripts/setup.sh` with no arguments creates a local `.venv` and installs the app. Pip uses `PIP_DEFAULT_TIMEOUT` (default 120 seconds).

The first Pi install is the existing `scripts/deploy-pi.sh` path. The setup script forwards to it and does not add a second installer. SSH `ConnectTimeout` is 10 seconds. The first install also runs a local camera smoke check.

```bash
bash scripts/setup.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS
bash scripts/deploy-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --models
```

Later source updates use `scripts/update-pi.sh`. It does not download models or run the camera check. An optional identity is a path, not a key:

```bash
DOORBELL_SSH_IDENTITY=OWNER_SSH_KEY bash scripts/update-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS FULL_COMMIT_SHA
```

### Pair the Grok Bot webhook

Follow [Bot setup](docs/BOT_SETUP.md#grok-bot-xai--cursor-step-by-step-pairing). Paste [examples/grok-bot-routine.md](examples/grok-bot-routine.md) into the existing routine. Save the routine URL in the dashboard and the key only in the Pi environment file `~/.config/agent-doorbell/environment`. Confirm the synthetic test in the owning chat before enabling sharing.

An optional command queue is separate from that webhook. Leave `DOORBELL_GROK_COMMAND_QUEUE_URL` unset until a queue exists. The queue bearer name is `DOORBELL_GROK_COMMAND_QUEUE_KEY`. Put either value only in the private environment. A finished command posts `reply_text` on the existing webhook. See [outbound commands](docs/OUTBOUND_COMMANDS.md).

### Enroll a household face

Install local vision weights with `scripts/install-local-models.py` or the `--models` installer above, then use **Overview → Add people** on the dashboard. See [local models](docs/LOCAL_MODELS.md#add-people-on-your-phone). One clear consented JPEG or PNG. The blur gate rejects a face under 70 pixels on its shorter side, or a face-crop Laplacian variance under 30.

### Add a guest

One consented photo and a name, over the existing SSH session. Commands and the receipt are in [the guest contract](docs/GROK_BOT_GUEST.md). A visit window is optional (`--from` / `--until`).

### Issue a PIN

```bash
.venv/bin/python -m agent_doorbell.bridge_client issue-pin \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --minutes 60 \
  --label 'Parcel'
```

The PIN prints once. The visitor enters it at `/pin`. This consumes a simulated pass only.

### Test ring

On the dashboard, **Test the doorbell** is the demo ring. When pairing is enabled it sends one `visit` webhook, the same message as a physical ring, after recognition and the caption settle. `event.observations` is `Probably {name} ({score})` when `event.match_name` and `event.match_score` are present, and `unknown` when they are absent. A short caption is `event.caption` on that same visit when one exists. **Guest approval → Connect your bot → Test ring with paired bot** is the single-bot confirmation. No physical GPIO button has been verified.

### Troubleshooting

| What you see | What to check |
| --- | --- |
| Button service | Install the gpio extra, set `DOORBELL_BUTTON_BCM` in `~/.config/agent-doorbell/environment` (no default pin), copy [deploy/agent-doorbell-button.service](deploy/agent-doorbell-button.service) to `~/.config/systemd/user/`, then `systemctl --user enable --now agent-doorbell-button.service`. The unit runs `scripts/gpio_button.py` and exits if the pin is missing or reserved. The API timeout is 3 seconds. |
| Camera aim | A usable face is about a quarter of a 960-pixel-wide frame, frontal and sharp. Smaller or softer faces stay `low_quality` and are not enrolled. |
| Captions cold start | The caption deadline includes queue wait, image preparation, cold model load, and inference. The default `DOORBELL_CAPTION_DEADLINE_SECONDS` is 30. A first caption after boot can miss that deadline and stay interrupted. Raise the deadline in the private environment and restart only `agent-doorbell.service`. Degenerate text such as ` 1` is dropped and not forwarded. |
| Blur gate | Enrollment and matching require face side ≥ 70 and Laplacian variance ≥ 30 on the decoded frame. The dashboard says the face is too small or blurry. Rejected photos are not stored as samples. |

A public copy of this history is a separate decision. Older commits can still contain a machine hostname. The safe publication path is a new empty repository and one squashed snapshot of this tree, with no history copy, and only after the owner agrees. This repository does not create that snapshot.

## Tree

```text
agent_doorbell/    dashboard, camera, webhooks, PIN, and local vision
scripts/           setup.sh, deploy-pi.sh, update-pi.sh, gpio_button.py, test.sh
deploy/            agent-doorbell.service and agent-doorbell-button.service
docs/              pairing, captions, guests, and demo alerts
tests/             backend and browser checks
examples/          Grok Bot routine prompt and payload samples
```

The button unit is [deploy/agent-doorbell-button.service](deploy/agent-doorbell-button.service). It starts `scripts/gpio_button.py`. `DOORBELL_BUTTON_BCM` is required. The file does not choose a pin.

Hardware-free demo, bridge, and pairing detail stay in [FORK_SETUP.md](docs/FORK_SETUP.md), [ENROLLMENT_BRIDGE.md](docs/ENROLLMENT_BRIDGE.md), and [BOT_SETUP.md](docs/BOT_SETUP.md). Deployment notes are in [VALIDATION.md](VALIDATION.md).

**The Overview Test the doorbell button sends one `visit` webhook, the same message as a physical ring, when pairing is enabled.** `event.observations` is `Probably {name} ({score})` or `unknown`. Motion and other clicks that do not create a ring stay local. **Guest approval → Connect your bot → Test ring with paired bot** remains the explicitly confirmed single-bot test. No physical GPIO button has been verified.

## Version 0.2: local recognition and personal bots

Current release: **0.3.16**. An optional local text model adds one short `event.summary` line on a visit and one sentence on the counts-only daily summary when `DOORBELL_TEXT_MODEL_PATH` points at a local file for Qwen 2.5 1.5B Instruct (local text model, llama.cpp). A missing file keeps the existing text. See [demo alerts](docs/DEMO_ALERTS.md). An optional outbound command queue can add a guest, issue a one-time PIN, or revoke one, then post a short `reply_text` on the existing webhook. See [outbound commands](docs/OUTBOUND_COMMANDS.md). Person-presence motion, quiet hours, and a counts-only daily summary are in [demo alerts](docs/DEMO_ALERTS.md). Local captions now place the image before the prompt text and pin the runtime media marker, and a usable caption can follow the ring as unverified text through the existing paired routine. One consented photo and a name can be enrolled over the existing SSH session; see [the Grok Bot guest contract](docs/GROK_BOT_GUEST.md). **0.3.8** added a disabled [restricted proposer](docs/RESTRICTED_PROPOSER.md): one private Mac inbox → dedicated SSH identity → fixed command → kernel-verified Unix socket → pending five-photo proposal. Separate owner Terminal approval creates temporary references and a one-time **simulated** visitor pass. Provisioning needs separate approval and remains inactive. Grok attachment export still requires permitted original files and an exact destination grant; no unsupported cloud API is assumed. Recognition checks every captured burst frame with conservative disagreement handling. The **Add people** flow connects: name → chosen reference photos → explicit local photo test. Quick detector observations remain independent of Bots. Optional richer **local** captions use an isolated, pinned SmolVLM 256M / llama.cpp CPU runtime with a bounded queue and owned-process timeout. The caption runtime/model must be installed explicitly; real caption quality, Pi latency and concurrent hardware performance remain unverified. See [add people](docs/LOCAL_MODELS.md#add-people-on-your-phone) and [local captions](docs/LOCAL_CAPTIONS.md).

The [visitor-pass and lock simulation guide](docs/ACCESS_SIMULATION.md) records advance authorization and unresolved live boundaries. Local fixtures do not actuate a lock, accept Grok photos or grant real access. Version 0.3.9 keeps the lock behind a simulation-only plugin. A probable guest match at the door creates an owner approval and cannot move the lock; the device label DONE is used only after the server confirms a simulation. `scripts/restricted-proposer-reconcile.py` prints a recovery plan, has no apply mode, and does not mint a replacement credential or delete a socket. That planner has not been run against a device. [Caption setup](docs/LOCAL_CAPTIONS.md#version-independent-owner-setup) checks, installs and enables separately without redeploying the app.

- Local familiar-person enrollment from user-selected, consented photos. YuNet finds a face; SFace creates a local embedding. Matching produces a **probable enrolled match**, unknown, uncertain, no-face, multiple-face or low-quality result. Manual corrections take priority.
- NanoDet reports positive detections from its canonical COCO object vocabulary on the Pi. Bot summaries focus on relevant observations and useful identity uncertainty. Detections do not establish occupation, activity, ownership, parcel possession or a full scene description.
- A physical ring and the Overview **Test the doorbell** button send one `visit` webhook to an explicitly paired **Grok Bot routine** after recognition and the caption settle. `event.observations` is `Probably {name} ({score})` when `event.match_name` and `event.match_score` are present, and `unknown` otherwise. `event.caption` is included when a short caption exists. The visit is deduplicated locally. Caption text is not a command and is not identity. Text sharing excludes embeddings; optional image sharing is separately configured and defaults off. Zone-motion captures stay local. A person present for the configured duration can send one unverified `motion` webhook, and quiet hours can hold visit and motion webhooks unless there is a probable known-face match. A command-queue result adds `reply_text` on the existing webhook.
- Hermes supports an existing active session, its existing memory scope, idempotent run submission, status polling and exact human tool-approval requests. Run output appears on this dashboard; automatic gateway delivery is separate.
- Bot adapters start unconfigured and disabled. Saving setup makes no network probe. A synthetic test and destination confirmation are required before explicitly enabling sharing. Dot and Muse show pending/unsupported trigger states rather than fabricated connections.

**The deployed package reports 0.3.9**, with captions enabled and the existing text pairing retained. One camera ring captured three frames. The corrected caption took 11.4 s and was forwarded as unverified text; the app reported the routine as started, which is not chat completion. The old on-device trials returned only ` 1`. Generated-shape limits are in [HANDOFF.md](HANDOFF.md). The disabled bridge stays parked. [VALIDATION.md](VALIDATION.md) retains historical checks.

## What works

- Responsive dashboard and event history, served entirely by the Pi—no CDN, external fonts or frontend build required.
- Browser test ring and an optional GPIO button adapter. The event is saved and streamed to connected browsers before camera work begins.
- Opt-in browser chime. Mobile/background delivery depends on the browser remaining active; this is not a push-notification service.
- On-demand snapshots and a preview refreshed roughly every three seconds. The UI explicitly says “fresh snapshots”; this is not a high-frame-rate video stream.
- Timestamped three-frame event sequences, stored locally. Camera-busy, offline, interrupted and unavailable-detector states remain visible in event history.
- Opt-in motion-zone monitoring, two successive changed frames and a configurable cooldown to suppress duplicates. Frame differences are not person detection.
- Optional CPU pedestrian detection using OpenCV’s built-in HOG detector. It is experimental, can miss close/partial people, and is not identity recognition. No large model download is required.
- Local familiar-person profiles, enrolled photos/embeddings, unknown/uncertain outcomes and manual event corrections. Matching requires the optional verified local weights; a prediction is not identity verification.
- Local event/context API and an approval inbox for reply proposals. Approved replies can speak on the approving browser; no door speaker is configured, and personal Bot sharing requires explicit pairing.
- Automatic local retention (1–30 days, default 7) and a 500-event cap. Deleting an event also deletes its images.

Basic rings, the UI and event persistence work with no camera, AI model, cloud key or paid service. Nothing unlocks a door.

## Run locally

Python 3.11 or newer is required. Node is only for development/browser tests.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m agent_doorbell
```

Open **http://127.0.0.1:8877**. Without a Pi camera command, the real offline state is shown and rings still work. Data defaults to `~/.local/share/agent-doorbell`; it is never written into the source tree.

On the Pi, allow local-network access with:

```bash
.venv/bin/python -m agent_doorbell --host 0.0.0.0 --port 8877
```

This prototype trusts users on your private LAN. It has no account system, and should not be exposed publicly. Browser cross-origin writes are rejected. HTTP camera/event responses are not cached. Face embeddings and enrollment photos stay local. Optional Bot text sharing requires secure pairing; optional Grok camera sharing additionally requires owner configuration, explicit image consent and verified cloud decoding. One image may leave the Pi per ring, and account Bots share cloud files. Local deletion cannot erase cloud chat, tool records or backups.

## Deploy to the Pi with one local SSH login

From a committed checkout on the Mac:

```bash
bash scripts/deploy-pi.sh OWNER@PI_HOST
```

If using a new IP whose key is already trusted under an existing hostname, the optional second argument uses that exact saved host-key alias:

```bash
bash scripts/deploy-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS
```

Enter the SSH password only in your Terminal. The script transfers **only committed source** through one SSH session, creates `~/agent-doorbell/.venv`, installs the app and optional CPU detector, and starts `agent-doorbell.service` as a user service on port **8877**. It then runs a local ring/camera smoke check. Installer capture remains local; the printed output contains status/metadata only. Optional Grok image sharing requires separate configured consent.

For an already managed installation, update source without downloading models or triggering a hardware test:

```bash
bash scripts/update-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS FULL_COMMIT_SHA
```

Replace `FULL_COMMIT_SHA` with the tested release's complete 40-character commit. Omitting it uses the checkout's committed HEAD. This updater requires the existing environment/dependencies, transfers only that committed archive, installs the package with `--no-deps`, restarts only the Doorbell user service and verifies its version/status. It preserves service configuration, pairing, enrollment, private data and model files. Its verification calls only local status APIs; it does not invoke a camera, enrollment or Bot test. SSH authentication stays in your Terminal and saved host-key checking remains enabled.

An existing unmarked `~/agent-doorbell` directory is never overwritten. Later deploys update only a directory previously marked by this script. No persistent SSH keys, firewall changes, sudo installation or login-policy changes are made. A user service starts with that user’s systemd session; unattended startup before login is **not promised** and the script does not enable lingering.

```bash
systemctl --user status agent-doorbell
journalctl --user -u agent-doorbell -n 50
systemctl --user stop agent-doorbell
```

Configuration overrides belong in `~/.config/agent-doorbell/environment`, not the repository. Default service values:

| Variable | Default / purpose |
| --- | --- |
| `DOORBELL_DATA_DIR` | `~/.local/share/agent-doorbell` |
| `DOORBELL_CAMERA` | `auto`: `rpicam-still`/`libcamera-still`, or offline |
| `DOORBELL_DETECTOR` | `none` locally; the Pi service requests `hog` |
| `DOORBELL_CAMERA_SHARE_URL` | `http://127.0.0.1:8000`; checked before each Pi capture |
| `DOORBELL_TEXT_MODEL_PATH` | Unset by default. Optional path to Qwen 2.5 1.5B Instruct (local text model, llama.cpp). Use a placeholder such as `/path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf`. An empty value or a missing file skips the text model and uses the existing fallback. |
| `DOORBELL_ENROLLMENT_BRIDGE_POLICY` | Owner-only Pi policy; absent/disabled by default |
| `DOORBELL_BUTTON_BCM` | Required by the button unit. No default pin. |

The explicit `synthetic` camera mode is exclusively for tests. It watermarks every frame and displays a persistent TEST MODE banner. It must never be represented as a real camera or visitor.

To update the app and explicitly install its approximately 43 MB of pinned official local vision weights, pass the third argument:

```bash
bash scripts/deploy-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --models
```

This downloads models into the private data directory, verifies size and SHA-256, preserves their licenses and restarts only the doorbell service. It never downloads weights on server startup. Motion remains paused unless separately enabled.

## Camera sharing

Before opening the camera, the app reads the existing local session’s `/status` at `DOORBELL_CAMERA_SHARE_URL`. It yields during an active or reserved session, including `PLACE_PALM`, capture and analysis. A stalled or unrecognized status fails closed. If that API is not running, the camera can be attempted normally.

Each capture opens the camera briefly and releases it. Busy errors create honest capture states; the app never kills another camera process, reboots the Pi, or stops the other local session. This is **best-effort coordination**, not an atomic cross-application camera broker. A simultaneous request from that session can still race a capture. If reliable concurrent ownership is required, a shared camera broker or a second camera needs an explicit design decision. Until then, leave motion monitoring off when that session is in active use.

No camera captures happen on server startup. They occur after a snapshot/preview request, a ring, or enabling motion monitoring. The deployment smoke test creates one clearly labelled test-ring event.

## Optional physical button and person detector

```bash
.venv/bin/python -m pip install '.[person]'
DOORBELL_DETECTOR=hog .venv/bin/python -m agent_doorbell
```

HOG runs on the CPU at a reduced image size. Its presence does not establish useful accuracy or speed at a particular door; real-scene evaluation remains necessary. If unavailable, the core keeps working.

For a normally-open switch wired between an explicitly chosen unused BCM GPIO and GND:

```bash
.venv/bin/python -m pip install '.[gpio]'
DOORBELL_BUTTON_BCM=UNUSED_BCM_PIN .venv/bin/python scripts/gpio_button.py
```

`--pin` still overrides that variable for a foreground check. No pin is chosen automatically. The adapter refuses GPIO 8, 10, 11, 17, 24 and 25 because they are reserved by the existing screen adapter wiring. Verify that any other selected pin is actually free before wiring. No physical button is assumed to be installed.

To keep the button running as a user service, set `DOORBELL_BUTTON_BCM` in `~/.config/agent-doorbell/environment` and install [deploy/agent-doorbell-button.service](deploy/agent-doorbell-button.service). The unit does not contain a pin number.

## Personal-agent flow

Use **Guest approval → Connect your bot**. Keys belong in the Pi's existing private environment file; the browser only reports whether a key is present. No keys, routines, sessions or device grants are created automatically.

For Grok, follow the [complete pairing steps](docs/BOT_SETUP.md#grok-bot-xai--cursor-step-by-step-pairing): create a webhook routine in the desired Bot, enter its key locally on the Pi, save the routine URL, verify a synthetic acknowledgment in the owning chat, then explicitly enable text sharing.

Grok accepts a JSON POST at the user's exact routine URL with its existing Bearer key. HTTP 200 is recorded as **accepted**, not completed. The owning Bot chat is the destination; there is no invented completion callback or image attachment API. If a connection drops after sending, the outcome is **uncertain** and is not automatically repeated. Documented text-only rejections can be retried with bounded backoff. Image-bearing jobs get one POST attempt and scrub bytes from terminal queue records.

Hermes capabilities and the exact existing session are checked during the explicitly requested test. Runs omit conversation history and previous response IDs so Hermes loads its existing transcript. The adapter uses an idempotency key for each event stage. Tool approval permits only one exact request (`once` or `deny`); it never grants permanent approval. Provider output is inert text, and cannot automatically speak or operate a device.

Only endpoint/session/destination metadata is saved in SQLite. Credentials remain in process environment and are never returned to the browser or written into delivery payloads. Changing setup or credentials invalidates verification and cancels queued work aimed at the old setup. Endpoint validation rejects embedded credentials, query secrets, redirects, unapproved private/metadata addresses and DNS rebinding. A local Hermes host requires a manually configured exact-host allowlist.

The local `/api/agent/context` and `/api/events` APIs expose history and current enriched events on the trusted LAN. They are not publicly exposed or automatically granted as external bot tools. `POST /api/agent/proposals` still places a proposed reply in a human approval inbox; speech stays on the approving browser.

Optional Grok scene descriptions use the reviewed image route; production receipt and interpretation must be verified. Occupation inference, automatic door-speaker delivery and public remote access are not implemented. No real personal bot is connected until its approved setup and destination are verified end to end.

## Test and inspect

```bash
bash scripts/test.sh
```

That is the local form of the CI backend and browser jobs: pytest, then the Playwright suite. It also runs `scripts/leak-check.py`. It does not run the disposable-runner SSH job, and it does not need the Pi or a secret. `make test` calls the same script.

The browser suite starts an explicitly synthetic server on port 8878 with a temporary database. It checks SSE ring delivery, snapshots, persistence, profile correction, settings, reply approval state and mobile layout. It does not access hardware or cloud services.

For a real Pi API smoke check, with explicit local camera capture:

```bash
.venv/bin/python scripts/hardware_smoke.py --capture
```

Screenshots, browser traces, test databases, captures, credentials, models and local hardware reports must never be committed. Before pushing, inspect `git status` and the staged file list.
