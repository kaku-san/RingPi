"""Print a reviewable activation plan; no write/apply mode exists."""
import argparse
import json
from agent_doorbell.restricted_setup import render_plan

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--app-user',required=True)
parser.add_argument('--app-home',required=True)
parser.add_argument('--mac-source',required=True)
parser.add_argument('--proposer-uid',type=int,default=900)
parser.add_argument('--proposer-gid',type=int,default=900)
args=parser.parse_args()
print(json.dumps(render_plan(args.app_user,args.app_home,args.mac_source,args.proposer_uid,args.proposer_gid),indent=2))
