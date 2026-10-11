# Doorbell Grok Bot: one photo and a name

This is the command the doorbell Grok Bot runs on the owner's computer after the owner supplies one consenting photo and a visitor name. It reuses the existing SSH session to the Pi. It does not create a credential, routine, account, or key, and it does not open a public webhook.

Replace every placeholder with the owner's own values. Do not commit those values.

## Command

```bash
.venv/bin/python -m agent_doorbell.bridge_client enroll-named \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --name 'Visitor name' \
  --photo /PATH/TO/PERMITTED.jpg
```

`--identity-file` is optional when the existing default SSH key already authenticates. When it is set, it must be that existing private key. The client sets `BatchMode=yes` and `StrictHostKeyChecking=yes`. A password prompt is refused. Host-key checking stays strict.

The remote command is fixed:

```text
exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _enroll-named
```

## Payload

The photo travels on SSH stdin, never in the command arguments. The JSON object has exactly these fields:

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `name` | The visitor name, 1–60 characters |
| `sha256` | Lowercase SHA-256 of the normalized JPEG bytes |
| `photo_base64` | Those JPEG bytes, standard Base64 |

The Pi enrolls the photo under that name in local face recognition and records one simulated visitor pass. The receipt contains the profile id, the name, `face_samples: 1`, `identity_proven: false`, and `real_actuation: false`. It contains no image and no embedding.

## What a later ring does

A completed probable match against that enrolled person can consume the simulated pass once, and only inside an optional visit window. Outside that window the result text is `recognized guest, outside allowed window (1:00-4:00 PM)` for a 13:00–16:00 window, and the pass is not used. The dashboard says the simulated open happened. The lock code still refuses a real driver. Any other enrolled person has no simulated pass from this command.

If the photo is not already a permitted local file, stop. Do not work around a blocked transfer.

## Visit window

`enroll-named` accepts an optional window. A later `set-guest-window` command can set one on an enrolled name. Times are `HH:MM` in 24-hour form. Omit the date for the same hours every day in the Pi timezone. `--date YYYY-MM-DD` applies to that date only. The timezone is `DOORBELL_GUEST_TIMEZONE`, default `Asia/Kolkata`. The dashboard guest list shows the window label and expiry is not a PIN.

```bash
.venv/bin/python -m agent_doorbell.bridge_client enroll-named \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --name 'Visitor name' \
  --photo /PATH/TO/PERMITTED.jpg \
  --from 13:00 --until 16:00

.venv/bin/python -m agent_doorbell.bridge_client set-guest-window \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --name 'Visitor name' \
  --from 13:00 --until 16:00
```

The window travels in the SSH stdin JSON. The remote commands stay fixed: `_enroll-named` and `_set-guest-window`.

## One-time guest PIN

When face recognition is not used, issue one simulated PIN over the same SSH bridge. The client sets `BatchMode=yes`, `StrictHostKeyChecking=yes`, and `IdentitiesOnly=yes`. A password prompt is refused. No new service is started.

```bash
.venv/bin/python -m agent_doorbell.bridge_client issue-pin \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --minutes 60 \
  --label 'Parcel'

.venv/bin/python -m agent_doorbell.bridge_client revoke-pin \
  --ssh-target OWNER@PI_HOST \
  --host-key-alias TRUSTED_HOST_ALIAS \
  --identity-file OWNER_SSH_KEY \
  --id PIN_ID
```

`--minutes` defaults to 60 and cannot exceed 24 hours. The Pi generates a random 6-digit PIN, stores only a salted hash plus expiry and a single-use flag, and prints the PIN once on stdout. The dashboard lists active PINs by label and expiry only. It never shows the PIN again. `PIN_ID` is the id from that one-time receipt, not the PIN.

The visitor enters the PIN on the dashboard keypad at `/pin`. Five wrong entries are refused. The next entry locks the keypad for 15 minutes. A correct, in-time, unused PIN consumes the existing simulated pass. The keypad says the simulated open happened. This does not actuate a lock.

## Outbound command queue

The same guest, PIN, and revoke actions can run from a queue the Pi polls. The poll uses the existing pairing bearer and a separate URL. The routine webhook stays the reply POST. See [outbound commands](OUTBOUND_COMMANDS.md).
