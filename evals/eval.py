#!/usr/bin/env python3
"""Capture and inspect paired claude-stfu CLI evaluations.

Only Python's standard library is used. Model calls are made by invoking the
installed ``claude -p`` executable. Captured text is never treated as a
reproducible model result; it is evidence for one recorded run.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
DEFAULT_FIXTURES_PATH = EVALS_DIR / "fixtures.json"
RULES_PATH = REPO_ROOT / "rules.md"
RUNS_DIR = EVALS_DIR / "runs"
EXPECTED_FIXTURE_COUNT = 12
DEPTH_VALUES = {"short", "medium", "detailed"}
ANSWER_VALUES = {"yes", "partial", "no"}
REVIEW_DEPTH_VALUES = {"too_short", "appropriate", "too_long"}
PREFERENCE_VALUES = {"A", "B", "tie"}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE)),
    (
        "assigned_secret",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*"
            r"[\"']?[A-Za-z0-9._~+/=-]{12,}",
            re.IGNORECASE,
        ),
    ),
)

LOCAL_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows_absolute_path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]+")),
    ("unc_path", re.compile(r"\\\\[^\s\\/]+[\\/][^\s\"'<>|]+")),
    ("posix_home_path", re.compile(r"/(?:home|Users)/[^/\s\"']+(?:/[^\s\"']*)?")),
    ("wsl_mount_path", re.compile(r"/mnt/[A-Za-z]/[^\s\"']+")),
    (
        "posix_local_path",
        re.compile(r"(?<![A-Za-z0-9:])/(?:tmp|var/tmp|opt|srv|workspace|root)/[^\s\"']+"),
    ),
)


SURFACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "praise_opener",
        re.compile(
            r"\A\s*(?:great question|good question|good catch|excellent question|"
            r"you(?:'|’)re absolutely right)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "concession_opener",
        re.compile(r"\A\s*(?:fair|sure|absolutely|of course)\b", re.IGNORECASE),
    ),
    (
        "closing_offer",
        re.compile(
            r"\b(?:let me know if|happy to (?:help|dive|expand)|if you(?:'|’)d like)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "template_roadmap",
        re.compile(
            r"\b(?:let(?:'|’)s break (?:this|it) down|in this (?:reply|response),? i(?:'|’)ll|"
            r"i(?:'|’)ll cover)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sincerity_marker",
        re.compile(r"\b(?:honestly|candidly|to be (?:honest|transparent))\b", re.IGNORECASE),
    ),
    (
        "discovery_theater",
        re.compile(
            r"\b(?:smoking gun|breakthrough|this changes everything|"
            r"discovered something (?:really )?interesting)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "false_collaboration_surface",
        re.compile(r"\blet(?:'|’)s\b", re.IGNORECASE),
    ),
    (
        "transition_turnstile_surface",
        re.compile(r"\b(?:moreover|furthermore|ultimately|in conclusion)\b", re.IGNORECASE),
    ),
    (
        "process_narration_surface",
        re.compile(
            r"\b(?:i (?:checked|looked at|reviewed) (?:the|your)|"
            r"i did a deep dive|i went ahead and)\b",
            re.IGNORECASE,
        ),
    ),
)


class EvalError(RuntimeError):
    """A user-facing validation or capture error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def argv_sha256(argv: list[str]) -> str:
    return sha256_text(json.dumps(argv, ensure_ascii=False, separators=(",", ":")))


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvalError(f"Could not read {path}: {exc}") from exc


def read_text(path: Path) -> str:
    data = read_bytes(path)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvalError(f"{path} is not UTF-8: {exc}") from exc


