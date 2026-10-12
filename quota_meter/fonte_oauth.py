# -*- coding: utf-8 -*-
"""fonte_oauth.py - OPT-IN: the exact account usage from the endpoint Claude Code itself uses for /usage.

Off by default. Turn it on with "fonte": "oauth" in config.json. It reads the OAuth token that Claude Code keeps in
~/.claude/.credentials.json at the moment of each request (one every 3 minutes at most), sends it only to
api.anthropic.com, and never prints, logs or saves it. It adds what the status line does not have: the per-model weekly
window and readings while no Claude Code session is open.

UNOFFICIAL: the endpoint is not a documented public API - it can change or stop working at any time.

PT: OPCIONAL. Le a cota exacta pelo endpoint que o proprio Claude Code usa para o /usage, com o token que ele guarda. Nao e
uma API publica documentada: pode mudar a qualquer momento. O token so vai para a api.anthropic.com.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
CREDENCIAIS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / ".credentials.json"
VERSAO = "0.1.0"


def _token() -> str:
    try:
        d = json.loads(CREDENCIAIS.read_text(encoding="utf-8"))
    except OSError:
        raise RuntimeError("no {} - is Claude Code logged in on this machine?".format(CREDENCIAIS))
    except ValueError:
        raise RuntimeError("{} is not valid JSON".format(CREDENCIAIS))
    t = ((d.get("claudeAiOauth") or {}).get("accessToken") or "") if isinstance(d, dict) else ""
    if not t:
        raise RuntimeError("no accessToken in the credentials - open Claude Code once to log in")
    return t


def ler_uso(timeout: float = 25.0) -> dict:
    """The /api/oauth/usage reply as a dict. Raises RuntimeError with the HTTP code on refusals (401/403: open Claude Code
    once to renew the login; 429: too many requests - the meter backs off)."""
    req = urllib.request.Request(URL, headers={
        "Authorization": "Bearer " + _token(),
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "claude-quota-meter/{} (unofficial)".format(VERSAO),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP {}".format(e.code))
