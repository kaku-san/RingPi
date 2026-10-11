"""Linux Unix-socket ingress authenticated by the caller's kernel UID.

The only operation stages an ImportInput. It never signs or decides proposals.
"""
import asyncio
import errno
import json
import os
from pathlib import Path
import socket
import stat
import struct

from .access_simulation import AccessError
from .enrollment_bridge import MAX_BODY
from .proposal_gate import MAGIC, MAX_REPLY


def peer_uid(connection):
    if not hasattr(socket, 'SO_PEERCRED'): raise OSError('Linux peer credentials required')
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('iII'))
    return struct.unpack('iII', raw)[1]


class RestrictedProposalServer:
    def __init__(self, enrollment, policy, peer=peer_uid):
        self.enrollment, self.policy, self.peer = enrollment, policy, peer
        self.path = Path(policy['socket']) if policy else None
        self.server, self.identity, self.issue = None, None, None
        self.connections = set()
        self.active = 0

    async def start(self):
        if not self.policy: return
        try:
            if self.peer is peer_uid and not hasattr(socket, 'SO_PEERCRED'):
                raise ValueError('Linux peer credentials required')
            parent = self.path.parent.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                    or parent.st_gid != self.policy['gid'] or parent.st_mode & 0o7777 != 0o2710):
                raise ValueError('privately provisioned socket directory required')
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_gid != self.policy['gid']:
                    raise ValueError('unsafe existing socket')
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    try: probe.connect(str(self.path))
                    except OSError as error:
                        if error.errno != errno.ECONNREFUSED: raise
                    else: raise ValueError('socket already active')
                self.path.unlink()
            self.server = await asyncio.start_unix_server(self.handle, path=str(self.path), start_serving=False)
            info = self.path.lstat(); self.identity = (info.st_dev, info.st_ino)
            if info.st_uid != os.getuid() or info.st_gid != self.policy['gid']:
                raise ValueError('socket identity mismatch')
            os.chmod(self.path, 0o660)
            await self.server.start_serving()
        except Exception:
            self.issue = 'Restricted proposal listener unavailable; owner provision review required'
            await self.close()

    async def close(self):
        if self.server:
            self.server.close(); await self.server.wait_closed(); self.server = None
        tasks = [task for task in self.connections if task is not asyncio.current_task()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self.identity:
            try:
                info = self.path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity:
                    self.path.unlink()
            except FileNotFoundError: pass
            self.identity = None

    async def reply(self, writer, result):
        raw = json.dumps(result, separators=(',', ':')).encode()
        if len(raw) > MAX_REPLY: raw = b'{"detail":"proposal_receipt_too_large"}'
        writer.write(struct.pack('!I', len(raw)) + raw)
        await asyncio.wait_for(writer.drain(), 3)

    async def read_bundle(self, reader):
        if await reader.readexactly(len(MAGIC)) != MAGIC: raise AccessError('proposal_protocol_required', 403)
        size = struct.unpack('!I', await reader.readexactly(4))[0]
        if not 0 < size <= MAX_BODY: raise AccessError('enrollment_bundle_too_large', 413)
        return await reader.readexactly(size)

    async def handle(self, reader, writer):
        task = asyncio.current_task(); self.connections.add(task)
        reserved = False
        try:
            if self.peer(writer.get_extra_info('socket')) != self.policy['uid']:
                raise AccessError('restricted_proposer_identity_required', 403)
            if self.active >= 2: raise AccessError('proposal_ingress_busy', 429)
            self.active += 1; reserved = True
            raw = await asyncio.wait_for(self.read_bundle(reader), 15)
            # No owner key, request routing, model inference or decision callback.
            item = await asyncio.to_thread(self.enrollment.ingest, raw, False)
            await self.reply(writer, item)
        except AccessError as error:
            await self.reply(writer, {'detail': error.code, 'real_actuation': False})
        except (asyncio.IncompleteReadError, TimeoutError):
            await self.reply(writer, {'detail': 'proposal_request_incomplete', 'real_actuation': False})
        except asyncio.CancelledError: raise
        except Exception:
            try: await self.reply(writer, {'detail': 'proposal_request_failed', 'real_actuation': False})
            except Exception: pass
        finally:
            if reserved: self.active -= 1
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass
            self.connections.discard(task)
