"""Scripted Pi-shaped child process; all control state stays in ignored local_state."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def action(prompt):
    directory = Path("local_state")
    role = os.environ["FRUTLUPS_SEAT"]
    counter = directory / f"{role}-count.txt"
    index = int(counter.read_text()) if counter.exists() else 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    actions = plan.get(role, [{}])
    selected = actions[min(index, len(actions) - 1)]
    counter.write_text(str(index + 1))
    (directory / f"{role}-{index}.md").write_text(prompt, encoding="utf-8")
    for path, text in selected.get("writes", {}).items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for source, destination in selected.get("renames", {}).items():
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", source, destination], check=True, capture_output=True)
    return selected


def report(prompt, selected):
    if "text" in selected:
        return selected["text"]
    scope = re.search(r"# Review prompt: (M\d{3}(?:-S\d{2})?)", prompt)[1]
    round_no = re.search(r"round (\d+|holistic)", prompt)[1]
    result = selected.get("verdict", "pass")
    rows = selected.get("rows", [])
    return (
        f"# Review: {scope} round {round_no}\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows)
        + "\n\n## Closure Decision\n\n"
        f"Objective status: {'achieved' if result == 'pass' else 'not_achieved'}\n"
        "Objective evidence: Scripted fixture evidence.\n\n## Verdict\n"
        f"Verdict: {result} - next: Continue the fixture.\n"
    )


def main():
    prompt = Path(sys.argv[1]).read_text(encoding="utf-8")
    selected = action(prompt)
    failure = selected.get("failure")
    if failure == "output":
        print("invalid json")
        return
    if failure == "transport":
        sys.exit(1)
    if failure == "timeout":
        time.sleep(30)
        return
    text = (
        selected.get("text", "Implemented scripted fixture change.")
        if os.environ["FRUTLUPS_SEAT"] == "coder"
        else report(prompt, selected)
    )
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "usage": {"input": 17, "output": 3, "cost": {"total": 0.01}},
    }
    if failure:
        message.update(
            stopReason="error",
            errorMessage={
                "auth": "401 authentication_error",
                "capacity": "429 rate_limit",
            }[failure],
        )
    print(json.dumps({"type": "message_end", "message": message}))
    print(json.dumps({"type": "agent_end"}))


if __name__ == "__main__":
    main()
