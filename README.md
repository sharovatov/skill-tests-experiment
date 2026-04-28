# claude-judge

A Stop-hook judge for [Claude Code](https://claude.com/claude-code) that checks whether Claude's last response actually obeyed the rules you wrote in `CLAUDE.md`.

When Claude finishes a turn, the hook:

- reads Claude's last assistant message from the session transcript
- runs each rule from `claude.md.tests` through GPT-4o-mini in parallel
- if any rule fails, blocks the turn and feeds the failure reasons back to Claude so it can correct itself

Rules live in plain Markdown, judging runs locally as a hook, and there's no extra tooling on the Claude side.

## Why

`CLAUDE.md` lets you give Claude project-specific instructions, but nothing enforces them. Claude can drift, forget, or reinterpret rules. This repo is a minimal harness for fixing that — but the value compounds in three directions:

- **Enforcement.** Write the rule once in `CLAUDE.md`, write its pass/fail criteria once in `claude.md.tests`, and every assistant turn gets graded automatically. Failed turns are blocked and Claude retries with the failure reason in hand.
- **A benchmark seed.** Every `claude.md.tests` entry is already a labeled eval — instruction, pass/fail criteria, model verdict. Run the same suite against different base models, different `CLAUDE.md` phrasings, or different judge models, and the harness stops being a guard and starts being a measurement instrument. The artifact written incidentally while authoring project rules turns into an internal instruction-following benchmark.
- **An instruction-design discipline.** Writing the `pass:` and `fail:` lines is a forcing function. "All dates must be in ISO format" sounds crisp until the test asks *what counts as a date* — does "2026" count? "Q2"? "yesterday"? Until that's pinned down, the rule isn't a rule, it's a vibe. TDD-for-prompts: the test sharpens the instruction before the instruction ever runs.

## How it works

```
Claude finishes turn
        │
        ▼
Stop hook fires (.claude/settings.json)
        │
        ▼
.claude-judge/judge.py
        │
        ├── reads transcript_path from hook stdin
        ├── extracts last assistant text
        ├── parses claude.md.tests into {tag: {instruction, criteria}}
        ├── calls GPT-4o-mini once per rule (parallel)
        │
        ▼
All PASS  →  exit silently, turn ends
Any FAIL  →  emit {"decision": "block", "reason": "..."} so Claude retries
```

The judge is stateless: each rule is evaluated in isolation against the response text alone. No conversation history, no tool calls, no other rules.

## Repository layout

- `CLAUDE.md` — the instructions Claude is supposed to follow
- `claude.md.tests` — pass/fail criteria for each rule, keyed by the same `[tag]` used in `CLAUDE.md`
- `.claude/settings.json` — registers `judge.py` as a Stop hook
- `.claude-judge/judge.py` — the judge itself
- `.env` — holds `OPENAI_API_KEY` (gitignored)

## Setup

Requires Python 3.9+ and a Claude Code install.

Clone the repo, then from the project root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env and paste a real OPENAI_API_KEY
```

That's it. The hook path in `.claude/settings.json` uses `$CLAUDE_PROJECT_DIR`, so it works as long as `.venv/bin/python3` exists at the project root.

Open the project in Claude Code and the Stop hook will run after every assistant turn.

## Writing rules

Each rule has two halves that must share the same tag.

In `CLAUDE.md`:

```markdown
## [test-date-format]
All dates must be in ISO 8601 format (YYYY-MM-DD). Never use other date formats.
```

In `claude.md.tests`:

```
## [test-date-format]
instruction: All dates must be in ISO 8601 format (YYYY-MM-DD)
pass: the output contains no dates, OR every date is in YYYY-MM-DD format
fail: at least one date is in a non-ISO format (e.g. "April 4th", "04/04/2026")
```

Notes for writing good criteria:

- Always include an "OR the output doesn't contain X" escape clause in `pass:`, otherwise rules fire on every response whether or not they're relevant.
- Be concrete in `fail:` — give example bad outputs. The judge is a small model and benefits from worked examples.
- Tag names are arbitrary; they only need to match between the two files.

## Trying it

The repo ships with three sample rules. Each row has a prompt that tends to trip Claude into the violation:

- `[test-date-format]` — ISO 8601 dates only. Try: *"when did World War II end?"* (Claude often answers "September 2, 1945")
- `[test-user-name]` — address the user as "Vitaly", not "you". Try: *"summarize this repo for me"* (Claude usually reaches for "you" or "the user")
- `[test-bullet-lists]` — 3+ items must be bullet points, not numbered or comma-separated. Try: *"give me five tips for writing a good README"* (Claude tends to default to a numbered list)

Open Claude Code in this directory and run any of those prompts. If Claude slips, the Stop hook blocks the turn and Claude retries.

You can also tail the hook's timing output:

```
Judge timing (total 1.4s, parallel):
  test-date-format: 1.2s
  test-user-name: 1.1s
  test-bullet-lists: 1.4s
```

## Limitations

- The judge model is GPT-4o-mini for cost and latency. It's good at obvious violations and noisier on edge cases. Swap `MODEL` in `judge.py` if you want stricter judging.
- Each rule costs one API call per turn. Three rules = three calls per turn. Parallelized, but not free.
- If the judge API call fails, the turn passes (fail-open). The reasoning: a flaky network shouldn't block work.
- The judge only sees the final assistant text — not tool calls, not intermediate thinking, not the user's prompt. Rules about behavior that doesn't show up in the text won't work.

## License

MIT.
