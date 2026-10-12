# -*- coding: utf-8 -*-
"""medidor.py - claude-quota-meter: a live, always-on-top desktop meter for your Claude Code quota (unofficial).

The Arc Reactor Mark II in a borderless, transparent window: the 5-hour and weekly windows, their resets, and the
FORCE - how fast you are spending against what still fits until the reset. Reads only: the status line readings (default)
or, if you turn it on, the exact usage endpoint (see fonte_oauth.py). Never calls a model.

Usage (pythonw opens no console):
  pythonw -m quota_meter               open (does nothing if it is already open)
  pythonw -m quota_meter --toggle      open or close (for a desktop shortcut)
  python  -m quota_meter --close       close the open meter
  python  -m quota_meter --state       one reading as JSON (no window, no API)
  python  -m quota_meter --probe DIR   draw the three openings with the real numbers into DIR (no window)
  python  -m quota_meter --probe DIR --demo   the same with sample numbers in the three force zones

Settings and data: ~/.claude-quota-meter/ (config.json, estado.json, history.jsonl, medidor.log).
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes as W
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

CASA = Path(os.environ.get("CLAUDE_QUOTA_METER_HOME") or (Path.home() / ".claude-quota-meter"))
CASA.mkdir(parents=True, exist_ok=True)
CONFIG = CASA / "config.json"
ESTADO = CASA / "estado.json"
REGISTO = CASA / "medidor.log"
REGISTO_MAX = 262144                      # 256 KB e roda (fica um .1): o medidor corre semanas a fio
NOME_TRANCA = "Local\\ClaudeQuotaMeter"
NOME_FECHAR = "Local\\ClaudeQuotaMeter-Fechar"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE, SYNCHRONIZE = 0x0002, 0x00100000
CPU_A_CADA_S = 600                        # de 10 em 10 min o medidor escreve no registo quanto CPU gastou (a prova da leveza)

CONFIG_OMISSAO = {
    "tamanho_px": 130,
    "fps_max": 30,
    "fps_repouso": 30,
    "teto_cpu_nucleo": 0.12,
    "fonte": "statusline",
    "_leia": ("tamanho_px: 130 micro (default), 180 small, 260 medium, 340 large. "
              "fps_max/fps_repouso: frames per second with and without calls. teto_cpu_nucleo: share of ONE core the "
              "drawing may use (0.12 = 1% of a 12-core machine). fonte: 'statusline' (default, official) or 'oauth' "
              "(opt-in, unofficial endpoint - see README)."),
}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
kernel32.CreateMutexW.restype = W.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
kernel32.CreateEventW.restype = W.HANDLE
kernel32.OpenEventW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
kernel32.OpenEventW.restype = W.HANDLE
kernel32.SetEvent.argtypes = [W.HANDLE]
kernel32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
kernel32.CloseHandle.argtypes = [W.HANDLE]

_TRAVA_REG = threading.Lock()


def registo(txt: str):
    """Uma linha no medidor/medidor.log (o pythonw nao tem consola: e aqui que se ve o que aconteceu)."""
    linha = "{} {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), txt)
    with _TRAVA_REG:
        try:
            if REGISTO.exists() and REGISTO.stat().st_size > REGISTO_MAX:
                os.replace(str(REGISTO), str(REGISTO) + ".1")
            with io.open(str(REGISTO), "a", encoding="utf-8") as fh:
                fh.write(linha)
        except OSError:
            pass


def ler_json(p: Path, omissao):
    try:
        return json.loads(io.open(str(p), encoding="utf-8").read())
    except Exception:
        return omissao


def gravar_json(p: Path, obj):
    tmp = str(p) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, str(p))


def validar_config(c) -> dict:
    """PURA. O config com as omissoes e os valores fora da regua corrigidos (um config estragado nao parte o medidor)."""
    out = dict(CONFIG_OMISSAO)
    if isinstance(c, dict):
        out.update({k: v for k, v in c.items() if k in CONFIG_OMISSAO})
    try:
        out["tamanho_px"] = int(min(480, max(110, int(out["tamanho_px"]))))
    except (TypeError, ValueError, OverflowError):
        out["tamanho_px"] = 260
    for k, lo, hi in (("fps_max", 5, 60), ("fps_repouso", 2, 30), ("teto_cpu_nucleo", 0.02, 0.5)):
        try:
            out[k] = min(hi, max(lo, float(out[k])))
        except (TypeError, ValueError):
            out[k] = CONFIG_OMISSAO[k]
    out["fps_repouso"] = min(out["fps_repouso"], out["fps_max"])
    if out.get("fonte") not in ("statusline", "oauth"):
        out["fonte"] = "statusline"
    return out


def ler_config() -> dict:
    c = ler_json(CONFIG, None)
    v = validar_config(c)
    if c is None:
        try:
            gravar_json(CONFIG, v)
        except OSError:
            pass
    return v


def pedir_fecho() -> bool:
    """Se houver um medidor aberto, pede-lhe que feche (evento com nome). True se havia."""
    h = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, NOME_FECHAR)
    if not h:
        return False
    kernel32.SetEvent(h)
    kernel32.CloseHandle(h)
    return True


# --probe --demo: os numeros de exemplo das tres zonas da forca (calma < 1x, ambar a partir de 1x, vermelha a partir de 1,4x)
DEMO = {
    "calma": {"sessao": 23.0, "semana": 41.0, "fable": 12.0, "forca": 0.62, "ritmo": 6.1, "cabe": 9.8,
              "reinicio_sessao_s": 8040.0, "reinicio_semana_s": 284400.0,
              "este_pc": {"sessoes": 2, "agentes": 0, "chamadas_min": 4}},
    "ambar": {"sessao": 58.0, "semana": 63.0, "fable": 22.0, "forca": 1.21, "ritmo": 14.1, "cabe": 11.6,
              "reinicio_sessao_s": 10140.0, "reinicio_semana_s": 226800.0,
              "este_pc": {"sessoes": 3, "agentes": 2, "chamadas_min": 11}},
    "vermelha": {"sessao": 81.0, "semana": 77.0, "fable": 31.0, "forca": 1.62, "ritmo": 19.4, "cabe": 12.0,
                 "reinicio_sessao_s": 4320.0, "reinicio_semana_s": 118800.0,
                 "este_pc": {"sessoes": 3, "agentes": 4, "chamadas_min": 18}},
}


def aberturas(r: dict, esc: float = 2.0, quadros: int = 45, tamanho: int = 130) -> dict:
    """As tres aberturas do medidor, desenhadas pelo MESMO codigo da janela (BGRA pre-multiplicado, como o Windows recebe):
    'minimizado' (a pilula), 'semi' (so o holograma, os numeros escondidos) e 'aberto' (holograma + painel). Devolve
    {nome: array} e o custo medio de um quadro do holograma em ms."""
    import types
    import numpy as np
    import skia
    import hologramas as H
    import janela as J
    t = time.time()                                     # o "faltam" dos reinicios conta-se deste relogio
    r = dict(r, t=t, leitura_ts=t)
    med = J.Medidor.__new__(J.Medidor)
    med.col = types.SimpleNamespace(retrato=lambda: r)
    med._pulsos, med._eventos_vistos, med._forca_antes, med.min, med.minimizou_ts = [], None, None, False, None
    med._ult_ret, med.registo, med._minimizar = {}, (lambda _t: None), (lambda *a, **k: None)
    J.Medidor._actualizar_q(med, 0.0)
    out, custo = {}, 0.0
    for nome, (lw, lh) in (("minimizado", J.PILULA), ("semi", (tamanho, tamanho)), ("aberto", (tamanho, tamanho + J.PAINEL_H))):
        buf = np.zeros((int(round(lh * esc)), int(round(lw * esc)), 4), np.uint8)
        cv = skia.Surface(buf, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType).getCanvas()
        ctx = H.Tela(cv)
        ctx.largura = tamanho
        st, t0 = {}, None
        for i in range(quadros):
            if i == 5:
                t0 = time.perf_counter()
            cv.clear(skia.Color4f(0, 0, 0, 0))
            cv.save()
            cv.scale(esc, esc)
            if nome == "minimizado":
                H.pilula(ctx, lw, lh, r, False)
            else:
                H.mark2(ctx, tamanho, tamanho, i / 30.0, 1 / 30.0, st)
                ctx.shadowBlur, ctx.globalAlpha = 0, 1
                ctx.setLineDash([])
                if nome == "aberto":
                    H.painel(ctx, tamanho, tamanho + 14, r)
            cv.restore()
        if nome == "semi":
            custo = (time.perf_counter() - t0) / max(1, quadros - 5) * 1000.0
        out[nome] = buf.copy()
    return {"imagens": out, "ms_por_quadro": round(custo, 3)}


def _png(buf, caminho, fundo=None):
    """BGRA pre-multiplicado -> PNG: transparente (fundo None) ou composto sobre uma cor."""
    import numpy as np
    from PIL import Image
    a = buf[:, :, 3:4].astype(np.float32) / 255.0
    rgb = buf[:, :, [2, 1, 0]].astype(np.float32)
    if fundo is None:
        com = np.where(a > 0, rgb / np.maximum(a, 1e-6), 0.0)
        img = np.concatenate([np.clip(com, 0, 255), a * 255.0], axis=2).astype(np.uint8)
        Image.fromarray(img, "RGBA").save(caminho)
    else:
        f = np.zeros_like(rgb)
        f[:, :] = fundo
        Image.fromarray(np.clip(rgb + f * (1 - a), 0, 255).astype(np.uint8), "RGB").save(caminho)


def sonda(destino: str, demo: bool = False):
    """Desenha o Reator Mark II nas tres aberturas - com os numeros REAIS de agora (uma volta do coletor, sem API) ou, com
    --demo, com os de exemplo das tres zonas da forca. PNG transparentes a 2x, uma folha sobre fundo escuro, e quanto custa
    um quadro do holograma."""
    import numpy as np
    from PIL import Image
    import dados as D
    if demo:
        casos = DEMO
    else:
        col = D.Coletor(ler_json(ESTADO, {}), ler_api=False)
        col.volta()
        time.sleep(2.1)
        casos = {"agora": col.volta()}
    os.makedirs(destino, exist_ok=True)
    custos = {}
    for zona, r in casos.items():
        a = aberturas(r)
        custos[zona] = a["ms_por_quadro"]
        for nome, buf in a["imagens"].items():
            _png(buf, os.path.join(destino, "mark2_{}_{}.png".format(zona, nome)))
        if zona in ("ambar", "agora"):                       # a folha das tres aberturas, lado a lado, sobre fundo escuro
            ims = [a["imagens"][n] for n in ("minimizado", "semi", "aberto")]
            gap, borda = 56, 48
            alto = max(b.shape[0] for b in ims) + 2 * borda
            largo = sum(b.shape[1] for b in ims) + gap * 2 + 2 * borda
            folha = np.zeros((alto, largo, 4), np.uint8)
            x = borda
            for b in ims:
                folha[borda:borda + b.shape[0], x:x + b.shape[1]] = b
                x += b.shape[1] + gap
            _png(folha, os.path.join(destino, "mark2_openings.png"), fundo=(13, 17, 23))
    if demo:                                                 # as tres zonas abertas, lado a lado
        ims = [aberturas(DEMO[z])["imagens"]["aberto"] for z in ("calma", "ambar", "vermelha")]
        gap, borda = 40, 40
        folha = np.zeros((ims[0].shape[0] + 2 * borda, sum(b.shape[1] for b in ims) + 2 * gap + 2 * borda, 4), np.uint8)
        x = borda
        for b in ims:
            folha[borda:borda + b.shape[0], x:x + b.shape[1]] = b
            x += b.shape[1] + gap
        _png(folha, os.path.join(destino, "mark2_forces.png"), fundo=(13, 17, 23))
    print(json.dumps({"ms_por_quadro": custos}, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="claude-quota-meter - a live desktop meter for your Claude Code quota")
    ap.add_argument("--toggle", "--alternar", dest="alternar", action="store_true", help="open or close (desktop shortcut)")
    ap.add_argument("--close", "--fechar", dest="fechar", action="store_true", help="close the open meter")
    ap.add_argument("--state", "--estado", dest="estado", action="store_true", help="one reading as JSON, no window, no API")
    ap.add_argument("--probe", "--sonda", dest="sonda", metavar="DIR", help="draw the three openings into DIR, no window")
    ap.add_argument("--demo", action="store_true", help="with --probe: sample numbers in the three force zones")
    ap.add_argument("--no-api", "--sem-api", dest="sem_api", action="store_true",
                    help="never call the opt-in usage endpoint (status line readings only)")
    a = ap.parse_args(argv)

    if sys.stderr is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
        try:                                                  # sem consola: os tracebacks vao para o registo
            sys.stderr = io.open(str(REGISTO), "a", encoding="utf-8", buffering=1)
        except OSError:
            pass

    if a.estado:
        import dados as D
        col = D.Coletor(ler_json(ESTADO, {}), ler_api=False)
        col.volta()
        time.sleep(2.1)
        r = col.volta()
        r.pop("janelas", None)
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    if a.sonda:
        sonda(a.sonda, demo=a.demo)
        return 0
    if a.fechar:
        havia = pedir_fecho()
        print("pedi ao medidor que fechasse" if havia else "nao havia medidor aberto")
        return 0
    if a.alternar and pedir_fecho():
        registo("--alternar: havia um aberto, pedi que fechasse")
        return 0

    ctypes.set_last_error(0)
    tranca = kernel32.CreateMutexW(None, False, NOME_TRANCA)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        registo("ja ha um medidor aberto (tranca {}): nao abro outro".format(NOME_TRANCA))
        return 0
    ev_fechar = kernel32.CreateEventW(None, True, False, NOME_FECHAR)

    import dados as D
    import janela as J
    cfg = ler_config()
    estado = ler_json(ESTADO, {}) or {}
    registo("abri (pid {}, tamanho {} px)".format(os.getpid(), cfg["tamanho_px"]))
    # os quadros chegavam atrasados. O medidor gasta < 1% da maquina: prioridade UM degrau acima do normal para o desenho sair
    # a horas (nunca "alta" nem "tempo real" - nao rouba nada a ninguem com este gasto).
    try:
        import psutil as _ps
        _ps.Process().nice(_ps.ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception as _ex:
        registo("nao subi a prioridade: {}".format(_ex))
    fonte = str((ler_json(CONFIG, {}) or {}).get("fonte") or "statusline")
    col = D.Coletor(estado, ler_api=(fonte == "oauth") and not a.sem_api, registo=registo)
    threading.Thread(target=col.correr, name="medidor-coletor", daemon=True).start()
    med = J.Medidor(col, cfg, estado, lambda e: gravar_json(ESTADO, e), lambda c: gravar_json(CONFIG, c), registo)

    vigia = {"mt": None, "cpu_t": time.time(), "cpu": None}
    try:
        vigia["mt"] = CONFIG.stat().st_mtime
    except OSError:
        pass

    def periodico():
        try:                                                  # o config.json mudou no disco: troca sem reiniciar
            mt = CONFIG.stat().st_mtime
            if mt != vigia["mt"]:
                vigia["mt"] = mt
                med.recarregar_cfg(validar_config(ler_json(CONFIG, {})))
                registo("config recarregado: tamanho {} px".format(med.cfg.get("tamanho_px")))
        except OSError:
            pass
        agora = time.time()
        if agora - vigia["cpu_t"] >= CPU_A_CADA_S:
            try:
                import psutil
                p = psutil.Process()
                ct = p.cpu_times()
                usado = ct.user + ct.system
                if vigia["cpu"] is not None:
                    frac = (usado - vigia["cpu"]) / max(1.0, agora - vigia["cpu_t"])
                    registo("cpu {:.2f}% de um nucleo = {:.2f}% da maquina ({} nucleos) · memoria {:.0f} MB · {:.1f} q/s · "
                            "{:.2f} ms/quadro".format(
                                frac * 100, frac * 100 / (psutil.cpu_count() or 1), psutil.cpu_count(),
                                p.memory_info().rss / 1048576.0, med._fps, med._custo * 1000))
                vigia["cpu"] = usado
            except Exception:
                pass
            vigia["cpu_t"] = agora
    med.periodico = periodico

    def esperar_fecho():
        kernel32.WaitForSingleObject(ev_fechar, 0xFFFFFFFF)
        registo("pediram para fechar (atalho ou --fechar)")
        med.fechar()
    threading.Thread(target=esperar_fecho, name="medidor-fecho", daemon=True).start()

    try:
        med.criar()
        med.correr()
    except Exception:
        registo("rebentou:\n" + traceback.format_exc())
        return 1
    finally:
        col.parar()
        try:
            gravar_json(ESTADO, dict(med.est, **col.estado_para_guardar()))
        except Exception:
            pass
        registo("fechei")
        kernel32.CloseHandle(tranca)
    return 0


if __name__ == "__main__":
    sys.exit(main())
