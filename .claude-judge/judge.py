#!/usr/bin/env python3
"""
CLAUDE.md instruction judge — Stop hook for Claude Code.

Reads the assistant's last response from the transcript, evaluates it
against user-defined pass/fail criteria in claude.md.tests, using
GPT-4o-mini as a stateless judge (parallel calls).
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Load .env from project root
_env_path = PROJECT_DIR / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip().strip("\"'"))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"

JUDGE_SYSTEM = "You are a compliance judge. You evaluate whether text follows a rule. Respond with exactly PASS or FAIL on the first line, then a one-sentence reason on the second line. Nothing else."

JUDGE_PROMPT_TEMPLATE = """## Rule
{instruction}

## Pass/Fail Criteria
{criteria}

## Text to Evaluate
{response}"""


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
    """Call GPT-4o-mini with the judge prompt, return (verdict, reason)."""
    user_prompt = JUDGE_PROMPT_TEMPLATE.format(
        instruction=instruction,
        criteria=criteria,
        response=response,
    )

    try:
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "max_tokens": 100,
            },
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        sys.stderr.write(f"Judge call failed: {e}\n")
        return "PASS", "judge unavailable"

    text = result["choices"][0]["message"]["content"].strip()
    lines = text.split("\n", 1)
    verdict = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else ""

    if "PASS" in verdict:
        verdict = "PASS"
    elif "FAIL" in verdict:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return verdict, reason


def main():
    if not OPENAI_API_KEY:
        sys.stderr.write("OPENAI_API_KEY not set, skipping judge\n")
        print(json.dumps({}))
        sys.exit(0)

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
    timings = {}
    total_start = time.time()

    def run_test(tag, test):
        t0 = time.time()
        verdict, reason = call_judge(
            test["instruction"], test["criteria"], response_text
        )
        elapsed = time.time() - t0
        return tag, verdict, reason, elapsed

    with ThreadPoolExecutor(max_workers=len(tests)) as pool:
        futures = {
            pool.submit(run_test, tag, test): tag
            for tag, test in tests.items()
        }
        for future in futures:
            tag, verdict, reason, elapsed = future.result()
            timings[tag] = elapsed
            if verdict == "FAIL":
                failures.append(f"[{tag}]: {reason}")

    total_elapsed = time.time() - total_start

    timing_lines = [f"  {tag}: {t:.1f}s" for tag, t in timings.items()]
    timing_summary = (
        f"Judge timing (total {total_elapsed:.1f}s, parallel):\n"
        + "\n".join(timing_lines)
    )
    sys.stderr.write(timing_summary + "\n")

    if failures:
        block_reason = "Instruction compliance check failed:\n" + "\n".join(
            f"  - {f}" for f in failures
        )
        output = {
            "decision": "block",
            "reason": block_reason,
        }
        print(json.dumps(output))
        sys.exit(0)

    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
