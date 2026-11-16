# Quota Meter (Arc Reactor) — Claude Code plugin

Reads your real Claude Code quota before you fan out subagents: the 5-hour and weekly windows, the time to each reset,
and the **force** (your real spending rate divided by what still fits until the reset). Then it says how many subagents
still fit.

Made by [3BRAIN](https://3brain.com.br) with Claude Code. Not affiliated with or endorsed by Anthropic.

## Install

```
/plugin install arc-quota-meter --marketplace fabiocdssilva69-prog/claude-quota-meter
```

Then run `/arc-quota-meter:setup` once. With your consent it downloads the open-source reader from this repository
(MIT, standard-library Python, folder `quota_governor/`), backs up `~/.claude/settings.json` and wires the status line
hook. It never replaces an existing status line.

## Skills

| skill | |
|---|---|
| `quota` | reads the quota and the force, and decides how many subagents fit before fanning out |
| `setup` | one-time setup: downloads the reader and wires the status line hook |

## Requirements

- Claude Code 2.1.80 or later on a claude.ai Pro or Max plan (that is when Claude Code started handing the official
  `rate_limits` to the status line)
- Python 3.10+ (standard library only) and git, on Windows, macOS or Linux

## Privacy

The hook writes only to `~/.claude-quota-meter/`. The plugin reads no token, calls no model and sends nothing anywhere.
To count subagents in flight it reads the timestamps of the local Claude Code session files; it keeps only counts.

## Desktop meter

The always-on-top Arc Reactor window for the Windows desktop lives in the root of this repository and reads the same
hook. See the repository README.

## License

MIT, © 2026 3BRAIN.
