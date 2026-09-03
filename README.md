# claude-stfu

A set of prompt instructions intended to make Claude's replies shorter, more direct, and stricter about unsupported claims. It shapes generation. It does not verify facts or guarantee compliance.

No model pass rate is claimed. The repository includes a repeatable specimen-capture harness, but it does not publish behavioral results until a reviewed capture exists. Model outputs are not expected to reproduce byte for byte.

## What it targets

The rules tell Claude to:

- answer before explaining;
- match the depth of the question;
- distinguish observed facts from inference;
- never invent logs, tests, measurements, sources, or actions taken;
- report incomplete and unverified work plainly; and
- remove recurring reply patterns such as praise openers, automatic agreement, discovery theater, template roadmaps, and closing offers.

Each failure pattern has a name so a miss can be discussed directly. The detailed [move catalog](references/move-catalog.md) is supporting editorial guidance, not evidence that a runtime rule "fired."

## What it cannot guarantee

This is a prompt, not an enforcement layer. It cannot prove that a claim is true, force Claude to use a tool, or ensure that every reply follows every rule.

For deterministic checks on observable prose patterns, use [slop-no-more](https://github.com/calebstacy/slop-no-more). A clean scan still cannot establish truth, correctness, authorship, or writing quality.

Claude Code also ships a built-in [Concise output style](https://code.claude.com/docs/en/output-styles#built-in-output-styles) for short, result-first replies. `claude-stfu` adds a stricter evidence contract, correction behavior, and named failure modes.

## Install

### Claude Code output style

Clone the repository into your personal skills directory.

macOS or Linux:

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/calebstacy/claude-stfu ~/.claude/skills/claude-stfu
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills"
git clone https://github.com/calebstacy/claude-stfu "$HOME\.claude\skills\claude-stfu"
```

Copy the generated output style into Claude Code's personal output-style directory.

macOS or Linux:

```bash
mkdir -p ~/.claude/output-styles
cp ~/.claude/skills/claude-stfu/output-style.md ~/.claude/output-styles/claude-stfu.md
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\output-styles"
Copy-Item "$HOME\.claude\skills\claude-stfu\output-style.md" "$HOME\.claude\output-styles\claude-stfu.md"
```

Run `/config`, select **Output style**, choose `claude-stfu`, then run `/clear` or start a new session. Claude Code reads the selected style at session start.

The output style keeps Claude Code's built-in software-engineering instructions. It adds these reply instructions without removing the default guidance for scoping, security, comments, and verification.

### Claude Code skill

The clone above also installs `/claude-stfu` as a personal skill. Invoke it directly when you want the rules for the current conversation:

```text
/claude-stfu
```

Claude may also load it automatically when a request matches its description. Direct invocation is the reliable option.

### CLAUDE.md import

Add this line to `~/.claude/CLAUDE.md` for all local projects, or to a project's `CLAUDE.md` for that project:

```text
@~/.claude/skills/claude-stfu/SKILL.md
```

Claude Code may ask you to approve an external import the first time it sees one.

### Claude app

For account or project instructions, paste the contents of [`rules.md`](rules.md) into the relevant instructions field.

To install it as a custom skill, make a ZIP whose top-level folder is named `claude-stfu` and contains `SKILL.md` plus `references/`. Upload that ZIP through **Customize > Skills > Add > Create skill > Upload a skill**. GitHub's source ZIP uses a branch-suffixed folder name, so extract and re-zip it first.

An uploaded skill loads when Claude decides it is relevant. Account or project instructions are the better fit when you want the rules applied by default.

### API

Send the contents of [`rules.md`](rules.md) in the Messages API's top-level `system` parameter on every request where the behavior should apply. The API is stateless, so the instruction must be included again on the next request.

## Scope

The rules govern Claude's conversational replies and work reports. They do not impose the same voice on copy, fiction, documentation, or another artifact you ask Claude to author. Your explicit tone, format, and depth instructions override the style defaults. Truth, safety, permissions, and required completeness remain higher priority.

Output styles apply to Claude Code's main conversation. Ordinary subagents use their own system prompts.

## Evidence and evaluation

The [`evals/`](evals/) harness separates recorded behavior from marketing examples. A publishable specimen must include:

- the exact prompt and supplied evidence;
- the raw baseline and treatment outputs;
- the requested and returned model identifiers;
- the date, settings, repository commit, and rules hash;
- deterministic measurements such as word count and configured scanner findings; and
- a disclosed human review of correctness, unsupported claims, completeness, and depth.

One generation per condition is a specimen, not a benchmark or general model pass-rate estimate. Generated reports must quote the stored raw output without editing it.

Raw captures stay in the git-ignored `evals/runs/` directory because CLI diagnostics can contain local paths or secrets. The [evaluation protocol](evals/protocol.md) requires a completed human review, integrity validation, path normalization, and a secret scan before `evals/public/` can be committed.

Until the repository contains a published, reviewed run, it has no evidence for a before-and-after claim.

## Repository layout

- [`rules.md`](rules.md) is the canonical always-on instruction body.
- [`SKILL.md`](SKILL.md) is the generated Claude skill wrapper.
- [`output-style.md`](output-style.md) is the generated Claude Code output-style wrapper.
- [`references/move-catalog.md`](references/move-catalog.md) holds the detailed diagnostic vocabulary.
- [`evals/`](evals/) holds frozen prompts, captured runs, and the evaluation protocol.

Edit `rules.md`, then regenerate the two wrappers. Do not edit the generated files by hand.

```bash
python scripts/generate_artifacts.py
python scripts/generate_artifacts.py --check
```

## Tuning

- To enforce a word cap, add one with an explicit exception for requests that need more detail.
- To allow exclamation marks or emoji by default, change the corresponding voice rule in `rules.md`.
- When a phrase slips through, add a literal prohibition only if the phrase itself is the problem. When the underlying rhetorical move is the problem, update the move definition and preserve the reason.
- Keep observable checks separate from judgment. A regex can identify an em dash. It cannot decide whether an answer is correct or appropriately detailed.

## Lineage

The pack draws from the author's own Claude Code preferences and case-study claim rules (author-reported internal inputs), Anthropic's [output-style guidance](https://code.claude.com/docs/en/output-styles), the move catalog in [`slop-no-more` v0.2.1](https://github.com/calebstacy/slop-no-more/tree/v0.2.1), John Swales' 1990 [*Genre Analysis*](https://shop.cambridge.org/english/product/2700033225), and Ken Hyland's 2005 [*Metadiscourse*](https://www.bloomsbury.com/us/metadiscourse-9780826476104/).

Those sources informed the rules. They do not validate this pack's behavioral effect.

## License

MIT.
