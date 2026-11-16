# -*- coding: utf-8 -*-
"""quota_state.py - one quota reading as JSON (Quota Governor for Claude Code).

Reads the readings the status line hook saved in ~/.claude-quota-meter/ and prints the 5-hour and weekly usage, the
time to each reset, the spending force and the subagents running on this computer. Standard library only; works on
Windows, macOS and Linux. Reads no token and calls no API.

    python quota_state.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados  # noqa: E402

col = dados.Coletor({}, ler_api=False)
col.volta()
time.sleep(2.1)          # the second pass measures calls per minute and subagents in flight
r = col.volta()
r.pop("janelas", None)
print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
