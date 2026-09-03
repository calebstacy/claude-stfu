#!/usr/bin/env python3
"""Generate the Claude wrappers from the canonical rules.md body."""

from __future__ import annotations

import argparse
import difflib
import sys
import textwrap
from pathlib import Path


SKILL_DESCRIPTION = (
    "Make Claude replies direct, concise, evidence-bound, and free of sycophancy "
    "or filler. Use when replies are verbose, padded, overly agreeable, or overstate "
    "what was established."
)
OUTPUT_STYLE_DESCRIPTION = (
    "Direct, concise, evidence-bound replies without sycophancy or filler"
)


def skill_frontmatter() -> str:
    if len(SKILL_DESCRIPTION) > 200:
        raise ValueError("SKILL.md description exceeds 200 characters")

    description_lines = textwrap.wrap(
        SKILL_DESCRIPTION,
        width=76,
        break_long_words=False,
        break_on_hyphens=False,
    )
    folded_description = "\n".join(f"  {line}" for line in description_lines)
    return (
        "---\n"
        "name: claude-stfu\n"
        "description: >-\n"
        f"{folded_description}\n"
        "---\n\n"
    )


def output_style_frontmatter() -> str:
    return (
        "---\n"
        "name: claude-stfu\n"
        f"description: {OUTPUT_STYLE_DESCRIPTION}\n"
        "keep-coding-instructions: true\n"
        "---\n\n"
    )


def read_rules(path: Path) -> str:
    try:
        rules = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FileNotFoundError(f"canonical rules file is missing: {path}") from error
    except UnicodeDecodeError as error:
        raise ValueError(f"canonical rules file is not valid UTF-8: {path}") from error

    if rules.startswith("\ufeff"):
        rules = rules.removeprefix("\ufeff")
    return rules.replace("\r\n", "\n").replace("\r", "\n")


def rendered_artifacts(rules: str) -> dict[str, str]:
    return {
        "SKILL.md": skill_frontmatter() + rules,
        "output-style.md": output_style_frontmatter() + rules,
    }


def write_artifacts(repo_root: Path, artifacts: dict[str, str]) -> int:
    for relative_path, content in artifacts.items():
        destination = repo_root / relative_path
        expected = content.encode("utf-8")
        if destination.exists() and destination.read_bytes() == expected:
            print(f"unchanged: {relative_path}")
            continue
        with destination.open("w", encoding="utf-8", newline="\n") as artifact:
            artifact.write(content)
        print(f"wrote: {relative_path}")
    return 0


def check_artifacts(repo_root: Path, artifacts: dict[str, str]) -> int:
    drifted = False
    for relative_path, content in artifacts.items():
        destination = repo_root / relative_path
        expected = content.encode("utf-8")
        if not destination.exists():
            print(f"missing generated artifact: {relative_path}", file=sys.stderr)
            drifted = True
            continue

        actual = destination.read_bytes()
        if actual == expected:
            print(f"ok: {relative_path}")
            continue

        drifted = True
        print(f"generated artifact is out of date: {relative_path}", file=sys.stderr)
        if b"\r\n" in actual:
            print("  contains CRLF line endings; expected LF", file=sys.stderr)
        try:
            actual_text = actual.decode("utf-8")
        except UnicodeDecodeError:
            print("  file is not valid UTF-8", file=sys.stderr)
            continue

        diff = difflib.unified_diff(
            actual_text.replace("\r\n", "\n").replace("\r", "\n").splitlines(True),
            content.splitlines(True),
            fromfile=relative_path,
            tofile=f"generated/{relative_path}",
        )
        sys.stderr.writelines(diff)

    if drifted:
        print(
            "Run `python scripts/generate_artifacts.py` and commit the results.",
            file=sys.stderr,
        )
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate SKILL.md and output-style.md from rules.md."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated artifacts without changing files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        rules = read_rules(repo_root / "rules.md")
        artifacts = rendered_artifacts(rules)
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.check:
        return check_artifacts(repo_root, artifacts)
    return write_artifacts(repo_root, artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
