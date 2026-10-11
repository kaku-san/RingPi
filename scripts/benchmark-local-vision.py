"""Local computational benchmark on blank images. No camera or biometric data is accessed."""
import argparse
import json
import platform
import statistics
import time
from pathlib import Path
from agent_doorbell.config import Config
from agent_doorbell.local_vision import LocalVision

parser=argparse.ArgumentParser()
parser.add_argument("--directory",type=Path)
args=parser.parse_args()
vision=LocalVision(args.directory or Config().data_dir / "models")
if not (vision.status["available"] and vision.status["objects_available"]):
    raise SystemExit("Install the verified local models and OpenCV first")
np=vision.np
image=np.zeros((540,960,3),np.uint8)
aligned=np.zeros((112,112,3),np.uint8)
tasks={"yunet_960x540":lambda:vision.extract(image),
       "sface_embedding_112x112":lambda:vision.recognizer.feature(aligned),
       "nanodet_416x416":lambda:vision.objects.infer(np.zeros((416,416,3),np.uint8))}
results={}
for name, operation in tasks.items():
    operation()
    values=[]
    for _ in range(5):
        start=time.perf_counter();operation();values.append((time.perf_counter()-start)*1000)
    results[name]={"median_ms":round(statistics.median(values),1),"max_ms":round(max(values),1)}
print(json.dumps({"system":platform.system(),"architecture":platform.machine(),"threads":2,"input":"blank synthetic arrays",
                  "accuracy_verified":False,"camera_acquisition":"not_measured","bot_acceptance":"not_paired","timings":results}))
