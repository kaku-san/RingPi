"""Root-owned, stdlib-only forced SSH command: one proposal, no signer or shell.

Provision separately after approval; execute with Python -I -S. No app imports.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys

MAGIC = b'DOORBELL_PROPOSAL_V1\n'
COMMAND = 'doorbell-propose-v1'
MAX_BODY = 10_100_000
MAX_REPLY = 65_536


def load_policy(path, trusted_uid=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != trusted_uid or info.st_mode & 0o022:
            raise ValueError('unsafe gate policy')
        raw = stream.read(2049)
    if len(raw) > 2048: raise ValueError('oversized gate policy')
    policy = json.loads(raw)
    if (not isinstance(policy, dict) or set(policy) != {'socket', 'proposer_uid'}
            or type(policy['proposer_uid']) is not int or policy['proposer_uid'] <= 0
            or not isinstance(policy['socket'], str) or not policy['socket'].startswith('/')
            or '..' in Path(policy['socket']).parts or len(policy['socket'].encode()) > 100):
        raise ValueError('invalid gate policy')
    return policy


def receive_exact(connection, size):
    result = bytearray()
    while len(result) < size:
        block = connection.recv(size - len(result))
        if not block: raise ValueError('incomplete bounded receipt')
        result.extend(block)
    return bytes(result)


def forward(policy, body, original_command, tty=False):
    if original_command != COMMAND or tty: raise ValueError('proposal command only')
    if os.geteuid() != policy['proposer_uid']: raise ValueError('wrong proposal identity')
    if not body or len(body) > MAX_BODY: raise ValueError('bounded proposal required')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(policy['socket'])
        connection.sendall(MAGIC + struct.pack('!I', len(body)) + body)
        size = struct.unpack('!I', receive_exact(connection, 4))[0]
        if size > MAX_REPLY: raise ValueError('oversized receipt')
        result = json.loads(receive_exact(connection, size))
    if not isinstance(result, dict): raise ValueError('invalid receipt')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/agent-doorbell/proposal-gate.json'))
    args = parser.parse_args()
    try:
        command, tty = os.environ.get('SSH_ORIGINAL_COMMAND', ''), bool(os.environ.get('SSH_TTY'))
        if command != COMMAND or tty: raise ValueError('proposal command only')
        policy = load_policy(args.config)
        result = forward(policy, sys.stdin.buffer.read(MAX_BODY + 1), command, tty)
        print(json.dumps(result, separators=(',', ':')))
        if 'detail' in result: raise SystemExit(1)
    except Exception:
        raise SystemExit('Proposal gate refused operation; no command, photo or credential was returned.') from None


if __name__ == '__main__': main()
