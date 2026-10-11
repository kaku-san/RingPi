"""Read-only restricted-proposer provisioning plan and effective-SSH audit.

This module never creates accounts, credentials, files, SSH grants or services.
"""
import ipaddress
from pathlib import Path
import re

PROPOSER = 'doorbell-proposer'
GATE = '/usr/local/libexec/agent-doorbell-propose.py'
GATE_POLICY = '/etc/agent-doorbell/proposal-gate.json'
KEYS = '/etc/ssh/agent-doorbell-proposer/authorized_keys'
SOCKET = '/run/agent-doorbell/proposals.sock'
FORCE = '/usr/bin/python3 -I -S ' + GATE + ' --config ' + GATE_POLICY


def ssh_requirements(force=FORCE, keys=KEYS):
    return {'forcecommand': force, 'authorizedkeysfile': keys, 'authenticationmethods': 'publickey',
            'pubkeyauthentication': 'yes', 'pubkeyacceptedalgorithms': 'ssh-ed25519',
            'passwordauthentication': 'no', 'kbdinteractiveauthentication': 'no',
            'permittty': 'no', 'permituserrc': 'no', 'permituserenvironment': 'no',
            'disableforwarding': 'yes', 'permittunnel': 'no', 'maxsessions': '1',
            'authorizedkeyscommand': 'none', 'usepam': 'yes'}


def audit_effective(output, force=FORCE, keys=KEYS):
    values = {}
    environment = {'acceptenv': [], 'setenv': []}
    expected = ssh_requirements(force, keys)
    for line in output.splitlines():
        if ' ' not in line: continue
        key, value = line.split(' ', 1)
        if key in environment:
            environment[key].extend(value.split())
            continue
        if key in expected and key in values: raise ValueError('Duplicate effective SSH setting')
        values[key] = value.strip()
    failed = sorted(key for key, wanted in expected.items() if values.get(key) != wanted)
    # Accepted loader/shell variables can run code before the isolated Python gate.
    if any(item not in {'LANG', 'LC_*'} for item in environment['acceptenv']): failed.append('acceptenv')
    if any(item != 'none' and item.split('=', 1)[0] != 'LANG'
           and not re.fullmatch(r'LC_[A-Z_]+=.*', item) for item in environment['setenv']): failed.append('setenv')
    if failed: raise ValueError('Effective proposer SSH policy differs: ' + ', '.join(failed))
    return {'restricted_proposer_ssh_verified': True, 'owner_login_settings_changed': False,
            'shell_or_forwarding': False, 'password_login': False}


