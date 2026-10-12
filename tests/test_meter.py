# -*- coding: utf-8 -*-
"""test_meter.py - the proofs of claude-quota-meter.

House rule: an invariant beats a test case. The pure functions (force, formatting, staying inside the screen, when to ask
the opt-in API, alerts, agents in flight) carry Hypothesis properties; the rate is checked against a LITERAL COPY of the
reference rate formula; the 18 holograms draw with random numbers (never breaking on None, 0% or 100%). The grid property:
two machines with the SAME series of readings - asked at different moments inside the step - give the SAME force and rate.

Run: python -m pytest tests -q
"""
import datetime as dt
import json
import math
import os
import random
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "quota_meter"))
import dados as D            # noqa: E402
import hologramas as H

pct = st.floats(min_value=0, max_value=100, allow_nan=False)
ts0 = 1_790_000_000.0


def ritmo_cota_py(leituras, agora, minutos, idx=0):
    """COPIA LITERAL da conta de ritmo de referencia (so com leituras e agora como argumentos): o medidor tem de dar o mesmo."""
    pts = [(t, vals[idx]) for t, *vals in leituras
           if vals[idx] is not None and (agora - t).total_seconds() <= minutos * 60]
    if len(pts) < 2:
        return None, 0.0
    corte = 0
    for i in range(1, len(pts)):
        if pts[i][1] < pts[i - 1][1] - 1:
            corte = i
    pts = pts[corte:]
    dh = (pts[-1][0] - pts[0][0]).total_seconds() / 3600.0
    if dh < 0.08:
        return None, dh * 60
    return (pts[-1][1] - pts[0][1]) / dh, dh * 60


# as duas contas sao exactas e a comparacao mede a regra, nao o arredondamento
serie = st.lists(st.tuples(st.integers(min_value=10, max_value=600), st.floats(min_value=-3, max_value=4)), min_size=0, max_size=25)


@given(passos=serie, v0=st.floats(min_value=0, max_value=90), minutos=st.sampled_from([40.0, 60.0, 1440.0]))
def test_ritmo_real_e_a_conta_do_cota_py(passos, v0, minutos):
    t, v, lst = ts0, v0, []
    for dtt, dv in passos:
        t += dtt
        v = max(0.0, min(100.0, v + dv))
        lst.append((t, v))
    agora = t + 5
    meu = D.ritmo_real(lst, agora, minutos)
    tz = dt.timezone.utc
    ref, _span = ritmo_cota_py([(dt.datetime.fromtimestamp(a, tz), b) for a, b in lst], dt.datetime.fromtimestamp(agora, tz),
                               minutos)
    if ref is None:
        assert meu is None
    else:
        assert meu is not None and math.isclose(meu, ref, rel_tol=1e-9, abs_tol=1e-9)


@given(r=st.floats(min_value=0, max_value=40), n=st.integers(3, 20))
def test_ritmo_de_uma_recta_e_a_inclinacao(r, n):
    pts = [(ts0 + i * 300, 10 + r * i * 300 / 3600.0) for i in range(n)]
    assume(pts[-1][1] <= 100)
    got = D.ritmo_real(pts, pts[-1][0], 1e6)
    assert got is not None and math.isclose(got, r, rel_tol=1e-6, abs_tol=1e-6)


def test_ritmo_corta_no_reinicio_e_precisa_de_tempo():
    pts = [(ts0, 80), (ts0 + 600, 90), (ts0 + 900, 2), (ts0 + 1800, 5)]   # reinicio a meio: so conta 2 -> 5 em 15 min
    assert math.isclose(D.ritmo_real(pts, ts0 + 1800, 60), 12.0)
    assert D.ritmo_real([(ts0, 1)], ts0, 60) is None
    assert D.ritmo_real([(ts0, 1), (ts0 + 60, 2)], ts0 + 60, 60) is None    # 1 min < 0,08 h


# ------------------------------------------------------------------ o que cabe e a forca
@given(u=pct, h=st.floats(min_value=0.01, max_value=200))
def test_cabe_pela_regua_do_cota_py(u, h):
    s = D.cabe_por_hora("sessao", u, h)
    w = D.cabe_por_hora("semana", u, h)
    assert math.isclose(s, (100 - u) / h)
    assert math.isclose(w, (100 - u) / (max(1.0, h / 24.0) * 8.0))
    assert D.cabe_por_hora("sessao", u, 0) is None and D.cabe_por_hora("semana", None, 5) is None


janela = st.fixed_dictionaries({"nome": st.sampled_from(["sessao", "semana", "Fable"]), "utilizacao": st.one_of(st.none(), pct),
                                "cabe": st.one_of(st.none(), st.floats(min_value=0, max_value=50)),
                                "real": st.one_of(st.none(), st.floats(min_value=-2, max_value=60))})


@given(js=st.lists(janela, max_size=3))
def test_forca_e_a_da_janela_que_bate_no_tecto_primeiro(js):
    r = D.forca_e_aperto(js)
    validas = [j for j in js if j["real"] is not None and j["cabe"] not in (None,) and j["cabe"] > 0 and j["utilizacao"] is not None]
    if not validas:
        assert r["forca"] is None
        return
    def minutos(j):
        return (100 - j["utilizacao"]) / j["real"] * 60 if j["real"] > D.PARADO_PCT_H else 1e9
    melhor = min(minutos(j) for j in validas)
    assert math.isclose(r["minutos_ao_tecto"], melhor)
    assert r["forca"] >= 0
    escolhida = next(j for j in validas if math.isclose(minutos(j), melhor))
    assert math.isclose(r["forca"], max(0.0, escolhida["real"] / escolhida["cabe"]))


@given(modo=st.sampled_from(["verde", "amarelo", "vermelho", "economico", "?", None]),
       f=st.one_of(st.none(), st.floats(min_value=0, max_value=5)), g=st.floats(min_value=0, max_value=5))
def test_severidade_e_a_pior_e_cresce_com_a_forca(modo, f, g):
    s = D.severidade(modo, f)
    assert s in (0, 1, 2)
    n = D.nivel_do_modo(modo)
    assert s >= (n or 0)
    if f is not None and f > 1.4:
        assert s == 2
    if f is not None and f > 1.0:
        assert s >= 1
    if f is not None and g >= f:
        assert D.severidade(modo, g) >= s
    if (n or 0) == 0 and (f is None or f <= 1.0):
        assert s == 0


# ------------------------------------------------------------------ o alerta
@given(f=st.one_of(st.none(), st.floats(min_value=0, max_value=3)), fa=st.one_of(st.none(), st.floats(min_value=0, max_value=3)),
       mini=st.booleans(), desde=st.one_of(st.none(), st.floats(min_value=0, max_value=7200)))
def test_alerta_abre_so_minimizado_e_so_acima_de_1x(f, fa, mini, desde):
    agora = ts0
    a = D.alerta(f, fa, mini, agora, None if desde is None else agora - desde)
    assert a["piscar"] == (f is not None and f > 1.0)
    if a["abrir"]:
        assert mini and f > 1.0
        assert a["cruzou"] or desde is None or desde >= D.REABRIR_APOS_MIN_S
    if mini and f is not None and f > 1.0 and (fa is None or fa <= 1.0):
        assert a["abrir"]                                       # acabou de passar de 1x: abre sempre
    if not mini:
        assert not a["abrir"]


PASSO = D.INTERVALO_API_S
G0 = 9_950_000 * PASSO            # uma fronteira da grelha (2026-10-03 04:00:00 UTC)
ESPACO_MIN = PASSO - (D.TOLERANCIA_GRADE_S - D.ATRASO_GRADE_S)       # 140 s: o mais perto que dois pedidos podem ficar


@given(passos=st.lists(st.tuples(st.floats(min_value=0.5, max_value=400), st.booleans()), min_size=1, max_size=150),
       t0=st.floats(min_value=0, max_value=10_000))
@settings(max_examples=200, deadline=None)
def test_grade_um_pedido_por_passo_e_nunca_no_recuo(passos, t0):
    agora, ug, up, recuo, falhas, pedidos = ts0 + t0, None, None, None, 0, []
    for dtt, ok in passos:
        agora += dtt
        g = D.decidir_api_grade(agora, ug, up, recuo)
        if g is None:
            continue
        assert D.ATRASO_GRADE_S <= agora - g <= D.TOLERANCIA_GRADE_S      # so no inicio do passo
        assert g % PASSO == 0                                              # a fronteira e a comum (UTC)
        assert recuo is None or g >= recuo                                 # nunca durante o recuo
        pedidos.append((g, agora))
        ug, up = g, agora
        if ok:
            falhas, recuo = 0, None
        else:
            falhas += 1
            recuo = g + D.proximo_recuo(falhas)
    gs = [g for g, _a in pedidos]
    assert gs == sorted(set(gs))                                           # nunca dois pedidos no mesmo passo
    for (_g1, a1), (_g2, a2) in zip(pedidos, pedidos[1:]):
        assert a2 - a1 >= ESPACO_MIN


