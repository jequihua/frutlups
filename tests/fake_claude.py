"""Scripted Claude JSON child using the same fixture action language."""

import json
import sys
import time

from fake_pi import action, report
from frutlups.render import dispatch_framing

observed = sys.stdin.buffer.read().decode("utf-8")
framing = dispatch_framing("claude")
if not observed.endswith(framing):
    sys.stderr.write("fake_claude: stdin lacks the exact Claude dispatch framing\n")
    sys.exit(2)
# The scripted task is exactly what precedes the accepted suffix (observed == task + framing).
prompt = observed[:-len(framing)]
selected = action(prompt)
failure = selected.get("failure")
if failure == "timeout":
    time.sleep(120)
    sys.exit(0)
if failure == "output":
    print("invalid json")
else:
    text = (
        {
            "auth": "API Error: unauthorized",
            "capacity": "API Error: quota",
            "transport": "API Error: connection closed",
        }[failure]
        if failure
        else report(prompt, selected)
    )
    print(
        json.dumps(
            {
                "is_error": bool(failure),
                "result": text,
                "total_cost_usd": 0.02,
                "usage": {
                    "input_tokens": 1,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 2,
                    "output_tokens": 4,
                },
            }
        )
    )
    sys.exit(1 if failure else 0)
