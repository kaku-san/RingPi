# Outbound commands

The Pi polls a command queue with its queue bearer, runs one command locally, and posts one short reply on the existing Grok webhook. The queue URL is separate from the routine webhook that starts a chat run. Nothing on the Pi listens for these commands. There is no port forward and no second assistant.

Leave `DOORBELL_GROK_COMMAND_QUEUE_URL` unset until that queue exists. Unset is the default, and an unset poller makes no outbound call. The poller also waits until the existing Grok webhook endpoint is saved, because the reply uses that POST. The queue bearer is `DOORBELL_GROK_COMMAND_QUEUE_KEY` when that variable is set. An unset queue key uses `DOORBELL_GROK_ROUTINE_KEY`, the same key already used for the webhook reply. Store either value only in the private service environment.

Put the queue URL in that same private environment when the queue is ready:

```text
DOORBELL_GROK_COMMAND_QUEUE_URL="https://QUEUE_HOST/commands"
DOORBELL_GROK_COMMAND_QUEUE_KEY="PASTE_QUEUE_BEARER_OR_LEAVE_UNSET"
```

`QUEUE_HOST` is a placeholder. Use the owner's own HTTPS queue. The poller skips a URL that is the saved routine webhook, so that webhook remains POST-only.

The poll waits about 25 seconds between attempts, handles one command at a time, and uses an 8 second timeout. Expired and already-seen command ids are refused. A `photo_url` is a plain https URL, fetched outbound. A redirect to a private or loopback address is refused. `photo_sha256` is optional. When it is present it must match the received photo bytes, and a mismatch replies `Refused: photo does not match photo_sha256.` An inline photo may be up to 5 MB. A photo larger than the existing enrollment size is resized on the Pi (longest side down, JPEG) and then enrolled on the existing path. A photo with no face replies `Refused: no face found in photo.` Every refusal puts the reason in `reply_text`.

## Queue contract

```http
GET {QUEUE_URL}
Authorization: Bearer {DOORBELL_GROK_COMMAND_QUEUE_KEY, or DOORBELL_GROK_ROUTINE_KEY when the queue key is unset}
```

Empty:

```json
{"commands": []}
```

One waiting command:

```json
{"commands": [{
  "id": "<unique id>",
  "expires_at": "<ISO-8601>",
  "command": "add-guest",
  "name": "<guest name, add-guest or revoke guest>",
  "label": "<pin label, issue-pin or revoke pin>",
  "minutes": 60,
  "window": {"from": "HH:MM", "until": "HH:MM"},
  "photo_sha256": "<hex, optional>",
  "photo_base64": "<one jpeg, omit if photos or photo_url is set>",
  "photos": ["<jpeg base64>", "<1 to 5 jpegs, omit if photo_base64 or photo_url is set>"],
  "photo_url": "<https URL, optional alternative to inline photos>"
}]}
```

`window.date` is optional. Omit it and the hours apply every day in `DOORBELL_GUEST_TIMEZONE` (default `Asia/Kolkata`). Include `"date": "YYYY-MM-DD"` and the window applies to that date only.

Send either `photo_base64` or `photos`, not both. `photos` holds 1 to 5 JPEG base64 strings. More than 5 is refused with a reason. When more than one face is usable, each is stored with the existing face-sample path (five samples per person). If some photos had no face, the reply says so. If none did, the reply is `Refused: no face found in photo.`

`command` is `add-guest`, `issue-pin`, or `revoke`. `add-guest` reuses named enrollment and an optional visit window. A later match stays a probable match, and the pass is simulated. `issue-pin` reuses the salted one-time PIN. The plaintext PIN is only in the one reply. `revoke` takes a PIN `label` or a guest `name` and removes that PIN or that guest grant.

## Reply

The Pi POSTs the existing webhook JSON and adds one field, `reply_text`. The bearer is unchanged. The body has no photo and no embedding.

```json
{"reply_text": "Guest added."}
```

Other replies look like `PIN 123456, good for 60 minutes.` and `Revoked.`

When the saved routine sees `reply_text`, show that text in the owning Bot chat and stop. A command reply is not a ring, a visitor, or a pairing test.

The Grok side may not have this queue yet. The Pi client is ready for the contract above, and it stays idle until the URL is set.
