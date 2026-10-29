"""JSON-lines logging to stdout for CLI jobs."""

import json
import sys
from datetime import UTC, datetime
from typing import Any


def log(event: str, **fields: Any) -> None:
    record = {"at": datetime.now(UTC).isoformat(), "event": event, **fields}
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()
