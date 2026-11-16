---
name: meter
description: Open, close or preview the always-on-top Arc Reactor quota meter on the user's Windows desktop. Use when the user asks to show, open, hide or close the quota meter, or to see what it looks like.
---

# Open or close the desktop meter

The meter is a native Windows window (Windows 10 or 11). It needs Python 3.10+ with `skia-python` and `numpy`; if they
are missing, run the `setup` skill first.

Open it, or close it if it is already open:

```bash
pythonw "${CLAUDE_PLUGIN_ROOT}/quota_meter/__main__.py" --toggle
```

Use `pythonw` so no console window stays open. If `pythonw` is not found, use `python` with the same arguments.

Close it:

```bash
python "${CLAUDE_PLUGIN_ROOT}/quota_meter/__main__.py" --close
```

Draw the three openings to PNG files without opening a window (useful on a machine where the user cannot see the
desktop, or to show a preview):

```bash
python "${CLAUDE_PLUGIN_ROOT}/quota_meter/__main__.py" --probe "<folder>"          # real numbers
python "${CLAUDE_PLUGIN_ROOT}/quota_meter/__main__.py" --probe "<folder>" --demo   # sample numbers
```

How to read it: the gold ring is the 5-hour window, the cyan ring is the week, and the colour is the force (green
below 1x, amber from 1x, red from 1.4x). Right-click the meter for the legend and the size options.

On macOS or Linux the window does not open. Use the `quota` skill for the numbers instead.
