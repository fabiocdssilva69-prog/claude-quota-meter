# -*- coding: utf-8 -*-
"""The status line hook: reads the official rate_limits, saves one reading per 3-minute step, never fails the status line."""
import json
import sys
from pathlib import Path

from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quota_meter"))
import statusline as S  # noqa: E402

AMOSTRA = {"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                           "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}}


def test_reads_the_documented_fields():
    rl = S.ler_rate_limits(AMOSTRA)
    assert rl == {"sessao": 23.5, "semana": 41.2, "reset_sessao": 1738425600.0, "reset_semana": 1738857600.0}


def test_each_window_may_be_absent():
    assert S.ler_rate_limits({"rate_limits": {"seven_day": {"used_percentage": 9}}})["sessao"] is None
    assert S.ler_rate_limits({}) == {"sessao": None, "semana": None, "reset_sessao": None, "reset_semana": None}
    assert S.ler_rate_limits(None)["semana"] is None and S.ler_rate_limits("junk")["sessao"] is None
    assert S.texto(S.ler_rate_limits({}), 0) == "quota: waiting for the first reply"


def test_the_segment():
    rl = S.ler_rate_limits(AMOSTRA)
    assert S.texto(rl, 1738425600 - 2 * 3600 - 14 * 60) == "5h 24% · 7d 41% · resets 2h14"
    assert S.fmt_dur(41 * 60) == "41m" and S.fmt_dur(3 * 86400 + 4 * 3600) == "3d4h" and S.fmt_dur(None) == "-"


def test_one_line_per_grid_step_and_the_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CASA", tmp_path)
    monkeypatch.setattr(S, "HISTORICO", tmp_path / "history.jsonl")
    monkeypatch.setattr(S, "RESETS", tmp_path / "resets.json")
    rl = S.ler_rate_limits(AMOSTRA)
    g = 1_790_000_100.0 - (1_790_000_100.0 % S.PASSO_S)
    assert S.guardar(rl, g + 5) is True
    assert S.guardar(rl, g + 90) is False                 # same step: nothing new
    assert S.guardar(rl, g + S.PASSO_S + 5) is True       # next step
    linhas = [json.loads(l) for l in (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(linhas) == 2 and all(l["quem"] == "statusline" and l["sessao"] == 23.5 for l in linhas)
    res = json.loads((tmp_path / "resets.json").read_text(encoding="utf-8"))
    assert res["five_hour_resets_at"].startswith("2025-02-01") and "seven_day_resets_at" in res
    assert S.guardar(S.ler_rate_limits({}), g + 3 * S.PASSO_S) is False      # no data: nothing written


@settings(max_examples=200, deadline=None)
@given(st.floats(0, 100), st.floats(0, 100), st.integers(1_700_000_000, 1_900_000_000))
def test_property_the_line_is_on_its_own_grid_step(s, w, t):
    l = S.linha({"sessao": s, "semana": w}, float(t))
    import datetime as dt
    g = dt.datetime.fromisoformat(l["grade"]).timestamp()
    assert g <= t < g + S.PASSO_S and g % S.PASSO_S == 0
