# jev_lesson_gate

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that decides **when** the background
memory/skill review should run. It replaces Hermes's fixed every-N-turns clock with a per-turn judgment.

Hermes's review runs on a counter: every 10 user turns for memory, every 10 tool iterations for skills. The counter can't tell
whether anything was learned. This plugin asks a small structured-decision model,
[Jev](https://openrouter.ai/typesafe/jev-1.13) (`typesafe/jev-1.13` via OpenRouter's Decisions API), after every turn:

| Question | Reads | Feeds |
|---|---|---|
| `corrects_assistant`: is the user correcting how the agent worked, what it assumed or what it forgot? | last assistant message + user message | memory **and** skills |
| `reveals_lasting_fact`: did the user reveal something lasting about themselves, their setup or their goal? | same | memory |
| `discovered_in_work`: did the agent find a fix, a pitfall, or a working method through its own work? | a compact summary of the turn's tool calls and results | skills |

It then returns `{"review": [...]}` to run a review the clock would have missed. When Hermes has
`auxiliary.background_review.judgment_can_skip: true`, it can also return `{"skip": [...]}` to drop a clock review. It skips only
when it judged **every** turn since that kind's last review and found no lesson. Any failure, timeout or unjudged
turn means it skips nothing, and the clock still runs.

Requires the `request_background_review` hook proposed in NousResearch/hermes-agent (PR link to be added).

## Measured (small samples; read the caveats)

- **User-side gate:** AUC **0.92** [0.84–0.97] on 57 hand-labelled turns from real coding-agent sessions (18 lessons).
  A free 120B chat model given the same definition scored 0.77. ⚠️ The questions were written after
  seeing these labels. A held-out set is pending.
- **Work-side gate:** AUC 0.90 on 20 review windows. ⚠️ In-sample.
- **Why skipping is per kind:** we ran Hermes's real review 30 times on windows where the user taught nothing
  (`nvidia/nemotron-3-ultra-550b-a55b`, Hermes default settings). 90% of runs wrote something. Of those 57 writes, 30 were real
  lessons found through the work, 20 were junk and 7 were duplicates. **20 of the 22 memory writes were junk or duplicates**, while 28 of the 35
  skill writes were real. Replayed over those writes (in-sample), the gate keeps 27 real and 2 junk, and drops 25 bad and 3 real.
- **Cost:** about $0.00004 per Jev call. Up to two calls per turn.

## Install

```bash
cp -r jev_lesson_gate ~/.hermes/plugins/
# config.yaml
plugins:
  enabled: [jev_lesson_gate]
auxiliary:
  background_review:
    judgment_can_skip: false   # true lets the gate drop clock reviews with no lesson
```

Needs `OPENROUTER_API_KEY` (read through Hermes's profile-scoped secrets). The questions in `questions*.json` are frozen
to the measured wording, so editing them invalidates the numbers above.

## License

MIT
