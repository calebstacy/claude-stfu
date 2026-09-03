# Captured paired evaluation protocol

This directory records what one installed Claude CLI returned for a frozen set of prompts. It makes a run inspectable and the procedure repeatable. It does not promise that another run will return the same text, estimate a model-wide pass rate, or establish behavior in Claude.ai, the API, or another Claude Code version.

## Scope

`fixtures.json` contains 12 synthetic, self-contained prompts. They were written for this evaluation. They are not real user transcripts, historical Claude replies, or evidence that any listed incident happened.

Each fixture separates:

- `allowed_facts`, claims supported by the evidence inside the prompt;
- `unsupported_conclusions`, tempting claims the prompt does not support;
- `required_points`, content a human reviewer expects a complete answer to cover; and
- `expected_depth`, a human judgment target, not a word-count gate.

The runner sends only `prompt` to Claude. The oracle fields remain local for review.

## Conditions

Each fixture is sent in a new non-persistent CLI session under two conditions:

- `baseline`: Claude Code's built-in system prompt with safe mode enabled;
- `treatment`: the same invocation with the exact bytes of repository-root `rules.md` added through `--append-system-prompt`.

Both conditions disable tools and Chrome, use safe mode, run from a newly created empty temporary directory, and receive the same requested model and effort. Safe mode disables user and project customizations, but Claude Code documents that administrator-managed policy may still apply. The built-in system prompt and provider behavior are not captured by this repository. The CLI version, requested model, returned JSON, and any model-usage identifiers in that JSON are captured instead.

Claude CLI does not expose every sampling or backend control. Model output is nondeterministic. A full model identifier is preferable to an alias, but neither makes the text reproducible byte for byte.

The fixture order and which condition runs first are shuffled from a recorded seed. Condition names are replaced with A/B labels in the human review sheet. This is partial blinding because the response style may reveal the treatment.

## Capture

Run from the repository root after committing the fixture set and `rules.md`:

```powershell
python evals/eval.py validate
python evals/eval.py capture --model <full-model-id> --effort low
```

The capture command refuses a dirty worktree by default. `--allow-dirty` exists for local experiments, and the dirty status is recorded. Do not use a dirty run as the repository's public evidence.

Each run is written under `evals/runs/<run-id>/`:

- `manifest.json` records the fixture snapshot and hash, rules text and hash, repository commit and status, CLI path and version, exact settings, randomized call plan, timestamps, and run status.
- `responses.jsonl` records one append-only object per attempted call. Each object contains the exact argument vector, prompt, condition, temporary-directory policy, process result, raw stdout JSON text, raw stderr text, parsed JSON when parsing succeeded, response text, hashes, usage, and timing.

The argument vector is the authoritative command record. It is stored as an array because reconstructing one shell command would introduce platform-specific quoting and make prompt text unsafe to copy through a shell.

Completed attempts plus launch, permission, timeout, malformed-output, and model-error failures remain in the run as explicit records. If the process is interrupted while a call is in flight, that call may have no record; the manifest is marked `interrupted` and validation reports the missing planned call. The report never silently drops a recorded failure. A retry is a new captured attempt, not a replacement for an old record.

Use dry run to inspect the planned isolation and command settings without making model calls:

```powershell
python evals/eval.py capture --model <full-model-id> --dry-run --allow-dirty
```

## Deterministic report

Generate a report from captured records:

```powershell
python evals/eval.py report evals/runs/<run-id>
```

The deterministic section counts words, headings, list items, dash characters, exclamation marks, and a small set of literal surface patterns. It also reports CLI token fields when present. These checks are exact for the recorded text and this script version. They do not determine truth, completeness, quality, authorship, or whether a phrase performs the rhetorical move suggested by its label.

The report quotes every captured output directly from `responses.jsonl`. It does not rewrite examples.

Before using any aggregate, validation rebuilds the exact expected argument vector and its hash from the frozen fixture, rules, settings, CLI record, and call plan. It also reparses raw stdout and recomputes response text and hash, usage, returned model identifiers, subtype, and capture status. Reports use those validated, recomputed values rather than trusting copied convenience fields.

## Human truth and depth review

Create a condition-blinded review sheet:

```powershell
python evals/eval.py review-template evals/runs/<run-id>
```

Copy `human-review.template.json` to `human-review.json`, fill every null judgment, add the reviewer and review time, and set `status` to `completed`. Then regenerate the report. Validation checks that prompts, oracle text, labels, and outputs still match the captured records.

Human review covers:

- whether the request was answered;
- which required points were preserved;
- which predeclared unsupported conclusions appeared;
- any other unsupported claims the reviewer found;
- whether depth was too short, appropriate, or too long; and
- an A/B/tie preference for the task.

The report keeps these judgments in a separate section and names the reviewer. If the review is absent, partial, or structurally invalid, no human aggregate is produced.

## Validation and claim boundary

Validate fixtures alone or a captured run:

```powershell
python evals/eval.py validate
python evals/eval.py validate --run evals/runs/<run-id>
```

A truthful summary names the exact captured run, fixture count, CLI version, requested and returned model information, date, and reviewer count. It reports counts such as "treatment was shorter in 8 of 12 captured pairs" or "the reviewer marked 2 unsupported conclusions in baseline outputs and 1 in treatment outputs."

It does not say that the rules eliminate hallucinations, make Claude concise in general, prove a causal improvement, or reproduce across surfaces. A 12-prompt synthetic run is a recorded specimen set, not a benchmark.

## Safe publication

Publication requires a complete successful run from a clean commit and a completed human review:

```powershell
python evals/eval.py publish evals/runs/<run-id>
```

The command leaves the private capture untouched. It creates `evals/public/<run-id>/` only after full integrity validation, normalizes the Claude executable and temporary-directory paths, recomputes path-dependent argument hashes, and scans every string plus every final serialized file for common API keys, private keys, bearer tokens, assigned secrets, Windows and UNC paths, WSL mount paths, and common POSIX local paths. Suspicious raw output causes publication to fail. The publisher never silently edits prompt or response text.

The public manifest discloses every path transformation and includes hashes of the private source manifest and response file. Those embedded hashes detect inconsistent artifacts. They do not prove authenticity against coordinated editing; a signed tag, external digest, or other independent record is required for that claim.
