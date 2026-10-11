"""Native isolation proof on a disposable GitHub-hosted Linux runner only.

Creates ephemeral fixture keys/account and a localhost SSH daemon. Never run on a Pi.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid

from agent_doorbell.access_simulation import AccessSimulation
from agent_doorbell.enrollment_bridge import EnrollmentBridge, fixture_references
from agent_doorbell.proposal_gate import COMMAND, MAGIC
from agent_doorbell.restricted_proposals import RestrictedProposalServer
from agent_doorbell.restricted_setup import audit_effective, ssh_requirements
from agent_doorbell.store import Store


def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


async def prove(root, username, uid, gid):
    directory=root/'socket';directory.mkdir();os.chown(directory,0,gid);directory.chmod(0o2710)
    data=root/'private';data.mkdir();data.chmod(0o700)
    policy=data/'owner-policy.json'
    policy.write_text(json.dumps({'enabled':True,'owner_key':'a'*64,'proposer_uid':uid,'proposer_gid':gid,
                                 'proposal_socket':str(directory/'proposals.sock')}));policy.chmod(0o600)
    store=Store(data/'state');access=AccessSimulation(store)
    class NoEnrollment:
        def enroll(self,raw):raise AssertionError('Proposal cannot invoke enrollment')
    service=EnrollmentBridge(store,access,NoEnrollment(),policy)
    server=RestrictedProposalServer(service,service.auth.restricted)
    await server.start();assert server.server and not server.issue
    gate=root/'gate.py';shutil.copyfile(Path(__file__).parents[1]/'agent_doorbell/proposal_gate.py',gate);gate.chmod(0o755)
    gate_config=root/'gate.json';gate_config.write_text(json.dumps({'socket':str(server.path),'proposer_uid':uid}));gate_config.chmod(0o644)
    host,credential=root/'host',root/'credential'
    for path in (host,credential):run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(path)])
    keys=root/'authorized_keys';keys.write_text('restrict,from="127.0.0.1/32" '+credential.with_suffix('.pub').read_text());keys.chmod(0o644)
    force='/usr/bin/python3 -I -S '+str(gate)+' --config '+str(gate_config)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    settings=ssh_requirements(force,str(keys))
    directives={'forcecommand':'ForceCommand','authorizedkeysfile':'AuthorizedKeysFile',
      'authenticationmethods':'AuthenticationMethods','pubkeyauthentication':'PubkeyAuthentication',
      'pubkeyacceptedalgorithms':'PubkeyAcceptedAlgorithms','passwordauthentication':'PasswordAuthentication',
      'kbdinteractiveauthentication':'KbdInteractiveAuthentication','permittty':'PermitTTY',
      'permituserrc':'PermitUserRC','permituserenvironment':'PermitUserEnvironment',
      'disableforwarding':'DisableForwarding','permittunnel':'PermitTunnel','maxsessions':'MaxSessions',
      'authorizedkeyscommand':'AuthorizedKeysCommand','usepam':'UsePAM'}
    config=root/'sshd.conf';config.write_text(
      f'Port {port}\nListenAddress 127.0.0.1\nHostKey {host}\nPidFile {root}/sshd.pid\n'
      f'AllowUsers {username}\nStrictModes yes\nLogLevel ERROR\nSubsystem sftp internal-sftp\nPermitUserEnvironment no\nUsePAM yes\nMatch User {username}\n'+
      ''.join('    '+directives[key]+' '+value+'\n' for key,value in settings.items() if key not in {'usepam','permituserenvironment'})+'    AcceptEnv LANG LC_*\nMatch all\n')
    run(['/usr/sbin/sshd','-t','-f',str(config)])
    effective=run(['/usr/sbin/sshd','-T','-f',str(config),'-C',f'user={username},host=localhost,addr=127.0.0.1']).stdout.decode()
    audit_effective(effective,force,str(keys))
    owner_effective=run(['/usr/sbin/sshd','-T','-f',str(config),'-C','user=root,host=localhost,addr=127.0.0.1']).stdout.decode()
    assert 'forcecommand none\n' in owner_effective and 'disableforwarding no\n' in owner_effective
    known=root/'known_hosts';public=host.with_suffix('.pub').read_text().split()
    known.write_text(f'[127.0.0.1]:{port} '+public[0]+' '+public[1]+'\n');known.chmod(0o600)
    daemon=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',port)
                writer.close();await writer.wait_closed();break
            except OSError:await asyncio.sleep(.1)
        else:raise AssertionError('Fixture SSH daemon did not start')
        base=['ssh','-F','/dev/null','-i',str(credential),'-p',str(port),'-o','BatchMode=yes',
          '-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),
          '-o','GlobalKnownHostsFile=/dev/null','-o','ControlMaster=no','-o','ControlPath=none',
          '-o','ForwardAgent=no','-o','ConnectTimeout=3']
        target=username+'@127.0.0.1'
        async def ssh(command,body=b'',options=(),env=None):
            return await asyncio.to_thread(subprocess.run,base+list(options)+[target,command],input=body,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,env=env)
        now=datetime.now(timezone.utc)
        raw=json.dumps({'schema_version':1,'request_id':uuid.uuid4().hex,'visitor_name':'Consenting CI visitor',
          'purpose':'temporary_visitor_pass','valid_from':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat(),
          'references':fixture_references()}).encode()
        submitted=await ssh(COMMAND,raw);assert submitted.returncode==0,submitted.stderr.decode()
        item=json.loads(submitted.stdout);assert item['status']=='pending' and not item['fixture']
        for command in ('id','cat '+str(policy),'sftp',
                        'python3 -m agent_doorbell.bridge_client _forward owner POST /decision',
                        COMMAND+'; touch '+str(root/'shell-executed')):
            refused=await ssh(command,raw)
            assert refused.returncode!=0 and 'owner_key' not in refused.stdout.decode()
        assert not (root/'shell-executed').exists()
        # A valid SFTP INIT would elicit VERSION from an available subsystem.
        subsystem=await asyncio.to_thread(subprocess.run,base+['-s',target,'sftp'],input=struct.pack('!IBI',5,1,3),
          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12)
        assert subsystem.returncode!=0 and not subsystem.stdout, {
          'sftp_returncode':subsystem.returncode,'sftp_stdout_bytes':len(subsystem.stdout),
          'sftp_error':subsystem.stderr.decode(errors='replace')}
        decision=json.dumps({'decision':'approve','payload_digest':item['digest']}).encode()
        refused=await ssh(COMMAND,decision)
        assert refused.returncode!=0 and json.loads(refused.stdout)['detail']=='invalid_enrollment_bundle'
        assert service.proposal(item['id'])['status']=='pending' and not access.snapshot()['passes']
        # OS ownership remains a barrier even independently of ForceCommand.
        denied=await asyncio.to_thread(subprocess.run,['runuser','-u',username,'--','cat',str(policy)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert denied.returncode!=0 and b'owner_key' not in denied.stdout
        direct=await asyncio.to_thread(subprocess.run,base+['-W','127.0.0.1:65534',target],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12)
        assert direct.returncode!=0 and b'administratively prohibited' in direct.stderr
        reverse=await asyncio.to_thread(subprocess.run,base+['-o','ExitOnForwardFailure=yes','-N','-R','0:127.0.0.1:65534',target],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12)
        assert reverse.returncode!=0 and b'forwarding failed' in reverse.stderr
        tty=await ssh('id',options=('-tt',));assert tty.returncode!=0 and b'PTY allocation request failed' in tty.stderr
        trap=root/'import-trap';trap.mkdir();os.chown(trap,uid,gid);trap.chmod(0o700)
        marker=trap/'executed';(trap/'json.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
        env=dict(os.environ,PYTHONPATH=str(trap))
        repeated=await ssh(COMMAND,raw,options=('-o','SendEnv=PYTHONPATH'),env=env)
        assert repeated.returncode==0 and not marker.exists()
        isolated=await asyncio.to_thread(subprocess.run,
          ['runuser','-u',username,'--','env','PYTHONPATH='+str(trap),'SSH_ORIGINAL_COMMAND='+COMMAND,
           '/usr/bin/python3','-I','-S',str(gate),'--config',str(gate_config)],input=raw,
          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12)
        assert isolated.returncode==0 and not marker.exists()
        # Kernel credentials reject the app/root identity on the proposal socket.
        reader,writer=await asyncio.open_unix_connection(str(server.path))
        size=struct.unpack('!I',await reader.readexactly(4))[0]
        assert json.loads(await reader.readexactly(size))['detail']=='restricted_proposer_identity_required'
        writer.close();await writer.wait_closed()
        assert not access.snapshot()['passes'] and not store.profiles() and not store.face_samples()
        return {'status':'native_restricted_ssh_verified','dedicated_identity':True,'kernel_peer_uid':True,
          'proposal_staged':True,'owner_decision_or_shell':False,'owner_policy_read':False,
          'tcp_forwarding_or_pty':False,'python_environment_injection':False,
          'photos':'five owned generated CI images','real_enrollment_or_hardware':False,'live_pi_changed':False}
    finally:
        daemon.terminate()
        try:daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:daemon.kill();daemon.wait()
        await server.close()


def main():
    if not (sys.platform=='linux' and os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true'
            and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
            and os.environ.get('DOORBELL_RESTRICTED_SSH_FIXTURE')=='1'):
        raise SystemExit('Refused: disposable GitHub-hosted Linux CI only. Never run on a Pi.')
    username='dbci'+uuid.uuid4().hex[:12]
    # OpenSSH StrictModes checks all key-file ancestors; /run is root-owned
    # and non-writable by the fixture identity, unlike the shared /tmp parent.
    with tempfile.TemporaryDirectory(prefix='dbssh-',dir='/run') as name:
        root=Path(name);root.chmod(0o755);home=root/'home';home.mkdir();home.chmod(0o755)
        created=False
        try:
            run(['useradd','--system','--user-group','--no-create-home','--home-dir',str(home),'--shell','/bin/sh',username]);created=True
            account=pwd.getpwnam(username);assert account.pw_uid!=0
            assert os.getgrouplist(username,account.pw_gid)==[account.pw_gid]
            Path('/run/sshd').mkdir(exist_ok=True,mode=0o755)
            result=asyncio.run(prove(root,username,account.pw_uid,account.pw_gid))
        finally:
            if created:
                # SSH session children can outlive the daemon by a few scheduler
                # ticks. This UID was created by this fixture and is never reused.
                for signal_name in ('TERM','KILL'):
                    subprocess.run(['pkill','-'+signal_name,'-u',str(account.pw_uid)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    for _ in range(50):
                        alive=subprocess.run(['pgrep','-u',str(account.pw_uid)],
                                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                        if alive.returncode==1:break
                        if alive.returncode!=0:raise AssertionError('Fixture process cleanup could not be verified')
                        time.sleep(.1)
                    else:continue
                    break
                else:raise AssertionError('Fixture account still has running processes')
                run(['userdel',username])
                try:grp.getgrnam(username)
                except KeyError:pass
                else:run(['groupdel',username])
    result['fixture_account_and_processes_removed']=True
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
