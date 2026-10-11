"""Plan-only recovery. No host, credential, or socket is touched."""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_doorbell import restricted_recovery
from agent_doorbell.restricted_recovery import reconcile


def observation(**values):
    base = {"credential_state": "partial", "account_present": False, "uid_reused": False,
            "gid_reused": False, "socket_present": True, "socket_active": False,
            "socket_inode_matches_record": True, "socket_connection_refused": True,
            "socket_owner_matches_record": True, "owner_ssh_policy_unchanged": True,
            "strict_host_key_checking": "yes", "receipts_preserved": True, "backups_preserved": True}
    base.update(values)
    return base


def test_partial_credential_is_reused_and_nothing_is_applied():
    plan = reconcile(observation())
    assert plan["decision"] == "reuse_existing_credential"
    assert plan["apply"] is False and plan["live_changes"] is False
    assert plan["generate_replacement_credential"] is False
    assert plan["delete_socket"] is False and plan["delete_uid"] is False and plan["delete_gid"] is False
    assert plan["weaken_ssh"] is False and plan["strict_host_key_checking"] == "yes"
    assert plan["owner_may_review_socket_removal"] is True
    assert "account_absent_after_partial_cleanup" in plan["reasons"]


def test_active_changed_or_reused_resources_are_not_deletion_candidates():
    active = reconcile(observation(socket_active=True, credential_state="created", account_present=True))
    assert active["decision"] == "owner_review" and active["delete_socket"] is False
    assert active["owner_may_review_socket_removal"] is False and active["generate_replacement_credential"] is False
    changed = reconcile(observation(socket_inode_matches_record=False))
    assert changed["delete_socket"] is False and changed["owner_may_review_socket_removal"] is False
    reused = reconcile(observation(uid_reused=True, gid_reused=True))
    assert reused["decision"] == "refused" and reused["delete_uid"] is False and reused["delete_gid"] is False


def test_weakened_checks_secrets_and_missing_receipts_are_refused():
    host = reconcile(observation(strict_host_key_checking="accept-new"))
    assert host["decision"] == "refused" and "accept-new" not in json.dumps(host)
    policy = reconcile(observation(owner_ssh_policy_unchanged=False))
    assert policy["decision"] == "refused" and policy["weaken_ssh"] is False
    receipts = reconcile(observation(receipts_preserved=False))
    assert receipts["decision"] == "refused"
    leaked = observation()
    leaked["private_key"] = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret"
    refused = reconcile(leaked)
    assert refused["decision"] == "refused" and "BEGIN" not in json.dumps(refused) and "secret" not in json.dumps(refused)
    assert reconcile(observation(credential_state="absent", account_present=False, socket_present=False))["decision"] == "not_started"


def test_planner_has_no_apply_mode_and_does_not_import_a_client(tmp_path):
    source = inspect.getsource(restricted_recovery)
    assert "subprocess" not in source and "paramiko" not in source and "import socket" not in source
    script = Path("scripts/restricted-proposer-reconcile.py")
    applied = subprocess.run([sys.executable, str(script), "--apply"], capture_output=True, text=True)
    assert applied.returncode != 0 and "no apply mode" in applied.stderr + applied.stdout
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation()))
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    result = subprocess.run([sys.executable, str(script), "--observation", str(path)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["apply"] is False and plan["generate_replacement_credential"] is False
    example = json.loads(Path("examples/restricted-recovery.example.json").read_text())
    assert reconcile(example)["decision"] == "reuse_existing_credential"
    assert "example-host" not in script.read_text() and "192.168." not in script.read_text()