def read_json(path: Path) -> Any:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise EvalError(f"Invalid JSON in {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvalError(f"Could not read {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise EvalError(f"Expected an object at {path}:{line_number}")
        records.append(value)
    return records


def _nonempty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def fixture_validation_errors(bundle: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(bundle, dict):
        return ["Fixture file must contain a JSON object."]
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"Fixture schema_version must be {SCHEMA_VERSION}.")
    if not isinstance(bundle.get("fixture_set_id"), str) or not bundle["fixture_set_id"].strip():
        errors.append("fixture_set_id must be a non-empty string.")
    if not isinstance(bundle.get("disclosure"), str) or not bundle["disclosure"].strip():
        errors.append("Fixture disclosure must be a non-empty string.")
    fixtures = bundle.get("fixtures")
    if not isinstance(fixtures, list):
        return errors + ["fixtures must be an array."]
    if len(fixtures) != EXPECTED_FIXTURE_COUNT:
        errors.append(
            f"Frozen fixture set must contain exactly {EXPECTED_FIXTURE_COUNT} fixtures; "
            f"found {len(fixtures)}."
        )

    seen_ids: set[str] = set()
    required_string_fields = ("id", "category", "prompt", "expected_depth")
    required_list_fields = (
        "allowed_facts",
        "unsupported_conclusions",
        "required_points",
        "target_moves",
    )
    for index, fixture in enumerate(fixtures):
        prefix = f"fixtures[{index}]"
        if not isinstance(fixture, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        for field in required_string_fields:
            if not isinstance(fixture.get(field), str) or not fixture[field].strip():
                errors.append(f"{prefix}.{field} must be a non-empty string.")
        fixture_id = fixture.get("id")
        if isinstance(fixture_id, str):
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fixture_id):
                errors.append(f"{prefix}.id must be lowercase kebab-case.")
            if fixture_id in seen_ids:
                errors.append(f"Duplicate fixture id: {fixture_id}")
            seen_ids.add(fixture_id)
        for field in required_list_fields:
            if not _nonempty_strings(fixture.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string array.")
        if fixture.get("expected_depth") not in DEPTH_VALUES:
            errors.append(
                f"{prefix}.expected_depth must be one of {sorted(DEPTH_VALUES)}."
            )
        provenance = fixture.get("provenance")
        if not isinstance(provenance, dict):
            errors.append(f"{prefix}.provenance must be an object.")
        else:
            if provenance.get("kind") != "synthetic":
                errors.append(f"{prefix}.provenance.kind must be synthetic in this set.")
            if provenance.get("not_a_recorded_exchange") is not True:
                errors.append(
                    f"{prefix}.provenance.not_a_recorded_exchange must be true."
                )
            if not isinstance(provenance.get("purpose"), str) or not provenance["purpose"].strip():
                errors.append(f"{prefix}.provenance.purpose must be a non-empty string.")
        allowed = fixture.get("allowed_facts")
        unsupported = fixture.get("unsupported_conclusions")
        if isinstance(allowed, list) and isinstance(unsupported, list):
            overlap = set(allowed).intersection(unsupported)
            if overlap:
                errors.append(
                    f"{prefix} repeats claims in allowed_facts and unsupported_conclusions: "
                    + ", ".join(sorted(overlap))
                )
    return errors


def load_and_validate_fixtures(path: Path = DEFAULT_FIXTURES_PATH) -> dict[str, Any]:
    bundle = read_json(path)
    errors = fixture_validation_errors(bundle)
    if errors:
        raise EvalError("Fixture validation failed:\n- " + "\n- ".join(errors))
    return bundle


def surface_metrics(text: str) -> dict[str, Any]:
    """Return deterministic surface counts without semantic judgments."""
    words = re.findall(r"\b[^\W_]+(?:['’][^\W_]+)*\b", text, flags=re.UNICODE)
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    paragraphs = [part for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    sentence_marks = re.findall(r"[.!?]+(?:[\"'’”)]*)?(?=\s|$)", text)
    sentence_count = len(sentence_marks)
    if text.strip() and sentence_count == 0:
        sentence_count = 1

    findings = {
        name: len(pattern.findall(text)) for name, pattern in SURFACE_PATTERNS
    }
    punctuation = {
        "em_dash": text.count("—"),
        "en_dash": text.count("–"),
        "exclamation": text.count("!"),
    }
    heading_count = sum(
        1 for line in text.splitlines() if re.match(r"^\s{0,3}#{1,6}\s+\S", line)
    )
    list_item_count = sum(
        1
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-+*]|\d+[.)])\s+\S", line)
    )
    return {
        "characters": len(text),
        "words": len(words),
        "sentences_surface": sentence_count,
        "nonempty_lines": len(nonempty_lines),
        "paragraphs": len(paragraphs),
        "headings_surface": heading_count,
        "list_items_surface": list_item_count,
        "punctuation": punctuation,
        "pattern_matches": findings,
        "surface_match_total": sum(findings.values()) + sum(punctuation.values()),
    }


def resolve_cli(name: str) -> Path:
    candidate = Path(name)
    if candidate.parent != Path(".") or candidate.is_absolute():
        resolved = candidate.expanduser().resolve()
        if not resolved.is_file():
            raise EvalError(f"Claude executable does not exist: {resolved}")
    else:
        found = shutil.which(name)
        if not found:
            raise EvalError(f"Could not find Claude executable on PATH: {name}")
        resolved = Path(found).resolve()
    if os.name == "nt" and resolved.suffix.lower() in {".cmd", ".bat"}:
        raise EvalError(
            "The resolved Claude command is a batch wrapper. Pass the native claude.exe path "
            "so fixture prompts never cross a command-shell quoting boundary."
        )
    return resolved


def decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def cli_version(executable: Path) -> dict[str, Any]:
    process = subprocess.run(
        [str(executable), "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    stdout = decode_output(process.stdout)
    stderr = decode_output(process.stderr)
    if process.returncode != 0:
        raise EvalError(
            f"Could not read Claude CLI version (exit {process.returncode}): "
            f"{stderr.strip() or stdout.strip()}"
        )
    return {
        "argv": [str(executable), "--version"],
        "stdout": stdout,
        "stderr": stderr,
        "return_code": process.returncode,
    }


def git_repo_state() -> dict[str, Any]:
    commit_process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    status_process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    commit_stdout = decode_output(commit_process.stdout).strip()
    status_stdout = decode_output(status_process.stdout)
    if commit_process.returncode != 0 or not commit_stdout:
        raise EvalError(
            "Could not read repository commit: "
            + (decode_output(commit_process.stderr).strip() or "unknown git error")
        )
    if status_process.returncode != 0:
        raise EvalError(
            "Could not read repository status: "
            + (decode_output(status_process.stderr).strip() or "unknown git error")
        )
    return {
        "commit": commit_stdout,
        "dirty": bool(status_stdout.strip()),
        "status_porcelain": status_stdout,
    }


def build_claude_argv(
    executable: Path,
    *,
    prompt: str,
    model: str,
    effort: str,
    condition: str,
    rules_text: str,
) -> list[str]:
    if condition not in {"baseline", "treatment"}:
        raise EvalError(f"Unknown condition: {condition}")
    argv = [
        str(executable),
        "-p",
        "--output-format",
        "json",
        "--safe-mode",
        "--no-session-persistence",
        "--no-chrome",
        "--strict-mcp-config",
        "--tools",
        "",
        "--prompt-suggestions",
        "false",
        "--model",
        model,
        "--effort",
        effort,
    ]
    if condition == "treatment":
        argv.extend(["--append-system-prompt", rules_text])
    argv.append(prompt)
    return argv


def make_call_plan(
    fixtures: list[dict[str, Any]],
    *,
    seed: int,
    executable: Path,
    model: str,
    effort: str,
    rules_text: str,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    ordered = list(fixtures)
    rng.shuffle(ordered)
    plan: list[dict[str, Any]] = []
    sequence = 0
    for fixture in ordered:
        labels = ["A", "B"]
        rng.shuffle(labels)
        label_by_condition = {"baseline": labels[0], "treatment": labels[1]}
        conditions = ["baseline", "treatment"]
        rng.shuffle(conditions)
        for condition in conditions:
            sequence += 1
            argv = build_claude_argv(
                executable,
                prompt=fixture["prompt"],
                model=model,
                effort=effort,
                condition=condition,
                rules_text=rules_text,
            )
            plan.append(
                {
                    "sequence": sequence,
                    "fixture_id": fixture["id"],
                    "condition": condition,
                    "blind_label": label_by_condition[condition],
                    "argv_sha256": argv_sha256(argv),
                }
            )
    return plan


def actual_model_identifiers(parsed: Any) -> list[str]:
    identifiers: set[str] = set()
    if not isinstance(parsed, dict):
        return []
    direct = parsed.get("model")
    if isinstance(direct, str) and direct:
        identifiers.add(direct)
    model_usage = parsed.get("modelUsage")
    if isinstance(model_usage, dict):
        identifiers.update(key for key in model_usage if isinstance(key, str) and key)
    return sorted(identifiers)


def parsed_result_text(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    result = parsed.get("result")
    return result if isinstance(result, str) else None


def derived_raw_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Derive every reportable response field from raw process evidence."""
    raw_stdout = record.get("raw_stdout")
    raw_stderr = record.get("raw_stderr")
    stdout = raw_stdout if isinstance(raw_stdout, str) else ""
    stderr = raw_stderr if isinstance(raw_stderr, str) else ""
    parsed: Any = None
    parse_error: str | None = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    else:
        parse_error = "stdout was empty"
    response_text = parsed_result_text(parsed)
    process = record.get("process") if isinstance(record.get("process"), dict) else {}
    launch_error = record.get("launch_error")
    capture_ok = (
        launch_error is None
        and process.get("launched") is True
        and process.get("timed_out") is False
        and process.get("return_code") == 0
        and isinstance(parsed, dict)
        and response_text is not None
        and parsed.get("is_error") is not True
    )
    return {
        "raw_stdout_sha256": sha256_text(stdout),
        "raw_stderr_sha256": sha256_text(stderr),
        "parsed_json": parsed,
        "json_parse_error": parse_error,
        "response_text": response_text,
        "response_text_sha256": sha256_text(response_text) if response_text is not None else None,
        "returned_model_identifiers": actual_model_identifiers(parsed),
        "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
        "model_usage": parsed.get("modelUsage") if isinstance(parsed, dict) else None,
        "result_subtype": parsed.get("subtype") if isinstance(parsed, dict) else None,
        "capture_ok": capture_ok,
    }


def expected_record_settings(manifest: dict[str, Any], condition: str) -> dict[str, Any]:
    settings = manifest["settings"]
    return {
        "requested_model": settings["requested_model"],
        "effort": settings["effort"],
        "output_format": "json",
        "safe_mode": True,
        "session_persistence": False,
        "tools": "disabled",
        "chrome": False,
        "strict_mcp_config": True,
        "prompt_suggestions": False,
        "treatment_rules_mode": "append-system-prompt" if condition == "treatment" else "none",
    }


def expected_argv(
    manifest: dict[str, Any], fixture: dict[str, Any], condition: str
) -> list[str]:
    return build_claude_argv(
        Path(manifest["claude_cli"]["executable"]),
        prompt=fixture["prompt"],
        model=manifest["settings"]["requested_model"],
        effort=manifest["settings"]["effort"],
        condition=condition,
        rules_text=manifest["rules_text"],
    )


def recomputed_record(
    record: dict[str, Any],
    *,
    manifest: dict[str, Any],
    fixture: dict[str, Any],
    planned_call: dict[str, Any],
) -> dict[str, Any]:
    """Return a canonical record whose identities and derivatives are rebuilt."""
    condition = planned_call["condition"]
    argv = expected_argv(manifest, fixture, condition)
    canonical = dict(record)
    canonical.update(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": manifest["run_id"],
            "sequence": planned_call["sequence"],
            "fixture_id": planned_call["fixture_id"],
            "condition": condition,
            "blind_label": planned_call["blind_label"],
            "argv": argv,
            "argv_sha256": argv_sha256(argv),
            "settings": expected_record_settings(manifest, condition),
            "prompt": fixture["prompt"],
            "prompt_sha256": sha256_text(fixture["prompt"]),
            "rules_sha256": (
                manifest["rules_sha256"] if condition == "treatment" else None
            ),
        }
    )
    canonical.update(derived_raw_fields(record))
    return canonical


def capture_one(
    *,
    run_id: str,
    call: dict[str, Any],
    fixture: dict[str, Any],
    executable: Path,
    model: str,
    effort: str,
    rules_text: str,
    rules_sha256: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    argv = build_claude_argv(
        executable,
        prompt=fixture["prompt"],
        model=model,
        effort=effort,
        condition=call["condition"],
        rules_text=rules_text,
    )
    started_at = utc_now()
    started_clock = time.monotonic()
    raw_stdout_bytes = b""
    raw_stderr_bytes = b""
    return_code: int | None = None
    timed_out = False
    temporary_path = ""
    temporary_created = False
    launched = False
    launch_error: dict[str, Any] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="claude-stfu-eval-") as temporary:
            temporary_created = True
            temporary_path = str(Path(temporary).resolve())
            process = subprocess.run(
                argv,
                cwd=temporary,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
            raw_stdout_bytes = process.stdout
            raw_stderr_bytes = process.stderr
            return_code = process.returncode
            launched = True
    except subprocess.TimeoutExpired as exc:
        raw_stdout_bytes = exc.stdout or b""
        raw_stderr_bytes = exc.stderr or b""
        timed_out = True
        launched = True
    except OSError as exc:
        launch_error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
        }
    duration_ms = round((time.monotonic() - started_clock) * 1000)
    ended_at = utc_now()
    raw_stdout = decode_output(raw_stdout_bytes)
    raw_stderr = decode_output(raw_stderr_bytes)
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": call["sequence"],
        "fixture_id": fixture["id"],
        "condition": call["condition"],
        "blind_label": call["blind_label"],
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "duration_ms": duration_ms,
        "isolated_working_directory": {
            "path_during_call": temporary_path,
            "was_new_and_empty": temporary_created,
            "removed_after_call": temporary_created,
        },
        "argv": argv,
        "argv_sha256": argv_sha256(argv),
        "settings": {
            "requested_model": model,
            "effort": effort,
            "output_format": "json",
            "safe_mode": True,
            "session_persistence": False,
            "tools": "disabled",
            "chrome": False,
            "strict_mcp_config": True,
            "prompt_suggestions": False,
            "treatment_rules_mode": (
                "append-system-prompt" if call["condition"] == "treatment" else "none"
            ),
        },
        "prompt": fixture["prompt"],
        "prompt_sha256": sha256_text(fixture["prompt"]),
        "rules_sha256": rules_sha256 if call["condition"] == "treatment" else None,
        "process": {
            "launched": launched,
            "return_code": return_code,
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
        },
        "launch_error": launch_error,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
    }
    record.update(derived_raw_fields(record))
    return record


def default_run_id(model: str, commit: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return f"{timestamp}-{model_slug[:48]}-{commit[:10]}"


def assert_safe_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", run_id):
        raise EvalError(
            "run-id must be 1-120 characters using only letters, numbers, dot, underscore, or hyphen."
        )


def command_capture(args: argparse.Namespace) -> int:
    fixtures_path = Path(args.fixtures).resolve()
    bundle = load_and_validate_fixtures(fixtures_path)
    rules_bytes = read_bytes(RULES_PATH)
    try:
        rules_text = rules_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvalError(f"{RULES_PATH} is not UTF-8: {exc}") from exc
    if not rules_text.strip():
        raise EvalError(f"Treatment rules file is empty: {RULES_PATH}")
    executable = resolve_cli(args.claude)
    version_record = cli_version(executable)
    repo = git_repo_state()
    if repo["dirty"] and not args.allow_dirty:
        raise EvalError(
            "The worktree is dirty. Commit the fixture set and rules before a captured run, "
            "or use --allow-dirty for a disclosed local experiment."
        )
    rules_sha = sha256_bytes(rules_bytes)
    plan = make_call_plan(
        bundle["fixtures"],
        seed=args.seed,
        executable=executable,
        model=args.model,
        effort=args.effort,
        rules_text=rules_text,
    )
    if args.dry_run:
        preview = {
            "mode": "dry-run",
            "model_calls_made": 0,
            "claim_scope": "This validates a call plan; it is not a captured evaluation run.",
            "fixture_set_id": bundle["fixture_set_id"],
            "fixture_count": len(bundle["fixtures"]),
            "planned_call_count": len(plan),
            "seed": args.seed,
            "requested_model": args.model,
            "effort": args.effort,
            "claude_executable": str(executable),
            "claude_version": version_record,
            "repo": repo,
            "fixtures_sha256": sha256_bytes(read_bytes(fixtures_path)),
            "rules_path": str(RULES_PATH),
            "rules_sha256": rules_sha,
            "settings": {
                "safe_mode": True,
                "new_empty_working_directory_per_call": True,
                "session_persistence": False,
                "tools": "disabled",
                "chrome": False,
                "strict_mcp_config": True,
                "prompt_suggestions": False,
                "treatment_rules_mode": "append-system-prompt",
            },
            "plan": plan,
        }
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    run_id = args.run_id or default_run_id(args.model, repo["commit"])
    assert_safe_run_id(run_id)
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        raise EvalError(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "manifest.json"
    records_path = run_dir / "responses.jsonl"
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "kind": "captured_claude_cli_pair_run",
        "claim_scope": (
            "This manifest records one CLI run. It does not promise byte-identical reruns, "
            "a model-wide pass rate, or behavior on another Claude surface."
        ),
        "status": "in_progress",
        "capture_started_at_utc": utc_now(),
        "capture_completed_at_utc": None,
        "repo": repo,
        "fixtures_path_at_capture": str(fixtures_path),
        "fixtures_sha256": sha256_bytes(read_bytes(fixtures_path)),
        "fixtures_text": read_text(fixtures_path),
        "fixture_bundle": bundle,
        "rules_path_at_capture": str(RULES_PATH),
        "rules_sha256": rules_sha,
        "rules_text": rules_text,
        "claude_cli": {
            "executable": str(executable),
            "version_call": version_record,
        },
        "settings": {
            "requested_model": args.model,
            "effort": args.effort,
            "safe_mode": True,
            "new_empty_working_directory_per_call": True,
            "session_persistence": False,
            "tools": "disabled",
            "chrome": False,
            "strict_mcp_config": True,
            "prompt_suggestions": False,
            "output_format": "json",
            "condition_baseline": "Claude Code built-in prompt under the recorded CLI invocation",
            "condition_treatment": "Baseline plus exact rules_text through --append-system-prompt",
            "timeout_seconds_per_call": args.timeout,
            "random_seed": args.seed,
        },
        "planned_calls": plan,
        "attempted_call_count": 0,
        "successful_capture_count": 0,
    }
    write_json_atomic(manifest_path, manifest)
    fixture_by_id = {fixture["id"]: fixture for fixture in bundle["fixtures"]}
    successful = 0
    attempted = 0
    try:
        for call in plan:
            record = capture_one(
                run_id=run_id,
                call=call,
                fixture=fixture_by_id[call["fixture_id"]],
                executable=executable,
                model=args.model,
                effort=args.effort,
                rules_text=rules_text,
                rules_sha256=rules_sha,
                timeout_seconds=args.timeout,
            )
            append_jsonl(records_path, record)
            attempted += 1
            successful += int(record["capture_ok"])
            manifest["attempted_call_count"] = attempted
            manifest["successful_capture_count"] = successful
            write_json_atomic(manifest_path, manifest)
            state = "captured" if record["capture_ok"] else "failed"
            print(
                f"[{call['sequence']:02d}/{len(plan):02d}] "
                f"{call['fixture_id']} {call['condition']}: {state}",
                flush=True,
            )
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        manifest["capture_completed_at_utc"] = utc_now()
        write_json_atomic(manifest_path, manifest)
        raise EvalError(f"Capture interrupted. Partial records remain in {run_dir}")
    except Exception:
        manifest["status"] = "aborted_internal_error"
        manifest["capture_completed_at_utc"] = utc_now()
        try:
            write_json_atomic(manifest_path, manifest)
        except OSError:
            pass
        raise
    manifest["capture_completed_at_utc"] = utc_now()
    if successful == len(plan):
        manifest["status"] = "completed"
    else:
        manifest["status"] = "completed_with_failures"
    write_json_atomic(manifest_path, manifest)
    print(f"Run: {run_dir}")
    print(f"Captured: {successful}/{len(plan)} calls")
    return 0 if successful == len(plan) else 1


def validate_record_integrity(
    record: dict[str, Any],
    *,
    manifest: dict[str, Any],
    fixture_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    prefix = f"record sequence {record.get('sequence')}"
    fixture_id = record.get("fixture_id")
    fixture = fixture_by_id.get(fixture_id)
    if fixture is None:
        errors.append(f"{prefix}: unknown fixture_id {fixture_id!r}")
        return errors
    plan_matches = [
        call
        for call in manifest.get("planned_calls", [])
        if isinstance(call, dict) and call.get("sequence") == record.get("sequence")
    ]
    if len(plan_matches) != 1:
        errors.append(f"{prefix}: sequence does not map to exactly one planned call")
        return errors
    planned_call = plan_matches[0]
    if planned_call.get("fixture_id") != fixture_id:
        errors.append(f"{prefix}: fixture_id differs from planned call")
        return errors

    raw_stdout = record.get("raw_stdout")
    if not isinstance(raw_stdout, str):
        errors.append(f"{prefix}: raw_stdout must be a string")
    raw_stderr = record.get("raw_stderr")
    if not isinstance(raw_stderr, str):
        errors.append(f"{prefix}: raw_stderr must be a string")
    process = record.get("process")
    if not isinstance(process, dict):
        errors.append(f"{prefix}: process must be an object")
    else:
        if not isinstance(process.get("launched"), bool):
            errors.append(f"{prefix}: process.launched must be boolean")
        return_code = process.get("return_code")
        if return_code is not None and (
            not isinstance(return_code, int) or isinstance(return_code, bool)
        ):
            errors.append(f"{prefix}: process.return_code must be an integer or null")
        if not isinstance(process.get("timed_out"), bool):
            errors.append(f"{prefix}: process.timed_out must be boolean")
        if process.get("timeout_seconds") != manifest.get("settings", {}).get(
            "timeout_seconds_per_call"
        ):
            errors.append(f"{prefix}: process timeout differs from manifest settings")
    launch_error = record.get("launch_error")
    if launch_error is not None:
        if not isinstance(launch_error, dict):
            errors.append(f"{prefix}: launch_error must be an object or null")
        elif not isinstance(launch_error.get("type"), str) or not isinstance(
            launch_error.get("message"), str
        ):
            errors.append(f"{prefix}: launch_error lacks a string type or message")

    canonical = recomputed_record(
        record,
        manifest=manifest,
        fixture=fixture,
        planned_call=planned_call,
    )
    expected_fields = (
        "schema_version",
        "run_id",
        "sequence",
        "fixture_id",
        "condition",
        "blind_label",
        "argv",
        "argv_sha256",
        "settings",
        "prompt",
        "prompt_sha256",
        "rules_sha256",
        "raw_stdout_sha256",
        "raw_stderr_sha256",
        "parsed_json",
        "json_parse_error",
        "response_text",
        "response_text_sha256",
        "returned_model_identifiers",
        "usage",
        "model_usage",
        "result_subtype",
        "capture_ok",
    )
    for field in expected_fields:
        if record.get(field) != canonical.get(field):
            errors.append(f"{prefix}: {field} differs from recomputed evidence")
    if planned_call.get("argv_sha256") != canonical["argv_sha256"]:
        errors.append(f"{prefix}: planned argv hash differs from full expected argv")
    return errors


def load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = run_dir / "manifest.json"
    records_path = run_dir / "responses.jsonl"
    if not manifest_path.is_file():
        raise EvalError(f"Missing run manifest: {manifest_path}")
    if not records_path.is_file():
        raise EvalError(f"Missing run records: {records_path}")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise EvalError(f"Run manifest must contain an object: {manifest_path}")
    return manifest, load_jsonl(records_path)


def run_validation_errors(
    manifest: dict[str, Any], records: list[dict[str, Any]], *, require_complete: bool
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("Manifest schema_version is invalid.")
    if manifest.get("kind") not in {
        "captured_claude_cli_pair_run",
        "published_redacted_claude_cli_pair_run",
    }:
        errors.append("Manifest kind is invalid.")
    bundle = manifest.get("fixture_bundle")
    fixture_errors = fixture_validation_errors(bundle)
    errors.extend(f"Captured fixture snapshot: {error}" for error in fixture_errors)
    if not isinstance(bundle, dict) or not isinstance(bundle.get("fixtures"), list):
        return errors, warnings
    fixtures_text = manifest.get("fixtures_text")
    if not isinstance(fixtures_text, str) or not fixtures_text:
        errors.append("Manifest fixtures_text must preserve the captured fixture source.")
    else:
        if manifest.get("fixtures_sha256") != sha256_text(fixtures_text):
            errors.append("Manifest fixtures_sha256 does not match fixtures_text.")
        try:
            parsed_fixture_source = json.loads(fixtures_text)
        except json.JSONDecodeError as exc:
            errors.append(f"Manifest fixtures_text is invalid JSON: {exc}")
        else:
            if parsed_fixture_source != bundle:
                errors.append("Manifest fixture_bundle differs from fixtures_text.")
    fixture_by_id = {fixture["id"]: fixture for fixture in bundle["fixtures"]}
    rules_text = manifest.get("rules_text")
    rules_hash = manifest.get("rules_sha256")
    if not isinstance(rules_text, str) or not rules_text:
        errors.append("Manifest rules_text must be a non-empty string.")
    elif rules_hash != sha256_text(rules_text):
        errors.append("Manifest rules_sha256 does not match rules_text.")
    settings = manifest.get("settings")
    if not isinstance(settings, dict):
        errors.append("Manifest settings must be an object.")
        settings = {}
    fixed_settings = {
        "safe_mode": True,
        "new_empty_working_directory_per_call": True,
        "session_persistence": False,
        "tools": "disabled",
        "chrome": False,
        "strict_mcp_config": True,
        "prompt_suggestions": False,
        "output_format": "json",
        "condition_baseline": "Claude Code built-in prompt under the recorded CLI invocation",
        "condition_treatment": "Baseline plus exact rules_text through --append-system-prompt",
    }
    for field, expected in fixed_settings.items():
        if settings.get(field) != expected:
            errors.append(f"Manifest settings.{field} differs from the capture protocol.")
    if not isinstance(settings.get("requested_model"), str) or not settings.get(
        "requested_model"
    ):
        errors.append("Manifest requested_model must be a non-empty string.")
    if settings.get("effort") not in {"low", "medium", "high", "xhigh", "max"}:
        errors.append("Manifest effort is invalid.")
    if not isinstance(settings.get("random_seed"), int) or isinstance(
        settings.get("random_seed"), bool
    ):
        errors.append("Manifest random_seed must be an integer.")
    if not isinstance(settings.get("timeout_seconds_per_call"), int) or settings.get(
        "timeout_seconds_per_call", 0
    ) <= 0:
        errors.append("Manifest timeout_seconds_per_call must be a positive integer.")
    cli = manifest.get("claude_cli")
    if not isinstance(cli, dict) or not isinstance(cli.get("executable"), str) or not cli.get(
        "executable"
    ):
        errors.append("Manifest claude_cli.executable must be a non-empty string.")
    plan = manifest.get("planned_calls")
    if not isinstance(plan, list):
        errors.append("Manifest planned_calls must be an array.")
        plan = []
    try:
        expected_plan = make_call_plan(
            bundle["fixtures"],
            seed=manifest["settings"]["random_seed"],
            executable=Path(manifest["claude_cli"]["executable"]),
            model=manifest["settings"]["requested_model"],
            effort=manifest["settings"]["effort"],
            rules_text=manifest["rules_text"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"Manifest cannot reconstruct the planned calls: {exc}")
        expected_plan = []
    if plan != expected_plan:
        errors.append(
            "Manifest planned_calls differ from the plan recomputed from fixtures, seed, "
            "CLI, model, effort, and rules."
        )
    plan_keys = [
        (
            item.get("sequence"),
            item.get("fixture_id"),
            item.get("condition"),
            item.get("blind_label"),
            item.get("argv_sha256"),
        )
        for item in plan
        if isinstance(item, dict)
    ]
    record_keys = [
        (
            item.get("sequence"),
            item.get("fixture_id"),
            item.get("condition"),
            item.get("blind_label"),
            item.get("argv_sha256"),
        )
        for item in records
    ]
    if len(record_keys) != len(set(record_keys)):
        errors.append("Run contains duplicate sequence/fixture/condition records.")
    unexpected = [key for key in record_keys if key not in plan_keys]
    if unexpected:
        errors.append(f"Run contains records outside the call plan: {unexpected}")
    missing = [key for key in plan_keys if key not in record_keys]
    if missing:
        warnings.append(f"Run is missing {len(missing)} planned call record(s).")
    for record in records:
        try:
            errors.extend(
                validate_record_integrity(
                    record, manifest=manifest, fixture_by_id=fixture_by_id
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                f"record sequence {record.get('sequence')}: could not recompute expected fields: {exc}"
            )
    recomputed_successes = sum(
        int(derived_raw_fields(record)["capture_ok"]) for record in records
    )
    if manifest.get("attempted_call_count") != len(records):
        errors.append(
            "Manifest attempted_call_count does not match the number of raw records."
        )
    if manifest.get("successful_capture_count") != recomputed_successes:
        errors.append(
            "Manifest successful_capture_count does not match capture_ok recomputed from raw records."
        )
    failed = [record for record in records if not derived_raw_fields(record)["capture_ok"]]
    if failed:
        warnings.append(f"{len(failed)} captured call record(s) recompute as failed.")
    status = manifest.get("status")
    if len(records) == len(plan):
        expected_status = "completed" if not failed else "completed_with_failures"
        if status != expected_status:
            errors.append(
                f"Manifest status is {status!r}; raw records require {expected_status!r}."
            )
    elif status not in {"in_progress", "interrupted", "aborted_internal_error"}:
        errors.append(
            f"Manifest status {status!r} is invalid for an incomplete call plan."
        )
    if status != "completed":
        warnings.append(f"Manifest status is {status!r}, not 'completed'.")
    if require_complete and warnings:
        errors.extend(warnings)
        warnings = []
    return errors, warnings


def canonical_records(
    manifest: dict[str, Any], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rebuild validated identities and report fields from the raw records."""
    fixture_by_id = {
        fixture["id"]: fixture for fixture in manifest["fixture_bundle"]["fixtures"]
    }
    call_by_sequence = {
        call["sequence"]: call for call in manifest["planned_calls"]
    }
    return [
        recomputed_record(
            record,
            manifest=manifest,
            fixture=fixture_by_id[record["fixture_id"]],
            planned_call=call_by_sequence[record["sequence"]],
        )
        for record in records
    ]


def command_validate(args: argparse.Namespace) -> int:
    fixtures_path = Path(args.fixtures).resolve()
    bundle = load_and_validate_fixtures(fixtures_path)
    rules_bytes = read_bytes(RULES_PATH)
    if not rules_bytes.strip():
        raise EvalError(f"Treatment rules file is empty: {RULES_PATH}")
    print(
        f"fixtures: PASS ({len(bundle['fixtures'])} frozen synthetic fixtures, "
        f"sha256 {sha256_bytes(read_bytes(fixtures_path))})"
    )
    print(f"rules: PASS (sha256 {sha256_bytes(rules_bytes)})")
    if args.run:
        run_dir = Path(args.run).resolve()
        manifest, records = load_run(run_dir)
        errors, warnings = run_validation_errors(manifest, records, require_complete=True)
        if errors:
            raise EvalError("Run validation failed:\n- " + "\n- ".join(errors))
        for warning in warnings:
            print(f"warning: {warning}")
        print(f"run: PASS ({manifest['run_id']}, {len(records)} captured records)")
    return 0


def records_by_fixture(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        fixture_id = record.get("fixture_id")
        condition = record.get("condition")
        if isinstance(fixture_id, str) and isinstance(condition, str):
            grouped.setdefault(fixture_id, {})[condition] = record
    return grouped


def make_review_template(
    manifest: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    errors, _warnings = run_validation_errors(manifest, records, require_complete=True)
    if errors:
        raise EvalError(
            "A complete, valid captured run is required for a review sheet:\n- "
            + "\n- ".join(errors)
        )
    records = canonical_records(manifest, records)
    bundle = manifest["fixture_bundle"]
    grouped = records_by_fixture(records)
    pairs: list[dict[str, Any]] = []
    for fixture in bundle["fixtures"]:
        condition_records = grouped[fixture["id"]]
        outputs = []
        for record in sorted(condition_records.values(), key=lambda item: item["blind_label"]):
            outputs.append(
                {
                    "blind_label": record["blind_label"],
                    "response_text_sha256": record["response_text_sha256"],
                    "text": record["response_text"],
                    "answered_request": None,
                    "required_points": [
                        {"point": point, "met": None} for point in fixture["required_points"]
                    ],
                    "unsupported_conclusions": [
                        {"conclusion": conclusion, "present": None}
                        for conclusion in fixture["unsupported_conclusions"]
                    ],
                    "other_unsupported_claims": [],
                    "depth": None,
                    "notes": "",
                }
            )
        pairs.append(
            {
                "fixture_id": fixture["id"],
                "category": fixture["category"],
                "prompt": fixture["prompt"],
                "allowed_facts": fixture["allowed_facts"],
                "unsupported_conclusions": fixture["unsupported_conclusions"],
                "required_points": fixture["required_points"],
                "expected_depth": fixture["expected_depth"],
                "outputs": outputs,
                "preferred_output": None,
                "preference_reason": "",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "status": "template_not_reviewed",
        "reviewer": None,
        "reviewed_at_utc": None,
        "method": (
            "Condition names are omitted from this sheet. Style may still reveal the condition. "
            "Truth, completeness, and depth are human judgments."
        ),
        "pairs": pairs,
    }


def command_review_template(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).resolve()
    manifest, records = load_run(run_dir)
    template = make_review_template(manifest, records)
    output = Path(args.output).resolve() if args.output else run_dir / "human-review.template.json"
    if output.exists() and not args.force:
        raise EvalError(f"Review template already exists: {output}. Use --force to replace it.")
    write_json_atomic(output, template)
    print(f"Review template: {output}")
    print("No human judgments were recorded. Copy it to human-review.json and complete every field.")
    return 0


def review_validation_errors(
    review: Any, manifest: dict[str, Any], records: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(review, dict):
        return ["Human review must be a JSON object."]
    if review.get("schema_version") != SCHEMA_VERSION:
        errors.append("Human review schema_version is invalid.")
    if review.get("run_id") != manifest.get("run_id"):
        errors.append("Human review run_id does not match the manifest.")
    if review.get("status") != "completed":
        errors.append("Human review status must be 'completed'.")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        errors.append("Human review reviewer must be a non-empty string.")
    if not isinstance(review.get("reviewed_at_utc"), str) or not review["reviewed_at_utc"].strip():
        errors.append("Human review reviewed_at_utc must be a non-empty string.")
    pairs = review.get("pairs")
    if not isinstance(pairs, list):
        return errors + ["Human review pairs must be an array."]
    bundle = manifest["fixture_bundle"]
    fixture_by_id = {fixture["id"]: fixture for fixture in bundle["fixtures"]}
    grouped = records_by_fixture(records)
    expected_ids = set(fixture_by_id)
    pair_ids = [
        pair.get("fixture_id") for pair in pairs if isinstance(pair, dict)
    ]
    string_pair_ids = [item for item in pair_ids if isinstance(item, str)]
    counts = Counter(string_pair_ids)
    duplicates = sorted(fixture_id for fixture_id, count in counts.items() if count != 1)
    if duplicates:
        errors.append(
            "Human review must contain exactly one pair per fixture; duplicate fixture ids: "
            + ", ".join(duplicates)
        )
    found_ids = set(string_pair_ids)
    if found_ids != expected_ids or len(pairs) != len(expected_ids):
        errors.append("Human review fixture ids do not exactly match the captured fixture set.")
    for pair in pairs:
        if not isinstance(pair, dict):
            errors.append("Every human review pair must be an object.")
            continue
        fixture_id = pair.get("fixture_id")
        fixture = fixture_by_id.get(fixture_id)
        if fixture is None:
            continue
        prefix = f"review pair {fixture_id}"
        for field in ("category", "prompt", "allowed_facts", "unsupported_conclusions", "required_points", "expected_depth"):
            if pair.get(field) != fixture.get(field):
                errors.append(f"{prefix}: {field} differs from the captured fixture snapshot")
        outputs = pair.get("outputs")
        if not isinstance(outputs, list) or len(outputs) != 2:
            errors.append(f"{prefix}: outputs must contain exactly two objects")
            continue
        raw_by_label = {
            record["blind_label"]: record for record in grouped.get(fixture_id, {}).values()
        }
        labels = {item.get("blind_label") for item in outputs if isinstance(item, dict)}
        if labels != {"A", "B"}:
            errors.append(f"{prefix}: outputs must contain labels A and B exactly once")
        for output in outputs:
            if not isinstance(output, dict):
                errors.append(f"{prefix}: output review must be an object")
                continue
            label = output.get("blind_label")
            raw = raw_by_label.get(label)
            output_prefix = f"{prefix} output {label}"
            if raw is None:
                errors.append(f"{output_prefix}: label does not map to a captured output")
                continue
            if output.get("text") != raw.get("response_text"):
                errors.append(f"{output_prefix}: text differs from the captured response")
            if output.get("response_text_sha256") != raw.get("response_text_sha256"):
                errors.append(f"{output_prefix}: response hash differs from the captured response")
            if output.get("answered_request") not in ANSWER_VALUES:
                errors.append(f"{output_prefix}: answered_request must be yes, partial, or no")
            if output.get("depth") not in REVIEW_DEPTH_VALUES:
                errors.append(
                    f"{output_prefix}: depth must be too_short, appropriate, or too_long"
                )
            required_reviews = output.get("required_points")
            expected_required = fixture["required_points"]
            if not isinstance(required_reviews, list) or len(required_reviews) != len(expected_required):
                errors.append(f"{output_prefix}: required_points review shape is invalid")
            else:
                for expected, item in zip(expected_required, required_reviews):
                    if not isinstance(item, dict) or item.get("point") != expected or not isinstance(item.get("met"), bool):
                        errors.append(f"{output_prefix}: required point judgments are incomplete or altered")
                        break
            unsupported_reviews = output.get("unsupported_conclusions")
            expected_unsupported = fixture["unsupported_conclusions"]
            if not isinstance(unsupported_reviews, list) or len(unsupported_reviews) != len(expected_unsupported):
                errors.append(f"{output_prefix}: unsupported_conclusions review shape is invalid")
            else:
                for expected, item in zip(expected_unsupported, unsupported_reviews):
                    if not isinstance(item, dict) or item.get("conclusion") != expected or not isinstance(item.get("present"), bool):
                        errors.append(f"{output_prefix}: unsupported conclusion judgments are incomplete or altered")
                        break
            other = output.get("other_unsupported_claims")
            if not isinstance(other, list) or not all(isinstance(item, str) and item.strip() for item in other):
                errors.append(f"{output_prefix}: other_unsupported_claims must be a string array")
            if not isinstance(output.get("notes"), str):
                errors.append(f"{output_prefix}: notes must be a string")
        if pair.get("preferred_output") not in PREFERENCE_VALUES:
            errors.append(f"{prefix}: preferred_output must be A, B, or tie")
        if not isinstance(pair.get("preference_reason"), str):
            errors.append(f"{prefix}: preference_reason must be a string")
    return errors


def reported_token(record: dict[str, Any], field: str) -> int | None:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def input_component_tokens(record: dict[str, Any]) -> int | None:
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    values = [reported_token(record, field) for field in fields]
    available = [value for value in values if value is not None]
    return sum(available) if available else None


def _sum_available(values: Iterable[int | None]) -> int | None:
    available = [value for value in values if value is not None]
    return sum(available) if available else None


def _fmt_number(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fenced_text(text: str) -> str:
    runs = [len(run) for run in re.findall(r"`+", text)]
    fence = "`" * max(3, (max(runs) + 1) if runs else 3)
    return f"{fence}\n{text}\n{fence}"


def deterministic_aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for condition in ("baseline", "treatment"):
        selected = [
            record
            for record in records
            if record.get("condition") == condition
            and record.get("capture_ok") is True
            and isinstance(record.get("response_text"), str)
        ]
        metrics = [surface_metrics(record["response_text"]) for record in selected]
        words = [item["words"] for item in metrics]
        aggregate[condition] = {
            "captured_outputs": len(selected),
            "median_words": statistics.median(words) if words else None,
            "total_words": sum(words),
            "total_surface_matches": sum(item["surface_match_total"] for item in metrics),
            "total_headings_surface": sum(item["headings_surface"] for item in metrics),
            "total_list_items_surface": sum(item["list_items_surface"] for item in metrics),
            "total_em_dash": sum(item["punctuation"]["em_dash"] for item in metrics),
            "total_en_dash": sum(item["punctuation"]["en_dash"] for item in metrics),
            "total_exclamation": sum(item["punctuation"]["exclamation"] for item in metrics),
            "pattern_match_totals": {
                name: sum(item["pattern_matches"][name] for item in metrics)
                for name, _pattern in SURFACE_PATTERNS
            },
            "reported_input_components": _sum_available(
                input_component_tokens(record) for record in selected
            ),
            "reported_output_tokens": _sum_available(
                reported_token(record, "output_tokens") for record in selected
            ),
        }
    return aggregate


def human_aggregate(
    review: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    grouped = records_by_fixture(records)
    conditions: dict[str, dict[str, Any]] = {
        condition: {
            "answered": {value: 0 for value in sorted(ANSWER_VALUES)},
            "required_met": 0,
            "required_total": 0,
            "predeclared_unsupported_present": 0,
            "other_unsupported_claims": 0,
            "depth": {value: 0 for value in sorted(REVIEW_DEPTH_VALUES)},
        }
        for condition in ("baseline", "treatment")
    }
    preferences = {"baseline": 0, "treatment": 0, "tie": 0}
    for pair in review["pairs"]:
        label_to_condition = {
            record["blind_label"]: record["condition"]
            for record in grouped[pair["fixture_id"]].values()
        }
        for output in pair["outputs"]:
            condition = label_to_condition[output["blind_label"]]
            target = conditions[condition]
            target["answered"][output["answered_request"]] += 1
            target["required_met"] += sum(
                int(item["met"]) for item in output["required_points"]
            )
            target["required_total"] += len(output["required_points"])
            target["predeclared_unsupported_present"] += sum(
                int(item["present"]) for item in output["unsupported_conclusions"]
            )
            target["other_unsupported_claims"] += len(output["other_unsupported_claims"])
            target["depth"][output["depth"]] += 1
        preferred = pair["preferred_output"]
        if preferred == "tie":
            preferences["tie"] += 1
        else:
            preferences[label_to_condition[preferred]] += 1
    return {"conditions": conditions, "preferences": preferences}


def build_report_markdown(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    review: dict[str, Any] | None,
) -> str:
    errors, warnings = run_validation_errors(manifest, records, require_complete=False)
    if errors:
        raise EvalError("Run integrity validation failed:\n- " + "\n- ".join(errors))
    records = canonical_records(manifest, records)
    bundle = manifest["fixture_bundle"]
    aggregate = deterministic_aggregate(records)
    grouped = records_by_fixture(records)
    actual_models = sorted(
        {
            model
            for record in records
            for model in record.get("returned_model_identifiers", [])
            if isinstance(model, str)
        }
    )
    lines = [
        "# Captured paired evaluation report",
        "",
        (
            "This report describes one captured Claude CLI run over a frozen synthetic fixture "
            "set. It is not a model benchmark, a pass-rate estimate, or evidence of behavior "
            "on another Claude surface. Repeating the procedure may produce different text."
        ),
        "",
        "## Run record",
        "",
        f"- Run ID: `{manifest.get('run_id')}`",
        f"- Status: `{manifest.get('status')}`",
        f"- Capture started: `{manifest.get('capture_started_at_utc')}`",
        f"- Capture completed: `{manifest.get('capture_completed_at_utc')}`",
        f"- Repository commit: `{manifest.get('repo', {}).get('commit')}`",
        f"- Dirty worktree at capture: `{manifest.get('repo', {}).get('dirty')}`",
        f"- Claude CLI: `{manifest.get('claude_cli', {}).get('version_call', {}).get('stdout', '').strip()}`",
        f"- Requested model: `{manifest.get('settings', {}).get('requested_model')}`",
        "- Returned model identifier(s): "
        + (", ".join(f"`{model}`" for model in actual_models) if actual_models else "not exposed in captured JSON"),
        f"- Effort: `{manifest.get('settings', {}).get('effort')}`",
        f"- Fixture set: `{bundle.get('fixture_set_id')}` ({len(bundle.get('fixtures', []))} prompts)",
        f"- Fixture SHA-256: `{manifest.get('fixtures_sha256')}`",
        f"- Rules SHA-256: `{manifest.get('rules_sha256')}`",
        "",
        bundle.get("disclosure", ""),
        "",
    ]
    if manifest.get("kind") == "published_redacted_claude_cli_pair_run":
        lines.extend(
            [
                "Publication copy: executable and ephemeral working-directory paths were normalized. Raw prompt, stdout, stderr, and response text were not edited.",
                "",
            ]
        )
    if warnings:
        lines.extend(["### Capture limitations", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    lines.extend(
        [
            "## Deterministic surface measurements",
            "",
            (
                "These counts come directly from the captured response text. Surface patterns "
                "do not establish truth, completeness, quality, authorship, or rhetorical function."
            ),
            "",
            "| Measure | Baseline | Treatment |",
            "| --- | ---: | ---: |",
        ]
    )
    row_specs = (
        ("Captured outputs", "captured_outputs"),
        ("Median words", "median_words"),
        ("Total words", "total_words"),
        ("Pattern and banned-punctuation matches", "total_surface_matches"),
        ("Markdown heading lines", "total_headings_surface"),
        ("Markdown list-item lines", "total_list_items_surface"),
        ("Em dashes", "total_em_dash"),
        ("En dashes", "total_en_dash"),
        ("Exclamation marks", "total_exclamation"),
        ("Reported input-token components", "reported_input_components"),
        ("Reported output tokens", "reported_output_tokens"),
    )
    for label, key in row_specs:
        lines.append(
            f"| {label} | {_fmt_number(aggregate['baseline'][key])} | "
            f"{_fmt_number(aggregate['treatment'][key])} |"
        )

    lines.extend(
        [
            "",
            "### Literal pattern matches",
            "",
            "| Configured surface pattern | Baseline | Treatment |",
            "| --- | ---: | ---: |",
        ]
    )
    for name, _pattern in SURFACE_PATTERNS:
        lines.append(
            f"| `{name}` | {aggregate['baseline']['pattern_match_totals'][name]} | "
            f"{aggregate['treatment']['pattern_match_totals'][name]} |"
        )

    complete_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for fixture in bundle["fixtures"]:
        pair = grouped.get(fixture["id"], {})
        baseline = pair.get("baseline")
        treatment = pair.get("treatment")
        if (
            baseline
            and treatment
            and baseline.get("capture_ok") is True
            and treatment.get("capture_ok") is True
        ):
            complete_pairs.append((baseline, treatment))
    word_comparison = {"treatment": 0, "baseline": 0, "tie": 0}
    surface_comparison = {"treatment": 0, "baseline": 0, "tie": 0}
    for baseline, treatment in complete_pairs:
        base_metrics = surface_metrics(baseline["response_text"])
        treatment_metrics = surface_metrics(treatment["response_text"])
        if treatment_metrics["words"] < base_metrics["words"]:
            word_comparison["treatment"] += 1
        elif treatment_metrics["words"] > base_metrics["words"]:
            word_comparison["baseline"] += 1
        else:
            word_comparison["tie"] += 1
        if treatment_metrics["surface_match_total"] < base_metrics["surface_match_total"]:
            surface_comparison["treatment"] += 1
        elif treatment_metrics["surface_match_total"] > base_metrics["surface_match_total"]:
            surface_comparison["baseline"] += 1
        else:
            surface_comparison["tie"] += 1
    lines.extend(
        [
            "",
            f"Complete captured pairs: {len(complete_pairs)}.",
            "",
            (
                f"Word count was lower for treatment in {word_comparison['treatment']} pairs, "
                f"lower for baseline in {word_comparison['baseline']}, and tied in {word_comparison['tie']}."
            ),
            "",
            (
                f"Literal surface-match count was lower for treatment in {surface_comparison['treatment']} pairs, "
                f"lower for baseline in {surface_comparison['baseline']}, and tied in {surface_comparison['tie']}."
            ),
            "",
            "### Pair-level surface counts",
            "",
            "| Fixture | Baseline words | Treatment words | Baseline matches | Treatment matches |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for fixture in bundle["fixtures"]:
        pair = grouped.get(fixture["id"], {})
        row: list[str] = []
        for condition in ("baseline", "treatment"):
            record = pair.get(condition)
            if record and record.get("capture_ok") is True:
                metrics = surface_metrics(record["response_text"])
                row.extend([str(metrics["words"]), str(metrics["surface_match_total"])])
            else:
                row.extend(["n/a", "n/a"])
        lines.append(
            f"| `{_markdown_cell(fixture['id'])}` | {row[0]} | {row[2]} | {row[1]} | {row[3]} |"
        )

    lines.extend(["", "## Human truth and depth review", ""])
    if review is None:
        lines.extend(
            [
                "No completed human review was supplied. This report makes no aggregate claim about truth, completeness, appropriate depth, or preference.",
                "",
            ]
        )
    else:
        review_errors = review_validation_errors(review, manifest, records)
        if review_errors:
            raise EvalError(
                "Human review validation failed:\n- " + "\n- ".join(review_errors)
            )
        human = human_aggregate(review, records)
        lines.extend(
            [
                f"Reviewer: `{review['reviewer']}`. Reviewed at: `{review['reviewed_at_utc']}`.",
                "",
                "These are disclosed human judgments, not deterministic measurements.",
                "",
                "| Judgment | Baseline | Treatment |",
                "| --- | ---: | ---: |",
            ]
        )
        for label, key in (("Answered: yes", "yes"), ("Answered: partial", "partial"), ("Answered: no", "no")):
            lines.append(
                f"| {label} | {human['conditions']['baseline']['answered'][key]} | "
                f"{human['conditions']['treatment']['answered'][key]} |"
            )
        lines.append(
            f"| Required points met | "
            f"{human['conditions']['baseline']['required_met']}/{human['conditions']['baseline']['required_total']} | "
            f"{human['conditions']['treatment']['required_met']}/{human['conditions']['treatment']['required_total']} |"
        )
        lines.append(
            f"| Predeclared unsupported conclusions present | "
            f"{human['conditions']['baseline']['predeclared_unsupported_present']} | "
            f"{human['conditions']['treatment']['predeclared_unsupported_present']} |"
        )
        lines.append(
            f"| Other unsupported claims noted | "
            f"{human['conditions']['baseline']['other_unsupported_claims']} | "
            f"{human['conditions']['treatment']['other_unsupported_claims']} |"
        )
        for label, key in (("Depth: appropriate", "appropriate"), ("Depth: too short", "too_short"), ("Depth: too long", "too_long")):
            lines.append(
                f"| {label} | {human['conditions']['baseline']['depth'][key]} | "
                f"{human['conditions']['treatment']['depth'][key]} |"
            )
        lines.extend(
            [
                "",
                (
                    f"Preference: treatment {human['preferences']['treatment']}, "
                    f"baseline {human['preferences']['baseline']}, ties {human['preferences']['tie']}."
                ),
                "",
            ]
        )

    lines.extend(["## Captured outputs", ""])
    for fixture in bundle["fixtures"]:
        lines.extend(
            [
                f"### `{fixture['id']}`",
                "",
                f"Synthetic fixture category: `{fixture['category']}`.",
                "",
                f"Prompt SHA-256: `{sha256_text(fixture['prompt'])}`.",
                "",
                "Frozen synthetic prompt:",
                "",
                _fenced_text(fixture["prompt"]),
                "",
            ]
        )
        pair = grouped.get(fixture["id"], {})
        for condition in ("baseline", "treatment"):
            record = pair.get(condition)
            lines.extend([f"#### Captured {condition} output", ""])
            if record is None:
                lines.extend(["No record was captured.", ""])
                continue
            lines.append(f"Capture status: `{'ok' if record.get('capture_ok') else 'failed'}`.")
            lines.append("")
            if isinstance(record.get("response_text"), str):
                lines.append(f"Response SHA-256: `{record.get('response_text_sha256')}`.")
                lines.append("")
                lines.append(_fenced_text(record["response_text"]))
                lines.append("")
            else:
                lines.extend(
                    [
                        "No response text was parsed. Inspect the raw stdout and stderr record in `responses.jsonl`.",
                        "",
                    ]
                )
    lines.extend(
        [
            "## Evidence files",
            "",
            "- `manifest.json` contains the frozen inputs, hashes, settings, CLI record, and call plan.",
            (
                "- `responses.jsonl` contains the raw CLI output and path-normalized argument vector for every attempted call."
                if manifest.get("kind") == "published_redacted_claude_cli_pair_run"
                else "- `responses.jsonl` contains the raw CLI output and exact argument vector for every attempted call."
            ),
        ]
    )
    if review is not None:
        lines.append("- `human-review.json` contains the disclosed human judgments used above.")
    lines.append("")
    return "\n".join(lines)


def command_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).resolve()
    manifest, records = load_run(run_dir)
    review_path: Path | None
    if args.review:
        review_path = Path(args.review).resolve()
    else:
        default_review = run_dir / "human-review.json"
        review_path = default_review if default_review.is_file() else None
    review = read_json(review_path) if review_path else None
    markdown = build_report_markdown(manifest, records, review)
    output = Path(args.output).resolve() if args.output else run_dir / "report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"Report: {output}")
    if review is None:
        print("Human review: not supplied; no truth, completeness, depth, or preference aggregate was generated.")
    return 0


def iter_string_values(value: Any, location: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_string_values(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_string_values(item, f"{location}[{index}]")


def publication_findings(value: Any) -> list[dict[str, str]]:
    """Find possible secrets or local absolute paths without echoing matches."""
    findings: list[dict[str, str]] = []
    for location, text in iter_string_values(value):
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"location": location, "kind": "secret", "pattern": name})
        for name, pattern in LOCAL_PATH_PATTERNS:
            if pattern.search(text):
                findings.append({"location": location, "kind": "local_path", "pattern": name})
    unique = {
        (item["location"], item["kind"], item["pattern"]): item for item in findings
    }
    return [unique[key] for key in sorted(unique)]


def public_run_copy(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    source_manifest_sha256: str,
    source_responses_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create a disclosed path-neutral copy without changing raw model output."""
    public_manifest = copy.deepcopy(manifest)
    public_manifest["kind"] = "published_redacted_claude_cli_pair_run"
    public_manifest["claim_scope"] = (
        "This is a path-neutral publication copy of one captured CLI run. Raw stdout, "
        "stderr, prompts, and response text are unchanged. Executable and temporary-directory "
        "paths were normalized and do not reproduce the original local path strings."
    )
    public_manifest["fixtures_path_at_capture"] = "evals/fixtures.json"
    public_manifest["rules_path_at_capture"] = "rules.md"
    public_manifest["claude_cli"]["executable"] = "claude"
    version_argv = public_manifest["claude_cli"].get("version_call", {}).get("argv")
    if isinstance(version_argv, list) and version_argv:
        version_argv[0] = "claude"
    public_manifest["publication"] = {
        "created_at_utc": utc_now(),
        "source_manifest_sha256": source_manifest_sha256,
        "source_responses_sha256": source_responses_sha256,
        "path_transformations": [
            "claude executable path normalized to 'claude'",
            "ephemeral working-directory paths replaced with a placeholder",
            "fixture and rules source paths changed to repository-relative paths",
            "argv hashes recomputed after executable-path normalization",
        ],
        "raw_content_changed": False,
        "secret_and_local_path_scan": "passed before publication",
        "authenticity_limit": (
            "Embedded hashes detect inconsistent artifacts, not coordinated malicious edits. "
            "External commit or signature provenance is separate."
        ),
    }
    public_plan = make_call_plan(
        public_manifest["fixture_bundle"]["fixtures"],
        seed=public_manifest["settings"]["random_seed"],
        executable=Path("claude"),
        model=public_manifest["settings"]["requested_model"],
        effort=public_manifest["settings"]["effort"],
        rules_text=public_manifest["rules_text"],
    )
    public_manifest["planned_calls"] = public_plan
    public_call_by_sequence = {call["sequence"]: call for call in public_plan}
    fixture_by_id = {
        fixture["id"]: fixture
        for fixture in public_manifest["fixture_bundle"]["fixtures"]
    }
    public_records: list[dict[str, Any]] = []
    for source in records:
        public_record = copy.deepcopy(source)
        public_record["source_capture_argv_sha256"] = source["argv_sha256"]
        working_directory = public_record.get("isolated_working_directory")
        if isinstance(working_directory, dict):
            working_directory["path_during_call"] = "<new-empty-temporary-directory>"
        planned_call = public_call_by_sequence[source["sequence"]]
        public_record = recomputed_record(
            public_record,
            manifest=public_manifest,
            fixture=fixture_by_id[source["fixture_id"]],
            planned_call=planned_call,
        )
        public_records.append(public_record)
    return public_manifest, public_records


def assert_output_inside_evals(output: Path) -> None:
    try:
        output.resolve().relative_to(EVALS_DIR.resolve())
    except ValueError as exc:
        raise EvalError("Publication output must stay inside evals/.") from exc


def command_publish(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).resolve()
    manifest, raw_records = load_run(run_dir)
    errors, warnings = run_validation_errors(manifest, raw_records, require_complete=True)
    if errors:
        raise EvalError("Publication requires a complete valid run:\n- " + "\n- ".join(errors))
    if warnings:
        raise EvalError("Publication refused because the run has warnings.")
    if manifest.get("repo", {}).get("dirty") is not False:
        raise EvalError("Publication requires a run captured from a clean worktree.")
    records = canonical_records(manifest, raw_records)
    review_path = Path(args.review).resolve() if args.review else run_dir / "human-review.json"
    if not review_path.is_file():
        raise EvalError("Publication requires a completed human-review.json.")
    review = read_json(review_path)
    review_errors = review_validation_errors(review, manifest, records)
    if review_errors:
        raise EvalError("Human review validation failed:\n- " + "\n- ".join(review_errors))

    public_manifest, public_records = public_run_copy(
        manifest,
        records,
        source_manifest_sha256=sha256_bytes(read_bytes(run_dir / "manifest.json")),
        source_responses_sha256=sha256_bytes(read_bytes(run_dir / "responses.jsonl")),
    )
    public_errors, public_warnings = run_validation_errors(
        public_manifest, public_records, require_complete=True
    )
    if public_errors or public_warnings:
        raise EvalError(
            "Path-neutral publication copy failed integrity validation:\n- "
            + "\n- ".join(public_errors + public_warnings)
        )
    report = build_report_markdown(public_manifest, public_records, review)
    publish_payload = {
        "manifest": public_manifest,
        "responses": public_records,
        "human_review": review,
        "report": report,
    }
    findings = publication_findings(publish_payload)
    if findings:
        summary = "; ".join(
            f"{item['kind']}:{item['pattern']} at {item['location']}" for item in findings
        )
        raise EvalError(
            "Publication refused because suspicious content remains. Matches are named but not "
            f"echoed: {summary}"
        )

    output = (
        Path(args.output).resolve()
        if args.output
        else (EVALS_DIR / "public" / manifest["run_id"]).resolve()
    )
    assert_output_inside_evals(output)
    if output.exists():
        raise EvalError(f"Publication directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".publish-", dir=output.parent))
    try:
        write_json_atomic(staging / "manifest.json", public_manifest)
        for record in public_records:
            append_jsonl(staging / "responses.jsonl", record)
        write_json_atomic(staging / "human-review.json", review)
        (staging / "report.md").write_text(report, encoding="utf-8", newline="\n")
        scan_receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": "passed",
            "scanned_at_utc": utc_now(),
            "secret_patterns": [name for name, _pattern in SECRET_PATTERNS],
            "local_path_patterns": [name for name, _pattern in LOCAL_PATH_PATTERNS],
            "finding_count": 0,
        }
        write_json_atomic(staging / "publication-scan.json", scan_receipt)
        final_text = {
            path.name: read_text(path)
            for path in staging.iterdir()
            if path.is_file()
        }
        final_findings = publication_findings(final_text)
        if final_findings:
            raise EvalError(
                "Publication refused because the serialized artifact failed its final secret/path scan."
            )
        os.replace(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(f"Publication: {output}")
    print("Secret and absolute-local-path scan: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture and inspect paired claude-stfu CLI evaluations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate the frozen fixtures, rules source, and optionally a captured run."
    )
    validate_parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES_PATH))
    validate_parser.add_argument("--run", help="Captured run directory to validate.")
    validate_parser.set_defaults(handler=command_validate)

    capture_parser = subparsers.add_parser(
        "capture", help="Capture baseline and treatment CLI outputs in fresh sessions."
    )
    capture_parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES_PATH))
    capture_parser.add_argument("--model", required=True, help="Requested Claude model identifier.")
    capture_parser.add_argument(
        "--effort", default="low", choices=("low", "medium", "high", "xhigh", "max")
    )
    capture_parser.add_argument("--claude", default="claude", help="Claude executable name or path.")
    capture_parser.add_argument("--seed", type=int, default=20260903)
    capture_parser.add_argument("--timeout", type=int, default=180)
    capture_parser.add_argument("--run-id")
    capture_parser.add_argument("--dry-run", action="store_true")
    capture_parser.add_argument("--allow-dirty", action="store_true")
    capture_parser.set_defaults(handler=command_capture)

    review_parser = subparsers.add_parser(
        "review-template", help="Create an A/B human-review template from a complete captured run."
    )
    review_parser.add_argument("run")
    review_parser.add_argument("--output")
    review_parser.add_argument("--force", action="store_true")
    review_parser.set_defaults(handler=command_review_template)

    report_parser = subparsers.add_parser(
        "report", help="Generate deterministic counts and, when supplied, separate human review results."
    )
    report_parser.add_argument("run")
    report_parser.add_argument("--review")
    report_parser.add_argument("--output")
    report_parser.set_defaults(handler=command_report)

    publish_parser = subparsers.add_parser(
        "publish",
        help="Create a path-neutral, secret-scanned publication copy of a reviewed run.",
    )
    publish_parser.add_argument("run")
    publish_parser.add_argument("--review")
    publish_parser.add_argument("--output")
    publish_parser.set_defaults(handler=command_publish)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "timeout", 1) <= 0:
        parser.error("--timeout must be greater than zero")
    try:
        return args.handler(args)
    except EvalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
