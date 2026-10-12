# -*- coding: utf-8 -*-
"""dados.py - what the meter reads and the numbers it draws (claude-quota-meter).

THE ACCOUNT (the same numbers on every machine logged into it):
  5 h / 7 days / per model   the quota windows, from one of two sources:
                             - "statusline" (default): the official `rate_limits` that Claude Code hands to its status line
                               script; the hook (statusline.py) saves one reading per 3-minute grid step to history.jsonl;
                             - "oauth" (opt-in, unofficial): the endpoint Claude Code uses for /usage (fonte_oauth.py), at most
                               one request per 3-minute step, with back-off on refusals. Adds the per-model weekly window.
  resets                     the resets_at of each window, to the minute.
  rate / fits / FORCE        rate = how fast the window really rose (last 40 min for the 5-hour window, last 24 h for the
                             week); fits = what still fits per hour until the reset (the week per WORKED hour, 8-hour day);
                             FORCE = rate / fits of the window that hits its ceiling first. Above 1x you are burning faster
                             than the quota allows - the hologram speeds up and changes colour.
THIS MACHINE (never in the force, the colour or the speed):
  pulse, sessions, agents    the Claude Code transcripts in ~/.claude/projects: calls per minute, sessions open in the last
                             10 minutes and subagents in flight.

PT: o que o medidor le e os numeros que desenha. A conta vem da linha de estado (por omissao) ou do endpoint OAuth (opcional).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import random
import re
import sys
import threading
import time
from pathlib import Path

CASA_CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CASA = Path(os.environ.get("CLAUDE_QUOTA_METER_HOME") or (Path.home() / ".claude-quota-meter"))
HIST_COTA = CASA / "history.jsonl"     # the account readings: one per 3-minute grid step (status line hook or opt-in API)
RESET_COTA = CASA / "resets.json"      # the reset times the status line hook saw last
PROJECTOS = CASA_CLAUDE / "projects"   # Claude Code transcripts: pulse of calls, open sessions, agents in flight
BRT = dt.timezone(dt.timedelta(hours=-3))

JANELA_RITMO_SESSAO_MIN = 40.0
JANELA_RITMO_SEMANA_MIN = 1440.0
JORNADA_H = 8.0
RITMO_MIN_H = 0.08
QUEDA_REINICIO = 1.0
PARADO_PCT_H = 0.05

# --- do medidor
INTERVALO_API_S = 180.0
ATRASO_GRADE_S = 5.0
                                 # desfasados e o pedido tem de cair no mesmo passo nas duas
TOLERANCIA_GRADE_S = 45.0
                                 # acordar) ja nao e daquele ponto: espera-se pelo passo seguinte
FOLGA_GRADE_S = 600.0
                                 # torna "grade_parcial" (grade_cobre)
RECUO_API_S = (360.0, 720.0, 1800.0)   # depois de uma recusa (429, rede): 6, 12, 30 min sem pedir - nao martelar o endpoint
FORCA_AMARELA = 1.0
FORCA_VERMELHA = 1.4             # a zona vermelha do velocimetro (G18) e da galeria: 1,4x
REABRIR_APOS_MIN_S = 15 * 60
MAX_SILENCIO_AGENTE_S = 20 * 60  # um subagente calado ha 20 min morreu (o Bash mais longo e de 10 min)
QUENTE_S = 20 * 60               # transcritos mexidos nos ultimos 20 min sao vigiados de 2 em 2 s
TAIL_AGENTE = 262144             # le-se no maximo 256 KB do fim de um transcrito de agente
MODOS = ("verde", "amarelo", "vermelho")


def importar_reguas() -> list:
    """Kept for compatibility: the rules are the constants above."""
    return []


# ======================================================================== PURAS
def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def ts_iso(iso):
    """PURA. Instante (segundos) de um ISO com ou sem fuso ('Z' aceite); sem fuso = Brasilia. None se ilegivel."""
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=BRT)).timestamp()


def ritmo_real(pontos, agora_ts: float, minutos: float, min_h: float = RITMO_MIN_H, queda: float = QUEDA_REINICIO):
    """PURA. O ritmo real: %/h do primeiro ao ultimo ponto dos ultimos `minutos`, so desde a
    ultima queda (> `queda` pontos = reinicio). [(ts, valor)] em qualquer ordem; None sem 2 pontos ou com < min_h horas."""
    pts = sorted((float(t), float(v)) for t, v in (pontos or ())
                 if _f(t) is not None and _f(v) is not None and 0 <= float(agora_ts) - float(t) <= minutos * 60.0)
    if len(pts) < 2:
        return None
    corte = 0
    for i in range(1, len(pts)):
        if pts[i][1] < pts[i - 1][1] - queda:
            corte = i
    pts = pts[corte:]
    if len(pts) < 2:
        return None
    dh = (pts[-1][0] - pts[0][0]) / 3600.0
    if dh < min_h:
        return None
    return (pts[-1][1] - pts[0][1]) / dh


def cabe_por_hora(janela: str, utilizacao, horas_ate_reset, jornada: float = JORNADA_H):
    """PURA. O que cabe por hora ate ao reinicio: a sessao em horas de RELOGIO; a semana e o Fable
    por hora TRABALHADA (dias x jornada). None sem utilizacao ou sem reinicio no futuro."""
    u, h = _f(utilizacao), _f(horas_ate_reset)
    if u is None or h is None or h <= 0:
        return None
    livre = max(0.0, 100.0 - u)
    if janela == "sessao":
        return livre / h
    return livre / (max(1.0, h / 24.0) * float(jornada))


def forca_e_aperto(janelas):
    """PURA. janelas = [{nome, utilizacao, cabe, real}] pela ordem (sessao, semana, Fable). A que MANDA e a que
    bate no tecto PRIMEIRO ao ritmo real (minutos = livre / real x 60; parado = nunca); forca = real / cabe
    dela, nunca negativa. Sem nenhuma janela com real e cabe > 0: forca None (o medidor diz 'a medir')."""
    melhor = None
    for j in janelas or ():
        real, cabe, u = _f(j.get("real")), _f(j.get("cabe")), _f(j.get("utilizacao"))
        if real is None or cabe is None or cabe <= 0 or u is None:
            continue
        livre = max(0.0, 100.0 - u)
        minutos = (livre / real * 60.0) if real > PARADO_PCT_H else 1e9
        if melhor is None or minutos < melhor["minutos_ao_tecto"]:
            melhor = {"janela": j.get("nome"), "real": real, "cabe": cabe, "forca": max(0.0, real / cabe),
                      "minutos_ao_tecto": minutos}
    if melhor is None:
        return {"janela": None, "real": None, "cabe": None, "forca": None, "minutos_ao_tecto": None}
    return melhor


def nivel_do_modo(modo):
    """PURA. verde 0, amarelo 1, vermelho 2 (o economico/esgotado do regime tambem e 2). Desconhecido = None."""
    m = str(modo or "").strip().lower()
    if m in ("verde", "normal"):
        return 0
    if m == "amarelo":
        return 1
    if m in ("vermelho", "economico", "esgotado"):
        return 2
    return None


def severidade(modo, forca) -> int:
    """PURA. A cor do medidor: a PIOR entre o modo informado (se houver) e a forca (acima de 1x amarela, acima de 1,4x vermelha)."""
    n = nivel_do_modo(modo)
    f = _f(forca)
    nf = 0 if f is None else (2 if f > FORCA_VERMELHA else (1 if f > FORCA_AMARELA else 0))
    return max(n if n is not None else 0, nf)


def alerta(forca, forca_antes, minimizado: bool, agora_ts: float, minimizou_ts) -> dict:
    """PURA. O alerta da forca: muda de cor, pisca devagar e, minimizado, abre sozinho.
    piscar = forca > 1x. abrir = minimizado e forca > 1x e (acabou de PASSAR de 1x, ou foi minimizado ha >= 15 min)."""
    f, fa = _f(forca), _f(forca_antes)
    acima = f is not None and f > FORCA_AMARELA
    cruzou = acima and (fa is None or fa <= FORCA_AMARELA)
    mt = _f(minimizou_ts)
    passou = mt is None or float(agora_ts) - mt >= REABRIR_APOS_MIN_S
    abrir = bool(minimizado and acima and (cruzou or passou))
    return {"piscar": acima, "abrir": abrir, "cruzou": cruzou}


def proximo_recuo(n_falhas: int) -> float:
    """PURA. Segundos de recuo depois da n-esima recusa seguida (6, 12, 30, 30... min)."""
    n = max(1, int(n_falhas))
    return RECUO_API_S[min(n, len(RECUO_API_S)) - 1]


def fronteira_grade(ts: float, passo: float = INTERVALO_API_S) -> float:
    """PURA. A fronteira da grelha em que `ts` cai: multiplo de `passo` s desde a epoca (UTC) - hh:00, hh:03, hh:06... nas
    duas maquinas, sem combinarem nada."""
    return math.floor(float(ts) / passo) * passo


def iso_utc(ts: float) -> str:
    """PURA. '2026-10-09T21:30:00+00:00' (o campo "grade" do historico)."""
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).isoformat(timespec="seconds")


RETENTAR_S = (20.0, 45.0)


def plano_apos_recusa(agora_ts: float, g: float, tentativas_no_passo: int, falhas_seguidas: int, sorteio: float,
                      passo: float = INTERVALO_API_S, espera=RETENTAR_S, margem: float = 15.0) -> tuple:
    """PURA (10/10). Depois de uma recusa da API:
    ("retentar", ts) - uma 2.a tentativa no MESMO passo, `espera` s depois (sorteio 0..1), se ainda couber no passo; ou
    ("recuar", ate) - a PRIMEIRA fronteira em que se pode pedir outra vez (g + passo = o passo seguinte, normal). O recuo antigo (6, 12, 30 min) deixava
    o medidor meia hora num numero velho, enquanto outra maquina sem recusas ja mostrava o valor novo. Agora so 4+ recusas
    seguidas saltam 1 passo, e 8+ saltam 2 (no maximo 9 min sem ponto proprio)."""
    if int(tentativas_no_passo) <= 1:
        s = min(1.0, max(0.0, float(sorteio)))
        t = float(agora_ts) + espera[0] + (espera[1] - espera[0]) * s
        if t <= g + passo - margem:
            return ("retentar", t)
    f = int(falhas_seguidas)
    return ("recuar", g + passo * (1 if f < 4 else (2 if f < 8 else 3)))


def decidir_api_grade(agora_ts: float, ultima_grade, ultimo_pedido_ts, recuo_ate_ts=None, passo: float = INTERVALO_API_S,
                      atraso: float = ATRASO_GRADE_S, tolerancia: float = TOLERANCIA_GRADE_S, retentar_em=None):
    """PURA (09/10; substitui o decidir_api). A fronteira do passo a pedir AGORA, ou None. Um pedido por passo, so entre
    `atraso` e `tolerancia` s depois da fronteira; nunca durante o recuo de uma recusa (recuo_ate e uma fronteira: passos
    inteiros sem pedir); nunca a menos de passo - (tolerancia - atraso) s do pedido anterior (o arranque logo a seguir a um
    pedido do medidor antigo). Media: 1 pedido por passo, a frequencia de sempre, so que alinhada. Ja nao se salta o passo
    por o historico ter uma leitura fresca de outro programa: a grelha precisa do SEU ponto em cada passo."""
    a = float(agora_ts)
    g = fronteira_grade(a, passo)
    rt = _f(retentar_em)
    if rt is not None and fronteira_grade(rt, passo) == g and rt <= a <= g + passo - 5:
        return g
    if not (atraso <= a - g <= tolerancia):
        return None
    ug = _f(ultima_grade)
    if ug is not None and g <= ug <= a + passo:       # este passo ja foi pedido (muito no futuro = relogio que voltou atras:
        return None                                    # ignora-se, senao o medidor calava-se ate la)
    ra = _f(recuo_ate_ts)
    if ra is not None and g < ra <= a + max(RECUO_API_S) + passo:
        return None
    up = _f(ultimo_pedido_ts)
    if up is not None and up <= a + passo and a - up < passo - (tolerancia - atraso):
        return None
    return g


def linha_da_grade(leitura: dict, agora_ts: float, g: float) -> dict:
    """PURA. A linha que o medidor grava no history.jsonl: a de sempre (t, sessao, semana, auto, quem, por_modelo) e
    "grade" = a fronteira do passo em ISO UTC. O "t" continua a ser a hora real da resposta."""
    linha = {"t": dt.datetime.fromtimestamp(float(agora_ts)).astimezone().isoformat(timespec="seconds"),
             "sessao": leitura.get("sessao"), "semana": leitura.get("semana"), "auto": True, "quem": "medidor_cota",
             "grade": iso_utc(g)}
    if leitura.get("fable") is not None:
        linha["por_modelo"] = {"Fable": leitura["fable"]}
    return linha


def pontos_emprestados(linhas, grade, agora_ts: float, passo: float = INTERVALO_API_S, max_idade_s: float = 2 * 3600.0) -> list:
    """PURA (10/10). Quando a API recusa (429) e o medidor recua, a grelha para na ultima leitura propria, enquanto outros
    programas que leem a MESMA conta continuam a gravar no MESMO historico (linhas sem "grade"). Essas leituras passam
    a valer como pontos EMPRESTADOS da grelha - so para as fronteiras DEPOIS do ultimo ponto proprio (a grelha ja lida nunca se
    reescreve): [(g, sessao, semana, fable, t)], um por fronteira (a leitura mais recente dentro do passo), por ordem de g.
    Linhas com "grade" (de um medidor) e leituras com mais de `max_idade_s` ficam de fora."""
    ult_g = max((p[0] for p in grade or ()), default=None)
    por_g = {}
    for d in linhas or ():
        if not isinstance(d, dict) or d.get("grade") or d.get("sessao") is None:
            continue
        t, s = ts_iso(d.get("t")), _f(d.get("sessao"))
        if t is None or s is None or agora_ts - t > max_idade_s or t > agora_ts + passo:
            continue
        g = fronteira_grade(t, passo)
        if ult_g is not None and g <= ult_g:
            continue
        p = (g, s, _f(d.get("semana")), _f((d.get("por_modelo") or {}).get("Fable")), t)
        if g not in por_g or t > por_g[g][4]:
            por_g[g] = p
    return [por_g[g] for g in sorted(por_g)]


def grade_do_historico(linhas, passo: float = INTERVALO_API_S) -> list:
    """PURA. [(g, sessao, semana, fable, t)] das linhas com "grade" (so o medidor as escreve), POSICIONADAS na hora da
    grelha g (t = a hora real), por ordem de g. Um passo com duas linhas (o medidor reabriu a meio dele) fica com a que
    chegou primeiro; uma linha cuja hora real nao cai no proprio passo (|t - g| > passo) e lixo e fica de fora."""
    por_g = {}
    for d in linhas or ():
        if not isinstance(d, dict) or d.get("sessao") is None or not d.get("grade"):
            continue
        g, t, s = ts_iso(d.get("grade")), ts_iso(d.get("t")), _f(d.get("sessao"))
        if g is None or t is None or s is None or abs(t - g) > passo:
            continue
        pm = d.get("por_modelo")
        p = (g, s, _f(d.get("semana")), _f(pm.get("Fable")) if isinstance(pm, dict) else None, t)
        if g not in por_g or t < por_g[g][4]:
            por_g[g] = p
    return [por_g[g] for g in sorted(por_g)]


def grade_cobre(pts, fim_ts: float, minutos: float, folga: float = FOLGA_GRADE_S) -> bool:
    """PURA. A grelha cobre a janela [fim - minutos, fim]? O primeiro ponto dela dentro da janela tem de estar a menos de
    `folga` s do inicio. Logo apos a actualizacao (ou depois de o medidor estar fechado) a grelha ainda nao chega ao inicio
    da janela: o ritmo usa os pontos que ha e o retrato diz "grade_parcial". Um ponto em falta no inicio (uma recusa) nao
    muda isso; os do meio nunca contam (o ritmo e do primeiro ao ultimo ponto)."""
    fim = float(fim_ts)
    ini = fim - float(minutos) * 60.0
    primeiro = min((float(t) for t, _v in pts or () if _f(t) is not None and ini <= float(t) <= fim), default=None)
    return primeiro is not None and primeiro <= ini + folga


def dentro_do_ecra(x: int, y: int, w: int, h: int, areas) -> tuple:
    """PURA. Pos (x, y) da janela w x h dentro da area de trabalho de um monitor: o que mais a contem (ou o mais perto, se
    saiu de todos - mudou a resolucao, tirou-se um monitor). Se ja esta toda dentro de um, fica onde esta. areas =
    [(esq, topo, dir, baixo)]. Janela maior do que a area encosta ao canto de cima/esquerda."""
    rects = [tuple(int(v) for v in a) for a in (areas or ()) if len(a) == 4 and a[2] > a[0] and a[3] > a[1]]
    if not rects:
        return int(x), int(y)
    x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
    for l, t, r, b in rects:
        if x >= l and y >= t and x + w <= r and y + h <= b:
            return x, y

    def inter(a):
        l, t, r, b = a
        return max(0, min(r, x + w) - max(l, x)) * max(0, min(b, y + h) - max(t, y))

    def dist(a):
        l, t, r, b = a
        cx, cy = x + w / 2.0, y + h / 2.0
        dx = max(l - cx, 0, cx - r)
        dy = max(t - cy, 0, cy - b)
        return dx * dx + dy * dy
    alvo = max(rects, key=lambda a: (inter(a), -dist(a)))
    l, t, r, b = alvo
    nx = max(l, min(x, r - w))
    ny = max(t, min(y, b - h))
    return int(nx), int(ny)


def fmt_pct(v) -> str:
    """PURA. '18%' (inteiro, como o app) ou '—'."""
    f = _f(v)
    return "—" if f is None else "{:d}%".format(int(round(f)))


def fmt_forca(v) -> str:
    """PURA. '0,93x' ou '—'."""
    f = _f(v)
    return "—" if f is None else "{:.2f}x".format(max(0.0, f)).replace(".", ",")


def fmt_ritmo(v) -> str:
    """PURA. '6,2%/h' (uma casa) ou '—'."""
    f = _f(v)
    if f is None:
        return "—"
    return ("{:.2f}" if abs(f) < 1 else "{:.1f}").format(f).replace(".", ",") + "%/h"


def fmt_dur(seg) -> str:
    """PURA. Contagem para o reinicio: '2h49', '0h05', '3d15h'; '—' sem valor; negativo = '0h00'."""
    f = _f(seg)
    if f is None:
        return "—"
    s = max(0, int(f))
    d, resto = divmod(s, 86400)
    h, m = divmod(resto // 60, 60)
    if d:
        return "{}d{:02d}h".format(d, h)
    return "{}h{:02d}".format(h, m)


def fmt_usd(v) -> str:
    """PURA. 'US$ 4,10' ou '—'."""
    f = _f(v)
    return "—" if f is None else "US$ {:.2f}".format(f).replace(".", ",")


def fmt_idade(seg) -> str:
    """PURA. 'agora', 'ha 40 s', 'ha 3 min', 'ha 2 h'."""
    f = _f(seg)
    if f is None:
        return "sem leitura"
    s = max(0, int(f))
    if s < 15:
        return "agora"
    if s < 60:
        return "há <1 min"                      # ao segundo obrigava o painel a redesenhar-se todos os segundos
    if s < 5400:
        return "há {} min".format(int(round(s / 60.0)))
    return "há {} h".format(int(round(s / 3600.0)))


def ler_linhas_json(texto: str) -> list:
    """PURA. As linhas JSON inteiras de um bocado de texto (a primeira, cortada a meio, cai fora sozinha)."""
    fora = []
    for l in (texto or "").splitlines():
        l = l.strip()
        if not l.startswith("{"):
            continue
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if isinstance(d, dict):
            fora.append(d)
    return fora


def respostas_novas(linhas, vistos: set) -> int:
    """PURA (mexe em `vistos`). Quantas respostas NOVAS do modelo (type assistant, message.id ainda nao visto). Uma
    resposta ocupa varias linhas com o mesmo id: conta-se cada id uma vez."""
    n = 0
    for d in linhas or ():
        if d.get("type") != "assistant":
            continue
        mid = (d.get("message") or {}).get("id")
        if not mid or mid in vistos:
            continue
        vistos.add(mid)
        n += 1
    return n


def agente_terminou(linhas) -> bool:
    """PURA. A ultima resposta do agente e a final? (stop_reason end_turn/stop_sequence na ultima linha assistant)."""
    for d in reversed(list(linhas or ())):
        if d.get("type") == "assistant":
            return str((d.get("message") or {}).get("stop_reason")) in ("end_turn", "stop_sequence")
    return False


SESSAO_ACTIVA_S = 10 * 60          # uma sessao conta como aberta se o transcrito mexeu nos ultimos 10 min


def sessoes_activas(quentes: dict, agora: float, janela_s: float = SESSAO_ACTIVA_S) -> int:
    """PURA (04/10). Quantas sessoes do Claude Code estao abertas nesta maquina: transcritos PRINCIPAIS (nao 'agent-*', nao
    dentro de 'subagents') com mtime nos ultimos `janela_s`. As da outra maquina nao se veem aqui - entram na % da cota."""
    n = 0
    for p, info in (quentes or {}).items():
        nome = os.path.basename(str(p))
        if nome.startswith("agent-") or "subagents" in str(p).replace("\\", "/").split("/"):
            continue
        try:
            if agora - float((info or {}).get("mtime") or 0) <= janela_s:
                n += 1
        except (TypeError, ValueError):
            continue
    return n


def agente_em_voo(linhas_cauda, idade_s: float, tem_resultado: bool) -> bool:
    """PURA. Um subagente esta em voo se mexeu ha menos de MAX_SILENCIO_AGENTE_S, nao tem 'result' no journal e a ultima
    resposta dele ainda nao e a final."""
    if tem_resultado or idade_s is None or float(idade_s) > MAX_SILENCIO_AGENTE_S:
        return False
    return not agente_terminou(linhas_cauda)


def leituras_do_historico(linhas) -> list:
    """PURA. [(ts, sessao, semana, fable)] das linhas do history.jsonl com sessao (as de erro caem fora), por ordem.
    09/10: o medidor ja nao as usa para a cota (so a grelha: grade_do_historico); fica para as provas da conta antiga."""
    fora = []
    for d in linhas or ():
        if not isinstance(d, dict) or d.get("sessao") is None:
            continue
        t = ts_iso(d.get("t"))
        s = _f(d.get("sessao"))
        if t is None or s is None:
            continue
        fora.append((t, s, _f(d.get("semana")), _f((d.get("por_modelo") or {}).get("Fable"))))
    fora.sort(key=lambda x: x[0])
    return fora


def ler_resposta_api(d) -> dict:
    """PURA. Da resposta do /api/oauth/usage: sessao, semana, Fable e os tres reinicios (ts). A janela do Fable vem na
    lista `limits` (weekly_scoped com scope.model.display_name) desde 08/09."""
    out = {"sessao": None, "semana": None, "fable": None, "reset_sessao": None, "reset_semana": None, "reset_fable": None}
    if not isinstance(d, dict):
        return out
    fh, sd = d.get("five_hour") or {}, d.get("seven_day") or {}
    if isinstance(fh, dict):
        out["sessao"], out["reset_sessao"] = _f(fh.get("utilization")), ts_iso(fh.get("resets_at"))
    if isinstance(sd, dict):
        out["semana"], out["reset_semana"] = _f(sd.get("utilization")), ts_iso(sd.get("resets_at"))
    for l in d.get("limits") or ():
        if not isinstance(l, dict):
            continue
        nome = (((l.get("scope") or {}).get("model") or {}).get("display_name")) if isinstance(l.get("scope"), dict) else None
        if nome == "Fable" and _f(l.get("percent")) is not None:
            out["fable"], out["reset_fable"] = _f(l.get("percent")), ts_iso(l.get("resets_at"))
    return out


JANELAS_S = {"sessao": 5 * 3600.0, "semana": 7 * 86400.0, "fable": 7 * 86400.0}


def resets_efectivos(proprios, partilhados, agora_ts: float) -> tuple:
    """PURA (04/10 21:2x). Os reinicios que o medidor desenha: o proprio se ainda esta no futuro; senao o PARTILHADO
    (resets.json, que o gancho da linha de estado grava); senao o proprio projectado para a frente em janelas inteiras
    (5 h / 7 d), marcado como estimado. Sem isto, um reinicio no passado apagava a barrinha do tempo (0 s)."""
    out, est = {}, set()
    for k, passo in JANELAS_S.items():
        v, s = _f((proprios or {}).get(k)), _f((partilhados or {}).get(k))
        if v is not None and v > agora_ts:
            out[k] = v
        elif s is not None and s > agora_ts:
            out[k] = s
        elif v is not None:
            n = int((agora_ts - v) // passo) + 1
            out[k] = v + n * passo
            est.add(k)
        else:
            out[k] = None
    return out, est


def _ultimos_valores(serie):
    """PURA. (sessao, semana, fable, ts) da ultima leitura de `serie` ([(ts, sessao, semana, fable, ...)] por ordem); a semana
    e o Fable podem vir de uma leitura ate 30 min antes. Tudo None sem leituras."""
    if not serie:
        return None, None, None, None
    ult = serie[-1]
    fable = None
    for p in reversed(serie):
        if p[3] is not None:
            fable = p[3] if ult[0] - p[0] <= 1800 else None
            break
    semana = ult[2]
    if semana is None:
        for p in reversed(serie):
            if p[2] is not None and ult[0] - p[0] <= 1800:
                semana = p[2]
                break
    return ult[1], semana, fable, ult[0]


def _ao_minuto(ts):
    """PURA. O instante arredondado ao minuto (None fica None). A API devolve o mesmo resets_at com uns decimos de segundo
    diferentes a cada pedido (visto 09/10: 06:00:00.118 num, 05:59:59.948 noutro) - ao minuto, as duas maquinas dao igual."""
    return None if ts is None else round(ts / 60.0) * 60.0


# os numeros da CONTA no retrato: tem de ser iguais nas duas maquinas com a mesma serie da API (os reinicios em instante
# absoluto, t + reinicio_*_s, porque o "faltam" conta-se do relogio de cada uma)
CHAVES_DA_CONTA = ("sessao", "semana", "fable", "forca", "forca_janela", "ritmo", "cabe", "minutos_ao_tecto", "ritmo_de",
                   "sessao_velha", "severidade", "grade_ts")


def montar(grade, agora_ts: float, resets: dict, este_pc: dict = None) -> dict:
    """PURA. O retrato que o medidor desenha. 09/10 (os medidores medem a CONTA inteira, nao cada
    computador): os numeros da cota saem SO de `grade` = [(g, sessao, semana, fable[, t])], os pontos que o
    proprio medidor leu da API da conta nas fronteiras comuns em UTC (grade_do_historico), por ordem de g; resets = {sessao,
    semana, fable} em ts (da API). Valores = o ultimo ponto; ritmo de cada janela = so pontos da grelha, na hora da grelha, a
    acabar no ultimo ponto; o que cabe conta-se desse ponto e do reinicio ao minuto. Com a mesma serie da API as duas
    maquinas dao os mesmos numeros, olhem quando olharem dentro do passo. `este_pc` (pulso, agentes, sessoes, modo e gasto da
    app, erro da API desta maquina) passa para retrato["este_pc"] e NUNCA entra num numero da conta, nem na cor."""
    grade = sorted(grade or (), key=lambda p: p[0])
    g_ult = grade[-1][0] if grade else None
    sessao, semana, fable, ult_ts = _ultimos_valores(grade)
    leitura_ts = (grade[-1][4] if len(grade[-1]) > 4 else g_ult) if grade else None
    rs, rw, rf = (_ao_minuto(_f((resets or {}).get(k))) for k in ("sessao", "semana", "fable"))
    sessao_velha = bool(rs is not None and ult_ts is not None and 0 < rs - agora_ts <= JANELAS_S["sessao"]
                        and ult_ts < rs - JANELAS_S["sessao"])
    if sessao_velha:
        sessao = 0.0
    # sem ponto da conta ha mais do que a janela da sessao (a API a recusar ha muito, o PC a dormir) ja nao se sabe o ritmo:
    # a forca fica "a medir" (como fazia a conta antiga com a janela vazia); um ponto muito no futuro = relogio errado
    sem_ritmo = g_ult is None or not (-INTERVALO_API_S <= agora_ts - g_ult <= JANELA_RITMO_SESSAO_MIN * 60.0)
    janelas = []
    for nome, idx, minutos, util, reset in (("sessao", 1, JANELA_RITMO_SESSAO_MIN, sessao, rs),
                                            ("semana", 2, JANELA_RITMO_SEMANA_MIN, semana, rw),
                                            ("Fable", 3, JANELA_RITMO_SEMANA_MIN, fable, rf)):
        de, real, horas = "sem_grade", None, None
        if not sem_ritmo:
            pts = [(p[0], p[idx]) for p in grade if p[idx] is not None]
            real = ritmo_real(pts, g_ult, minutos)
            de = "grade" if grade_cobre(pts, g_ult, minutos) else "grade_parcial"
            if reset is not None:
                horas = (reset - g_ult) / 3600.0      # do ultimo ponto da grelha, nao de "agora": igual dentro do passo
        janelas.append({"nome": nome, "utilizacao": util, "real": real, "ritmo_de": de,
                        "cabe": cabe_por_hora("sessao" if nome == "sessao" else "semana", util, horas)})
    # semana dava 2,16 %/h num e 0 no outro e a forca saia 1,30x contra 0,02x. A sessao (40 min) enche depressa e continua a
    # poder mandar parcial (logo apos actualizar); a semana e o Fable so quando a grelha os cobre por inteiro - igual nas duas.
    # O ritmo parcial continua no retrato (janelas) para o painel.
    ap = forca_e_aperto([dict(j, real=j["real"] if (j["ritmo_de"] == "grade" or j["nome"] == "sessao") else None)
                         for j in janelas])
    manda = next((j for j in janelas if j["nome"] == ap["janela"]), janelas[0])
    return {"t": agora_ts, "sessao": sessao, "semana": semana, "fable": fable,
            "leitura_ts": leitura_ts,
            "reinicio_sessao_s": (rs - agora_ts) if rs is not None else None,
            "reinicio_semana_s": (rw - agora_ts) if rw is not None else None,
            "forca": ap["forca"], "forca_janela": ap["janela"], "ritmo": ap["real"], "cabe": ap["cabe"],
            "minutos_ao_tecto": ap["minutos_ao_tecto"], "ritmo_de": manda["ritmo_de"], "grade_ts": g_ult,
            "janelas": janelas, "sessao_velha": sessao_velha,
            "severidade": severidade(None, ap["forca"]),
            "este_pc": dict(este_pc or {})}


# ======================================================================== O MUNDO
def _cauda(p: Path, n: int) -> str:
    try:
        with open(str(p), "rb") as fh:
            fh.seek(0, 2)
            tam = fh.tell()
            fh.seek(max(0, tam - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _json(p: Path):
    try:
        return json.loads(io.open(str(p), encoding="utf-8", errors="replace").read())
    except Exception:
        return None


class Coletor:
    """Recolhe tudo de 2 em 2 s numa thread (leve: so le o que mudou). retrato() devolve a ultima copia."""

    def __init__(self, estado: dict = None, ler_api: bool = True, registo=None, relogio=None, hist_cota: Path = None):
        self.trava = threading.Lock()
        self._ret = {}
        self.ler_api = ler_api
        self.registo = registo or (lambda txt: None)
        self.relogio = relogio or time.time
        self.hist_cota = Path(hist_cota) if hist_cota else HIST_COTA
        est = estado or {}
        self.resets = dict(est.get("resets") or {})        # {sessao, semana, fable} em ts - guardados no estado.json
        self.ultima_api = _f(est.get("ultima_api_ts"))
        self.ultima_grade = _f(est.get("ultima_grade_ts"))
        self._leitura_api_ts = None                         # o "t" da ultima linha que o proprio medidor gravou
        self._grade = []                                    # [(g, sessao, semana, fable, t)] das ultimas 26 h
        self._emprestados = []
        self._fontes_ant = None
        self._fio_api = None
        self.recuo_ate = _f(est.get("recuo_ate_ts"))
        self.falhas_api = int(_f(est.get("falhas_api")) or 0)
        self.retentar_em = None
        self._tentativas = {}                                # {fronteira: pedidos feitos nesse passo}
        self.sorteio = random.random
        self.erro_api = None
        self._api_a_correr = False
        self.eventos = 0
        self._ev_ts = []                                     # instantes das chamadas (ultimo minuto)
        self._quentes = {}                                   # caminho -> {tam, mtime}
        self._ids = set()
        self._agentes = {}                                   # caminho -> em voo?
        self._journal = {}                                   # pasta -> (mtime, set(agentIds com resultado))
        self._t_varrer = 0.0
        self._hist_mt = None
        self._t_hist = 0.0
        self._cal = None
        self._parar = threading.Event()
        try:
            importar_reguas()
        except Exception:
            pass

    # ---------------------------------------------------------------- historico da cota
    def _ler_historico(self, agora: float, forcar: bool = False):
        try:
            st = self.hist_cota.stat()
            mt = (st.st_mtime, st.st_size)
        except OSError:
            return
        if not forcar and mt == self._hist_mt and agora - self._t_hist < 60:
            return
        self._hist_mt, self._t_hist = mt, agora
        # 24 h de leituras (a semana mede o ritmo em 24 h): ~600-800 linhas de ~120-160 bytes -> 512 KB chegam com folga.
        linhas = ler_linhas_json(_cauda(self.hist_cota, 524288))
        corte = agora - 26 * 3600
        self._grade = [p for p in grade_do_historico(linhas) if p[0] >= corte]
        # mesma conta preenchem a grelha depois do ultimo ponto proprio - o medidor nunca fica parado numa leitura velha
        self._emprestados = pontos_emprestados(linhas, self._grade, agora)

    def _pedir_api(self, g: float):
        try:
            import fonte_oauth as FO               # opt-in ("fonte": "oauth"): the token is read inside, never kept here
            d = FO.ler_uso()
            r = ler_resposta_api(d)
            if r["sessao"] is None:
                raise RuntimeError("resposta sem five_hour")
            agora = self.relogio()
            res = {k: r["reset_" + k] for k in ("sessao", "semana", "fable") if r.get("reset_" + k)}
            linha = linha_da_grade(r, agora, g)
            with io.open(str(self.hist_cota), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
            with self.trava:
                self.resets.update(res)
                self.falhas_api = 0
                self.recuo_ate = None
                self.retentar_em = None
                self.erro_api = None
                self._leitura_api_ts = ts_iso(linha["t"])
            self._ler_historico(agora, forcar=True)
        except BaseException as ex:
            if isinstance(ex, KeyboardInterrupt):
                raise
            with self.trava:
                self.falhas_api += 1
                self.erro_api = "{}: {}".format(type(ex).__name__, re.sub(r"\s+", " ", str(ex))[:80])
                plano, quando = plano_apos_recusa(self.relogio(), g, self._tentativas.get(g, 1), self.falhas_api, self.sorteio())
                if plano == "retentar":
                    self.retentar_em, self.recuo_ate = quando, None
                else:
                    self.retentar_em, self.recuo_ate = None, (quando if quando > g + INTERVALO_API_S else None)
            if plano == "retentar":
                self.registo("API recusou ({}), nova tentativa as {}".format(self.erro_api, time.strftime("%H:%M:%S", time.localtime(quando))))
            else:
                self.registo("API recusou ({}), proximo pedido no passo das {}".format(
                    self.erro_api, time.strftime("%H:%M", time.localtime(quando))))
        finally:
            self._api_a_correr = False

    def _talvez_api(self, agora: float):
        """Lanca o pedido do passo da grelha, se for a hora dele (decidir_api_grade). Devolve a fronteira pedida ou None."""
        if not self.ler_api or self._api_a_correr:
            return None
        ug = self.ultima_grade
        if self._grade and (ug is None or self._grade[-1][0] > ug):
            ug = self._grade[-1][0]           # o ultimo ponto que ESTE medidor ja gravou: reabriu a meio do passo -> nao repete
        g = decidir_api_grade(agora, ug, self.ultima_api, self.recuo_ate, retentar_em=self.retentar_em)
        if g is None:
            return None
        with self.trava:
            # do passo seguinte ficava bloqueado por ela); conta a 1.a tentativa do passo
            retentativa = self.retentar_em is not None and fronteira_grade(self.retentar_em) == g
            self.ultima_grade = g                             # marcado ANTES de pedir: nunca dois pedidos no mesmo passo
            if not retentativa:
                self.ultima_api = agora
            self.retentar_em = None
            self._tentativas = {k: v for k, v in self._tentativas.items() if k >= g - 3 * INTERVALO_API_S}
            self._tentativas[g] = self._tentativas.get(g, 0) + 1
        self._api_a_correr = True
        self._fio_api = threading.Thread(target=self._pedir_api, args=(g,), name="medidor-api", daemon=True)
        self._fio_api.start()
        return g

    # ---------------------------------------------------------------- transcritos (pulso + agentes da sessao)
    def _varrer(self, agora: float):
        quentes = {}
        try:
            for raiz, _d, fs in os.walk(str(PROJECTOS)):
                for nome in fs:
                    if not nome.endswith(".jsonl") or nome == "journal.jsonl":
                        continue
                    p = os.path.join(raiz, nome)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if agora - st.st_mtime <= QUENTE_S:
                        quentes[p] = self._quentes.get(p) or {"tam": st.st_size, "mtime": st.st_mtime}
        except OSError:
            pass
        for p in list(self._agentes):
            if p not in quentes:
                self._agentes.pop(p, None)
        self._quentes = quentes

    def _resultados_journal(self, pasta: str) -> set:
        j = os.path.join(pasta, "journal.jsonl")
        try:
            mt = os.path.getmtime(j)
        except OSError:
            return set()
        c = self._journal.get(pasta)
        if c and c[0] == mt:
            return c[1]
        ids = set()
        for d in ler_linhas_json(_cauda(Path(j), 1048576)):
            if d.get("type") in ("result", "error", "failed", "cancelled", "stopped") and d.get("agentId"):
                ids.add(d["agentId"])
        self._journal[pasta] = (mt, ids)
        return ids

    def _transcritos(self, agora: float) -> int:
        novos = 0
        for p, info in list(self._quentes.items()):
            try:
                st = os.stat(p)
            except OSError:
                self._quentes.pop(p, None)
                continue
            nome = os.path.basename(p)
            e_agente = nome.startswith("agent-")
            if st.st_size != info["tam"] or st.st_mtime != info["mtime"]:
                if st.st_size > info["tam"]:
                    try:
                        with open(p, "rb") as fh:
                            fh.seek(info["tam"])
                            bloco = fh.read(min(st.st_size - info["tam"], 8 * 1048576)).decode("utf-8", "replace")
                    except OSError:
                        bloco = ""
                    novos += respostas_novas(ler_linhas_json(bloco), self._ids)
                info["tam"], info["mtime"] = st.st_size, st.st_mtime
                if e_agente:
                    info["cauda"] = None                 # muda: rele-se a cauda abaixo
            if e_agente:
                aid = nome[len("agent-"):-len(".jsonl")]
                res = aid in self._resultados_journal(os.path.dirname(p))
                if info.get("cauda") is None:
                    info["cauda"] = ler_linhas_json(_cauda(Path(p), TAIL_AGENTE))[-40:]
                self._agentes[p] = agente_em_voo(info["cauda"], agora - st.st_mtime, res)
        if len(self._ids) > 20000:                           # memoria limitada: os ids antigos ja nao voltam
            self._ids = set(list(self._ids)[-5000:])
        return novos

    # ---------------------------------------------------------------- a volta
    def volta(self, agora: float = None) -> dict:
        agora = agora or self.relogio()
        novos = 0
        if agora - self._t_varrer >= 30 or not self._quentes:
            self._varrer(agora)
            self._t_varrer = agora
        novos += self._transcritos(agora)
        self._ler_historico(agora)
        self._talvez_api(agora)
        if novos:
            self.eventos += novos
            self._ev_ts.extend([agora] * novos)
        self._ev_ts = [t for t in self._ev_ts if agora - t <= 60]
        with self.trava:
            part = _json(RESET_COTA) or {}
            resets, estimados = resets_efectivos(self.resets, {"sessao": ts_iso(part.get("five_hour_resets_at")),
                                                               "semana": ts_iso(part.get("seven_day_resets_at"))}, agora)
            # tudo o que e DESTE PC: vai para retrato["este_pc"] e nunca entra na forca, na cor nem na velocidade
            este_pc = {"reinicio_estimado": sorted(estimados),
                       "agentes_sessao": sum(1 for v in self._agentes.values() if v),
                       "sessoes": sessoes_activas(self._quentes, agora),
                       "eventos": self.eventos, "chamadas_min": len(self._ev_ts),
                       "erro_api": self.erro_api, "ultima_api_ts": self.ultima_api,
                       "fonte": ("api" if self.ler_api else "statusline") if self._grade else None, "resets": resets}
        este_pc["agentes"] = este_pc["agentes_sessao"]
        emprestados = list(self._emprestados)
        r = montar(list(self._grade) + emprestados, agora, resets, este_pc)
        if r.get("leitura_ts") and self._leitura_api_ts and abs(r["leitura_ts"] - self._leitura_api_ts) < 1:
            r["este_pc"]["fonte"] = "api"
        elif emprestados:
            r["este_pc"]["fonte"] = "emprestado"
        # grelha a cobrir as janelas depois da actualizacao
        fontes = tuple(j.get("ritmo_de") for j in r.get("janelas") or ())
        if fontes != self._fontes_ant:
            self._fontes_ant = fontes
            self.registo("ritmo da conta: sessao={} semana={} Fable={}".format(*fontes))
        with self.trava:
            self._ret = r
        return r

    def retrato(self) -> dict:
        with self.trava:
            return dict(self._ret)

    def estado_para_guardar(self) -> dict:
        with self.trava:
            return {"resets": dict(self.resets), "ultima_api_ts": self.ultima_api, "ultima_grade_ts": self.ultima_grade,
                    "recuo_ate_ts": self.recuo_ate, "falhas_api": self.falhas_api}

    def correr(self, intervalo: float = 2.0):
        while not self._parar.is_set():
            t0 = time.monotonic()
            try:
                self.volta()
            except Exception as ex:                       # uma volta que rebenta nao mata o medidor; fica no registo
                self.registo("volta falhou: {}: {}".format(type(ex).__name__, str(ex)[:120]))
            self._parar.wait(max(0.2, intervalo - (time.monotonic() - t0)))

    def parar(self):
        self._parar.set()


if __name__ == "__main__":
    # python quota_meter/dados.py  -> one pass (no API) and the reading as JSON
    c = Coletor(ler_api="--api" in sys.argv)
    c.volta()
    time.sleep(2.2)
    r = c.volta()
    r.pop("janelas", None)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
