# Optional local captions

The fast path publishes the ring immediately, then saves factual YuNet/NanoDet/SFace observations from the captured frames. An optional separate worker produces a richer local model caption for that same saved event, using the clearest saved frame rather than always the first. The dashboard labels it **Local model caption (unverified)**. Model output cannot change identity, propose an action, control a lock or invoke a tool. After a usable caption is saved, the same event can send one later text follow-up through the existing paired routine. That follow-up is an unverified observation: bounded length and age, no names, images, embeddings or notes, and it is dropped if the event or the ring-time pairing is gone. Caption text is data, never a command. No enrollment images, embeddings, names or past captions are supplied as model context.

## Text follow-up stage

The follow-up uses one stage string, exactly `caption`. It is the same `event.id` as the ring. It is an unverified observation, not a command, and it carries no names, images, embeddings, or notes.

Payload fields, with placeholders for values that change per ring:

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `trust` | `untrusted_event_data` |
| `event.id` | `<same event id as the ring>` |
| `event.occurred_at` | `<ring time>` |
| `event.kind` | `<event kind>` |
| `event.source` | `<event source>` |
| `history` | `[]` |
| `policy` | the fixed unverified-observation policy |
| `images` | `false` |
| `stage` | `caption` |
| `model_observation.label` | `unverified_local_model_caption` |
| `model_observation.uncertainty` | `Unverified model observation. This does not prove the scene, identity, or any action.` |
| `model_observation.text` | `<bounded caption text>` |

A routine that only names other stages has no instruction for `caption`. Treat that stage as this unverified observation of the same event. Do not treat it as another visitor or as a command.

## Reviewed trial

