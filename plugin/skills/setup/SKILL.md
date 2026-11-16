---
name: setup
description: One-time setup of the quota meter's status line hook, so the quota skill has readings. Use when the user installs this plugin or when the quota skill reports no readings.
disable-model-invocation: true
---

# Set up the quota hook

Claude Code hands the official `rate_limits` (5-hour and weekly usage and reset times) only to the status line command,
so the hook must sit there. It needs claude.ai Pro or Max and Claude Code 2.1.80 or later, with Python 3.10+.

1. Show the user what will change:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/install.py"
   ```

2. Explain the output in one or two lines and ask for consent. Then run:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" --apply
   ```

   It copies the hook to `~/.claude-quota-meter/statusline.py` (a stable place that survives plugin updates) and adds
   the `statusLine` entry after backing up `~/.claude/settings.json`. If a status line already exists it changes
   nothing and prints the one line to add to the user's own script; help them add it.

3. The first reading is saved after Claude Code's next reply. Then use the `quota` skill.

The hook writes only to `~/.claude-quota-meter/`, reads no token and sends nothing anywhere. After a plugin update, run
step 2 again to refresh the copied hook.
