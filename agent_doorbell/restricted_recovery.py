"""Plan-only reconcile for one interrupted restricted-proposer transaction.

This module does not SSH, create a credential, delete a socket, or change sshd.
An active or changed socket and a reused UID or GID are never deletion candidates.
"""

CREDENTIAL_STATES = {"absent", "partial", "created"}
BOOLEAN_FIELDS = (
    "account_present", "uid_reused", "gid_reused", "socket_present", "socket_active",
    "socket_inode_matches_record", "socket_connection_refused", "socket_owner_matches_record",
    "owner_ssh_policy_unchanged", "receipts_preserved", "backups_preserved",
)
SECRET_MARKERS = ("BEGIN ", "ssh-ed25519", "PRIVATE KEY", "gho_", "sk-", "AKIA")


def _refused(reasons):
    return {"mode": "plan_only", "apply": False, "live_changes": False,
            "generate_replacement_credential": False, "delete_socket": False,
            "delete_uid": False, "delete_gid": False, "weaken_ssh": False,
            "strict_host_key_checking": "yes", "decision": "refused", "reasons": reasons,
            "owner_may_review_socket_removal": False}


def _contains_secret(value):
    if isinstance(value, str):
        return any(marker in value for marker in SECRET_MARKERS)
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


def reconcile(observation):
    """Return a refusal or a no-apply plan. The returned object never carries secret text."""
    if not isinstance(observation, dict) or _contains_secret(observation):
        return _refused(["observation_rejected"])
    expected = {"credential_state", "strict_host_key_checking", *BOOLEAN_FIELDS}
    if set(observation) != expected:
        return _refused(["observation_fields_rejected"])
    if observation.get("strict_host_key_checking") != "yes":
        return _refused(["strict_host_key_checking_required"])
    if observation.get("credential_state") not in CREDENTIAL_STATES:
        return _refused(["credential_state_rejected"])
    if any(type(observation[field]) is not bool for field in BOOLEAN_FIELDS):
        return _refused(["observation_fields_rejected"])
    if not observation["owner_ssh_policy_unchanged"]:
        return _refused(["owner_ssh_policy_must_stay_unchanged"])
    if not observation["receipts_preserved"] or not observation["backups_preserved"]:
        return _refused(["private_receipts_and_backups_must_be_preserved"])
    if observation["uid_reused"] or observation["gid_reused"]:
        return _refused(["reused_uid_or_gid_must_not_be_deleted"])
    socket_blocked = (observation["socket_present"] and (
        observation["socket_active"] or not observation["socket_inode_matches_record"]
        or not observation["socket_owner_matches_record"] or not observation["socket_connection_refused"]))
    review_socket = observation["socket_present"] and not socket_blocked
    reasons = []
    if socket_blocked:
        reasons.append("active_or_changed_socket_requires_owner_review")
    if observation["credential_state"] in {"partial", "created"}:
        reasons.append("reuse_existing_credential")
        decision = "owner_review" if socket_blocked else "reuse_existing_credential"
    elif socket_blocked:
        decision = "owner_review"
    else:
        reasons.append("credential_not_started")
        decision = "not_started"
    if not observation["account_present"] and observation["credential_state"] == "partial":
        reasons.append("account_absent_after_partial_cleanup")
    return {"mode": "plan_only", "apply": False, "live_changes": False,
            "generate_replacement_credential": False, "delete_socket": False,
            "delete_uid": False, "delete_gid": False, "weaken_ssh": False,
            "strict_host_key_checking": "yes", "decision": decision, "reasons": reasons,
            "owner_may_review_socket_removal": review_socket,
            "credential_state": observation["credential_state"]}
