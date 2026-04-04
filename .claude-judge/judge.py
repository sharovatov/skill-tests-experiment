#!/usr/bin/env python3
"""
CLAUDE.md instruction judge — Stop hook for Claude Code.

Reads the assistant's last response from the transcript, evaluates it
against user-defined pass/fail criteria in claude.md.tests, using a
local Ollama model (gemma4:e2b) as a stateless judge.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e2b"
PROJECT_DIR = Path(__file__).resolve().parent.parent

JUDGE_PROMPT_TEMPLATE = """You are a compliance judge. Your job is to determine whether an assistant's response follows a specific rule.

## Rule
{instruction}

## Pass/Fail Criteria
{criteria}

## Assistant's Response
{response}

## Your Verdict
Does the response follow the rule according to the criteria above?
Respond with exactly one word on the first line: PASS or FAIL
On the second line, give a brief reason (one sentence max)."""


def read_stdin():
    return json.load(sys.stdin)


def extract_last_response(transcript_path):
    """Read the JSONL transcript and extract the last assistant text."""
    last_assistant_text = ""
    with open(transcript_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "assistant":
                parts = []
                for block in entry.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        parts.append(block["text"])
                if parts:
                    last_assistant_text = "\n".join(parts)
    return last_assistant_text


def parse_tests(tests_path):
    """Parse claude.md.tests into a dict of {tag: {instruction, criteria}}."""
    tests = {}
    current_tag = None
    current_lines = []

    with open(tests_path, "r") as f:
        for line in f:
            match = re.match(r"^##\s+\[(.+?)\]", line)
            if match:
                if current_tag:
                    tests[current_tag] = _parse_test_block(current_lines)
                current_tag = match.group(1)
                current_lines = []
            elif current_tag is not None:
                current_lines.append(line)

    if current_tag:
        tests[current_tag] = _parse_test_block(current_lines)

    return tests


def _parse_test_block(lines):
    """Extract instruction, pass, and fail from a test block's lines."""
    block = {"instruction": "", "criteria": ""}
    pass_lines = []
    fail_lines = []
    instruction_line = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("instruction:"):
            instruction_line = stripped[len("instruction:"):].strip()
        elif stripped.startswith("pass:"):
            pass_lines.append(stripped[len("pass:"):].strip())
        elif stripped.startswith("fail:"):
            fail_lines.append(stripped[len("fail:"):].strip())

    block["instruction"] = instruction_line
    criteria_parts = []
    if pass_lines:
        criteria_parts.append("PASS when: " + "; ".join(pass_lines))
    if fail_lines:
        criteria_parts.append("FAIL when: " + "; ".join(fail_lines))
    block["criteria"] = "\n".join(criteria_parts)

    return block


def call_judge(instruction, criteria, response):
    """Call Ollama with the judge prompt, return (verdict, reason)."""
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        instruction=instruction,
        criteria=criteria,
        response=response,
    )

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # If Ollama is down or errors, don't block — just warn
        print(json.dumps({}))
        sys.exit(0)

    text = result.get("response", "").strip()
    lines = text.split("\n", 1)
    verdict = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else ""

    # Normalize: accept variations like "PASS." or "**PASS**"
    if "PASS" in verdict:
        verdict = "PASS"
    elif "FAIL" in verdict:
        verdict = "FAIL"
    else:
        verdict = "PASS"  # When in doubt, don't block

    return verdict, reason


def main():
    hook_input = read_stdin()
    transcript_path = hook_input.get("transcript_path")

    if not transcript_path:
        sys.exit(0)

    response_text = extract_last_response(transcript_path)
    if not response_text:
        sys.exit(0)

    tests_file = PROJECT_DIR / "claude.md.tests"
    if not tests_file.exists():
        sys.exit(0)

    tests = parse_tests(tests_file)
    if not tests:
        sys.exit(0)

    failures = []

    for tag, test in tests.items():
        verdict, reason = call_judge(
            test["instruction"], test["criteria"], response_text
        )
        if verdict == "FAIL":
            failures.append(f"[{tag}]: {reason}")

    if failures:
        block_reason = "Instruction compliance check failed:\n" + "\n".join(
            f"  - {f}" for f in failures
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": block_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # All tests passed — allow
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