def render_plan(app_user, app_home, source, uid=900, gid=900):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', app_user) or app_user == PROPOSER:
        raise ValueError('Choose the existing application owner')
    home = Path(app_home)
    if not home.is_absolute() or '..' in home.parts or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(home)):
        raise ValueError('Use the existing owner home path')
    network = ipaddress.ip_network(source, strict=True)
    if network.version != 4 or network.prefixlen != 32 or not network.network_address.is_private:
        raise ValueError('Approve one private Mac IPv4 address, as /32')
    if type(uid) is not int or type(gid) is not int or not 100 <= uid <= 999 or not 100 <= gid <= 999:
        raise ValueError('Choose separately unused system UID/GID values')
    settings = ssh_requirements()
    # UsePAM is an existing global prerequisite, not changed by this Match block.
    names = {'forcecommand':'ForceCommand','authorizedkeysfile':'AuthorizedKeysFile',
             'authenticationmethods':'AuthenticationMethods','pubkeyauthentication':'PubkeyAuthentication',
             'pubkeyacceptedalgorithms':'PubkeyAcceptedAlgorithms','passwordauthentication':'PasswordAuthentication',
             'kbdinteractiveauthentication':'KbdInteractiveAuthentication','permittty':'PermitTTY',
             'permituserrc':'PermitUserRC',
             'disableforwarding':'DisableForwarding','permittunnel':'PermitTunnel',
             'maxsessions':'MaxSessions','authorizedkeyscommand':'AuthorizedKeysCommand'}
    match = 'Match User ' + PROPOSER + '\n' + ''.join('    '+names[key]+' '+value+'\n'
                for key,value in settings.items() if key not in {'usepam', 'permituserenvironment'}) + '    AcceptEnv LANG LC_*\nMatch all\n'
    files = [
      {'path':GATE,'owner':'root:root','mode':'0755','source':'agent_doorbell/proposal_gate.py; exact reviewed SHA256'},
      {'path':GATE_POLICY,'owner':'root:root','mode':'0644','content':{'socket':SOCKET,'proposer_uid':uid}},
      {'path':KEYS,'owner':'root:root','mode':'0644',
       'content':'restrict,from="'+str(network)+'" ssh-ed25519 <NEW_MAC_PUBLIC_KEY_AFTER_APPROVAL> doorbell-proposer-only\n'},
      {'path':'/etc/ssh/sshd_config.d/80-agent-doorbell-proposer.conf','owner':'root:root','mode':'0644','content':match},
      {'path':'/etc/tmpfiles.d/agent-doorbell-proposals.conf','owner':'root:root','mode':'0644',
       'content':'d /run/agent-doorbell 2710 '+app_user+' '+PROPOSER+' -\n'},
      {'path':str(home/'.config/agent-doorbell/enrollment-bridge.json'),'owner':app_user,'mode':'0600',
       'content':{'enabled':False,'owner_key':'<CREATE_ON_PI_ONLY_AFTER_APPROVAL>',
                  'proposer_uid':uid,'proposer_gid':gid,'proposal_socket':SOCKET}}
    ]
    return {'mode':'plan_only','live_changes':False,'credentials_created':False,'photos_transferred':False,
      'account':{'name':PROPOSER,'uid':uid,'gid':gid,'supplementary_groups':[],
                 'home':'/var/lib/agent-doorbell-proposer','home_owner':'root:root','home_mode':'0755',
                 'login_shell':'/bin/sh used only to invoke the fixed ForceCommand','password_locked':True,'sudo':False},
      'source':str(network),'destination':'named Pi existing SSH listener, proposal socket only',
      'operation':'doorbell-propose-v1; one bounded ImportInput; stage pending only',
      'files':files,'socket_mode':'0660, application owner:doorbell-proposer',
      'credentials':{'Mac':'one dedicated Ed25519 SSH private key, mode 0600; never exported',
                     'Pi':'that public key plus one owner-review HMAC key, mode 0600; no proposal signing key'},
      'mac_inbox_bridge':{'inbox':'one owner-selected absolute private folder, mode 0700',
                         'config':'owner-only proposal-inbox.json outside the granted folder, mode 0600',
                         'Grok_grant':'only permitted bounded proposal bundle files to that exact folder',
                         'startup':'owner-started proposal_inbox process; no login/boot configuration',
                         'general_mac_execution_grant':False,'owner_operations':False,
                         'automatic_retry':False,'Mac_must_be_awake_and_online':True},
      'data':{'input':'exactly five consented JPEG/PNG photos, visitor name, bounded visit window',
              'output':'bounded proposal metadata, ID, digest, five hashes; no key, photo or embedding',
              'pending_ttl_seconds':300,'cleanup':'after denial, expiry, use or revocation; household enrollment unchanged'},
      'network':{'new_tcp_listener':False,'public_endpoint':False,'tunnel':False,'forwarding':False,
                 'new_unix_listener':SOCKET,'existing_ssh_only':True},
      'required_preflight':['Refuse existing account/name/UID/GID collisions or owner UID/GID reuse',
                            'Verify locked password, no extra groups or sudo, root-owned home and gate/key/config paths',
                            'Require existing global UsePAM yes and PermitUserEnvironment no; do not change other accounts',
                            'Refuse effective AcceptEnv outside LANG/LC_* or unsafe SetEnv values',
                            'sshd -t, then sshd -T -C user=doorbell-proposer,host=pi,addr='+str(network.network_address),
                            'audit_effective must pass all restrictions before SSH reload or granting the public key',
                            'Retain strict existing Pi host-key trust; no credential copy to the cloud'],
      'after_separate_approval':['Create dedicated Mac credential; keep public-key registration pending',
                                 'Install scoped root-owned Match policy with empty key file, audit effective policy, then reload SSH',
                                 'Only after enforcement: create the separate collision-checked Pi account/group, root-owned home/gate/config and socket directory',
                                 'Repeat account/file/effective-SSH audit before registering exactly the approved Mac public key',
                                 'Create one Pi-owner review key and restricted policy; restart only Doorbell',
                                 'Confirm restricted listener ready without photos, enrollment, Bot send or physical test',
                                 'Enable owner-only Mac inbox configuration and start its fixed proposal-only process after folder/data-forwarding approval'],
      'owner_review':'Separate owner Terminal authentication; only owner review key can decide exact ID/digest',
      'physical_control':False,'caption_install':'Separate pending owner step; not performed by this plan'}
