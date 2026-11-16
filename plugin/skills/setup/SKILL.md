---
name: setup
description: One-time setup of the quota reader and its status line hook, so the quota skill has readings. Use when the user installs this plugin or when the quota skill reports no readings.
disable-model-invocation: true
---

# Set up the quota reader

Claude Code hands the official `rate_limits` (5-hour and weekly usage and reset times) only to the status line command,
so a small hook must sit there. The hook and the reader are open source (MIT) and live on GitHub; this plugin ships only
these instructions. Requirements: claude.ai Pro or Max, Claude Code 2.1.80 or later, Python 3.10+ and git.

Call `READER` the folder `.claude-quota-meter/reader` inside the user's home folder (for example
`C:/Users/<name>/.claude-quota-meter/reader` on Windows or `/Users/<name>/.claude-quota-meter/reader` on macOS). Use
the full path in every command.

## 1. Ask first

Tell the user, in two lines, what will happen: a read-only copy of
https://github.com/fabiocdssilva69-prog/claude-quota-meter goes into `READER`, and the hook is wired in
`~/.claude/settings.json` after a backup. It uses only the Python standard library, reads no token and sends nothing
anywhere. Continue only if they agree.

## 2. Get the reader

If `READER` does not exist:

```bash
git clone --depth 1 https://github.com/fabiocdssilva69-prog/claude-quota-meter "READER"
```

If it exists, update it:

```bash
git -C "READER" pull --ff-only
```

## 3. Wire the hook

Show the change, then apply it:

```bash
python "READER/quota_governor/install.py"
python "READER/quota_governor/install.py" --apply
```

It copies the hook to `~/.claude-quota-meter/statusline.py` and adds the `statusLine` entry after backing up
`~/.claude/settings.json`. If a status line already exists it changes nothing and prints the one line to add to the
user's own script; help them add it.

## 4. Done

The first reading is saved after Claude Code's next reply. Then use the `quota` skill.
