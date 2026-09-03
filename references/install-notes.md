# Install notes

The README covers the common path. These details matter only when you are packaging the skill or using it across Claude surfaces.

## Claude Code

- Output styles are read when a session starts. After changing styles, run `/clear` or begin a new session.
- `keep-coding-instructions: true` in `output-style.md` preserves Claude Code's built-in software-engineering guidance.
- Output styles affect the main conversation. Separate subagents use their own system prompts.
- `/claude-stfu` invokes the skill directly. Automatic skill matching depends on Claude choosing it from the description.
- Claude Code may ask for approval the first time a `CLAUDE.md` imports a file outside the project.

## Claude app skill

Create a ZIP whose top-level folder is named `claude-stfu` and contains `SKILL.md` plus `references/`. Upload it through **Customize > Skills > Add > Create skill > Upload a skill**.

GitHub's source ZIP includes the branch in its folder name, so extract it, rename the folder to `claude-stfu`, and zip that folder before uploading.

## API

The Messages API is stateless. Include `rules.md` in the top-level `system` parameter on every request where you want it applied.

## Anthropic documentation

- [Output styles](https://code.claude.com/docs/en/output-styles)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)
- [Creating custom skills](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)
