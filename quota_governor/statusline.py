# -*- coding: utf-8 -*-
"""statusline.py - the Claude Code status line hook of claude-quota-meter.

Claude Code runs this script with the session JSON on stdin (settings.json -> "statusLine"). From the official
`rate_limits` field (claude.ai Pro and Max, Claude Code 2.1.80+) it saves one reading per 3-minute step to
~/.claude-quota-meter/history.jsonl and the reset times to resets.json - the meter window reads both - and prints a short
segment for the status line itself:

    5h 23% · 7d 41% · resets 2h14

Nothing leaves your machine and no token is read: Claude Code hands the numbers to the script.

    python -m quota_meter.statusline              (what goes in settings.json; prints the segment)
    python -m quota_meter.statusline --quiet      (saves only - for calling from your own status line script)

PT: o gancho da linha de estado do Claude Code. Guarda uma leitura por passo de 3 min e imprime um resumo curto.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import sys
from pathlib import Path

CASA = Path(os.environ.get("CLAUDE_QUOTA_METER_HOME") or (Path.home() / ".claude-quota-meter"))
HISTORICO = CASA / "history.jsonl"
RESETS = CASA / "resets.json"
PASSO_S = 180.0                     # one reading per 3-minute grid step (the same grid the meter uses)
HISTORICO_MAX = 4 * 1024 * 1024     # 4 MB is weeks of readings; then it keeps the newer half


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def ler_rate_limits(d) -> dict:
    """PURE. {sessao, semana, reset_sessao, reset_semana} (percent and epoch seconds) from the status line JSON. Each window
    may be absent (Claude Code drops a window once its reset time passes): its values are None."""
    out = {"sessao": None, "semana": None, "reset_sessao": None, "reset_semana": None}
    rl = (d or {}).get("rate_limits") if isinstance(d, dict) else None
    if not isinstance(rl, dict):
        return out
    for nome, chave in (("sessao", "five_hour"), ("semana", "seven_day")):
        w = rl.get(chave)
        if isinstance(w, dict):
            out[nome] = _f(w.get("used_percentage"))
            out["reset_" + nome] = _f(w.get("resets_at"))
    return out


def fronteira(ts: float, passo: float = PASSO_S) -> float:
    return math.floor(float(ts) / passo) * passo


def iso_utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).isoformat(timespec="seconds")


def linha(rl: dict, agora_ts: float) -> dict:
    """PURE. The history line (the format the meter reads): the local time, the two percentages and the grid step."""
    return {"t": dt.datetime.fromtimestamp(float(agora_ts)).astimezone().isoformat(timespec="seconds"),
            "sessao": rl.get("sessao"), "semana": rl.get("semana"), "auto": True, "quem": "statusline",
            "grade": iso_utc(fronteira(agora_ts))}


def fmt_dur(seg) -> str:
    """PURE. '2h14', '41m', '3d4h'."""
    s = _f(seg)
    if s is None or s < 0:
        return "-"
    m = int(s // 60)
    if m >= 2 * 24 * 60:
        return "{}d{}h".format(m // 1440, (m % 1440) // 60)
    if m >= 60:
        return "{}h{:02d}".format(m // 60, m % 60)
    return "{}m".format(m)


def texto(rl: dict, agora_ts: float) -> str:
    """PURE. The status line segment: '5h 23% · 7d 41% · resets 2h14' (the reset of the 5-hour window)."""
    if rl.get("sessao") is None and rl.get("semana") is None:
        return "quota: waiting for the first reply"
    partes = []
    if rl.get("sessao") is not None:
        partes.append("5h {:.0f}%".format(rl["sessao"]))
    if rl.get("semana") is not None:
        partes.append("7d {:.0f}%".format(rl["semana"]))
    if rl.get("reset_sessao"):
        partes.append("resets " + fmt_dur(rl["reset_sessao"] - agora_ts))
    return " · ".join(partes)


def _ultima_grade(p: Path):
    try:
        with open(str(p), "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - 4096))
            cauda = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for l in reversed(cauda):
        try:
            return json.loads(l).get("grade")
        except ValueError:
            continue
    return None


def _encolher(p: Path) -> None:
    try:
        if p.stat().st_size <= HISTORICO_MAX:
            return
        dados = p.read_bytes()
        meio = dados.find(b"\n", len(dados) // 2) + 1
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(dados[meio:])
        os.replace(str(tmp), str(p))
    except OSError:
        pass


def guardar(rl: dict, agora_ts: float) -> bool:
    """Saves the reading (one per grid step) and the reset times. True if a new line was written."""
    if rl.get("sessao") is None:
        return False
    CASA.mkdir(parents=True, exist_ok=True)
    nova = linha(rl, agora_ts)
    escrita = False
    if _ultima_grade(HISTORICO) != nova["grade"]:
        with io.open(str(HISTORICO), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(nova, ensure_ascii=False) + "\n")
        escrita = True
        _encolher(HISTORICO)
    res = {}
    if rl.get("reset_sessao"):
        res["five_hour_resets_at"] = iso_utc(rl["reset_sessao"])
    if rl.get("reset_semana"):
        res["seven_day_resets_at"] = iso_utc(rl["reset_semana"])
    if res:
        tmp = RESETS.with_name(RESETS.name + ".tmp")
        tmp.write_text(json.dumps(res), encoding="utf-8")
        os.replace(str(tmp), str(RESETS))
    return escrita


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import time
    agora = time.time()
    try:
        bruto = sys.stdin.read() if not sys.stdin.isatty() else ""
        d = json.loads(bruto) if bruto.strip() else {}
    except (ValueError, OSError):
        d = {}
    rl = ler_rate_limits(d)
    try:
        guardar(rl, agora)
    except OSError:
        pass                                     # the status line never fails because of the meter
    if "--quiet" not in argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:                        # noqa: BLE001
            pass
        print(texto(rl, agora))
    return 0


if __name__ == "__main__":
    sys.exit(main())
