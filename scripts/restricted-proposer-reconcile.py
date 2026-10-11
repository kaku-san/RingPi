"""Print a no-apply recovery plan. This command never contacts a host or creates a credential."""
import argparse
import json
from pathlib import Path
import sys

from agent_doorbell.restricted_recovery import reconcile

if any(arg == "--apply" or arg.startswith("--apply=") for arg in sys.argv[1:]):
    raise SystemExit("This planner has no apply mode")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--observation", required=True, help="Owner-local JSON observation. Never commit it.")
args = parser.parse_args()
observation = json.loads(Path(args.observation).read_text())
print(json.dumps(reconcile(observation), indent=2))