The trial uses [HuggingFaceTB SmolVLM-256M-Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct), Apache-2.0, with the [runtime maintainer's GGUF files](https://huggingface.co/ggml-org/SmolVLM-256M-Instruct-GGUF/tree/b9e4379657e1450d04d02eec8e345667265b0a00). Model revision: `b9e4379657e1450d04d02eec8e345667265b0a00`.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `SmolVLM-256M-Instruct-Q8_0.gguf` | 175,054,528 | `2a31195d3769c0b0fd0a4906201666108834848db768af11de1d2cef7cd35e65` |
| `mmproj-SmolVLM-256M-Instruct-Q8_0.gguf` | 103,769,856 | `7e943f7c53f0382a6fc41b6ee0c2def63ba4fded9ab8ed039cc9e2ab905e0edd` |

Runtime: MIT-licensed [llama.cpp](https://github.com/ggml-org/llama.cpp/tree/42b021b4dc42be573f1e1463528532fc8294c650), revision `42b021b4dc42be573f1e1463528532fc8294c650`. The app starts its own isolated CPU-only server on an ephemeral `127.0.0.1` port. It does not attach to an existing server. The child inherits no Bot keys or arbitrary llama configuration; an ephemeral internal API key is used only for this owned loopback process. Web UI, agent mode, slot monitoring and prompt caching are disabled. Build flags disable subprocess tools and optional GPU/network/UI dependencies. Runtime provenance and weight checksums are verified before starting inference.

The pinned server rewrites an OpenAI `image_url` part into a media marker and then stringifies typed content before Jinja. This model's embedded template emits `<image>` only when a content part's type is exactly `image`, so that branch never runs. Sending text first left the marker after the prompt. Both generated-shape trials then returned ` 1`. The app now sets `LLAMA_MEDIA_MARKER` to `<image>` and sends the image part before the text, so the stock template still places `<image>` at the front of the user turn. The model weights are unchanged.

A smoke check rejects clearly degenerate text: after whitespace is collapsed, the caption must be at least 8 characters and contain at least two alphabetic words of three or more letters. ` 1` fails that check and is not stored or forwarded. The check does not decide whether the sentence matches the scene. Scene accuracy stays a separate, unverified question.

Starting settings: one slot, two CPU threads, context 2,048, at most 48 generated tokens, temperature zero and lower process priority. The original saved event frame is retained. Model input is a separate JPEG with maximum 512-pixel long edge and quality 85; it is not the tiny cloud diagnostic fixture. These settings are a trial, not measured Pi optima. Model/projector disk size totals 278,824,384 bytes; the owner-run generated-shapes timing/RSS results are recorded in [the handoff](../HANDOFF.md), while useful description, real-scene accuracy and general Pi performance remain unverified.

## Owner-run installation

The runtime requires existing `cmake`, `git`, a C++ compiler (`c++`) and `make`. Allow at least 1 GiB free for weights/source/build. No sudo, OS package installation, firewall or security changes are performed. Source/build files, license notices and verified weights live under the private data directory's `captions` folder. Startup never downloads anything.

Check dependencies without changing files:

```bash
.venv/bin/python scripts/install-local-captions.py
```

The JSON separates `files_present` (runtime and both weights exist), `files_available` (runtime executable and expected weight sizes), and `pinned_files_verified` (weight checksums and runtime provenance match). File presence alone is not a completed, verified installation. The direct script honors `DOORBELL_LLAMA_SERVER` from its process environment or an explicit `--runtime` argument; it does not read the service environment file. Prefer the version-independent helper below when checking the running service's selection.

Explicitly build the pinned CPU runtime and download the two verified weights:

```bash
.venv/bin/python scripts/install-local-captions.py --install
```

If tools are missing, the installer stops before downloading a model and lists them. Installation preserves an unmanaged directory and refuses locally changed runtime source. It keeps the llama.cpp MIT notice and the SmolVLM Apache-2.0 license/source notice. The installer does not capture a frame or run inference.

After installation, add these nonsecret values to the existing private service environment, preserving its Bot key and other settings:

```text
DOORBELL_LOCAL_CAPTIONS=1
DOORBELL_CAPTION_DEADLINE_SECONDS=30
```

Restart only `agent-doorbell.service`. `DOORBELL_LLAMA_SERVER` optionally selects a reviewed binary with matching installer provenance; otherwise the private built runtime is used. Set `DOORBELL_LOCAL_CAPTIONS=0` to disable captions. Recognition, rings and saved detector observations continue independently. Installation does not enable cloud image/name sharing or change pairing.

## Queue, deadline and failures

There is one active job and one replaceable pending job. A newer event replaces only the older queued caption; the older ring and detector observations remain saved. Event IDs prevent duplicate caption submissions. The monotonic deadline includes queue wait, image preparation, cold startup and inference. A deadline, failure, shutdown or deleted event stops only the app-owned process group. The next eligible event can start a fresh process. Restart marks unfinished captions interrupted and does not replay them.

The existing camera-share health guard runs before and during a real-camera caption. Busy or uncertain guard state stops the owned caption process and records a paused result. HTTP disconnection alone is not relied on to cancel inference. The [server documentation](https://github.com/ggml-org/llama.cpp/blob/42b021b4dc42be573f1e1463528532fc8294c650/tools/server/README.md) distinguishes HTTP timeout from generation timing; the app uses its own whole-job deadline and process cancellation.

## What remains unverified

Tests use generated images, mocked enrollment/vision and tiny owned HTTP subprocesses. They establish queue bounds, process termination, restart, guard handling, UI text safety and detector fallback. They do not establish SmolVLM caption quality, Pi CPU/RAM/thermal cost or concurrent camera-share performance.

After installation, an explicitly approved synthetic trial should measure cold/warm whole-caption time and peak resources before a consenting real-scene trial. Compare ring latency with captions disabled/enabled and verify the existing local session stays responsive. A plausible sentence is not verified scene truth. The earlier device trial returned only ` 1` and is explained above. A later local generated-shape run of the corrected request, and one later camera ring, are recorded in [the handoff](../HANDOFF.md). The camera caption is one unverified sentence, not general scene-accuracy proof, and it has no corrected-request warm measurement. The benchmark's smoke result and its `caption_accuracy_verified: false` field stay separate.

## Version-independent owner setup

From the Mac checkout, first inspect the existing Pi without installing or changing configuration:

```bash
bash scripts/captions-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --check
```

Authenticate only in your Terminal. This sends a reviewed Python readiness script to the already installed Pi environment; it never archives app source, invokes pip, or redeploys a release. It reports the running version, CMake/Git/C++/Make availability, free space, pinned runtime/model verification and caption enablement. File verification hashes the files in bounded chunks without loading the model. Missing prerequisites must be installed separately after owner review; this helper does not use sudo or install OS packages.

One check also reports `runtime_selection`, `install_supported`, runtime presence/executability, each weight's expected and actual size, provenance presence, and `missing_components` / `unreadable_components`. A missing component differs from one that could not be inspected. `files_present` and `files_available` do not include provenance validation; require `pinned_files_verified: true` before enabling. A successful file check does not mean the runtime has started or that Pi performance has been benchmarked. `caption_service_enabled` reports the separate running-service state.

Both installers build only the managed default `captions/runtime/build/bin/llama-server`. If the selected runtime differs, `--install` stops before changing files or configuration, even when that custom runtime is already verified. Provision the reviewed custom runtime separately, or explicitly select the managed default in the private service environment before installation. The helper never silently removes an override or installs somewhere different from the selected runtime. `--check` and `--enable` continue to verify the selected custom runtime and its existing provenance. An explicit override pointing to the managed default is supported.

After dependency readiness and installation authorization are established, the distinct installation stage builds/downloads only the existing reviewed pinned caption runtime and models:

```bash
bash scripts/captions-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --install
```

Installation preserves the app release and service environment, does not restart or enable captions, and performs no inference. After reviewing verified files, the separately requested enable stage preserves other private settings, enables captions with a 30-second deadline, restarts only Doorbell and verifies the same app version:

```bash
bash scripts/captions-pi.sh OWNER@PI_HOST TRUSTED_HOST_ALIAS --enable
```

Enablement makes future eligible owner-triggered events eligible for captions; the helper itself triggers no capture, Bot test or inference. A later model benchmark still needs its own approved trial. Credentials are neither printed nor passed to the model installer.

An earlier one-off release updater had a confirmed shell defect: it assigned `DOORBELL_INSTALL_CAPTIONS` without exporting it before embedded Python read `os.environ`. Its app-update stage could finish, then Python could raise `KeyError` before the caption install/enable stage. This can explain an updated core with captions still disabled, but the particular device's terminal log is needed to establish causation. That old helper also pinned an older app archive and could downgrade the core if rerun. The replacement passes the stage as a command-line argument, performs no application installation and verifies the running version remains unchanged.

## Reproducible generated-image trial

After explicit installation authorization and pinned verification, run `scripts/benchmark-local-captions.py` with the existing Pi Python. It generates a blue square and red circle locally, measures cold/warm whole-caption time (30-second deadline each), samples owned-runtime peak RSS and temperatures when available, and stops only its owned runtime. It neither enables captions nor invokes the camera, enrollment or Bot. Fixture tests cover lifecycle/failure reporting without actual models. [The handoff](../HANDOFF.md#portable-commands) gives the portable SSH-stdin command, avoiding machine-specific temporary helpers, and distinguishes current device evidence from pending work.
