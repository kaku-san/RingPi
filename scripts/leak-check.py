#!/usr/bin/env python3
"""Fail the tracked tree on obvious secrets and personal-data patterns.

Hostnames use a generic mDNS-suffix rule. Do not add a specific machine name here.
Generated launch art is allowlisted. Every other image, database, and weight still fails.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Drawn locally for the launch README. Not photos, and not a general image exception.
ALLOWED_GENERATED_ASSETS = frozenset({
    "assets/flow.png",
    "assets/social-preview.png",
    "assets/hero.gif",
    "assets/hero.mp4",
})
FORBIDDEN_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".sqlite", ".sqlite3", ".db",
    ".onnx", ".gguf", ".pt", ".tflite", ".pem", ".npy", ".npz", ".weights",
}
# Built without a contiguous private-key banner or a live token prefix in this file.
_KEY_BANNER = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----(?:[ \t]*\r?\n)?[A-Za-z0-9+/=]{40,}"
)
_MDNS = re.compile(
    r"(?<![A-Za-z0-9.-])(?!localhost\b)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.local\b"
)
_LAN = re.compile(
    r"(?<![0-9])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?:/\d{1,2})?(?![0-9])"
)
_ASSIGN = re.compile(
    r"""(?i)(?:^|[\s{,])(?:export\s+)?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]\s*['\"]([A-Za-z0-9_\-+/=]{24,})['\"]"""
)
_TOKEN_PREFIXES = ("gh" + "p_", "github_" + "pat_", "xo" + "xb-", "xo" + "xa-", "AK" + "IA", "s" + "k-")
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(item) for item in _TOKEN_PREFIXES) + r")[A-Za-z0-9_\-]{20,}"
)
_PLACEHOLDER = re.compile(
    r"(?i)^(paste|your|example|fake|test|changeme|placeholder|owner|dummy|sample)"
)


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [ROOT / item for item in result.stdout.decode().split("\0") if item]


def assignment_is_token(value):
    if _PLACEHOLDER.match(value):
        return False
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return False
    return any(char.isdigit() for char in value) and any(char.isalpha() for char in value)


def findings_in(path, text):
    found = []
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES or path.name in {"id_rsa", "id_ed25519"}:
        found.append((1, "tracked photo, database, weight, or key file"))
    for number, line in enumerate(text.splitlines(), 1):
        if _MDNS.search(line):
            found.append((number, "mDNS hostname"))
        if _LAN.search(line):
            found.append((number, "private LAN address"))
        for match in _ASSIGN.finditer(line):
            if assignment_is_token(match.group(1)):
                found.append((number, "token-shaped assignment"))
                break
        if _TOKEN.search(line):
            found.append((number, "token-shaped value"))
    match = _KEY.search(text)
    if match:
        found.append((text.count("\n", 0, match.start()) + 1, "private key"))
    return found


def scan_tree():
    problems = []
    for path in tracked_files():
        if not path.is_file():
            continue
        if path.relative_to(ROOT).as_posix() in ALLOWED_GENERATED_ASSETS:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            problems.append(f"{path.relative_to(ROOT)}:1: binary tracked file")
            continue
        text = data.decode()
        for number, reason in findings_in(path, text):
            problems.append(f"{path.relative_to(ROOT)}:{number}: {reason}")
    return problems


def self_test():
    host = "door." + "local"
    key = _KEY_BANNER + "\n" + ("A" * 64)
    token_line = "api_key=" + "'Ab1" + ("c" * 24) + "'"
    clean = "http://127.0.0.1:8877\n192.0.2.0/24\nlocalhost\nDOORBELL_BUTTON_BCM=\n"
    assert findings_in(Path("sample.txt"), host + "\n")
    assert findings_in(Path("sample.txt"), key)
    assert findings_in(Path("sample.txt"), token_line)
    assert ALLOWED_GENERATED_ASSETS == frozenset({
        "assets/flow.png",
        "assets/social-preview.png",
        "assets/hero.gif",
        "assets/hero.mp4",
    })
    assert findings_in(Path("photo.jpg"), "x")
    assert findings_in(Path("photo.png"), "x")
    assert findings_in(Path("assets/flow.png"), "x")
    assert findings_in(Path("clip.gif"), "x")
    assert not findings_in(Path("readme.md"), clean)
    assert not findings_in(Path("note.md"), "DOORBELL_GROK_ROUTINE_KEY=\"PASTE_ONLY_THE_KEY_HERE\"\n")
    assert not findings_in(Path("note.md"), "https://example.com/work#ask-for-reviewable-results\n")
    assert findings_in(Path("note.md"), "s" + "k-" + ("a1" * 12) + "\n")
    leaked = "-----BEGIN OPENSSH PRIVATE KEY-----\\nsecret"
    assert not any(reason == "private key" for _, reason in findings_in(Path("t.py"), leaked))


def main():
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    problems = scan_tree()
    if problems:
        sys.stderr.write("\n".join(problems) + "\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
