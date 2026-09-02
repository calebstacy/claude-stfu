# claude-stfu

A rule set that sits in front of Claude's replies. Put it where Claude reads before every turn and it shapes the draft: answer first, shortest complete reply, claims verified before they're asserted, none of the tells that mark text as machine-written. It governs how Claude talks to you. It is not an editor for text you hand it, and it is not a filter that runs after the fact.

## What changes

A question:

> **Before.** Great question! Redis can absolutely work as a queue, and it's a popular choice for many teams. Let's break down the key considerations: **Pros:** simple, fast, you already run it. **Cons:** no built-in acknowledgement with lists, limited visibility. Ultimately, it depends on your use case. Let me know if you'd like me to dive deeper into any of these!
>
> **After.** Use Redis Streams rather than a list if a consumer can die mid-job. Streams keep a pending list per consumer group, so an unacknowledged job can be claimed again; a plain list hands the job over on pop and forgets it. If you also need delays, retries with backoff, and dead-lettering, a broker (RabbitMQ, SQS) has them built in and you'd be writing all three yourself on Redis.

Agreement bait:

> **Before.** You're absolutely right, the middleware is the likely culprit here! Let me take a look and fix it.
>
> **After.** Not the middleware. The request never reaches the app: the gateway log shows the 401 at 14:02:11 and the app log has nothing at that timestamp. The gateway's client certificate expired yesterday; renewing it is the fix.

A task report:

> **Before.** I did a deep dive and discovered something really interesting! It turns out the config loader was silently swallowing errors, and this changes everything about how we should approach the fix. I've gone ahead and fixed it. Next, I'll add some tests. One thing to watch: a couple of callers may depend on the old behavior. Let me know if you'd like me to handle those!
>
> **After.** Fixed and verified. The config loader caught every exception and returned defaults, so a malformed YAML file looked like an empty one. It now re-raises on parse errors. Two new tests cover that; the full suite is 212 passing, 0 failing. Not done: two callers relied on the silent default, and whether a missing file should be fatal is your call.

A hedge:

> **Before.** It's worth noting that the retry logic may potentially cause some issues in certain edge cases, so you might want to consider possibly adding some safeguards.
>
> **After.** The retry loop has no cap, so a dead upstream gets hit every 200 ms until the process dies. Cap it at 5 attempts or 30 seconds, whichever comes first.

A short answer wearing a report:

> **Before.** ## Recommendation. **Option 1: cron job.** Simple, but less flexible. **Option 2: queue worker.** Scalable, but more infrastructure. In conclusion, both approaches have their tradeoffs.
>
> **After.** The cron job. The job runs once a day, and the queue version needs a worker you'd have to deploy and monitor for no gain at that rate.

## Install

Root install first. A skill loads when Claude decides it's relevant; root text is read every turn.

**Claude Code, as an output style.** This replaces Claude Code's default reply guidance with these rules.

```bash
git clone https://github.com/calebstacy/claude-stfu ~/.claude/skills/claude-stfu
```

```bash
cp ~/.claude/skills/claude-stfu/output-style.md ~/.claude/output-styles/claude-stfu.md
```

Then pick it with `/output-style claude-stfu`.

**Claude Code, as a CLAUDE.md import.** Add one line to `~/.claude/CLAUDE.md` (every project) or a project's `CLAUDE.md`. The frontmatter comes along as text and does no harm.

```
@~/.claude/skills/claude-stfu/SKILL.md
```

**claude.ai and the desktop app.** Paste the body of `SKILL.md` (everything after the second `---`) into the field your surface reads every turn: a Project's instructions, the account-level preferences under Settings, or a custom style. Uploading the repo as a skill (Settings, Capabilities) also works, and is weaker: the skill loads when you complain about verbosity, root text loads every turn.

**API.** Put the body in the `system` parameter.

**Claude Code, as a skill only.** The clone above is enough. Claude loads it when you say it's being verbose, sycophantic, or sounds like AI, then keeps it for the conversation.

## Why it sits in front of generation

A rule that runs after the draft (a Stop hook that blocks and asks for a rewrite, a scanner over the output) costs a second generation on every catch, and the rewrite starts from the shape of the draft it replaces. Rules in front of generation decide the shape once.

Rules in a prompt also leak. In [slop-no-more](https://github.com/calebstacy/slop-no-more)'s measurement, a one-line "no em dashes" instruction was followed on 7 of 10 test strings, and the same rule as a regex caught 10 of 10. That's why this pack is written at the move level, with the reason each move damages the argument: a bare ban gets paraphrased around, a mechanism is harder to route past. It's still not a guarantee, and no measurement of this pack's pass rate on chat replies exists yet. For a document where a hard gate matters, run slop-no-more over the output.

## Tuning

- Length. The pack sets no word cap; "shortest reply that fully answers" is the rule. Add a cap to your copy if you want one ("under 150 words unless asked for more").
- Emoji and exclamation marks are allowed only when the person uses them first. Change that line if you want them.
- The Never list is literal and short on purpose. When a phrase slips through, add the phrase. When a move slips through, add the move with the reason it damages the argument, not a phrase alone.

## Lineage

Distilled from four sources: the author's own Claude Code output style, in daily use since August 2026; Claude Code's built-in reply guidance (lead with the outcome, one idea per sentence, code out of prose); the move catalog in [slop-no-more](https://github.com/calebstacy/slop-no-more), which rests on Swales' move analysis (1990) and Hyland's metadiscourse work (2005); and the claim gates from a case-study writing skill (tense tracks the truth state, sequence is not causality, an ordinary example is not a revelation), generalized here to any claim Claude makes.

## License

MIT.
