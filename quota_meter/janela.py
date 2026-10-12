# -*- coding: utf-8 -*-
"""janela.py - the meter window (Windows): borderless, per-pixel transparent, always on top, draggable.

The "-" button (or a double click) shrinks it to a pill with the numbers; when the force passes 1x it changes colour,
pulses slowly and, if minimised, opens by itself. Right click: change the hologram, the size, the legend, close.

PT: a janela do medidor (Windows): sem moldura, transparente pixel a pixel, sempre por cima, arrastavel.
"""
from __future__ import annotations

import ctypes
import math
import threading
import time
from ctypes import wintypes as W

import numpy as np
import skia

import dados as D
import hologramas as H

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
except OSError:
    wtsapi32 = None

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00080000, 0x00000008, 0x00000080, 0x08000000
CS_DBLCLKS = 0x0008
WM_DESTROY, WM_CLOSE, WM_TIMER, WM_MOUSEMOVE = 0x0002, 0x0010, 0x0113, 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0201, 0x0202, 0x0203, 0x0205
WM_MOUSELEAVE, WM_CAPTURECHANGED, WM_DISPLAYCHANGE, WM_SETTINGCHANGE = 0x02A3, 0x0215, 0x007E, 0x001A
WM_DPICHANGED, WM_WTSSESSION_CHANGE, WM_NULL, WM_SETCURSOR, WM_MOUSEACTIVATE = 0x02E0, 0x02B1, 0x0000, 0x0020, 0x0021
WM_APP = 0x8000
MA_NOACTIVATE = 3
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
HWND_TOPMOST = W.HWND(-1)
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
TME_LEAVE = 0x00000002
MF_STRING, MF_POPUP, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0000, 0x0010, 0x0800, 0x0008, 0x0001
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x0100, 0x0002, 0x0080
SW_SHOWNOACTIVATE = 4
IDC_ARROW, IDC_SIZEALL, IDC_HAND = 32512, 32646, 32649
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8
MONITOR_DEFAULTTONEAREST = 2

# --- reguas (cada uma com o porque)
FPS_MAX = 30.0                 # desenho a <= 30 fps
FPS_REPOUSO = 30.0
                               # M9 e 2,5 ms/quadro - 30 q/s cabem no tecto de CPU (o governador continua a mandar)
FPS_MINIMIZADO = 2.0           # a pilula so tem numeros e a contagem; 2/s chega para a ver mudar
FPS_PISO = 4.0                 # o governador nunca desce abaixo disto com o holograma aberto (abaixo fica aos solavancos)
TECTO_CPU_NUCLEO = 0.12        # 12% de UM nucleo = 1% da maquina com 12 nucleos (meta: <= 2% em repouso)
VIVO_APOS_CHAMADA_S = 3.0      # 30 fps durante 3 s depois de cada chamada (o flash e as ondas duram ~1-2 s)
ESPACO_PULSOS_S = 0.35         # varias chamadas na mesma volta do coletor batem uma a uma, nao todas juntas
MAX_PULSOS_POR_VOLTA = 6       # uma rajada de 40 respostas nao vira 40 batidas seguidas
PERIODO_PISCAR_S = 2.6         # "pisca devagar": um ciclo a cada 2,6 s, de 100% a 55% de opacidade
OPACIDADE_MIN_PISCAR = 0.55
TOPO_A_CADA_S = 10.0           # reafirma "sempre por cima" (outra janela topmost pode passar a frente)
ALFA_AGARRAR = 3               # /255: invisivel, mas os pixels com alfa > 0 recebem o rato (para arrastar)
RAIO_AGARRAR = 0.20
                               # tapava os icones do ambiente de trabalho debaixo dos vazios do holograma
PILULA = (348, 30)             # tamanho logico da pilula
PAINEL_H = 78
TAMANHOS = {"micro": 130, "mini": 150, "pequeno": 180, "compacto": 200, "medio": 260, "grande": 340}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", W.DWORD * 3)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE),
                ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("dwFlags", W.DWORD), ("hwndTrack", W.HWND), ("dwHoverTime", W.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", W.HWND), ("message", W.UINT), ("wParam", W.WPARAM), ("lParam", W.LPARAM), ("time", W.DWORD),
                ("pt", POINT), ("lPrivate", W.DWORD)]


