---
name: quota
description: Read the user's real Claude Code subscription quota (5-hour session and weekly windows, time to each reset, spending force) and decide how many subagents still fit before fanning out. Use before launching several subagents or a long multi-agent workflow, when the user asks how much quota is left, or when they ask whether to slow down.
---

# Quota Governor: check before you fan out

Most of a Claude Code subscription goes into subagents: every subagent loads its own context. Reasoning less saves
almost nothing; launching fewer subagents at the wrong moment saves a lot. Read the real quota before you start
several, and size the fan-out to what still fits until the reset.

The reader is installed by `/arc-quota-meter:setup` into the folder `.claude-quota-meter/reader` in the user's home;
call it `READER` and use its full path. It uses only the Python standard library, on Windows, macOS and Linux.

## Requirements

- Claude Code **2.1.80 or later** on a claude.ai **Pro or Max** plan. That is when Claude Code started handing the
  official `rate_limits` to the status line.
- Python 3.10+.

## First-time setup

If `READER` does not exist or no reading exists yet, ask the user to run `/arc-quota-meter:setup` once.

## 1. Read the quota

```bash
python "READER/quota_governor/quota_state.py"
```

It prints one JSON reading. The fields that matter:

| field | meaning |
|---|---|
| `sessao` | percent used of the 5-hour window |
| `semana` | percent used of the 7-day window |
| `reinicio_sessao_s`, `reinicio_semana_s` | seconds until each window resets |
| `forca` | real spending rate ÷ the rate that still fits until the reset, for the window that hits its ceiling first |
| `ritmo`, `cabe` | %/hour being spent and %/hour that still fits |
| `minutos_ao_tecto` | minutes until the ceiling at the current rate |
| `sessao_velha` | true when the last reading is old; say so instead of trusting it |
| `este_pc.agentes` | subagents running on this computer right now |

If `sessao` and `semana` are both `null`, no reading has been saved yet: run `/arc-quota-meter:setup`, or send one
message so the hook records the first reading. The force needs a few readings, 3 minutes apart.

## 2. Decide the fan-out

Use the **smaller** free share of the two windows (100 minus the larger of `sessao` and `semana`). A roomy 5-hour
window does not save a tight week.

| free share | subagents |
|---|---|
| 60% or more | up to 4 |
| 35% to 59% | up to 3 |
| 20% to 34% | up to 2 |
| under 20% | none, work serially |

Then apply the force:

- `forca` below 1: keep the number from the table.
- `forca` from 1 to 1.4: at most 1 subagent; prefer serial work.
- `forca` above 1.4: no subagents. Tell the user they will hit the ceiling before the reset at this pace.

Count the subagents already running (`este_pc.agentes`) against the limit. Two small rounds in sequence beat one wide
round: a wide round that hits the ceiling midway delivers nothing.

## 3. Report it in one short block

- session % and time to its reset;
- week % and time to its reset;
- the force, and whether the pace fits;
- the number of subagents you will use, and why.

Do not invent numbers. If a field is `null`, say it is unknown.

## Want to see it all the time?

The free, open-source Claude Quota Meter draws the same readings as an always-on-top Arc Reactor on the Windows
desktop, and works with this skill's hook: https://github.com/fabiocdssilva69-prog/claude-quota-meter

---
Made by 3BRAIN (https://3brain.com.br) with Claude Code. Support: contato@3brain.com.br.
Not affiliated with or endorsed by Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic.
