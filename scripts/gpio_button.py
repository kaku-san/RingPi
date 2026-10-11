"""Optional normally-open button from a chosen BCM GPIO to GND. No pins are assumed."""
import argparse
import json
import os
import signal
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pin", type=int, help="Explicit unused BCM GPIO. Defaults to DOORBELL_BUTTON_BCM.")
parser.add_argument("--url", default="http://127.0.0.1:8877")
args = parser.parse_args()
if args.pin is None:
    raw = os.environ.get("DOORBELL_BUTTON_BCM", "").strip()
    if not raw:
        parser.error("DOORBELL_BUTTON_BCM is required when --pin is omitted. No BCM pin is assumed.")
    try:
        pin = int(raw)
    except ValueError:
        parser.error("DOORBELL_BUTTON_BCM must be an integer BCM number.")
else:
    pin = args.pin
if pin in {8, 10, 11, 17, 24, 25}:
    parser.error("That pin is reserved by the existing screen adapter wiring.")
if not 0 <= pin <= 27:
    parser.error("Choose a valid unused BCM GPIO from 0 to 27.")
try:
    from gpiozero import Button
except ImportError:
    raise SystemExit("Optional dependency missing. Install this app's gpio extra first.")


def ring():
    request = urllib.request.Request(args.url + "/api/ring",
        data=json.dumps({"source": "physical"}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            print("Physical ring accepted", response.status, flush=True)
    except OSError as exc:
        print("Doorbell API unavailable:", type(exc).__name__, flush=True)


with Button(pin, pull_up=True, bounce_time=0.25) as button:
    button.when_pressed = ring
    print(f"Listening on BCM GPIO {pin}. Press Ctrl-C to stop.")
    signal.pause()
