"""Scripted Claude JSON child using the same fixture action language."""

import json
import sys

from fake_pi import action, report

prompt = sys.stdin.buffer.read().decode("utf-8")
selected = action(prompt)
failure = selected.get("failure")
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
