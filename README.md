# Claude Quota Meter

**A live, always-on-top desktop meter for your Claude Code quota.** The Arc Reactor Mark II floats over your desktop and
shows your 5-hour and weekly windows, when they reset, and the **force**: how fast you are spending compared with what
still fits until the reset. Below 1x it stays calm and green. From 1x it turns amber and speeds up. From 1.4x it turns red.

![The three openings at force 1.21x: minimised, half open and open](docs/img/mark2_openings.png)

> **Unofficial.** Not affiliated with or endorsed by Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic.

[Português](README.pt-BR.md) · Made by [3BRAIN](https://3brain.com.br) with Claude Code

---

## What it shows

| | |
|---|---|
| **5 h · 7 d** | how much of each window you have used (the gold and cyan rings) |
| **Per-model week** | the violet ring, when the exact reading is on (see below) |
| **Resets** | time left until each window resets (the thin inner and outer rings) |
| **Force** | your real spending rate ÷ the rate that still fits until the reset, taken from the window that hits its ceiling first. This sets the hologram's speed and colour. |
| **Rate** | %/hour you are really spending and %/hour that fits (the week counts worked hours: an 8-hour day) |
| **This PC** | Claude Code sessions open, subagents in flight and model calls per minute, plus a flash on every call. This line never enters the force or the colour. |

When the force crosses 1x the meter changes colour, pulses slowly and, if you minimised it, opens by itself. Right-click
it to change its size or to see the legend.

## Three openings

| | size | |
|---|---|---|
| **Minimised** | 348 × 30 px | a pill with session, week, force, session reset and subagents |
| **Half open** | 130 × 130 px | the reactor only; the numbers hide behind the three-line button |
| **Open** | 130 × 208 px | the reactor with the panel of numbers underneath |

The window buttons and a double click switch between them, and your choice is remembered.

![Force 0.62x green, 1.21x amber, 1.62x red](docs/img/mark2_forces.png)

## Requirements

- Windows 10 or 11 (the window is a native layered Win32 window)
- Python 3.10+
- Claude Code **2.1.80 or later** on a claude.ai **Pro or Max** plan. That is when Claude Code started handing your
  quota to the status line.

## Install

```bash
git clone https://github.com/fabiocdssilva69-prog/claude-quota-meter
cd claude-quota-meter
pip install -r requirements.txt
```

### 1. Feed it from the status line (default, official, no token)

Claude Code sends the official `rate_limits` (5-hour and 7-day usage and reset times) to your status line script on
every reply. The hook saves one reading per 3-minute step and prints a short segment. Add this to
`~/.claude/settings.json` (use your own path to the folder):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:/path/to/claude-quota-meter/quota_meter/statusline.py",
    "refreshInterval": 60
  }
}
```

Your status line will show `5h 23% · 7d 41% · resets 2h14`. `refreshInterval` keeps the readings coming while a
session is idle.

**Already have a status line?** Keep yours, and pipe the same JSON into the hook in quiet mode from your own script:
`python .../quota_meter/statusline.py --quiet`.

### 2. Open the meter

```bash
pythonw -m quota_meter            # open (no console)
pythonw -m quota_meter --toggle   # open or close (handy for a desktop shortcut)
python  -m quota_meter --state    # one reading as JSON
python  -m quota_meter --probe out          # draw the three openings with your real numbers
python  -m quota_meter --probe out --demo   # the same with sample numbers in the three force zones
```

Drag it anywhere. The `-` button (or a double click) shrinks it to a pill with the numbers, and the three-line button
hides the numbers and leaves only the reactor.

## Optional: the exact reading (unofficial)

The status line has no per-model weekly window, and it only updates while Claude Code is replying. If you want both, set
`"fonte": "oauth"` in `~/.claude-quota-meter/config.json`. The meter will then ask the endpoint Claude Code itself uses
for `/usage`, at most once every 3 minutes, and back off on refusals.

> **Read this first.** That endpoint is **not a documented public API**, so it can change or stop working without notice.
> The meter reads the OAuth token Claude Code keeps in `~/.claude/.credentials.json` at the moment of each request,
> sends it **only** to `api.anthropic.com`, and never prints, logs or stores it. It stays off unless you turn it on.

## Settings

`~/.claude-quota-meter/config.json` (created on first run):

| key | default | |
|---|---|---|
| `tamanho_px` | `130` | size: 130 micro, 180 small, 260 medium, 340 large |
| `fps_max` / `fps_repouso` | `30` | frames per second with and without calls |
| `teto_cpu_nucleo` | `0.12` | share of ONE core the drawing may use (0.12 ≈ 1% of a 12-core machine) |
| `fonte` | `statusline` | `statusline` (default) or `oauth` (opt-in, see above) |

## Privacy

- **Reads:** the readings the hook saved, the Claude Code transcripts in `~/.claude/projects` (for the pulse, sessions
  and agents only), and, if you opt in, the usage endpoint.
- **Writes:** only `~/.claude-quota-meter/` (`history.jsonl`, `resets.json`, `config.json`, `estado.json`, `medidor.log`).
- **Never** calls a model, never sends your data anywhere else, and never needs an API key.

## How the force is computed

Every reading lands on a shared 3-minute grid (UTC). The **rate** of a window is how much it really rose over the last 40
minutes (5-hour window) or 24 hours (week), using grid points only. **Fits** is what is left of the window divided by the
hours until it resets, counting worked hours for the week. **Force = rate ÷ fits** for the window that would hit 100%
first. With the same readings, two machines show exactly the same numbers. That grid property is proven by the tests.

## Tests

```bash
pip install pytest hypothesis
python -m pytest tests -q
```

53 tests, including Hypothesis properties: the force never breaks on `None`, 0% or 100%; two machines with the same
readings agree; the hook writes exactly one reading per grid step.

## License

[MIT](LICENSE) © 2026 3BRAIN
