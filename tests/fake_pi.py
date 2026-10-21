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
    role = "probe" if prompt.strip() == "Reply with OK only." else os.environ["FRUTLUPS_SEAT"]
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
    if prompt.strip() == "Reply with OK only.":
        return "OK"
    if "text" in selected:
        return selected["text"]
    if prompt.startswith("Format repair only."):
        reference = re.search(r"original contract (?:at )?`([^`]+)`", prompt)[1]
        prompt = Path(reference).read_text("utf-8")
    scope = re.search(r"# Review prompt: (M\d{3}(?:-S\d{2})?)", prompt)[1]
    round_no = re.search(r"round (\d+|holistic)", prompt)[1]
    result = selected.get("verdict", "pass")
    rows = selected.get("rows", [])
    updates = []
    for identifier in selected.get("close_ids", []):
        records = [json.loads(line) for line in Path("05_governance/ledger.jsonl").read_text("utf-8").splitlines()]
        source = next(row for row in reversed(records)
                      if row["ev"] in ("reviewed", "holistic_reviewed")
                      and f"| {identifier} |" in Path(row["report"]).read_text("utf-8"))
        updates.append(f"| {source['report']} | {source['sha']} | {identifier} | closed_by_review | - |")
    update_table = ("\n\n## Finding updates\n\n| source | sha | id | disposition | related |\n"
                    "| --- | --- | --- | --- | --- |\n" + "\n".join(updates)) if updates else ""
    return (
        f"# Review: {scope} round {round_no}\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows) + update_table
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
        time.sleep(120)
        return
    text = (
        selected.get("text", "Implemented scripted fixture change.")
        if os.environ["FRUTLUPS_SEAT"] == "coder"
        else report(prompt, selected)
    )
    if os.environ["FRUTLUPS_SEAT"] == "coder" and "text" not in selected:
        contracts = re.findall(r"```json\n(.*?)\n```", prompt, re.S)
        if contracts:
            value = json.loads(contracts[-1])
            value.update(requirement="Scripted fixture", reason=selected.get("outcome_reason", "Implemented scripted fixture change."))
            text = json.dumps(value)
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
    if not failure:
        # Pi's terminal settlement record; the real reader requires it for completion.
        print(json.dumps({"type": "agent_settled"}))


if __name__ == "__main__":
    main()