@given(passos=st.lists(st.floats(min_value=0.5, max_value=10), min_size=60, max_size=400), t0=st.floats(0, 10_000))
@settings(max_examples=100, deadline=None)
def test_grade_sem_recusas_nao_salta_passos(passos, t0):
    """Com voltas de ate 10 s (a do coletor e de 2 s) e sem recusas, nenhum passo fica por pedir: buraco feito por nos seria
    uma diferenca entre as duas maquinas."""
    agora, ug, up, gs = ts0 + t0, None, None, []
    for dtt in passos:
        agora += dtt
        g = D.decidir_api_grade(agora, ug, up, None)
        if g is not None:
            gs.append(g)
            ug, up = g, agora
    assert all(b - a == PASSO for a, b in zip(gs, gs[1:]))


def test_grade_exemplos():
    assert D.fronteira_grade(G0 + 179.9) == G0 and D.fronteira_grade(G0 + 180) == G0 + PASSO
    assert D.iso_utc(G0) == "2026-10-03T04:00:00+00:00"
    assert D.decidir_api_grade(G0 + 4, None, None) is None                  # cedo de mais (relogios desfasados)
    assert D.decidir_api_grade(G0 + 5, None, None) == G0
    assert D.decidir_api_grade(G0 + 45, None, None) == G0
    assert D.decidir_api_grade(G0 + 46, None, None) is None                 # tarde: ja nao e deste passo
    assert D.decidir_api_grade(G0 + 9, G0, G0 + 5) is None                  # este passo ja foi pedido
    assert D.decidir_api_grade(G0 + 185, G0, G0 + 5) == G0 + PASSO
    assert D.decidir_api_grade(G0 + 185, G0, G0 + 5, G0 + 360) is None      # recusa no G0: recuo de 2 passos
    assert D.decidir_api_grade(G0 + 365, G0, G0 + 5, G0 + 360) == G0 + 360
    assert D.decidir_api_grade(G0 + 5, None, G0 - 95) is None               # o medidor antigo pediu ha 100 s
    assert D.decidir_api_grade(G0 + 45, None, G0 - 95) == G0                # ha 140 s: ja pode
    assert D.decidir_api_grade(G0 + 5, G0 + 7200, G0 + 7000) == G0          # relogio que voltou 2 h atras: nao se cala
    assert [D.proximo_recuo(n) for n in (1, 2, 3, 9)] == [360.0, 720.0, 1800.0, 1800.0]


# ------------------------------------------------------------------ a posicao dentro do ecra
area = st.tuples(st.integers(-3000, 3000), st.integers(-2000, 2000), st.integers(400, 3000), st.integers(300, 2000)).map(
    lambda a: (a[0], a[1], a[0] + a[2], a[1] + a[3]))


@given(areas=st.lists(area, min_size=1, max_size=3), x=st.integers(-9000, 9000), y=st.integers(-9000, 9000),
       w=st.integers(1, 400), h=st.integers(1, 300))
def test_a_janela_fica_sempre_dentro_de_um_monitor(areas, x, y, w, h):
    nx, ny = D.dentro_do_ecra(x, y, w, h, areas)
    assert any(nx >= l and ny >= t and nx + w <= r and ny + h <= b for l, t, r, b in areas)
    assert D.dentro_do_ecra(nx, ny, w, h, areas) == (nx, ny)                     # idempotente
    if any(x >= l and y >= t and x + w <= r and y + h <= b for l, t, r, b in areas):
        assert (nx, ny) == (x, y)                                             # ja estava dentro: nao mexe


def test_mudar_a_resolucao_puxa_a_janela_para_dentro():
    grande, pequeno = [(0, 0, 2560, 1400)], [(0, 0, 1366, 728)]
    x, y = D.dentro_do_ecra(2200, 900, 325, 438, grande)
    assert (x, y) == (2200, 900)
    nx, ny = D.dentro_do_ecra(x, y, 325, 438, pequeno)
    assert (nx, ny) == (1366 - 325, 728 - 438)
    # tirou-se o segundo monitor (a direita): volta para o principal
    assert D.dentro_do_ecra(2500, 100, 325, 438, [(0, 0, 1920, 1040)]) == (1920 - 325, 100)
    assert D.dentro_do_ecra(5, 5, 10, 10, []) == (5, 5)


# ------------------------------------------------------------------ formatacao
@given(v=st.one_of(st.none(), st.floats(min_value=-1e6, max_value=1e6)))
def test_formatos_nunca_rebentam(v):
    for f in (D.fmt_pct, D.fmt_forca, D.fmt_ritmo, D.fmt_dur, D.fmt_usd, D.fmt_idade):
        s = f(v)
        assert isinstance(s, str) and s
    if v is None:
        assert D.fmt_pct(v) == "—" and D.fmt_forca(v) == "—" and D.fmt_dur(v) == "—"
    else:
        assert "-" not in D.fmt_forca(v)                                      # a forca nunca aparece negativa


@given(s=st.integers(0, 30 * 86400))
def test_fmt_dur_le_se_de_volta(s):
    txt = D.fmt_dur(s)
    if "d" in txt:
        d, h = txt.rstrip("h").split("d")
        assert int(d) * 86400 + int(h) * 3600 <= s < int(d) * 86400 + (int(h) + 1) * 3600
    else:
        h, m = txt.split("h")
        assert int(h) * 3600 + int(m) * 60 <= s < int(h) * 3600 + (int(m) + 1) * 60


def test_formatos_exemplos():
    assert D.fmt_pct(18.4) == "18%" and D.fmt_forca(0.934) == "0,93x" and D.fmt_dur(2 * 3600 + 49 * 60 + 5) == "2h49"
    assert D.fmt_dur(87 * 3600 + 25 * 60) == "3d15h" and D.fmt_usd(4.1) == "US$ 4,10" and D.fmt_ritmo(6.25) == "6,2%/h"
    assert D.fmt_ritmo(0.366) == "0,37%/h" and D.fmt_idade(5) == "agora" and D.fmt_idade(200) == "há 3 min"




# ------------------------------------------------------------------ agentes em voo e o pulso
def _resp(mid, stop=None):
    return {"type": "assistant", "message": {"id": mid, "stop_reason": stop}}


@given(ids=st.lists(st.sampled_from(["a", "b", "c", "d", "e"]), max_size=30), outros=st.integers(0, 5))
def test_respostas_contam_uma_vez_por_id(ids, outros):
    vistos = set()
    linhas = [_resp(i) for i in ids] + [{"type": "user"}] * outros
    assert D.respostas_novas(linhas, vistos) == len(set(ids))
    assert D.respostas_novas(linhas, vistos) == 0                             # as mesmas outra vez: nada de novo


def test_agente_em_voo():
    a_meio = [_resp("1", "tool_use"), {"type": "user"}]
    acabou = [_resp("1", "tool_use"), {"type": "user"}, _resp("2", "end_turn")]
    assert D.agente_em_voo(a_meio, 30, False)
    assert not D.agente_em_voo(acabou, 30, False)
    assert not D.agente_em_voo(a_meio, 30, True)                              # o journal ja tem o result
    assert not D.agente_em_voo(a_meio, D.MAX_SILENCIO_AGENTE_S + 1, False)    # calado ha 20 min: morreu
    assert D.agente_em_voo([], 10, False)                                    # acabou de nascer, ainda sem resposta


def test_linhas_json_e_historico():
    txt = 'ado":1}\n{"t": "2026-10-04T12:00:06-03:00", "sessao": 24.0, "semana": 61.0, "por_modelo": {"Fable": 19.0}}\n' \
          '{"t": "2026-10-04T11:00:00-03:00", "sessao": 20}\n{"t": "x", "erro": "HTTP 429"}\nlixo\n'
    ls = D.ler_linhas_json(txt)
    assert len(ls) == 3
    h = D.leituras_do_historico(ls)
    assert [p[1] for p in h] == [20.0, 24.0] and h[1][3] == 19.0 and h[0][2] is None


def test_resposta_da_api():
    d = {"five_hour": {"utilization": 29.0, "resets_at": "2026-10-04T18:00:00Z"},
         "seven_day": {"utilization": 62.0, "resets_at": "2026-10-08T03:00:00+00:00"},
         "limits": [{"kind": "weekly_scoped", "percent": 19.0, "resets_at": "2026-10-08T03:00:00Z",
                     "scope": {"model": {"display_name": "Fable"}}}, {"kind": "session", "percent": 29}]}
    r = D.ler_resposta_api(d)
    assert (r["sessao"], r["semana"], r["fable"]) == (29.0, 62.0, 19.0)
    assert r["reset_sessao"] == dt.datetime(2026, 10, 4, 18, tzinfo=dt.timezone.utc).timestamp()
    assert D.ler_resposta_api(None)["sessao"] is None


