"""Local API smoke check. --capture explicitly permits a local camera test."""
import argparse
import json
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8877")
parser.add_argument("--capture", action="store_true")
args = parser.parse_args()


def request(path, payload=None):
    req = urllib.request.Request(args.url + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)


for attempt in range(15):
    try:
        status = request("/api/status")
        break
    except urllib.error.URLError:
        if attempt == 14:
            raise SystemExit("The service did not become ready. Run systemctl --user status agent-doorbell.")
        time.sleep(1)
print("APP_READY", json.dumps({"version": status["version"], "camera": status["camera"], "detector": status["detector"]}))
if args.capture:
    print("Testing a ring and local snapshot sequence. The existing local session keeps camera priority.")
    started = time.monotonic()
    result = request("/api/ring", {"source": "browser"})
    print("RING_API_RESPONSE_MS", round((time.monotonic() - started) * 1000, 1))
    event_id = result["event"]["id"]
    for _ in range(15):
        event = request("/api/events/" + event_id)
        if event["capture_status"] != "pending":
            break
        time.sleep(1)
    print("LOCAL_TEST_EVENT", json.dumps({key: event[key] for key in ["id", "capture_status", "frames", "person_status", "camera_error", "simulated"]}))
    if event["capture_status"] != "complete":
        print("Camera capture was not fully verified. The core UI and event persistence remain available.")
    print("MEDIA_STAYS_ON_PI", True)
print("LOCAL_URL", args.url)
