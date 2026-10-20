"""Container health check: fails when the event loop stops touching the beat."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MAX_AGE = int(os.getenv("HEARTBEAT_MAX_AGE", "90"))
path = Path(os.getenv("DATA_DIR") or "/data") / ".heartbeat"

try:
    age = time.time() - path.stat().st_mtime
except OSError:
    print("no heartbeat file yet")
    sys.exit(1)

if age > MAX_AGE:
    print(f"heartbeat is {age:.0f}s old (limit {MAX_AGE}s)")
    sys.exit(1)

print(f"ok ({age:.0f}s ago)")