@given(s=st.lists(st.tuples(st.integers(0, 7200), pct, st.one_of(st.none(), pct), st.one_of(st.none(), pct)), max_size=20),
       modo=st.sampled_from(["verde", "amarelo", "vermelho", None]), rs=st.one_of(st.none(), st.integers(-100, 18000)))
def test_montar_e_coerente(s, modo, rs):
    leit = sorted(((ts0 + a, b, c, d) for a, b, c, d in s), key=lambda p: p[0])     # pontos da grelha (na hora dela)
    agora = ts0 + 7300
    res = {"sessao": None if rs is None else agora + rs}
    r = D.montar(leit, agora, res, {"modo": modo, "agentes": 3})
    assert r["severidade"] == D.severidade(None, r["forca"])
    assert r["este_pc"] == {"modo": modo, "agentes": 3}
    outro = D.montar(leit, agora, res, {"modo": "vermelho", "agentes": 9, "eventos": 500, "torre_usd_hoje": 80.0})
    assert {k: outro[k] for k in D.CHAVES_DA_CONTA} == {k: r[k] for k in D.CHAVES_DA_CONTA}   # deste PC nao mexe na conta
    if leit:
        rr = D._ao_minuto(res["sessao"]) if rs is not None else None
        velha = rr is not None and 0 < rr - agora <= 5 * 3600 and leit[-1][0] < rr - 5 * 3600
        assert r["sessao"] == (0.0 if velha else leit[-1][1]) and r["sessao_velha"] == velha
    else:
        assert r["sessao"] is None and r["forca"] is None
    if r["forca"] is not None:
        assert r["forca"] >= 0 and r["forca_janela"] in ("sessao", "semana", "Fable")


# ------------------------------------------------------------------ config e hologramas
@given(c=st.one_of(st.none(), st.dictionaries(st.sampled_from(["holograma", "tamanho_px", "fps_max", "fps_repouso",
                                                                "teto_cpu_nucleo", "lixo"]),
                                               st.one_of(st.integers(-1000, 5000), st.text(max_size=5),
                                                         st.floats(allow_nan=True), st.none()), max_size=6)))
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_config_estragado_nao_parte_o_medidor(c):
    import medidor as M
    import hologramas as H
    v = M.validar_config(c)
    assert "holograma" not in v and 110 <= v["tamanho_px"] <= 480          # 12/10: um so holograma, sem chave de escolha
    assert 5 <= v["fps_max"] <= 60 and 2 <= v["fps_repouso"] <= v["fps_max"] and 0.02 <= v["teto_cpu_nucleo"] <= 0.5
    assert "lixo" not in v


@given(a0=st.floats(min_value=-20, max_value=20), a1=st.floats(min_value=-20, max_value=20), ccw=st.booleans())
def test_varrimento_do_arco_e_o_do_canvas(a0, a1, ccw):
    import hologramas as H
    sw = H._sweep(a0, a1, ccw)
    tau = 2 * math.pi
    if not ccw:
        assert 0 <= sw <= tau
        if a1 - a0 >= tau:
            assert sw == tau
    else:
        assert -tau <= sw <= 0
        if a0 - a1 >= tau:
            assert sw == -tau


