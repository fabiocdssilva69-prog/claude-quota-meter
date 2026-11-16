---
name: setup
description: Install the quota meter's status line hook and its Python dependencies, so the quota and meter skills have readings. Use the first time the user installs this plugin, or when the quota skill reports no readings.
disable-model-invocation: true
---

# Set up the quota meter

Claude Code hands the official `rate_limits` (5-hour and weekly usage and reset times) only to the **status line**
command. A plugin cannot install a status line by itself, so this skill wires it in `~/.claude/settings.json`, with the
user's consent. It needs claude.ai **Pro or Max** and Claude Code **2.1.80 or later**.

## 1. Check Python

Run `python --version`. It must be 3.10 or later. If Python is missing, stop and tell the user to install it from
python.org.

## 2. Install the dependencies (desktop meter only)

The `quota` skill and the status line use only the Python standard library. The desktop window also needs:

```bash
python -m pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
```

Ask before installing. Skip this on macOS or Linux, where the window does not open.

## 3. Copy the hook to a stable place

The plugin folder changes on every plugin update, so the status line must not point into it. Copy the hook to the
meter's own folder:

```bash
python -c "import pathlib,shutil; d=pathlib.Path.home()/'.claude-quota-meter'; d.mkdir(exist_ok=True); shutil.copy2(r'${CLAUDE_PLUGIN_ROOT}/quota_meter/statusline.py', d/'statusline.py'); print(d/'statusline.py')"
```

Note the printed path; call it `HOOK`. Run this step again after each plugin update to refresh the copy.

## 4. Wire the status line

Read `~/.claude/settings.json`.

- **No `statusLine` key:** show the user this change and add it after they agree. Write `HOOK` with forward slashes.

  ```json
  "statusLine": {
    "type": "command",
    "command": "python HOOK",
    "refreshInterval": 60
  }
  ```

- **A `statusLine` already exists:** do not replace it. Show the user the line to add at the start of their own status
  line script, so it also feeds the meter. It reads the same JSON from stdin and prints nothing:

  ```bash
  python HOOK --quiet
  ```

  The script has to pass its stdin to this command. If you are not sure how their script handles stdin, explain the
  line and let the user add it.

Keep every other key in the file unchanged.

## 5. Confirm

The status line updates after Claude Code's next reply. Then run the `quota` skill. The first reading appears after
that reply; the force needs a few readings, spaced 3 minutes apart.

## Privacy

The hook writes only to `~/.claude-quota-meter/`. It reads no token, calls no model and sends nothing anywhere.
