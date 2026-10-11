# Guided setup for your own fork

The repository remains private. This guide is for people with access to their own authorized fork; it does not require the original owner's computer, address, keys, photos or Bot. Run commands from your chosen checkout. Replace each SSH target, trusted alias, visit date and local photo path with your own values.

## 1. Prove the full flow without hardware

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python scripts/bridge-demo.py
```

Expect `hardware_free_flow_passed`. This uses five generated images, a mock owner decision, temporary references, one advance-approved simulated arrival and cleanup. No camera, real face, key, SSH or Bot is involved. For the UI, start `DOORBELL_CAMERA=none .venv/bin/python -m agent_doorbell` and open `http://127.0.0.1:8877/#agent`.

## 2. Configure your own Pi

Use the [normal installation guide](../README.md#deploy-to-the-pi-with-one-local-ssh-login). The initial installer performs its documented local camera/ring check, so use it only when that hardware step is authorized. Later source-only updates use `scripts/update-pi.sh` with a tested full commit. No other project's settings, SSH policy or firewall is changed.

Copy only values you need from [.env.example](../.env.example) into your Pi's private `~/.config/agent-doorbell/environment`. Keep runtime data outside your checkout. Face models and caption models have separate explicit installers; they are never downloaded on startup. [Local vision setup](LOCAL_MODELS.md) is required before real reference enrollment. [Caption setup](LOCAL_CAPTIONS.md#version-independent-owner-setup) is optional and does not redeploy/downgrade the app.

Optional local captions are a separate install. The pinned runtime request puts the image before the text and pins the media marker to `<image>`, because this llama.cpp revision otherwise stringifies the image away from the placeholder the SmolVLM template understands. A smoke check rejects degenerate text such as a lone digit. That check is not a scene-accuracy test. A usable caption can later follow the ring as unverified text on the same event, through the pairing you already saved. It does not create a routine, key, or account, and it does not wait in front of the ring notification.

Noninteractive SSH that fails with `Permission denied (publickey,password)` needs a one-time key install from your own Terminal, where you can enter the account password. This repository does not store that password or a machine address. After the key works, source updates use `scripts/update-pi.sh`. Do not use `deploy-pi.sh` to update an existing install.

## 3. Pair your own Grok Bot for notifications

Follow [Bot setup](BOT_SETUP.md#grok-bot-xai--cursor-step-by-step-pairing) using your own Bot, routine endpoint and securely entered key. Verify its one synthetic receipt and destination before enabling text sharing. This is outbound notification pairing; it is independent of the photo bridge. Names and images default off. Do not reuse another person's routine endpoint, key or receipt.

## 4. Set up the enrollment bridge only after approval

For agent proposals, follow the [restricted proposer guide](RESTRICTED_PROPOSER.md). Its read-only plan specifies a dedicated SSH identity, root-owned forced command, kernel-verified Unix socket and private local inbox. If an earlier attempt stopped halfway, use `scripts/restricted-proposer-reconcile.py` on a private observation file. That command has no apply mode and will not mint a replacement credential. Activation needs separate approval; no installer silently creates users, keys or grants. Restricted mode has one owner-review key on the Pi and no proposal signing key. It opens no new TCP listener or tunnel. Verify the exact release's native `restricted_ssh` CI result before activation.

The current client expects the installed app under `~/agent-doorbell` and the default private policy path; these are user-relative conventions, not an owner-specific path. Use already-authorized SSH authentication. A password-based installation may require Terminal authentication for each command; this guide never creates an SSH key or changes login policy automatically.

Owner review remains a separate authenticated Terminal action. The [older bridge guide](ENROLLMENT_BRIDGE.md) describes an interactive owner-assisted tool with two API signing scopes; its shared owner SSH account is not an agent identity. Do not grant Grok broad execution of that account. The new inbox needs only exact permitted original-file export and folder/forwarding scope, with configuration and dedicated credential outside the folder.

## 5. Use an exact five-photo proposal

Obtain permission for the five selected photos/name and visit window. Verify that Grok attachment export to your local computer is permitted; a blocked copy is not permission to use another route. The supported client accepts only selected local JPEG/PNG paths. Prepare the private bundle, approve its transfer to your named Pi, review the returned proposal ID/digest, then approve temporary reference enrollment and one use. The guide supplies all commands and an adaptable Grok instruction template.

One rejected photo fails the entire batch. Temporary references are kept separate from household profiles and removed after rejection, use, revocation or window expiry. Source copies on your Mac and in Grok require their own deletion. Nothing actuates a lock. Real arrival credential verification and hardware integration remain a separate, explicitly configured project.

## 6. Verify your fork

```bash
bash scripts/test.sh
```

`scripts/test.sh` runs the leak scan, the backend pytest suite, and the browser suite. Those are the same checks CI runs for backend and browser. The restricted-SSH job stays on the disposable Linux runner and is not part of this script.

The backend's official-model smoke test skips unless you explicitly provide pinned test weights through `DOORBELL_TEST_MODELS_DIR`; mock bridge coverage does not require them. Browser tests use a generated-camera localhost server. CI runs both suites plus an actual restricted-SSH test on a disposable Linux runner. The SSH harness refuses to run on a Pi or ordinary workstation. Keep `.doorbell-bridge/`, runtime policy, photos, embeddings, databases and model weights out of Git; the repository ignores those paths. Live biometric accuracy and real device performance must be verified separately on your own consenting test setup.

The same CI workflow also supports an owner-requested manual run: `gh workflow run test.yml --ref main` from your own authorized checkout. Verify the run's full head SHA matches the release you intend to deploy; a green run for an older commit does not validate new source.
