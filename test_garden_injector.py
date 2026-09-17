import json
import os
import subprocess
import sys

payload = {
    "version": 1,
    "type": "garden_wake",
    "reason": "manual_test",
    "message": "Local wake test."
}

print("BASE_URL:", os.environ.get("PRESENCE_BASE_URL"))
print("UID:", os.environ.get("PRESENCE_UID"))
print("CHAR_ID:", os.environ.get("PRESENCE_CHAR_ID"))
print("TOKEN configured:", bool(os.environ.get("PRESENCE_INTEGRATION_TOKEN")))

result = subprocess.run(
    [
        sys.executable,
        r"D:\ai\Emerald-presence\integrations\galatea_garden\inject.py",
    ],
    input=json.dumps(payload).encode("utf-8"),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=os.environ.copy(),
)

print("stdout:", result.stdout.decode("utf-8", errors="replace"))
print("stderr:", result.stderr.decode("utf-8", errors="replace"))
print("exit code:", result.returncode)
