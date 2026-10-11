"""Owner-started local inbox bridge. Only stages proposals with a restricted key.

No command field, owner operation, general local-execution grant or automatic retry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .bridge_client import private_read, send_restricted
from .enrollment_bridge import ImportInput, MAX_BODY, parse


def private_directory(path):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Owner-only inbox/config/state directory required')


def load_config(path):
    private_directory(path.parent)
    value=json.loads(private_read(path,8192,True))
    if not isinstance(value,dict) or type(value.get('enabled')) is not bool:
        raise ValueError('Invalid private inbox configuration')
    if value['enabled'] is False:return None
    if set(value)!={'enabled','inbox','ssh_target','host_key_alias','identity_file'}:
        raise ValueError('Inbox accepts no command, URL or owner scope')
    if not isinstance(value['ssh_target'],str) or not re.fullmatch(r'doorbell-proposer@[A-Za-z0-9_.:-]+',value['ssh_target']):
        raise ValueError('Dedicated proposer identity required')
    if not isinstance(value['host_key_alias'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+',value['host_key_alias']):
        raise ValueError('Explicit saved Pi host-key alias required')
    for key in ('inbox','identity_file'):
        text=value[key]
        if not isinstance(text,str) or not text.startswith('/') or '..' in Path(text).parts:
            raise ValueError('Use owner-selected absolute paths')
    private_directory(Path(value['inbox']))
    return value


def write_state(path,value,exclusive=False):
    flags=os.O_WRONLY|os.O_NOFOLLOW|(os.O_CREAT|os.O_EXCL if exclusive else os.O_TRUNC)
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,separators=(',',':'));stream.write('\n');stream.flush();os.fsync(stream.fileno())


def read_inbox_bundle(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_mode&0o022 or info.st_nlink!=1):
            raise ValueError('Owned unshared input file required')
        raw=stream.read(MAX_BODY+1)
    if len(raw)>MAX_BODY:raise ValueError('Inbox bundle exceeds bound')
    return raw


class ProposalInbox:
    def __init__(self,config_path,sender=send_restricted):
        self.path,self.sender=Path(config_path),sender
        self.config=load_config(self.path)
        self.state=self.path.parent/'proposal-inbox-state'
        if self.config:
            self.state.mkdir(mode=0o700,exist_ok=True);private_directory(self.state)

    def once(self):
        if not self.config:return {'enabled':False,'transfers':0}
        private_directory(Path(self.config['inbox']));private_directory(self.state)
        completed=[]
        for source in sorted(Path(self.config['inbox']).iterdir())[:20]:
            if not re.fullmatch(r'[0-9a-f]{32}\.json',source.name):continue
            state=self.state/source.name
            if state.exists() or state.is_symlink():continue
            if len(list(self.state.iterdir()))>=100:
                return {'enabled':True,'transfers':len(completed),'issue':'Owner journal review required at capacity'}
            try:
                # Native file saves may use 0644; the enclosing 0700 inbox is
                # owner-only. Refuse shared/writable, hard-linked or symlink inputs.
                raw=read_inbox_bundle(source);envelope=parse(raw,ImportInput)
                if envelope.request_id+'.json'!=source.name:continue
            except Exception:continue  # incomplete/invalid copies never trigger a transfer
            journal={'request_id':envelope.request_id,'bundle_sha256':hashlib.sha256(raw).hexdigest(),
                     'status':'sending','automatic_retry':False}
            try:write_state(state,journal,True)
            except FileExistsError:continue
            try:
                receipt=self.sender(self.config['ssh_target'],self.config['host_key_alias'],
                                    Path(self.config['identity_file']),raw)
                if not isinstance(receipt,dict) or receipt.get('status')!='pending' or receipt.get('id')!=envelope.request_id:
                    raise ValueError('Unexpected receipt requires owner review')
                journal.update(status='pending_owner_review',proposal_id=receipt['id'],payload_digest=receipt['digest'])
                completed.append({'proposal_id':receipt['id'],'payload_digest':receipt['digest']})
            except Exception:
                journal['status']='needs_owner_review'
            write_state(state,journal)
        return {'enabled':True,'transfers':len(completed),'proposals':completed,
                'owner_decisions':False,'shell_commands':False,'physical_control':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('~/.config/agent-doorbell/proposal-inbox.json').expanduser())
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    try:
        inbox=ProposalInbox(args.config)
        while True:
            result=inbox.once();print(json.dumps(result),flush=True)
            if args.once or not inbox.config or result.get('issue'):return
            time.sleep(5)
    except KeyboardInterrupt:return
    except Exception:raise SystemExit('Inbox bridge stopped; owner configuration/state review required. No retry or owner fallback.') from None


if __name__=='__main__':main()
