# Visitor passes and lock commands: simulation only

Release 0.3.6 provides a local **Guest approval → Try a visitor pass** workflow. It connects only to a pure memory lock fixture. The actual lock state and physical door position remain unknown. The existing Grok integration sends notifications outward; it cannot send commands, tag photos or enroll people on the Pi. No inbound webhook, new credential, device grant, public endpoint, GPIO/relay call or real actuation is configured.

Release 0.3.7 adds a separate [reviewed five-photo bridge](ENROLLMENT_BRIDGE.md), disabled by default, and a generated-image demo. With separately approved setup/transfer/review it can stage and locally enroll temporary references, then create a simulated pass. It introduces no physical control or automatic authority for model output. The notification webhook remains outbound only.

## Try the local workflow

1. For a direct command, open **Simulated lock commands**, choose **Unlock**, **Lock**, or **Open**, then propose it. Review and approve the exact simulated command within five minutes. Every result says simulation; none confirms a physical action. The default simulator supports lock/unlock only. Physical opening is a separate capability, and attempting it fails rather than substituting unlock.
2. For an advance visit, enter a temporary visitor name, a pretend reference count (normally five) and the allowed time window. The window lasts at most 24 hours, starts no more than seven days ahead and ends in the future. A start within the preceding 15 minutes is allowed so filling out the form does not invalidate it. These are metadata placeholders, not uploaded photos or enrolled face samples.
3. Review the name, pretend references and exact window, then **Approve simulated pass** within five minutes. This records the owner decision in advance. The pass permits exactly one simulated unlock within that window; it never grants permanent household access.
4. **Simulate arrival** with a matching visitor face and a pretend verified visitor credential. A valid, previously approved pass does **not** ask for another owner approval at arrival. Missing, unknown, uncertain, poor-quality or multiple-face evidence, a different visitor, or a missing/rejected factor denies the arrival. A denied credential check can be corrected with a new attempt while the pass remains valid.
5. Revoke a pending or approved pass to prevent use. Expired, revoked, used and uncertain passes cannot be reapproved or replayed. After an unconfirmed action attempt, the pass remains uncertain and cannot automatically retry.

Owner confirmation and visitor authentication in this workflow are explicit **mock interfaces**. The dashboard never authenticates a real owner or generates a real visitor PIN/phone credential. Actual face recognition and caption workers have no connection to pass issuance or lock execution. These simulation checks do not make face-only real actuation available.

## Desired Grok workflow and the unconfigured boundaries

The requested future experience is: upload up to five consenting visitor reference photos in Grok, name the person, review an enrollment proposal and issue a one-time time-window pass in advance. Once properly authorized, arrival should not require repeated owner approval. That intent is captured in the pass lifecycle rather than a permanent recognition grant.

The current implementation accepts only bounded local simulation metadata. Photo fields are rejected, and no reference bytes, embeddings or credentials are transferred. Temporary visitor records use separate `access_visitors`/`access_passes` tables; the existing household profiles and enrolled photos are unchanged. A future reference bundle must retain consent, exact owner-reviewed visitor binding, independent expiry/revocation/deletion and separation from permanent enrollment. Grok attachments and model-generated names are untrusted input until that review completes. Cloud photo storage and transfer would require separate explicit permission and a reviewed authenticated transport.

Before any real flow can be designed or enabled, resolve:

- The lock's exact brand/model, existing hub/interface, owner-controlled integration and capabilities. Unlocking and physically opening must remain distinct.
- Reliable lock/door-position feedback and action-bound confirmations, plus documented unknown-state, power-loss and failed-command behavior. An HTTP acceptance alone cannot establish successful actuation.
- A secure, authenticated Grok-to-Pi return transport with a reviewed access scope and separately approved connection. The existing notification webhook is not an inbound command capability.
- Real owner identity/authentication for exact commands, enrollment proposals and pass issuance/revocation. A name, Bot message, local checkbox or payload hash is not real authentication.
- The visitor factor and its verifier. Face plus a one-time PIN/phone credential is the proposed live safeguard; neither the choice nor credential infrastructure is configured. Ordinary camera matching has no verified spoof resistance and never authorizes real entry here.

`HardwareAdapter` defines state/read/action-confirmation operations; `UnconfiguredHardware` always refuses execution and reports unknown. `LockDriver` is the plug-in boundary a later lock can implement. `load_lock_driver` accepts only `simulation`, and `register_lock_driver` refuses every other name. The simulation controller rejects a driver whose `simulated` flag is not true. `UnconfiguredGrokTransport` rejects every incoming envelope. There is no environment option, dashboard switch or automatic upgrade to real operation.

Door recognition can store a guest-approval request when every compared frame agrees on one temporary visitor. That request is evidence. It does not call the lock, and its device label stays pending. Owner approval plus the existing mock visitor factor can then run the already approved simulated pass. DONE is returned only when the server records `simulated_completed`, `simulation_only`, `server_confirmed` true, and both `hardware_confirmed` and `real_actuation` false. A caption, a name, or a probable match cannot select that result.

## Ledger, replay and failure behavior

Every proposal has a request ID, immutable payload digest, bounded expiry and explicit decision. The hash binds review to exact fields; it is a deduplication/review identifier, not an authentication token. Reusing an ID with changed data fails. Direct command approvals are idempotent. Arrival IDs are durably reserved before any simulated action; concurrent calls share that reservation or atomically compete for the approved pass. Only one can consume it. A new arrival ID cannot reuse a used pass.

The local SQLite audit records proposal, approval, expiry, revocation, denial and simulated action outcomes. It contains no photos, embeddings, PINs or raw tool/error text. Model output cannot create proposals, issue passes or invoke adapters. Inputs reject unexpected fields and accept at most 8 KiB of simulation metadata. History is bounded to 200 records per command/pass/arrival ledger and 1,000 audit records; hitting the limit refuses additional work. No unbounded permanent grant is created.

An interrupted in-flight operation becomes uncertain on restart and is never automatically replayed. Missing or inconsistent simulation acknowledgments also yield uncertain state; they cannot be reported as successful physical actions. A denied precondition leaves an approved pass available until expiry/revocation. Once an action attempt is made, lack of confirmation cannot silently restore its use. The memory lock fixture resets on process restart; ledger receipts remain historical simulation outcomes. Run the application with one Uvicorn worker as described in the architecture guide.

## Verification limits

Mock tests cover the complete advance-approval/arrival lifecycle, exact digest binding, expiry/revocation, visitor disagreement, missing factors, unknown state, exceptions and invalid confirmations, duplicate/concurrent requests, restart recovery, isolated temporary records and refusal of real hardware/transport. Browser tests exercise command review, separate open behavior, one advance approval without an arrival prompt, literal hostile names, revocation and a 390×844 layout. No real person, credential, lock, Grok inbound command or photo transfer is involved. Actual iPhone/Safari testing, physical actuation, live authentication and real visitor recognition remain unverified.