def _assinar():
    u, g, k = user32, gdi32, kernel32
    u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.DefWindowProcW.restype = LRESULT
    u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u.RegisterClassExW.restype = W.ATOM
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u.CreateWindowExW.restype = W.HWND
    u.GetMessageW.argtypes = [ctypes.POINTER(MSG), W.HWND, W.UINT, W.UINT]
    u.GetMessageW.restype = W.BOOL
    u.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    u.DispatchMessageW.restype = LRESULT
    u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.SetTimer.argtypes = [W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p]
    u.SetTimer.restype = ctypes.c_size_t
    u.KillTimer.argtypes = [W.HWND, ctypes.c_size_t]
    u.UpdateLayeredWindow.argtypes = [W.HWND, W.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), W.HDC,
                                      ctypes.POINTER(POINT), W.COLORREF, ctypes.POINTER(BLENDFUNCTION), W.DWORD]
    u.UpdateLayeredWindow.restype = W.BOOL
    u.GetDC.argtypes = [W.HWND]
    u.GetDC.restype = W.HDC
    u.ReleaseDC.argtypes = [W.HWND, W.HDC]
    u.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
    u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u.SetCapture.argtypes = [W.HWND]
    u.SetCapture.restype = W.HWND
    u.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
    u.CreatePopupMenu.restype = W.HMENU
    u.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
    u.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p]
    u.TrackPopupMenu.restype = ctypes.c_int
    u.DestroyMenu.argtypes = [W.HMENU]
    u.SetForegroundWindow.argtypes = [W.HWND]
    u.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    u.DestroyWindow.argtypes = [W.HWND]
    u.LoadCursorW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
    u.LoadCursorW.restype = W.HANDLE
    u.SetCursor.argtypes = [W.HANDLE]
    u.EnumDisplayMonitors.argtypes = [W.HDC, ctypes.c_void_p, MONITORENUMPROC, W.LPARAM]
    u.GetMonitorInfoW.argtypes = [W.HMONITOR, ctypes.POINTER(MONITORINFO)]
    u.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    u.MonitorFromWindow.restype = W.HMONITOR
    u.GetDpiForWindow.argtypes = [W.HWND]
    u.GetDpiForWindow.restype = W.UINT
    g.CreateCompatibleDC.argtypes = [W.HDC]
    g.CreateCompatibleDC.restype = W.HDC
    g.CreateDIBSection.argtypes = [W.HDC, ctypes.POINTER(BITMAPINFO), W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
    g.CreateDIBSection.restype = W.HBITMAP
    g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    g.SelectObject.restype = W.HGDIOBJ
    g.DeleteObject.argtypes = [W.HGDIOBJ]
    g.DeleteDC.argtypes = [W.HDC]
    k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    k.GetModuleHandleW.restype = W.HMODULE
    if wtsapi32 is not None:
        wtsapi32.WTSRegisterSessionNotification.argtypes = [W.HWND, W.DWORD]
        wtsapi32.WTSUnRegisterSessionNotification.argtypes = [W.HWND]


_assinar()


def dpi_por_monitor():
    """O processo passa a ver pixels FISICOS em todos os monitores (Per-Monitor v2); sem isto o Windows esticava o
    bitmap e o holograma ficava esborratado com a escala de 125%."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def areas_de_trabalho() -> list:
    """[(esq, topo, dir, baixo)] da area de trabalho (sem a barra de tarefas) de cada monitor, em pixels fisicos."""
    fora = []

    def cb(hmon, _hdc, _r, _lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            fora.append((r.left, r.top, r.right, r.bottom))
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return fora


def _lo(v):
    return ctypes.c_short(v & 0xFFFF).value


def _hi(v):
    return ctypes.c_short((v >> 16) & 0xFFFF).value


class Medidor:
    """A janela e o laco de desenho. `cfg` = config.json; `estado` = estado.json (posicao, minimizado, reinicios)."""

    def __init__(self, coletor, cfg: dict, estado: dict, gravar_estado, gravar_cfg, registo):
        self.col = coletor
        self.cfg = cfg
        self.est = estado
        self.gravar_estado = gravar_estado
        self.gravar_cfg = gravar_cfg
        self.registo = registo
        self.hwnd = None
        self.esc = 1.0
        self.min = bool(estado.get("minimizado"))
        self.painel_min = bool(estado.get("painel_min"))
        self.minimizou_ts = estado.get("minimizou_ts")
        self.x, self.y = estado.get("x"), estado.get("y")
        self.w = self.h = 0
        self.hover = False
        self.botao_hover = None
        self.botao_premido = None
        self.arrasto = None
        self.pausado = False
        self.st = {}
        self.ctx = None
        self._dib = None
        self._t_ant = time.monotonic()
        self._t0 = self._t_ant
        self._fps = 0.0
        self._custo = 0.002
        self._ult_ret = {}
        self._eventos_vistos = None
        self._pulsos = []
        self._ult_chamada = -1e9
        self._forca_antes = None
        self._t_topo = 0.0
        self._t_cfg = 0.0
        self._cfg_mt = None
        self.periodico = None
        self._quadros = 0
        self._wndproc = WNDPROC(self._proc_janela)

    # -------------------------------------------------------------- geometria
    def _logico(self):
        if self.min:
            return PILULA
        lado = int(self.cfg.get("tamanho_px") or 260)
        return lado, lado + (0 if getattr(self, "painel_min", False) else PAINEL_H)

    def _redimensionar(self, ancorar: bool = True):
        lw, lh = self._logico()
        nw, nh = int(round(lw * self.esc)), int(round(lh * self.esc))
        if self.x is None or self.y is None:
            areas = areas_de_trabalho() or [(0, 0, 1920, 1040)]
            l, t, r, b = next((a for a in areas if a[0] <= 0 < a[2] and a[1] <= 0 < a[3]), areas[0])
            self.x, self.y = r - nw - int(24 * self.esc), t + int(24 * self.esc)
        elif ancorar and self.w:
            # ancora no lado mais perto: encostado a direita, cresce para a esquerda (e vice-versa)
            areas = areas_de_trabalho() or [(0, 0, 1920, 1040)]
            cx = self.x + self.w / 2.0
            alvo = min(areas, key=lambda a: 0 if a[0] <= cx <= a[2] else min(abs(cx - a[0]), abs(cx - a[2])))
            if cx > (alvo[0] + alvo[2]) / 2.0:
                self.x = self.x + self.w - nw
        self.w, self.h = nw, nh
        self.x, self.y = D.dentro_do_ecra(self.x, self.y, self.w, self.h, areas_de_trabalho())
        self._criar_dib()
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, self.x, self.y, self.w, self.h, SWP_NOACTIVATE)

    def _criar_dib(self):
        self._largar_dib()
        hdc_ecra = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(hdc_ecra)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.w
        bmi.bmiHeader.biHeight = -self.h               # de cima para baixo: a linha 0 e o topo (como o skia)
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc_ecra, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not hbmp:
            raise OSError("CreateDIBSection falhou ({})".format(ctypes.get_last_error()))
        velho = gdi32.SelectObject(mem, hbmp)
        buf = (ctypes.c_uint8 * (self.w * self.h * 4)).from_address(bits.value)
        arr = np.ctypeslib.as_array(buf).reshape(self.h, self.w, 4)
        surf = skia.Surface(arr, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self._dib = {"hdc_ecra": hdc_ecra, "mem": mem, "hbmp": hbmp, "velho": velho, "arr": arr, "surf": surf, "buf": buf,
                     "agarrar": None if self.min else self._mascara_agarrar()}
        self.ctx = H.Tela(surf.getCanvas())
        self.st = {}

    def _mascara_agarrar(self):
        """O disco por tras do holograma e o fundo do painel com alfa ALFA_AGARRAR: pixels com alfa > 0 recebem o rato.
        Pintar isto com o skia custava 6-10 ms por quadro (um preenchimento translucido grande e lento nesta compilacao do
        skia, medido); como mascara de numpy (alfa = max(alfa, 3)) custa 0,05 ms e da o mesmo resultado pre-multiplicado
        (um pixel vazio vira preto a 3/255; um pixel com cor so ganha alfa se tinha 1 ou 2)."""
        lado = float(self.cfg.get("tamanho_px") or 260) * self.esc
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        cx, cy, r = lado / 2.0, lado / 2.0 - 6 * self.esc, lado * RAIO_AGARRAR
        m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
        m |= (yy >= lado - 4 * self.esc) & (xx >= 6 * self.esc) & (xx <= self.w - 6 * self.esc)
        return (m * ALFA_AGARRAR).astype(np.uint8)

    def _largar_dib(self):
        d = self._dib
        if not d:
            return
        self.ctx = None
        d["surf"] = None
        gdi32.SelectObject(d["mem"], d["velho"])
        gdi32.DeleteObject(d["hbmp"])
        gdi32.DeleteDC(d["mem"])
        user32.ReleaseDC(None, d["hdc_ecra"])
        self._dib = None

    # -------------------------------------------------------------- dados -> Q
    def _actualizar_q(self, agora: float):
        r = self.col.retrato()
        if not r:
            return
        self._ult_ret = r
        Q = H.Q
        Q.sessao = float(r.get("sessao") or 0.0)
        Q.semana = float(r.get("semana") or 0.0)
        Q.fable = float(r.get("fable") or 0.0)
        Q.forca_real = r.get("forca")
        Q.ritmo = float(r.get("ritmo") or 0.0)
        Q.cabe = float(r.get("cabe") or 0.0)
        pc = r.get("este_pc") or {}
        Q.agentes = int(pc.get("agentes") or 0)
        idade = time.time() - float(r.get("t") or time.time())
        Q.reinicioS = max(0.0, float(r["reinicio_sessao_s"]) - idade) if r.get("reinicio_sessao_s") is not None else None
        Q.reinicioW = max(0.0, float(r["reinicio_semana_s"]) - idade) if r.get("reinicio_semana_s") is not None else None
        nm = D.nivel_do_modo(pc.get("modo"))
        Q.modo = nm if nm is not None else 0
        Q.sev = D.severidade(None, r.get("forca"))
        ev = int(pc.get("eventos") or 0)
        if self._eventos_vistos is None:
            self._eventos_vistos = ev
        elif ev > self._eventos_vistos:
            n = min(MAX_PULSOS_POR_VOLTA, ev - self._eventos_vistos)
            base = max([agora] + self._pulsos)
            self._pulsos.extend(base + i * ESPACO_PULSOS_S for i in range(n))
            self._eventos_vistos = ev
        al = D.alerta(r.get("forca"), self._forca_antes, self.min, time.time(), self.minimizou_ts)
        self._forca_antes = r.get("forca")
        if al["abrir"]:
            self.registo("forca {} passou de 1x: abri sozinho".format(D.fmt_forca(r.get("forca"))))
            self._minimizar(False, manual=False)

    def _bater(self, agora: float):
        Q = H.Q
        while self._pulsos and self._pulsos[0] <= agora:
            self._pulsos.pop(0)
            Q.ultima_chamada = agora
            Q.chamadas.append(agora)
            self._ult_chamada = agora
        Q.chamadas = [t for t in Q.chamadas if agora - t < 60.0]

    # -------------------------------------------------------------- desenho
    def _botoes(self):
        """{nome: (x, y)} em coordenadas logicas."""
        if self.min:
            return {"abrir": (PILULA[0] - 18, PILULA[1] / 2.0)}
        lado = int(self.cfg.get("tamanho_px") or 260)
        return {"minimizar": (lado - 14, 14), "menu": (lado - 38, 14), "numeros": (lado - 62, 14)}

    def _botao_em(self, lx, ly):
        for nome, (bx, by) in self._botoes().items():
            if (lx - bx) ** 2 + (ly - by) ** 2 <= 12 ** 2:
                return nome
        return None

    def desenhar(self):
        if not self._dib or self.pausado:
            return
        t0 = time.perf_counter()
        agora = time.monotonic()
        dt = min(0.1, max(0.0, agora - self._t_ant))
        self._t_ant = agora
        self._bater(agora)
        ctx = self.ctx
        cv = ctx.c
        r = self._ult_ret
        lw, lh = self._logico()
        cv.save()
        cv.scale(self.esc, self.esc)
        if self.min:
            H.pilula(ctx, lw, lh, r, self.botao_hover == "abrir")
        else:
            lado = lw
            cv.clear(skia.Color4f(0, 0, 0, 0))
            ctx.largura = lado
            # camada com a opacidade do piscar); os numeros e a janela ficam sempre firmes (antes piscava a janela inteira)
            f_ = r.get("forca")
            a_pisca = 1.0
            if f_ is not None and f_ > D.FORCA_AMARELA:
                fase_ = (math.cos(TAU_ * (agora - self._t0) / PERIODO_PISCAR_S) + 1) / 2.0
                a_pisca = OPACIDADE_MIN_PISCAR + (1 - OPACIDADE_MIN_PISCAR) * fase_
            camada = a_pisca < 0.999
            if camada:
                cv.saveLayerAlpha(skia.Rect.MakeWH(lado, lado), int(255 * a_pisca))
            try:
                H.mark2(ctx, lado, lado, agora - self._t0, dt, self.st)
            except Exception as ex:                           # um quadro que rebenta nao apaga o medidor: recomeca o estado
                self.registo("o holograma rebentou ({}: {}); recomeco o estado".format(type(ex).__name__, str(ex)[:100]))
                self.st = {}
            if camada:
                cv.restore()
            ctx.shadowBlur = 0
            ctx.globalAlpha = 1
            ctx.setLineDash([])
            if not self.painel_min:
                H.painel(ctx, lw, lado + 14, r)
            if self.hover:
                for nome, (bx, by) in self._botoes().items():
                    H.botao(ctx, bx, by, nome, self.botao_hover == nome)
        cv.restore()
        mask = self._dib.get("agarrar")
        if mask is not None and not self.min:
            alfa_canal = self._dib["arr"][:, :, 3]
            np.maximum(alfa_canal, mask, out=alfa_canal)
        # pisca devagar quando a forca passa de 1x
        self._mostrar(255)
        self._quadros += 1
        custo = time.perf_counter() - t0
        self._custo = self._custo * 0.9 + custo * 0.1

    def _mostrar(self, alfa: int):
        d = self._dib
        pos = POINT(self.x, self.y)
        tam = SIZE(self.w, self.h)
        org = POINT(0, 0)
        bf = BLENDFUNCTION(AC_SRC_OVER, 0, max(0, min(255, alfa)), AC_SRC_ALPHA)
        if not user32.UpdateLayeredWindow(self.hwnd, d["hdc_ecra"], ctypes.byref(pos), ctypes.byref(tam), d["mem"],
                                          ctypes.byref(org), 0, ctypes.byref(bf), ULW_ALPHA):
            err = ctypes.get_last_error()
            if self._quadros % 300 == 0:
                self.registo("UpdateLayeredWindow falhou ({})".format(err))

    # -------------------------------------------------------------- ritmo dos quadros
    def _fps_alvo(self, agora: float) -> float:
        if self.pausado:
            return 1.0
        if self.min:
            return FPS_MINIMIZADO
        vivo = (agora - self._ult_chamada) < VIVO_APOS_CHAMADA_S or self.hover or self.arrasto is not None or self._pulsos
        alvo = float(self.cfg.get("fps_max") or FPS_MAX) if vivo else float(self.cfg.get("fps_repouso") or FPS_REPOUSO)
        teto = float(self.cfg.get("teto_cpu_nucleo") or TECTO_CPU_NUCLEO)
        por_cpu = teto / max(1e-4, self._custo)
        return max(min(FPS_PISO, alvo), min(alvo, por_cpu))

    def _rearmar(self):
        fps = self._fps_alvo(time.monotonic())
        if abs(fps - self._fps) > 0.5:
            self._fps = fps
            if not getattr(self, "_hires", False):
                user32.SetTimer(self.hwnd, 1, max(16, int(1000.0 / fps)), None)

    # -------------------------------------------------------------- accoes
    def _minimizar(self, sim: bool, manual: bool = True):
        if sim == self.min:
            return
        self.min = sim
        if sim and manual:
            self.minimizou_ts = time.time()
        self._redimensionar(ancorar=True)
        self._guardar()
        self.desenhar()
        self._rearmar()

    def _painel(self, esconder: bool):
        """04/10: esconde ou mostra os numeros por baixo
        do holograma (o holograma fica); botao das tres linhas ou o menu. Guarda a escolha."""
        if esconder == self.painel_min:
            return
        self.painel_min = esconder
        self._redimensionar(ancorar=True)
        self._guardar()
        self.desenhar()

    def _guardar(self):
        e = dict(self.est)
        e.update({"painel_min": bool(self.painel_min)})
        e.update({"x": int(self.x), "y": int(self.y), "minimizado": bool(self.min), "minimizou_ts": self.minimizou_ts,
                  "escala": self.esc})
        e.update(self.col.estado_para_guardar())
        self.est = e
        try:
            self.gravar_estado(e)
        except Exception as ex:
            self.registo("nao gravei o estado: {}".format(ex))

    def _menu(self):
        m = user32.CreatePopupMenu()
        tam = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING, 1, "Abrir" if self.min else "Minimizar")
        user32.AppendMenuW(m, MF_STRING, 3, "Mostrar os números" if self.painel_min else "Esconder os números")
        lado = int(self.cfg.get("tamanho_px") or 260)
        for i, (nome, px) in enumerate(TAMANHOS.items()):
            user32.AppendMenuW(tam, MF_STRING | (MF_CHECKED if px == lado else 0), 200 + i, "{} ({} px)".format(nome.capitalize(), px))
        user32.AppendMenuW(m, MF_POPUP, ctypes.cast(tam, ctypes.c_void_p).value, "Tamanho")
        r = self._ult_ret
        leg = user32.CreatePopupMenu()
        for sigla, txt_ in H.LEGENDA:
            user32.AppendMenuW(leg, MF_STRING, 0, "{}  {}".format(sigla, txt_))
        user32.AppendMenuW(m, MF_POPUP, ctypes.cast(leg, ctypes.c_void_p).value, "Legenda")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        pc = r.get("este_pc") or {}
        info = "Fonte: {} · {}".format(pc.get("fonte") or "?", pc.get("erro_api") or "ok")
        user32.AppendMenuW(m, MF_STRING | MF_GRAYED, 0, info)
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, 2, "Fechar o medidor")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd == 1:
            self._minimizar(not self.min)
        elif cmd == 2:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        elif cmd == 3:
            self._painel(not self.painel_min)
        elif 200 <= cmd < 200 + len(TAMANHOS):
            self.cfg["tamanho_px"] = list(TAMANHOS.values())[cmd - 200]
            self.ctx.memo.limpar() if self.ctx else None
            self._redimensionar(ancorar=True)
            self._gravar_cfg()
            self._guardar()

    def _gravar_cfg(self):
        try:
            self.gravar_cfg(self.cfg)
            self._cfg_mt = None
        except Exception as ex:
            self.registo("nao gravei o config: {}".format(ex))

    def recarregar_cfg(self, novo: dict):
        """O config.json mudou no disco: troca o tamanho (e o resto) sem reiniciar."""
        mudou_tam = int(novo.get("tamanho_px") or 260) != int(self.cfg.get("tamanho_px") or 260)
        self.cfg = novo
        if mudou_tam:
            self._redimensionar(ancorar=True)

    # -------------------------------------------------------------- o procedimento da janela
    def _proc_janela(self, hwnd, msg, wp, lp):
        try:
            return self._tratar(hwnd, msg, wp, lp)
        except Exception as ex:                               # uma excepcao num callback do ctypes nao pode matar o laco
            self.registo("excepcao na janela (msg {}): {}: {}".format(msg, type(ex).__name__, str(ex)[:160]))
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _tratar(self, hwnd, msg, wp, lp):
        if msg == WM_TIMER:
            if getattr(self, "_hires", False):
                return 0                                      # o relogio de alta precisao manda nos quadros
            self._tick()
            return 0
        return self._tratar_resto(hwnd, msg, wp, lp)

    def _tick(self):
        """Um quadro: dados de meio em meio segundo, reafirmar 'sempre por cima', desenhar, rearmar o ritmo."""
        hwnd = self.hwnd
        if True:
            agora = time.monotonic()
            if agora - getattr(self, "_t_dados", 0) >= 0.5:
                self._t_dados = agora
                self._actualizar_q(agora)
            if agora - self._t_topo >= TOPO_A_CADA_S:
                self._t_topo = agora
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                if self.periodico is not None:
                    self.periodico()
            self.desenhar()
            self._rearmar()

    def _tratar_resto(self, hwnd, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_MOUSEMOVE:
            lx, ly = _lo(lp) / self.esc, _hi(lp) / self.esc
            if self.arrasto is not None:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                dx, dy = pt.x - self.arrasto["cx"], pt.y - self.arrasto["cy"]
                if abs(dx) + abs(dy) > 3:
                    self.arrasto["mexeu"] = True
                if self.arrasto["mexeu"]:
                    self.x, self.y = self.arrasto["wx"] + dx, self.arrasto["wy"] + dy
                    user32.SetWindowPos(hwnd, None, self.x, self.y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                return 0
            if not self.hover:
                self.hover = True
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
                self._rearmar()
            self.botao_hover = self._botao_em(lx, ly)
            return 0
        if msg == WM_SETCURSOR:
            user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND if self.botao_hover else IDC_SIZEALL)))
            return 1
        if msg == WM_MOUSELEAVE:
            self.hover = False
            self.botao_hover = None
            return 0
        if msg == WM_LBUTTONDOWN:
            lx, ly = _lo(lp) / self.esc, _hi(lp) / self.esc
            b = self._botao_em(lx, ly)
            if b:
                self.botao_premido = b
                return 0
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self.arrasto = {"cx": pt.x, "cy": pt.y, "wx": self.x, "wy": self.y, "mexeu": False}
            user32.SetCapture(hwnd)
            return 0
        if msg == WM_LBUTTONUP:
            if self.arrasto is not None:
                mexeu = self.arrasto["mexeu"]
                self.arrasto = None
                user32.ReleaseCapture()
                if mexeu:
                    self.x, self.y = D.dentro_do_ecra(self.x, self.y, self.w, self.h, areas_de_trabalho())
                    user32.SetWindowPos(hwnd, None, self.x, self.y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                    self._guardar()
                return 0
            lx, ly = _lo(lp) / self.esc, _hi(lp) / self.esc
            b = self._botao_em(lx, ly)
            if b and b == self.botao_premido:
                if b == "minimizar":
                    self._minimizar(True)
                elif b == "abrir":
                    self._minimizar(False)
                elif b == "menu":
                    self._menu()
                elif b == "numeros":
                    self._painel(not self.painel_min)
            self.botao_premido = None
            return 0
        if msg == WM_LBUTTONDBLCLK:
            self._minimizar(not self.min)
            return 0
        if msg == WM_CAPTURECHANGED:
            if self.arrasto is not None and self.arrasto.get("mexeu"):
                self._guardar()
            self.arrasto = None
            return 0
        if msg == WM_RBUTTONUP:
            self._menu()
            return 0
        if msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
            # mudou a resolucao, tirou-se um monitor ou a barra de tarefas mexeu: volta para dentro do ecra
            self.x, self.y = D.dentro_do_ecra(self.x, self.y, self.w, self.h, areas_de_trabalho())
            user32.SetWindowPos(hwnd, None, self.x, self.y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            return 0
        if msg == WM_DPICHANGED:
            self.esc = max(0.5, _hi(wp) / 96.0)
            if self.ctx:
                self.ctx.memo.limpar()
            self._redimensionar(ancorar=False)
            return 0
        if msg == WM_WTSSESSION_CHANGE:
            if wp == WTS_SESSION_LOCK:
                self.pausado = True
            elif wp == WTS_SESSION_UNLOCK:
                self.pausado = False
            self._rearmar()
            return 0
        if msg == WM_CLOSE:
            self._guardar()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            if wtsapi32 is not None:
                try:
                    wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                except Exception:
                    pass
            user32.KillTimer(hwnd, 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # -------------------------------------------------------------- arranque e laco
    def criar(self):
        dpi_por_monitor()
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = CS_DBLCLKS
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = "ClaudeQuotaMeter"
        user32.RegisterClassExW(ctypes.byref(wc))
        self._wc = wc
        self.hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                           "ClaudeQuotaMeter", "Claude Quota Meter", WS_POPUP, 0, 0, 10, 10,
                                           None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW falhou ({})".format(ctypes.get_last_error()))
        try:
            self.esc = max(0.5, user32.GetDpiForWindow(self.hwnd) / 96.0)
        except Exception:
            self.esc = 1.0
        if self.est.get("escala") and abs(float(self.est["escala"]) - self.esc) > 0.01 and self.x is not None:
            pass                                              # a posicao e em pixels fisicos: vale com qualquer escala
        self._redimensionar(ancorar=False)
        if wtsapi32 is not None:
            try:
                wtsapi32.WTSRegisterSessionNotification(self.hwnd, 0)
            except Exception:
                pass
        self._actualizar_q(time.monotonic())
        self.desenhar()
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self._rearmar()

    def correr(self):
        """04/10: o SetTimer anda em passos de 15,6 ms (pedir 30 q/s dava ~21 e aos solavancos). O laco
        espera agora num RELOGIO DE ALTA PRECISAO do Windows (CreateWaitableTimerExW + CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
        Windows 10 1803+) OU numa mensagem - sem mudar a resolucao do relogio da maquina inteira. Sem esse relogio (Windows
        antigo), fica o SetTimer de antes."""
        h = _relogio_alta_precisao()
        if not h:
            return self._correr_settimer()
        self._hires = True
        user32.KillTimer(self.hwnd, 1)
        msg = MSG()
        handles = (W.HANDLE * 1)(h)
        prox = time.perf_counter()
        try:
            while True:
                periodo = 1.0 / max(1.0, self._fps or FPS_MAX)
                agora = time.perf_counter()
                if prox - agora > periodo or agora - prox > periodo:
                    prox = agora + periodo                   # atrasado ou adiantado demais (ecra bloqueado, arrasto): recomeca
                falta = max(0.0, prox - agora)
                due = ctypes.c_longlong(-int(falta * 1e7))   # negativo = relativo, em 100 ns
                kernel32.SetWaitableTimer(h, ctypes.byref(due), 0, None, None, False)
                r = user32.MsgWaitForMultipleObjectsEx(1, handles, 0xFFFFFFFF, QS_ALLINPUT, MWMO_INPUTAVAILABLE)
                if r == WAIT_OBJECT_0:
                    prox += periodo
                    self._tick()
                    continue
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_QUIT:
                        return
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            kernel32.CloseHandle(h)
            self._largar_dib()

    def _correr_settimer(self):
        msg = MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._largar_dib()

    def fechar(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


TAU_ = math.pi * 2

CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002
TIMER_ALL_ACCESS = 0x1F0003
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WAIT_OBJECT_0 = 0
PM_REMOVE = 0x0001
WM_QUIT = 0x0012
kernel32.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, W.LPCWSTR, W.DWORD, W.DWORD]
kernel32.CreateWaitableTimerExW.restype = W.HANDLE
kernel32.SetWaitableTimer.argtypes = [W.HANDLE, ctypes.POINTER(ctypes.c_longlong), W.LONG, ctypes.c_void_p, ctypes.c_void_p, W.BOOL]
kernel32.SetWaitableTimer.restype = W.BOOL
kernel32.CloseHandle.argtypes = [W.HANDLE]
user32.MsgWaitForMultipleObjectsEx.argtypes = [W.DWORD, ctypes.POINTER(W.HANDLE), W.DWORD, W.DWORD, W.DWORD]
user32.MsgWaitForMultipleObjectsEx.restype = W.DWORD
user32.PeekMessageW.argtypes = [ctypes.c_void_p, W.HWND, W.UINT, W.UINT, W.UINT]
user32.PeekMessageW.restype = W.BOOL
user32.KillTimer.argtypes = [W.HWND, ctypes.c_size_t]


def _relogio_alta_precisao():
    """O relogio de alta precisao (Windows 10 1803+); None se o Windows nao o tiver."""
    try:
        h = kernel32.CreateWaitableTimerExW(None, None, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS)
        return h or None
    except Exception:
        return None
