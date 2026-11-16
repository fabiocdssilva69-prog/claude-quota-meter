---
name: quota
description: Read the user's Claude Code quota (5-hour session and weekly windows, time to each reset, spending force) and decide how many subagents fit before fanning out. Use before launching several subagents or a long workflow, when the user asks how much quota is left, or when they ask whether to slow down.
---

# Quota check before you fan out

Each subagent costs far more than the reasoning around it: most of the quota goes into the context every subagent
loads. Read the real quota before you start several, and size the fan-out to what still fits until the reset.

## 1. Read the quota

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/quota_meter/__main__.py" --state
```

It prints one JSON reading and opens no window. It reads only files on this computer. The readings come from the
status line hook (see the `setup` skill). The fields that matter:

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

If `sessao` and `semana` are both `null`, the status line hook has not saved a reading yet. Tell the user to run the
`setup` skill, or to send one message in Claude Code with the hook installed, and stop here.

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

Count the subagents already running (`este_pc.agentes`) against the limit.

## 3. Report it in one short block

Give the user:

- session % and time to its reset;
- week % and time to its reset;
- the force, and whether the pace fits;
- the number of subagents you will use, and why.

Do not invent numbers. If a field is `null`, say it is unknown.
