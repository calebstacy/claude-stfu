# claude-stfu

Make Claude answer the question, cut the ass-kissing, and stop claiming it checked things it never checked.

`claude-stfu` is a reusable set of reply instructions for Claude Code, Claude, and the API. It tells Claude to lead with the answer, keep the useful detail, challenge bad assumptions, and be honest about what it actually did.

The goal isn't tiny answers. It's answers with no wasted motion.

## What it changes

- **Answer first.** No warm-up paragraph before the thing you asked for.
- **No automatic agreement.** Claude checks your premise instead of rewarding it.
- **No fake certainty.** It doesn't say "fixed," "verified," or "the logs show" unless it actually checked.
- **No work theater.** You get the result, not a play-by-play of routine tool calls.
- **No canned ending.** The reply stops when the answer is complete.
- **Enough detail to act.** Short when the question is simple, thorough when it isn't.

It also gives recurring bad habits names such as `praise opener`, `discovery theater`, and `premature done`, so you can point to the exact thing that annoyed you.

## Install in Claude Code

### Always on

macOS or Linux:

```bash
mkdir -p ~/.claude/skills ~/.claude/output-styles
git clone https://github.com/calebstacy/claude-stfu ~/.claude/skills/claude-stfu
cp ~/.claude/skills/claude-stfu/output-style.md ~/.claude/output-styles/claude-stfu.md
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills", "$HOME\.claude\output-styles"
git clone https://github.com/calebstacy/claude-stfu "$HOME\.claude\skills\claude-stfu"
Copy-Item "$HOME\.claude\skills\claude-stfu\output-style.md" "$HOME\.claude\output-styles\claude-stfu.md"
```

Run `/config`, choose **Output style**, select `claude-stfu`, then run `/clear` or start a new session.

This keeps Claude Code's normal coding instructions and changes how Claude replies in the main conversation.

### Only when you want it

The clone above also installs a skill. Run:

```text
/claude-stfu
```

### Through CLAUDE.md

Add this to `~/.claude/CLAUDE.md` for every project, or to one project's `CLAUDE.md`:

```text
@~/.claude/skills/claude-stfu/SKILL.md
```

See the [install notes](references/install-notes.md) for the less common session, subagent, ZIP, and API details.

## Claude app and API

In the Claude app, paste [`rules.md`](rules.md) into your account or project instructions.

For the API, send the same text in the top-level `system` parameter on each request.

## What's in the repo

- [`rules.md`](rules.md): the actual instructions.
- [`SKILL.md`](SKILL.md): the Claude skill.
- [`output-style.md`](output-style.md): the Claude Code output style.
- [`references/move-catalog.md`](references/move-catalog.md): names and definitions for the annoying moves.
- [`evals/`](evals/): frozen prompts and tooling for capturing real baseline and treatment outputs.

`rules.md` is the source of truth. After changing it, regenerate the two wrappers:

```bash
python scripts/generate_artifacts.py
python scripts/generate_artifacts.py --check
```

## Does it actually work?

It's a prompt, not a hard filter. Claude can still miss.

The first README used invented before-and-after examples. That's exactly the behavior this project is supposed to stop, so they're gone. [`evals/`](evals/) now captures the real prompt, model information, and raw outputs together. When there's a reviewed run worth publishing, it'll go here.

## Sources

See [lineage and sources](references/lineage.md).

## License

MIT.