def _desenhar(hid, q, quadros=4, sem_memoria=False):
    import numpy as np
    import skia
    import hologramas as H
    for k, v in q.items():
        setattr(H.Q, k, v)
    buf = np.zeros((300, 260, 4), np.uint8)
    surf = skia.Surface(buf, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
    ctx = H.Tela(surf.getCanvas())
    ctx.largura = 260
    ctx._gravando = bool(sem_memoria)            # a gravar = tudo directo: o desenho ao vivo, sem a memoria de desenho
    st_ = {}
    for i in range(quadros):
        H.Q.ultima_chamada = 1000 + (i // 2)
        H.HOLOGRAMAS[hid][0](ctx, 260, 260, i / 20.0, 1 / 20.0, st_)
    return buf


q_estrategia = st.fixed_dictionaries({
    "sessao": pct, "semana": pct, "fable": pct, "ritmo": st.floats(min_value=0, max_value=80),
    "cabe": st.floats(min_value=0, max_value=80), "forca_real": st.one_of(st.none(), st.floats(min_value=0, max_value=4)),
    "agentes": st.integers(0, 7), "reinicioS": st.one_of(st.none(), st.floats(min_value=0, max_value=6 * 3600)),
    "reinicioW": st.one_of(st.none(), st.floats(min_value=0, max_value=8 * 86400)), "sev": st.integers(0, 2),
    "modo": st.integers(0, 2), "torre_usd": st.floats(min_value=0, max_value=100)})


@given(q=q_estrategia)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_o_mark2_desenha_com_qualquer_cota(q):
    assert list(H.HOLOGRAMAS) == ["m9"] and H.HOLOGRAMAS["m9"][0] is H.mark2   # 12/10: o unico holograma
    buf = _desenhar("m9", q)
    assert buf[:, :, 3].max() > 0                                             # desenhou alguma coisa
    rgb_max = buf[:, :, :3].max(axis=2)
    assert (rgb_max <= buf[:, :, 3]).all()                                    # pre-multiplicado valido (o Windows exige)


def test_painel_e_pilula_com_retrato_vazio_e_cheio():
    import numpy as np
    import skia
    import hologramas as H
    for r in ({}, {"sessao": 29, "semana": 62, "fable": 19, "forca": 1.3, "ritmo": 14.1, "cabe": 9.0, "forca_janela": "sessao",
                   "reinicio_sessao_s": 9000, "reinicio_semana_s": 300000, "leitura_ts": 10, "t": 70,
                   "este_pc": {"modo": "amarelo", "agentes": 2, "agentes_torre": 1, "agentes_sessao": 1, "sessoes": 2,
                               "torre_usd_hoje": 17.8, "torre_pct_hoje": 4.1, "torre_chamadas_hoje": 222, "fonte": "api",
                               "erro_api": "RuntimeError: HTTP 429"}}):
        buf = np.zeros((120, 360, 4), np.uint8)
        ctx = H.Tela(skia.Surface(buf, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType).getCanvas())
        H.painel(ctx, 260, 14, r)
        H.pilula(ctx, 348, 30, r, True)
        assert buf[:, :, 3].max() > 0


def test_memoria_de_desenho_da_o_mesmo_que_desenhar_directo():
    """A copia da memoria tem de ser igual (a menos de arredondamento) ao desenho ao vivo - senao a optimizacao mente."""
    import numpy as np
    import hologramas as H
    q = {"sessao": 30, "semana": 60, "fable": 20, "ritmo": 1, "cabe": 2, "forca_real": 0.5, "agentes": 0, "reinicioS": 3600,
         "reinicioW": 90000, "sev": 0, "modo": 0, "torre_usd": 1}
    a = _desenhar("m9", q, quadros=6, sem_memoria=True)    # tudo ao vivo, os seis quadros
    b = _desenhar("m9", q, quadros=6)                      # do terceiro em diante os aneis vem da memoria (mesma cota)
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 3



# 10 min quase vazia)
def test_barras_do_m9_como_ele_pediu():
    assert H.barras_uso(0, 30) == 0 and H.barras_uso(100, 30) == 30 and H.barras_uso(50, 30) == 15
    assert H.barras_tempo(5 * 3600, 5 * 3600, 40) == 40
    assert H.barras_tempo(2.5 * 3600, 5 * 3600, 40) == 20
    assert 1 <= H.barras_tempo(600, 5 * 3600, 40) <= 2
    assert H.barras_tempo(0, 5 * 3600, 40) == 0 and H.barras_tempo(None, 5 * 3600, 40) == 0
    assert H.barras_tempo(7 * 86400, 7 * 86400, 40) == 40


@given(st.floats(allow_nan=True, allow_infinity=True), st.integers(1, 60))
@settings(max_examples=300, deadline=None)
def test_barras_nunca_saem_do_anel(v, n):
    assert 0 <= H.barras_uso(v, n) <= n
    assert 0 <= H.barras_tempo(v, 5 * 3600, n) <= n


@given(st.floats(0, 5 * 3600), st.floats(0, 5 * 3600))
@settings(max_examples=300, deadline=None)
def test_menos_tempo_nunca_tem_mais_barras(a, b):
    lo, hi = min(a, b), max(a, b)
    assert H.barras_tempo(lo, 5 * 3600, 40) <= H.barras_tempo(hi, 5 * 3600, 40)


def test_sessoes_activas_conta_so_as_principais_recentes():
    agora = 10_000.0
    q = {"C:/u/.claude/projects/p/aaa.jsonl": {"mtime": agora - 60},
         "C:/u/.claude/projects/p/bbb.jsonl": {"mtime": agora - 3 * 3600},
         "C:/u/.claude/projects/p/aaa/subagents/agent-x.jsonl": {"mtime": agora - 5},
         "C:/u/.claude/projects/q/agent-y.jsonl": {"mtime": agora - 5},
         "C:/u/.claude/projects/q/ccc.jsonl": {"mtime": agora - 100}}
    assert D.sessoes_activas(q, agora) == 2
    assert D.sessoes_activas({}, agora) == 0 and D.sessoes_activas(None, agora) == 0



def test_rodar_e_a_forca():
    assert H.velocidade_do_gasto(0) == H.RAD_S_PARADO
    assert abs(H.velocidade_do_gasto(1.0) - (H.RAD_S_PARADO + H.RAD_S_POR_FORCA)) < 1e-9
    assert H.velocidade_do_gasto(1.4) > 3.5                       # a 1,4x ve-se a rodar depressa
    assert H.velocidade_do_gasto(2) > H.velocidade_do_gasto(1.4) > H.velocidade_do_gasto(1) > H.velocidade_do_gasto(.5)
    assert H.velocidade_do_gasto(None) == H.RAD_S_PARADO and H.velocidade_do_gasto(-5) == H.RAD_S_PARADO
    assert H.velocidade_do_gasto(10_000) == H.RAD_S_MAX


def test_reinicio_partilhado_e_projeccao():
    agora = 1_000_020.0
    r, e = D.resets_efectivos({"sessao": agora - 1800, "semana": agora + 3600}, {"sessao": agora + 4 * 3600}, agora)
    assert r["sessao"] == agora + 4 * 3600 and r["semana"] == agora + 3600 and not e
    r, e = D.resets_efectivos({"sessao": agora - 1800}, {}, agora)
    assert r["sessao"] == agora - 1800 + 5 * 3600 and e == {"sessao"}
    m = D.montar([(agora - 6 * 3600, 60.0, 70.0, None)], agora, {"sessao": agora + 3600, "semana": agora + 86400})
    assert m["sessao"] == 0.0 and m["sessao_velha"] and m["reinicio_sessao_s"] == 3600


@given(st.one_of(st.none(), st.floats(-1e7, 1e7)), st.one_of(st.none(), st.floats(-1e7, 1e7)))
@settings(max_examples=300, deadline=None)
def test_reinicio_efectivo_nunca_no_passado(proprio, partilhado):
    r, _e = D.resets_efectivos({"sessao": proprio}, {"sessao": partilhado}, 0.0)
    assert r["sessao"] is None or r["sessao"] > 0.0


@given(st.floats(allow_nan=True, allow_infinity=True))
@settings(max_examples=300, deadline=None)
def test_velocidade_sempre_finita_e_limitada(v):
    x = H.velocidade_do_gasto(v)
    assert H.RAD_S_PARADO <= x <= H.RAD_S_MAX


# horas diferentes (aqui 4 programas escrevem nele, la so o medidor), e a API da % inteiras. Depois a ordem: "os medidores de
# cota medem a CONTA inteira e nao os dispositivos individuais". A regra que fica: com a MESMA serie da API, as duas maquinas
# mostram os MESMOS numeros da conta, a mesma cor e a mesma velocidade - nem a hora do pedido dentro do passo, nem as leituras
# (dentro do passo), nem os decimos de segundo do resets_at mudam um numero da conta.
def _linha_outro(t, s, w, f=None, quem=None):
    """Uma leitura de outro programa que tambem le a conta: sem "grade"."""
    l = {"t": dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds"), "sessao": s, "semana": w, "auto": True}
    if f is not None:
        l["por_modelo"] = {"Fable": f}
    if quem:
        l["quem"] = quem
    return l


def _ler(linhas):
    """As linhas como o Coletor as le do ficheiro: texto JSON -> (leituras, grade)."""
    js = D.ler_linhas_json("\n".join(json.dumps(l, ensure_ascii=False) for l in linhas) + "\n")
    return D.leituras_do_historico(js), D.grade_do_historico(js)


def _minuto(ts):
    return round(ts / 60.0) * 60.0


def _conta(r):
    """Os numeros da CONTA de um retrato (os reinicios em instante absoluto: o "faltam" conta-se do relogio de cada maquina)."""
    c = {k: r[k] for k in D.CHAVES_DA_CONTA}
    c["janelas"] = [(j["nome"], j["utilizacao"], j["real"], j["cabe"], j["ritmo_de"]) for j in r["janelas"]]
    for k in ("reinicio_sessao_s", "reinicio_semana_s"):
        c[k] = None if r[k] is None else r["t"] + r[k]
    return c


def _forca_antiga(leituras, agora, resets):
    """A conta de ANTES de 09/10 (o montar de 04/10, so a forca): ritmo sobre o historico LOCAL ate "agora", o que cabe a
    partir de "agora". Fica so para provar a diferenca."""
    s, w, f, _t = D._ultimos_valores(leituras)
    js = []
    for nome, idx, mins, util, k in (("sessao", 1, D.JANELA_RITMO_SESSAO_MIN, s, "sessao"),
                                     ("semana", 2, D.JANELA_RITMO_SEMANA_MIN, w, "semana"),
                                     ("Fable", 3, D.JANELA_RITMO_SEMANA_MIN, f, "fable")):
        real = D.ritmo_real([(p[0], p[idx]) for p in leituras if p[idx] is not None], agora, mins)
        js.append({"nome": nome, "utilizacao": util, "real": real,
                   "cabe": D.cabe_por_hora("sessao" if nome == "sessao" else "semana", util, (resets[k] - agora) / 3600.0)})
    return D.forca_e_aperto(js)["forca"]


def test_linha_da_grade_volta_a_ler_se():
    l = D.linha_da_grade({"sessao": 24.0, "semana": 61.0, "fable": 19.0}, G0 + 5.7, G0)
    assert l["grade"] == "2026-10-03T04:00:00+00:00" and l["quem"] == "medidor_cota" and l["auto"] is True
    assert D.ts_iso(l["t"]) == G0 + 5                                   # "t" continua a ser a hora real (ao segundo)
    leit, grade = _ler([l])
    assert grade == [(G0, 24.0, 61.0, 19.0, G0 + 5)] and leit == [(G0 + 5, 24.0, 61.0, 19.0)]
    depois = D.linha_da_grade({"sessao": 99.0, "semana": 99.0}, G0 + 30, G0)    # reabriu a meio do passo: fica a primeira
    lixo = dict(l, grade=D.iso_utc(G0 - 3600))                          # hora real fora do proprio passo: fica de fora
    outro = _linha_outro(G0 + 60, 25.0, 61.0)                           # sem "grade": so entra nas leituras
    leit, grade = _ler([depois, lixo, outro, l])
    assert grade == [(G0, 24.0, 61.0, 19.0, G0 + 5)] and len(leit) == 4


def test_grade_cobre_a_janela():
    pts = [(G0 + k * PASSO, 1.0) for k in range(20)]                    # 57 min de grelha
    fim = pts[-1][0]
    assert D.grade_cobre(pts, fim, 40)
    assert not D.grade_cobre(pts[-8:], fim, 40)                         # so 21 min: ainda nao chega ao inicio da janela
    sem_inicio = [p for p in pts if p[0] not in (fim - 13 * PASSO, fim - 12 * PASSO)]
    assert D.grade_cobre(sem_inicio, fim, 40)                           # uma recusa no inicio nao troca a fonte
    assert not D.grade_cobre([], fim, 40) and not D.grade_cobre(pts, fim, 1440)


@st.composite
def _duas_maquinas(draw):
    """A MESMA serie da API vista por duas maquinas. A (como a outra maquina): so os pontos da grelha. B (como este PC): os
    pontos pedidos a outras horas dentro do passo, leituras de outros programas fora da grelha e, as vezes, um segundo pedido
    no mesmo passo (o medidor reabriu). Os buracos (PC desligado, API a recusar as duas) sao os mesmos - a mesma serie."""
    rnd = random.Random(draw(st.integers(0, 2 ** 32 - 1)))
    n = draw(st.one_of(st.integers(2, 45), st.integers(482, 520)))     # curta (a grelha ainda nao cobre) ou mais de 24 h
    p_falta = draw(st.sampled_from([0.0, 0.0, 0.05, 0.25]))
    s, w, f = rnd.randint(0, 60), rnd.randint(0, 80), rnd.randint(0, 80)
    api = []
    for k in range(n):
        if rnd.random() < 0.02:
            s = rnd.randint(0, 3)                                       # a sessao reinicia a meio
        s = min(100, s + rnd.choice((0, 0, 0, 1, 1, 2)))
        w = min(100, w + (rnd.random() < 0.08))
        f = min(100, f + (rnd.random() < 0.05))
        if k == n - 1 or rnd.random() >= p_falta:
            api.append((G0 + k * PASSO, float(s), float(w), None if rnd.random() < 0.03 else float(f)))
    g_ult = api[-1][0]

    def pedido():                                                       # dentro da janela do passo + ate 25 s de resposta
        return rnd.uniform(D.ATRASO_GRADE_S, D.TOLERANCIA_GRADE_S) + rnd.uniform(0, 25)

    a = [D.linha_da_grade({"sessao": s_, "semana": w_, "fable": f_}, g + pedido(), g) for g, s_, w_, f_ in api]
    b = []
    for g, s_, w_, f_ in api:
        t = g + pedido()
        b.append(D.linha_da_grade({"sessao": s_, "semana": w_, "fable": f_}, t, g))
        if rnd.random() < 0.05:
            b.append(D.linha_da_grade({"sessao": float(rnd.randint(0, 100)), "semana": w_}, t + rnd.uniform(1, 30), g))
    for _ in range(rnd.randint(0, 3 * len(api))):                       # os outros programas, a qualquer hora
        g, s_, w_, f_ = rnd.choice(api)
        t = min(g + rnd.uniform(-175, 175), g_ult + 70)
        ruido = rnd.choice((-1, 0, 0, 1, 2)) if rnd.random() < 0.9 else rnd.randint(-50, 50)
        b.append(_linha_outro(t, float(max(0, min(100, s_ + ruido))), w_, f_, rnd.choice((None, "outro_programa"))))
    rnd.shuffle(b)                                                      # no ficheiro, as linhas de varios programas misturam-se
    agora_a = g_ult + rnd.uniform(75, PASSO + 60)                       # cada uma olha a sua hora, dentro do passo seguinte
    agora_b = g_ult + rnd.uniform(75, PASSO + 60)
    base = {"sessao": _minuto(g_ult + rnd.uniform(PASSO + 300, 5 * 3600 - 60)),
            "semana": _minuto(g_ult + rnd.uniform(3600, 7 * 86400)), "fable": _minuto(g_ult + rnd.uniform(3600, 7 * 86400))}
    res_a = {k: v + rnd.uniform(-0.99, 0.99) for k, v in base.items()}
    res_b = {k: v + rnd.uniform(-0.99, 0.99) for k, v in base.items()}
    pc_a = {"agentes": 0, "agentes_torre": 0, "agentes_sessao": 0, "eventos": 0, "chamadas_min": 0, "sessoes": 1,
            "modo": None, "torre_usd_hoje": 0.0, "erro_api": None}
    pc_b = {"agentes": rnd.randint(0, 9), "agentes_torre": rnd.randint(0, 4), "agentes_sessao": rnd.randint(0, 5),
            "eventos": rnd.randint(0, 5000), "chamadas_min": rnd.randint(0, 120), "sessoes": rnd.randint(0, 6),
            "modo": rnd.choice(("verde", "amarelo", "vermelho", "economico")), "torre_usd_hoje": rnd.uniform(0, 90),
            "erro_api": rnd.choice((None, "RuntimeError: HTTP 429"))}
    return a, b, agora_a, agora_b, res_a, res_b, pc_a, pc_b, n, p_falta


@given(c=_duas_maquinas())
@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_duas_maquinas_com_a_mesma_serie_da_api_mostram_os_mesmos_numeros_da_conta(c):
    """A propriedade central: a mesma serie da API, historicos locais diferentes (leituras dos outros programas, pedidos a
    outras horas do passo, um segundo pedido no mesmo passo) e tudo o resto deste PC diferente (pulso, agentes, sessoes, modo
    e gasto da app, um 429) -> EXACTAMENTE os mesmos numeros da conta, a mesma cor e a mesma velocidade do holograma."""
    a, b, agora_a, agora_b, res_a, res_b, pc_a, pc_b, n, p_falta = c
    _la, grade_a = _ler(a)
    _lb, grade_b = _ler(b)
    ra = D.montar(grade_a, agora_a, res_a, pc_a)
    rb = D.montar(grade_b, agora_b, res_b, pc_b)
    assert _conta(ra) == _conta(rb)                                     # sessao, semana, Fable, forca, ritmo, cabe, tecto...
    assert ra["severidade"] == rb["severidade"]                         # a mesma cor
    assert H.velocidade_do_gasto(ra["forca"]) == H.velocidade_do_gasto(rb["forca"])   # a mesma velocidade
    assert ra["este_pc"] == pc_a and rb["este_pc"] == pc_b              # o que e deste PC fica a parte, tal e qual
    assert ra["sessao"] is not None and all(j["ritmo_de"] in ("grade", "grade_parcial") for j in ra["janelas"])
    if p_falta == 0 and n >= 482:                                       # mais de 24 h sem buracos: a grelha cobre as janelas
        assert ra["janelas"][0]["ritmo_de"] == ra["janelas"][1]["ritmo_de"] == "grade"


def test_o_caso_real_a_mesma_cota_e_forcas_diferentes():
    """O caso real, ao minimo: a mesma serie da API (+1 ponto a cada 4 min); na maquina A so o medidor le (pontos da
    grelha, 6 s depois da fronteira), aqui o medidor le 9 s depois e outro programa leu 5 s depois do inicio da janela de
    40 min, ainda antes de a % subir. Pela conta antiga as forcas diferem ~10%; pela grelha sao iguais."""
    api = [(G0 + k * PASSO, float(10 + (k * 3) // 4), 40.0, 20.0) for k in range(41)]
    g_ult = api[-1][0]
    agora = g_ult + 60
    resets = {"sessao": g_ult + 3 * 3600, "semana": g_ult + 3 * 86400, "fable": g_ult + 3 * 86400}
    huntai = [D.linha_da_grade({"sessao": s, "semana": w, "fable": f}, g + 6, g) for g, s, w, f in api]
    aqui = [D.linha_da_grade({"sessao": s, "semana": w, "fable": f}, g + 9, g) for g, s, w, f in api]
    ini = agora - D.JANELA_RITMO_SESSAO_MIN * 60
    primeiro = next(p for p in api if p[0] + 6 >= ini)
    aqui.append(_linha_outro(ini + 5, primeiro[1] - 1, 40.0, 20.0))
    (la_l, la_g), (ca_l, ca_g) = _ler(huntai), _ler(aqui)
    antiga = [_forca_antiga(l, agora, resets) for l in (la_l, ca_l)]
    nova = [D.montar(g, agora, resets) for g in (la_g, ca_g)]
    assert abs(antiga[0] - antiga[1]) / antiga[0] > 0.05                 # a mesma cota, forcas diferentes
    assert _conta(nova[0]) == _conta(nova[1]) and nova[0]["ritmo_de"] == "grade"
    assert nova[0]["forca_janela"] == "sessao" and nova[0]["sessao"] == 40.0


def test_dois_coletores_num_relogio_falso_mostram_a_mesma_conta(tmp_path, monkeypatch):
    """Ponta a ponta: dois Coletor (as "maquinas" A e B), cada um com o seu historico e a mesma API falsa, com voltas a horas
    diferentes dentro do passo. A B tem ainda tudo o que e de um PC com app: leituras de outros programas no historico, o
    modo da app a vermelho, gasto da app hoje, 4 agentes da app, uma sessao a fazer chamadas (o pulso). Cada um pede UMA
    vez por passo, grava "grade", recua os mesmos passos depois de uma recusa e no fim mostram os MESMOS numeros da conta, a
    mesma cor e a mesma velocidade - a olhar a horas diferentes; o que e deste PC fica em retrato["este_pc"]."""
    for nome in ("PROJECTOS", "LIVRO_TORRE", "MODO_COTA", "PARECER", "TORRE_JSON", "CALIBRACAO_TORRE", "RESET_COTA"):
        monkeypatch.setattr(D, nome, tmp_path / ("nada_" + nome.lower()), raising=False)
    modo_b, livro_b, proj_b = tmp_path / "modo_b.json", tmp_path / "livro_b.jsonl", tmp_path / "proj_b"
    (proj_b / "x").mkdir(parents=True)
    transcrito_b = proj_b / "x" / "sessao_b.jsonl"
    transcrito_b.write_text("", encoding="utf-8")
    modo_b.write_text(json.dumps({"modo": "vermelho", "porque": "apertado", "t_iso": "2026-10-04T01:00:00-03:00"}),
                      encoding="utf-8")
    lugares = {"A": {"MODO_COTA": tmp_path / "nada_modo", "LIVRO_TORRE": tmp_path / "nada_livro", "PROJECTOS": tmp_path / "nada"},
               "B": {"MODO_COTA": modo_b, "LIVRO_TORRE": livro_b, "PROJECTOS": proj_b}}
    rnd = random.Random(9102026)
    ini_vivo, n_vivo = 500, 30                    # 500 passos (25 h) ja no historico; 30 vividos pelos Coletor
    falha = ini_vivo + 10                         # a API recusa esse passo as duas maquinas (429)
    rs = G0 + (ini_vivo + n_vivo) * PASSO + 2 * 3600
    rw = G0 + 4 * 86400

    def valores(k):
        return float((k // 3) % 95), 20.0 + k // 40, 5.0 + k // 60

    def iso(ts):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()

    relogio, quem, pedidos = [0.0], ["A"], {"A": [], "B": []}

    class Api:
        def token(self):
            return "tok", {}

        def pede(self, tok):
            g = D.fronteira_grade(relogio[0])
            pedidos[quem[0]].append(g)
            k = int(round((g - G0) / PASSO))
            if k == falha:
                raise RuntimeError("HTTP 429: rate_limit_error")
            s, w, f = valores(k)
            frac = rnd.uniform(-0.9, 0.9)
            return {"five_hour": {"utilization": s, "resets_at": iso(rs + frac)},
                    "seven_day": {"utilization": w, "resets_at": iso(rw + frac)},
                    "limits": [{"percent": f, "resets_at": iso(rw + frac), "scope": {"model": {"display_name": "Fable"}}}]}

    import fonte_oauth as _FO                             # a leitura opcional (o token fica la dentro)
    monkeypatch.setattr(_FO, "ler_uso", lambda *a_, **k_: Api().pede(Api().token()))
    hist = {"A": tmp_path / "hist_a.jsonl", "B": tmp_path / "hist_b.jsonl"}
    for nome, p in hist.items():                  # o passado: 25 h de grelha (a B com outro programa a ler de 10 em 10 min)
        with open(p, "w", encoding="utf-8") as fh:
            for k in range(ini_vivo):
                g = G0 + k * PASSO
                s, w, f = valores(k)
                d = rnd.uniform(D.ATRASO_GRADE_S, D.TOLERANCIA_GRADE_S) + rnd.uniform(0, 20)
                fh.write(json.dumps(D.linha_da_grade({"sessao": s, "semana": w, "fable": f}, g + d, g)) + "\n")
                if nome == "B" and k % 10 == 3:
                    fh.write(json.dumps(_linha_outro(g + 64, max(0.0, s - 1), w, f)) + "\n")
    with open(livro_b, "w", encoding="utf-8") as fh:
        quando = dt.datetime.fromtimestamp(G0 + (ini_vivo - 1) * PASSO, D.BRT).isoformat(timespec="seconds")
        for _i in range(3):
            fh.write(json.dumps({"t_iso": quando, "custo_tabela_usd": 2.5, "lancou": True}) + "\n")
    col = {n: D.Coletor({}, ler_api=True, relogio=lambda: relogio[0], hist_cota=hist[n]) for n in hist}
    col["A"]._processos_torre = lambda agora: 0       # sem varrer os processos reais desta maquina

    def torre_b(agora):
        col["B"]._agentes_torre = 4
        return 0
    col["B"]._processos_torre = torre_b

    def volta(nome, t):
        for k_, v_ in lugares[nome].items():          # o "PC" de cada uma: o modo, o livro e os transcritos dela
            monkeypatch.setattr(D, k_, v_)
        relogio[0], quem[0] = t, nome
        col[nome].volta(t)
        if col[nome]._fio_api is not None:
            col[nome]._fio_api.join(10)
            col[nome]._fio_api = None

    toques = {"A": (2, 9, 50, 120), "B": (6, 44, 100, 170)}    # cedo / pede / tarde / meio do passo
    for k in range(ini_vivo, ini_vivo + n_vivo):
        g = G0 + k * PASSO
        with open(transcrito_b, "a", encoding="utf-8") as fh:  # a sessao da B a responder: o pulso deste PC
            for i in range(2):
                fh.write(json.dumps({"type": "assistant", "message": {"id": "msg_%d_%d" % (k, i)}}) + "\n")
        for nome in ("A", "B"):
            for d in toques[nome]:
                volta(nome, g + d + rnd.uniform(0, 0.9))
        if k % 3 == 0:                            # aqui (B) os outros programas leem a meio do passo, as vezes 1 ponto abaixo
            s, w, f = valores(k)
            with open(hist["B"], "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_linha_outro(g + 95, max(0.0, s - k % 2), w, f)) + "\n")
    g_fim = G0 + (ini_vivo + n_vivo - 1) * PASSO
    volta("A", g_fim + 130)
    volta("B", g_fim + 175)
    ra, rb = col["A"].retrato(), col["B"].retrato()

    esperados = sorted([G0 + k * PASSO for k in range(ini_vivo, ini_vivo + n_vivo)] + [G0 + falha * PASSO])
    assert pedidos["A"] == pedidos["B"] == esperados
    for p in hist.values():
        vivos = [d for d in D.ler_linhas_json(p.read_text(encoding="utf-8"))
                 if d.get("grade") and D.ts_iso(d["grade"]) >= G0 + ini_vivo * PASSO]
        assert [D.ts_iso(d["grade"]) for d in vivos] == [G0 + k * PASSO for k in range(ini_vivo, ini_vivo + n_vivo) if k != falha]
        assert all(D.ATRASO_GRADE_S - 1 <= D.ts_iso(d["t"]) - D.ts_iso(d["grade"]) <= D.TOLERANCIA_GRADE_S for d in vivos)
        assert all(d["quem"] == "medidor_cota" for d in vivos)
    assert _conta(ra) == _conta(rb) and ra["forca"] is not None
    assert [j["ritmo_de"] for j in ra["janelas"]] == ["grade"] * 3
    assert H.velocidade_do_gasto(ra["forca"]) == H.velocidade_do_gasto(rb["forca"])
    # ESTE PC: diferente, e so ai
    pa, pb = ra["este_pc"], rb["este_pc"]
    assert (pa["agentes"], pa["eventos"], pa["sessoes"]) == (0, 0, 0)
    assert pb["sessoes"] == 1 and pb["eventos"] > 0 and "modo" not in pb and "torre_usd_hoje" not in pb
    assert pa["fonte"] == pb["fonte"] == "api"
    # pela conta antiga (historico local ate "agora") as duas diferiam: o teste morde
    leit = {n: D.leituras_do_historico(D.ler_linhas_json(p.read_text(encoding="utf-8"))) for n, p in hist.items()}
    assert _forca_antiga(leit["A"], ra["t"], pa["resets"]) != _forca_antiga(leit["B"], rb["t"], pb["resets"])
    est = col["A"].estado_para_guardar()                  # ao reabrir, nao repete o ultimo passo
    assert est["ultima_grade_ts"] == esperados[-1] and est["ultima_api_ts"] > esperados[-1]


def test_o_janela_poe_so_a_conta_na_cor_e_na_forca():
    """O retrato chega ao holograma pelo janela (_actualizar_q): com a mesma conta e o resto deste PC diferente (agentes,
    pulso, modo vermelho, gasto da app), a forca, a cor e os aneis sao os mesmos; os agentes e o app chegam so onde sao
    deste PC (as pas, a linha PC)."""
    import types
    import janela as J
    conta = {"sessao": 41.0, "semana": 63.0, "fable": 22.0, "forca": 1.23, "ritmo": 14.0, "cabe": 11.4,
             "reinicio_sessao_s": 7200.0, "reinicio_semana_s": 300000.0}

    def q_de(este_pc):
        r = dict(conta, este_pc=este_pc)
        med = J.Medidor.__new__(J.Medidor)
        med.col = types.SimpleNamespace(retrato=lambda: r)
        med._pulsos, med._eventos_vistos, med._forca_antes, med.min, med.minimizou_ts = [], None, None, False, None
        med._ult_ret, med.registo, med._minimizar = {}, (lambda t: None), (lambda *a, **k: None)
        J.Medidor._actualizar_q(med, 0.0)
        return ({k: getattr(H.Q, k) for k in ("sessao", "semana", "fable", "forca_real", "ritmo", "cabe", "sev")},
                H.Q.agentes)
    qa, pa = q_de({"agentes": 0, "eventos": 0, "chamadas_min": 0})
    qb, pb = q_de({"agentes": 6, "eventos": 40, "chamadas_min": 12})
    assert qa == qb and qa["sev"] == D.severidade(None, 1.23) == 1      # the colour is the account's force (1.23x = amber)
    assert pa == 0 and pb == 6


def test_grade_parcial_logo_apos_actualizar_e_sem_grade_quando_a_conta_cala():
    """Acabado de actualizar (12 min de grelha): os numeros da conta ja saem dela, com o ritmo dos pontos que ha
    ("grade_parcial"). Sem ponto da conta ha mais de 40 min (a API a recusar, o PC a dormir): os valores ficam (e o LT diz
    a idade), mas a forca fica "a medir" - nunca um ritmo de ha uma hora a passar por actual. Sem ponto nenhum: tudo "—"."""
    grade = [(G0 + k * PASSO, 10.0 + k, 30.0, 5.0, G0 + k * PASSO + 6) for k in range(5)]
    res = {"sessao": G0 + 4 * 3600, "semana": G0 + 3 * 86400, "fable": G0 + 3 * 86400}
    r = D.montar(grade, grade[-1][0] + 60, res)
    assert r["sessao"] == 14.0 and r["forca"] is not None and r["forca_janela"] == "sessao"
    assert [j["ritmo_de"] for j in r["janelas"]] == ["grade_parcial"] * 3 and math.isclose(r["ritmo"], 20.0)
    velha = D.montar(grade, grade[-1][0] + 41 * 60, res)
    assert velha["sessao"] == 14.0 and velha["forca"] is None and velha["ritmo_de"] == "sem_grade"
    assert velha["leitura_ts"] == grade[-1][4] and velha["severidade"] == 0
    nada = D.montar([], G0, res)
    assert nada["sessao"] is None and nada["forca"] is None and nada["leitura_ts"] is None


ESTADO_DESTE_PC = {"fl", "flash", "ult", "q", "y", "b", "fa", "ex"}   # o clarao, a batida, as bolhas, as faiscas


def _estado_da_conta(hid, st_):
    """O estado de um holograma que tem de vir SO da conta (rotacoes, fases, valores suavizados): tudo menos o que reage
    ao pulso deste PC."""
    fora = set(ESTADO_DESTE_PC) | ({"p"} if hid == "m8" else set())  # no Nucleo dos agentes (m8) o "p" e o clarao
    out = {}
    for k, v in st_.items():
        if k in fora:
            continue
        if k == "on":                                                # pulsar: os aneis da forca ("fraco"); os fortes sao chamadas
            out[k] = [o["r"] for o in v if o.get("fraco")]
        elif isinstance(v, list):                                    # as particulas (vortex, particulas): os angulos
            out[k] = [p.get("a") for p in v if isinstance(p, dict)]
        else:
            out[k] = v
    return out


@pytest.mark.parametrize("hid", ["m9"])
def test_cada_holograma_roda_a_velocidade_da_conta_e_nao_a_deste_pc(hid):
    """A mesma conta e, deste PC, tudo diferente (6 agentes, uma chamada a cada 3 quadros, modo vermelho, US$ 55 da app):
    cada um dos 18 hologramas fica no MESMO estado de rotacao - so o clarao, as pas e as bolhas (deste PC) mudam."""
    import numpy as np
    import skia
    conta = {"sessao": 41.0, "semana": 63.0, "fable": 22.0, "ritmo": 14.0, "cabe": 11.4, "forca_real": 1.23,
             "reinicioS": 7200.0, "reinicioW": 300000.0, "sev": D.severidade(None, 1.23)}

    def rodar(agentes, chama, modo, gasto_local):
        for k, v in dict(conta, agentes=agentes, modo=modo, usd=gasto_local).items():
            setattr(H.Q, k, v)
        H.Q.chamadas, H.Q.ultima_chamada = [], 0.0
        random.seed(20261009)                                        # os hologramas de particulas nascem ao acaso
        buf = np.zeros((300, 260, 4), np.uint8)
        ctx = H.Tela(skia.Surface(buf, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType).getCanvas())
        ctx.largura = 260
        st_ = {}
        for i in range(36):
            if chama and i % 3 == 1:
                H.Q.ultima_chamada = 1000.0 + i
                H.Q.chamadas.append(1000.0 + i)
            H.HOLOGRAMAS[hid][0](ctx, 260, 260, i / 20.0, 1 / 20.0, st_)
        return st_
    a = rodar(0, False, 0, 0.0)
    b = rodar(6, True, 2, 55.0)
    assert _estado_da_conta(hid, a) == _estado_da_conta(hid, b)


def test_reabrir_nao_repete_o_passo_nem_encurta_o_recuo(tmp_path, monkeypatch):
    """Ele fecha e abre o medidor pelo atalho: reaberto a meio do passo nao pede outra vez. 10/10 22:5x: uma recusa da UMA 2.a
    tentativa no mesmo passo (perde-se se o medidor for reaberto - o passo seguinte e normal); com 4+ recusas seguidas salta-se
    1 passo, e esse recuo fica no estado.json: reabrir nao o encurta."""
    for nome in ("PROJECTOS", "LIVRO_TORRE", "MODO_COTA", "PARECER", "TORRE_JSON", "CALIBRACAO_TORRE", "RESET_COTA"):
        monkeypatch.setattr(D, nome, tmp_path / ("nada_" + nome.lower()), raising=False)
    relogio, recusa, pedidos = [0.0], [True], []

    class Api:
        def token(self):
            return "tok", {}

        def pede(self, tok):
            pedidos.append(relogio[0])
            if recusa[0]:
                raise RuntimeError("HTTP 429: rate_limit_error")
            return {"five_hour": {"utilization": 7.0, "resets_at": D.iso_utc(G0 + 4 * 3600)}}

    import fonte_oauth as _FO                             # a leitura opcional (o token fica la dentro)
    monkeypatch.setattr(_FO, "ler_uso", lambda *a_, **k_: Api().pede(Api().token()))

    def abrir(estado):
        c = D.Coletor(estado, ler_api=True, relogio=lambda: relogio[0], hist_cota=tmp_path / "hist.jsonl")
        c._processos_torre = lambda agora: 0
        c.sorteio = lambda: 0.0
        return c

    def volta(c, t):
        relogio[0] = t
        c.volta(t)
        if c._fio_api is not None:
            c._fio_api.join(10)
            c._fio_api = None

    c = abrir({})
    volta(c, G0 + 6)                                         # 1.a recusa -> 2.a tentativa as G0 + 26
    c = abrir(c.estado_para_guardar())                       # fechou e abriu no mesmo passo: nao repete o passo
    volta(c, G0 + 30)
    assert pedidos == [G0 + 6]
    volta(c, G0 + PASSO + 6)                                 # o passo seguinte e normal (2.a recusa seguida)
    assert pedidos == [G0 + 6, G0 + PASSO + 6] and c.retentar_em == G0 + PASSO + 26
    volta(c, G0 + PASSO + 27)                                # 3.a recusa (a 2.a tentativa): o passo seguinte continua normal
    assert c.recuo_ate is None and c.falhas_api == 3
    volta(c, G0 + 2 * PASSO + 6)                             # 4.a recusa -> 2.a tentativa
    volta(c, G0 + 2 * PASSO + 27)                            # 5.a recusa -> salta 1 passo: so volta a pedir em G0 + 4P
    assert c.recuo_ate == G0 + 4 * PASSO and len(pedidos) == 5
    c = abrir(c.estado_para_guardar())                       # reaberto a meio do recuo: nao o encurta
    volta(c, G0 + 3 * PASSO + 6)
    assert len(pedidos) == 5
    recusa[0] = False
    volta(c, G0 + 4 * PASSO + 6)
    assert pedidos[-1] == G0 + 4 * PASSO + 6 and c.falhas_api == 0
    c = abrir(c.estado_para_guardar())                       # reabriu no mesmo passo, depois de ja ter lido
    volta(c, G0 + 4 * PASSO + 30)
    assert len(pedidos) == 6


def test_09out_a_semana_parcial_nao_manda_na_forca():
    """09/10 21:22: esta maquina (grelha desde as 19:15) via a semana parcial a 2,16 %/h e a forca 1,30x vinha dela; a outra maquina
    (grelha desde as 20:36) via a semana parada e a forca 0,02x vinha da sessao - cores diferentes para a mesma conta. A semana
    (e o Fable) parcial ja nao compete; a sessao manda nas duas e a forca e a cor ficam iguais."""
    res = {"sessao": G0 + 2 * 3600, "semana": G0 + 3 * 86400, "fable": G0 + 3 * 86400}
    # A: 2 h de grelha com a semana a subir 1 ponto por hora; B: so os ultimos 45 min (a semana parada). Sessao igual nas duas.
    a = [(G0 - k * PASSO, 45.0, 30.0 - k * PASSO / 3600.0, 0.0, G0 - k * PASSO + 6) for k in range(40)][::-1]
    b = [(G0 - k * PASSO, 45.0, 30.0, 0.0, G0 - k * PASSO + 6) for k in range(15)][::-1]
    ra, rb = D.montar(a, G0 + 60, res), D.montar(b, G0 + 60, res)
    assert ra["forca_janela"] == rb["forca_janela"] and ra["forca"] == rb["forca"] and ra["severidade"] == rb["severidade"]
    assert [j["ritmo_de"] for j in ra["janelas"]][1] == "grade_parcial"          # a semana continua no painel, so nao manda


def test_10out_sem_api_o_medidor_empresta_as_leituras_dos_outros_programas():
    """10/10: a API recusou (429) tres vezes seguidas, o medidor recuou ate 30 min e ficou nos 22% da ultima grelha
    enquanto outro programa gravava 49-50% da MESMA conta no mesmo historico. As leituras dos outros programas valem como pontos emprestados, so DEPOIS do ultimo proprio."""
    grade = [(G0 - PASSO, 22.0, 49.0, 1.0, G0 - PASSO + 6), (G0, 22.0, 49.0, 1.0, G0 + 6)]
    linhas = [
        {"t": D.iso_utc(G0 - PASSO + 30), "sessao": 21.0, "semana": 49.0, "quem": "outro_programa"},   # antes do ultimo proprio: fora
        {"t": D.iso_utc(G0 + 2 * PASSO + 10), "sessao": 44.0, "semana": 52.0, "quem": "outro_programa"},
        {"t": D.iso_utc(G0 + 2 * PASSO + 130), "sessao": 45.0, "semana": 52.0, "quem": "amostra"},   # mesmo passo: fica a mais recente
        {"t": D.iso_utc(G0 + 4 * PASSO + 5), "sessao": 49.0, "semana": 53.0, "auto": True},
        {"t": D.iso_utc(G0 + 3 * PASSO + 5), "sessao": 47.0, "grade": D.iso_utc(G0 + 3 * PASSO)},     # de um medidor: nao se empresta
        {"t": D.iso_utc(G0 + 5 * PASSO), "erro": "429"},                                              # sem sessao: fora
    ]
    agora = G0 + 4 * PASSO + 60
    emp = D.pontos_emprestados(linhas, grade, agora)
    assert [(p[0], p[1], p[2], p[3]) for p in emp] == [(G0 + 2 * PASSO, 45.0, 52.0, None), (G0 + 4 * PASSO, 49.0, 53.0, None)]
    r = D.montar(grade + emp, agora, {"sessao": G0 + 2 * 3600, "semana": G0 + 3 * 86400, "fable": None})
    assert r["janelas"][0]["utilizacao"] == 49.0 and r["janelas"][1]["utilizacao"] == 53.0, "mostra a leitura fresca, nao os 22%"
    assert D.pontos_emprestados(linhas, grade, agora + 3 * 3600) == [], "leituras com mais de 2 h nao valem"
    assert D.pontos_emprestados([], grade, agora) == []
    assert D.pontos_emprestados(linhas, [], agora)[0][0] == G0 - PASSO, "sem grelha propria, empresta tudo"


def test_10out_o_coletor_mostra_a_leitura_emprestada_e_di_lo(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text(json.dumps({"t": D.iso_utc(G0 + 6), "grade": D.iso_utc(G0), "sessao": 22.0, "semana": 49.0}) + chr(10)
                    + json.dumps({"t": D.iso_utc(G0 + 3 * PASSO + 20), "sessao": 49.0, "semana": 53.0, "quem": "outro_programa"}) + chr(10),
                    encoding="utf-8")
    relogio = [G0 + 3 * PASSO + 90]
    c = D.Coletor({}, ler_api=False, relogio=lambda: relogio[0], hist_cota=hist)
    c._processos_torre = lambda agora: 0
    r = c.volta(relogio[0])
    assert r["janelas"][0]["utilizacao"] == 49.0 and r["janelas"][1]["utilizacao"] == 53.0
    assert r["este_pc"]["fonte"] == "emprestado"
    assert c._grade[-1][0] == G0 and c._talvez_api(relogio[0]) is None, "a grelha propria nao muda e sem API nao se pede"


def test_10out_depois_de_uma_recusa_tenta_outra_vez_no_mesmo_passo():
    """10/10 22:4x: o recuo de 6/12/30 min deixava o medidor meia hora num numero velho. Agora: 2.a tentativa 20-45 s depois no
    mesmo passo; se falhar, o passo seguinte e normal; so 4+ recusas seguidas saltam passos (no maximo 2)."""
    assert D.plano_apos_recusa(G0 + 6, G0, 1, 1, 0.0) == ("retentar", G0 + 26)
    assert D.plano_apos_recusa(G0 + 6, G0, 1, 1, 1.0) == ("retentar", G0 + 51)
    assert D.plano_apos_recusa(G0 + 150, G0, 1, 1, 0.5)[0] == "recuar", "ja nao cabe no passo"
    assert D.plano_apos_recusa(G0 + 40, G0, 2, 2, 0.5) == ("recuar", G0 + PASSO), "a 2.a tambem falhou: o passo seguinte e normal"
    assert D.plano_apos_recusa(G0 + 40, G0, 2, 5, 0.5) == ("recuar", G0 + 2 * PASSO), "4+ recusas seguidas: salta 1 passo"
    assert D.plano_apos_recusa(G0 + 40, G0, 2, 9, 0.5) == ("recuar", G0 + 3 * PASSO), "8+: salta 2 passos"
    assert D.decidir_api_grade(G0 + 30, G0, G0 + 6, None, retentar_em=G0 + 26) == G0, "a 2.a tentativa passa por cima"
    assert D.decidir_api_grade(G0 + 20, G0, G0 + 6, None, retentar_em=G0 + 26) is None, "ainda nao e a hora"
    assert D.decidir_api_grade(G0 + PASSO + 6, G0, G0 + 6, None, retentar_em=G0 + 26) == G0 + PASSO, "o passo seguinte normal"


def test_10out_o_coletor_retenta_e_grava_o_ponto_do_passo(monkeypatch, tmp_path):
    relogio, pedidos, recusa = [G0 + 6], [], [True]

    class Api:
        def token(self):
            return "t", {}

        def pede(self, tok):
            pedidos.append(relogio[0])
            if recusa[0]:
                recusa[0] = False
                raise RuntimeError("HTTP 429: rate_limit_error")
            return {"five_hour": {"utilization": 42.0, "resets_at": D.iso_utc(G0 + 4 * 3600)}, "seven_day": {"utilization": 50.0}}

    import fonte_oauth as _FO                             # a leitura opcional (o token fica la dentro)
    monkeypatch.setattr(_FO, "ler_uso", lambda *a_, **k_: Api().pede(Api().token()))
    c = D.Coletor({}, ler_api=True, relogio=lambda: relogio[0], hist_cota=tmp_path / "hist.jsonl")
    c._processos_torre = lambda agora: 0
    c.sorteio = lambda: 0.0

    def volta(t):
        relogio[0] = t
        c.volta(t)
        if c._fio_api is not None:
            c._fio_api.join(10)
            c._fio_api = None

    volta(G0 + 6)                                  # recusa -> 2.a tentativa as G0 + 26
    assert pedidos == [G0 + 6] and c.retentar_em == G0 + 26 and c.recuo_ate is None
    volta(G0 + 20)
    assert pedidos == [G0 + 6]
    volta(G0 + 27)                                 # a 2.a tentativa: aceite, grava o ponto do passo G0
    assert pedidos == [G0 + 6, G0 + 27] and c.falhas_api == 0
    assert c._grade and c._grade[-1][0] == G0 and c._grade[-1][1] == 42.0
    volta(G0 + 60)
    assert len(pedidos) == 2, "um ponto por passo: nao pede mais"


def test_as_tres_aberturas_desenham_com_os_numeros_de_exemplo():
    """12/10: o --probe
    desenha as tres pelo mesmo codigo da janela - a pilula 348x30, o holograma 130x130 e o holograma com o painel por baixo -
    com pixels pre-multiplicados validos e a cor da zona da forca."""
    import numpy as np
    import medidor as M
    import janela as J
    for zona, r in M.DEMO.items():
        a = M.aberturas(r, esc=1.0, quadros=6)
        im = a["imagens"]
        assert im["minimizado"].shape[:2] == (J.PILULA[1], J.PILULA[0])
        assert im["semi"].shape[:2] == (130, 130) and im["aberto"].shape[:2] == (130 + J.PAINEL_H, 130)
        for b in im.values():
            assert b[:, :, 3].max() > 0 and (b[:, :, :3].max(axis=2) <= b[:, :, 3]).all()
        assert np.abs(im["aberto"][:130].astype(int) - im["semi"].astype(int)).max() <= 3   # o mesmo reator nas duas
        assert a["ms_por_quadro"] >= 0
    assert [D.severidade(None, M.DEMO[z]["forca"]) for z in ("calma", "ambar", "vermelha")] == [0, 1, 2]
