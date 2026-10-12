# -*- coding: utf-8 -*-
"""hologramas.py - the Arc Reactor Mark II of claude-quota-meter, drawn with skia, plus the panel and the pill.

The hologram reacts to the account: the FORCE (rate / what fits until the reset) sets its speed and colour (cyan below
1x, amber from 1x, red from 1.4x), the rings show the 5-hour and weekly windows and their resets, and every model call on
this machine makes it flash. Under it, the panel of numbers (legend in the right-click menu).

PT: o Reator Mark II do medidor (skia), o painel e a pilula. A FORCA da conta da a velocidade e a cor; os aneis sao a
sessao de 5 h e a semana.
"""
from __future__ import annotations

import math

import skia

TAU = math.pi * 2
C = {"cy": "#2dd4e8", "ouro": "#f2c230", "ok": "#34d399", "mau": "#f87171", "am": "#fbbf24", "vi": "#a78bfa",
     "az": "#60a5fa", "tx": "#cfe7ee", "dim": "#7f9aa5"}          # dim mais claro do que na galeria: aqui nao ha fundo escuro
MODOS = [("VERDE", "#34d399"), ("AMARELO", "#fbbf24"), ("VERMELHO", "#f87171")]
CINZA = "#8aa0aa"
FONTE_MONO = ("Cascadia Mono", "Consolas")
BLUR_REAL_MAX_PX2 = 6000.0      # desfoque verdadeiro so em formas pequenas (medido: um anel de 205 px desfocado = 11 ms)

_CORES = {}


def rgba(c, a_mult: float = 1.0):
    """'#rrggbb' | 'rgba(r,g,b,a)' -> skia Color4f (com cache)."""
    k = (c, a_mult)
    v = _CORES.get(k)
    if v is not None:
        return v
    s = str(c).strip()
    if s.startswith("#"):
        n = int(s[1:7], 16)
        r, g, b, a = (n >> 16) & 255, (n >> 8) & 255, n & 255, 1.0
    elif s.startswith("rgba") or s.startswith("rgb"):
        p = [x.strip() for x in s[s.index("(") + 1:s.rindex(")")].split(",")]
        r, g, b = int(float(p[0])), int(float(p[1])), int(float(p[2]))
        a = float(p[3]) if len(p) > 3 else 1.0
    else:
        r, g, b, a = 255, 255, 255, 1.0
    v = skia.Color4f(r / 255.0, g / 255.0, b / 255.0, max(0.0, min(1.0, a * a_mult)))
    if len(_CORES) < 4096:
        _CORES[k] = v
    return v


def hexa(c, a: float) -> str:
    """rgba() da galeria: '#rrggbb' + alfa -> 'rgba(...)'."""
    n = int(str(c)[1:7], 16)
    return "rgba({},{},{},{})".format((n >> 16) & 255, (n >> 8) & 255, n & 255, a)


def _sweep(a0, a1, ccw):
    """O varrimento de arc() do Canvas 2D (a especificacao: 2pi ou mais = circulo inteiro; senao modulo 2pi)."""
    if not ccw:
        d = a1 - a0
        return TAU if d >= TAU else d % TAU
    d = a0 - a1
    return -TAU if d >= TAU else -(d % TAU)


class Gradiente:
    def __init__(self, tipo, args):
        self.tipo, self.args, self.stops = tipo, args, []

    def addColorStop(self, off, cor):
        self.stops.append((float(off), cor))

    def shader(self):
        # PREMUL como o <canvas> do Chrome: sem isto o "k -> rgba(0,0,0,0)" do nucleo passa por cinzento (anel cinza a volta
        # do nucleo do Reator, visto na sonda sobre o fundo claro)
        cores = [rgba(c).toColor() for _o, c in self.stops] or [0, 0]
        pos = [o for o, _c in self.stops] or None
        fl = skia.GradientShader.kInterpolateColorsInPremul_Flag
        if self.tipo == "linear":
            x0, y0, x1, y1 = self.args
            return skia.GradientShader.MakeLinear([skia.Point(x0, y0), skia.Point(x1, y1)], cores, pos,
                                                  skia.TileMode.kClamp, fl)
        x0, y0, r0, x1, y1, r1 = self.args
        if r0 <= 0 and x0 == x1 and y0 == y1:
            return skia.GradientShader.MakeRadial(skia.Point(x1, y1), max(0.01, r1), cores, pos, skia.TileMode.kClamp, fl)
        return skia.GradientShader.MakeTwoPointConical(skia.Point(x0, y0), r0, skia.Point(x1, y1), max(0.01, r1), cores, pos,
                                                       skia.TileMode.kClamp, fl)


class Memo:
    """MEMORIA DE DESENHO (04/10, medido): um anel grosso custava ~1 ms, as faixas de brilho ~0,9 ms cada, o nucleo em
    gradiente 2 ms e um texto com contorno 0,9 ms - e quase tudo e IGUAL de um quadro para o outro (os aneis so mudam
    quando a cota muda). Uma forma vista dois quadros seguidos com a mesma chave (caminho, tinta, transformacao) passa a
    ser desenhada uma vez numa imagem e depois so copiada (~0,1 ms). O que roda (pas, satelites) muda de chave a cada
    quadro e continua a desenhar-se ao vivo - nunca fica pior do que antes. O alfa global sem brilho fica FORA da chave e
    aplica-se na copia (e linear), para os nucleos que pulsam tambem caberem."""

    MAX = 220
    MAX_BYTES = 48 * 1024 * 1024    # tecto da memoria das imagens (uma imagem de anel ~0,45 MB)
    MAX_VISTOS = 3000
    MAX_PX = 700 * 700

    def __init__(self):
        self.imgs = {}
        self.vistos = set()
        self.acertos = 0
        self.falhas = 0
        self.bytes = 0

    def limpar(self):
        self.imgs.clear()
        self.vistos.clear()
        self.bytes = 0

    @staticmethod
    def _tam(img) -> int:
        return int(img[0].width()) * int(img[0].height()) * 4

    def guardar(self, chave, img):
        novo = self._tam(img)
        while self.imgs and (len(self.imgs) >= self.MAX or self.bytes + novo > self.MAX_BYTES):
            k = next(iter(self.imgs))                            # o mais antigo sai primeiro (dict guarda a ordem)
            self.bytes -= self._tam(self.imgs.pop(k))
        self.imgs[chave] = img
        self.bytes += novo


def _chave_estilo(e):
    if isinstance(e, Gradiente):
        return ("g", e.tipo, tuple(round(float(x), 2) for x in e.args), tuple((round(o, 3), c) for o, c in e.stops))
    return e


class Tela:
    """O pedaco do CanvasRenderingContext2D que os hologramas usam, sobre um skia.Canvas."""

    def __init__(self, canvas: skia.Canvas):
        self.c = canvas
        self.strokeStyle = "#000000"
        self.fillStyle = "#000000"
        self.lineWidth = 1.0
        self.lineCap = "round"
        self.lineJoin = "miter"
        self.globalAlpha = 1.0
        self.shadowBlur = 0.0
        self.shadowColor = "rgba(0,0,0,0)"
        self.lineDashOffset = 0.0
        self._dash = []
        self.font_px = 12.0
        self.font_peso = 700
        self.textAlign = "center"
        self._pilha = []
        self.path = skia.Path()
        self._fontes = {}
        self.memo = Memo()
        self._gravando = False       # a desenhar uma imagem para a memoria: o que vem dentro desenha-se directo
        self.largura = None          # a largura do holograma: o texto encolhe para caber (a Cascadia e mais larga)

    # -------------------------------------------------------------- estado
    _CAMPOS = ("strokeStyle", "fillStyle", "lineWidth", "lineCap", "lineJoin", "globalAlpha", "shadowBlur", "shadowColor",
               "lineDashOffset", "_dash", "font_px", "font_peso", "textAlign")

    def save(self):
        self._pilha.append(tuple(getattr(self, k) for k in self._CAMPOS))
        self.c.save()

    def restore(self):
        if self._pilha:
            for k, v in zip(self._CAMPOS, self._pilha.pop()):
                setattr(self, k, v)
        self.c.restore()

    def translate(self, x, y):
        self.c.translate(x, y)

    def rotate(self, a):
        self.c.rotate(math.degrees(a))

    def scale(self, x, y=None):
        self.c.scale(x, x if y is None else y)

    def setLineDash(self, arr):
        self._dash = [float(x) for x in arr or []]

    # -------------------------------------------------------------- caminho
    def beginPath(self):
        self.path = skia.Path()

    def moveTo(self, x, y):
        self.path.moveTo(x, y)

    def lineTo(self, x, y):
        if self.path.countPoints() == 0:
            self.path.moveTo(x, y)
        else:
            self.path.lineTo(x, y)

    def quadraticCurveTo(self, cx, cy, x, y):
        if self.path.countPoints() == 0:
            self.path.moveTo(cx, cy)
        self.path.quadTo(cx, cy, x, y)

    def closePath(self):
        self.path.close()

    @staticmethod
    def _arco_em(path, rect, a0, a1, ccw):
        sw = _sweep(a0, a1, ccw)
        ini = math.degrees(a0)
        vazio = path.countPoints() == 0
        if abs(sw) >= TAU - 1e-9:
            meio = 180.0 if sw > 0 else -180.0
            path.arcTo(rect, ini, meio, vazio)
            path.arcTo(rect, ini + meio, meio, False)
        else:
            path.arcTo(rect, ini, math.degrees(sw), vazio)

    def arc(self, x, y, r, a0, a1, ccw=False):
        r = max(0.0, float(r))
        self._arco_em(self.path, skia.Rect.MakeLTRB(x - r, y - r, x + r, y + r), a0, a1, ccw)

    def ellipse(self, x, y, rx, ry, rot, a0, a1, ccw=False):
        tmp = skia.Path()
        self._arco_em(tmp, skia.Rect.MakeLTRB(-rx, -ry, rx, ry), a0, a1, ccw)
        m = skia.Matrix()
        m.setRotate(math.degrees(rot))
        m.postTranslate(x, y)
        modo = skia.Path.AddPathMode.kExtend_AddPathMode if self.path.countPoints() else skia.Path.AddPathMode.kAppend_AddPathMode
        self.path.addPath(tmp, m, modo)

    def rect(self, x, y, w, h):
        self.path.addRect(skia.Rect.MakeXYWH(x, y, w, h))

    def roundRect(self, x, y, w, h, r):
        self.path.addRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r))

    # -------------------------------------------------------------- tinta
    def _tinta(self, estilo, traco: bool, largura=None, alfa=1.0, tracejado=True):
        p = skia.Paint()
        p.setAntiAlias(True)
        if isinstance(estilo, Gradiente):
            p.setShader(estilo.shader())
            p.setAlphaf(max(0.0, min(1.0, self.globalAlpha * alfa)))
        else:
            p.setColor4f(rgba(estilo, max(0.0, min(1.0, self.globalAlpha * alfa))))
        if traco:
            p.setStyle(skia.Paint.kStroke_Style)
            p.setStrokeWidth(float(self.lineWidth if largura is None else largura))
            p.setStrokeCap({"round": skia.Paint.kRound_Cap, "square": skia.Paint.kSquare_Cap}.get(self.lineCap,
                                                                                                  skia.Paint.kButt_Cap))
            p.setStrokeJoin({"round": skia.Paint.kRound_Join, "bevel": skia.Paint.kBevel_Join}.get(self.lineJoin,
                                                                                                    skia.Paint.kMiter_Join))
            if tracejado and self._dash and len(self._dash) >= 2:
                p.setPathEffect(skia.DashPathEffect.Make(self._dash, float(self.lineDashOffset)))
        return p

    def _area_dispositivo(self, path) -> float:
        b = self.c.getTotalMatrix().mapRect(path.computeTightBounds())
        return max(0.0, b.width()) * max(0.0, b.height())

    def _brilho(self, traco: bool):
        """O shadowBlur: desfoque verdadeiro nas formas pequenas, duas faixas largas de pouco alfa nas grandes."""
        b = float(self.shadowBlur or 0.0)
        if b <= 0.5:
            return
        cor = self.shadowColor
        if not traco and self._area_dispositivo(self.path) + (b * 4) ** 2 <= BLUR_REAL_MAX_PX2:
            p = self._tinta(cor, False)
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, b / 2.0))
            self.c.drawPath(self.path, p)
            return
        base = float(self.lineWidth) if traco else 0.0
        for k, al in ((1.5, 0.10), (0.75, 0.20)):
            p = self._tinta(cor, True, largura=base + b * k, alfa=al, tracejado=False)
            self.c.drawPath(self.path, p)

    def _pela_memoria(self, traco: bool, desenhar, chave_extra=None, caixa=None) -> bool:
        """Copia da memoria (True) ou desenha numa imagem nova se a chave ja foi vista antes (True); senao regista a chave
        e devolve False (quem chamou desenha ao vivo)."""
        if self._gravando:
            return False
        m = self.c.getTotalMatrix()
        mk = tuple(m.get(i) for i in range(9))       # a mesma conta da os mesmos floats: sem round (custava 85 mil/s)
        brilho = float(self.shadowBlur or 0.0) > 0.5
        alfa_na_copia = not brilho
        if chave_extra is None:
            try:
                corpo = hash(bytes(self.path.serialize()))
            except Exception:
                return False
            chave = (traco, corpo, _chave_estilo(self.strokeStyle if traco else self.fillStyle),
                     round(float(self.lineWidth), 3) if traco else 0, self.lineCap if traco else "",
                     self.lineJoin if traco else "", tuple(self._dash) if traco else (),
                     round(float(self.lineDashOffset), 2) if (traco and self._dash) else 0,
                     None if alfa_na_copia else round(float(self.globalAlpha), 3),
                     (round(float(self.shadowBlur), 2), self.shadowColor) if brilho else None,
                     0 if traco else int(self.path.getFillType()), mk)
        else:
            alfa_na_copia = True
            chave = (chave_extra, mk)
        img = self.memo.imgs.get(chave)
        if img is None:
            if chave not in self.memo.vistos:
                if len(self.memo.vistos) > Memo.MAX_VISTOS:
                    self.memo.vistos.clear()
                self.memo.vistos.add(chave)
                self.memo.falhas += 1
                return False
            margem = (float(self.lineWidth) / 2.0 if traco else 0.0) + float(self.shadowBlur or 0.0) * 2.0 + 3.0
            b = caixa if caixa is not None else self.path.computeTightBounds()
            b = skia.Rect.MakeLTRB(b.left() - margem, b.top() - margem, b.right() + margem, b.bottom() + margem)
            ir = m.mapRect(b).roundOut()
            if ir.width() <= 0 or ir.height() <= 0 or ir.width() * ir.height() > Memo.MAX_PX:
                return False
            surf = skia.Surface(ir.width(), ir.height())
            c2 = surf.getCanvas()
            c2.clear(0)
            c2.translate(-ir.left(), -ir.top())
            c2.concat(m)
            c_antes, a_antes = self.c, self.globalAlpha
            self.c = c2
            self._gravando = True
            if alfa_na_copia:
                self.globalAlpha = 1.0
            try:
                desenhar()
            finally:
                self.c, self.globalAlpha, self._gravando = c_antes, a_antes, False
            img = (surf.makeImageSnapshot(), ir.left(), ir.top())
            self.memo.guardar(chave, img)
        else:
            self.memo.acertos += 1
        imagem, x, y = img
        self.c.save()
        self.c.resetMatrix()
        p = None
        if alfa_na_copia and self.globalAlpha < 0.999:
            p = skia.Paint()
            p.setAlphaf(max(0.0, min(1.0, float(self.globalAlpha))))
        self.c.drawImage(imagem, x, y, skia.SamplingOptions(), p)
        self.c.restore()
        return True

    def stroke(self):
        def ao_vivo():
            self._brilho(True)
            self.c.drawPath(self.path, self._tinta(self.strokeStyle, True))
        if not self._pela_memoria(True, ao_vivo):
            ao_vivo()

    def fill(self, regra: str = "nonzero"):
        self.path.setFillType(skia.PathFillType.kEvenOdd if regra == "evenodd" else skia.PathFillType.kWinding)

        def ao_vivo():
            self._brilho(False)
            self.c.drawPath(self.path, self._tinta(self.fillStyle, False))
        if not self._pela_memoria(False, ao_vivo):
            ao_vivo()

    def clip(self):
        self.c.clipPath(self.path, skia.ClipOp.kIntersect, True)

    def fillRect(self, x, y, w, h):
        self.c.drawRect(skia.Rect.MakeXYWH(x, y, w, h), self._tinta(self.fillStyle, False))

    def clearRect(self, x, y, w, h):
        p = skia.Paint()
        p.setBlendMode(skia.BlendMode.kClear)
        self.c.drawRect(skia.Rect.MakeXYWH(x, y, w, h), p)

    def createRadialGradient(self, x0, y0, r0, x1, y1, r1):
        return Gradiente("radial", (x0, y0, r0, x1, y1, r1))

    def createLinearGradient(self, x0, y0, x1, y1):
        return Gradiente("linear", (x0, y0, x1, y1))

    # -------------------------------------------------------------- texto
    def _fonte(self, px, peso):
        k = (round(px * 4) / 4.0, peso)
        f = self._fontes.get(k)
        if f is None:
            estilo = skia.FontStyle.Bold() if peso >= 650 else (skia.FontStyle(600, 5, skia.FontStyle.kUpright_Slant)
                                                                if peso >= 550 else skia.FontStyle.Normal())
            tf = None
            for fam in FONTE_MONO:
                tf = skia.Typeface.MakeFromName(fam, estilo)
                if tf is not None and tf.getFamilyName() == fam:
                    break
            f = skia.Font(tf, k[0])
            f.setEdging(skia.Font.Edging.kAntiAlias)
            f.setSubpixel(True)
            self._fontes[k] = f
        return f

    def fillText(self, s, x, y, halo: bool = True):
        s = str(s)
        px = float(self.font_px)
        f = self._fonte(px, self.font_peso)
        w = f.measureText(s)
        if self.largura and w > self.largura - 8 and self.textAlign == "center":
            px = max(6.0, px * (self.largura - 8) / w)           # a Cascadia e mais larga do que a JetBrains da galeria
            f = self._fonte(px, self.font_peso)
            w = f.measureText(s)
        if self.textAlign == "center":
            x -= w / 2.0
        elif self.textAlign in ("right", "end"):
            x -= w
        cor_txt = self.fillStyle

        def ao_vivo():
            if halo:
                hp = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=max(1.5, px * 0.17))
                hp.setStrokeJoin(skia.Paint.kRound_Join)
                hp.setColor4f(skia.Color4f(0.0, 0.03, 0.05, 0.45 * self.globalAlpha))
                self.c.drawString(s, x, y, f, hp)
            self.c.drawString(s, x, y, f, self._tinta(cor_txt, False))
        caixa = skia.Rect.MakeLTRB(x - 2, y - px * 1.05, x + w + 2, y + px * 0.4)
        sombra = self.shadowBlur
        self.shadowBlur = 0.0
        try:
            feito = self._pela_memoria(False, ao_vivo, chave_extra=("txt", s, round(px, 2), self.font_peso, halo,
                                                                     round(x, 2), round(y, 2), _chave_estilo(cor_txt)),
                                       caixa=caixa)
        finally:
            self.shadowBlur = sombra
        if not feito:
            ao_vivo()

    def medir(self, s) -> float:
        return self._fonte(self.font_px, self.font_peso).measureText(str(s))


# ======================================================================== o estado Q (a cota que os hologramas leem)
class Cota:
    """O 'Q' da galeria, com os numeros reais. forca None = ainda sem ritmo (o texto diz '—')."""

    def __init__(self):
        self.sessao = 0.0
        self.semana = 0.0
        self.fable = 0.0
        self.ritmo = 0.0
        self.cabe = 1.0
        self.forca_real = None
        self.agentes = 0
        self.usd = 0.0
        self.reinicioS = None        # segundos ate ao reinicio; None = ainda sem leitura da API (o texto diz "—")
        self.reinicioW = None
        self.chamadas = []           # instantes (s, relogio monotono) das chamadas do ultimo minuto
        self.ultima_chamada = 0.0
        self.modo = 0
        self.sev = 0
        self.t = 0.0

    def forca(self) -> float:
        return float(self.forca_real) if self.forca_real is not None else 0.0


Q = Cota()


def forca():
    return Q.forca()


def n2(x) -> str:
    return "{:.2f}".format(x).replace(".", ",")


def fx() -> str:
    return "—" if Q.forca_real is None else n2(Q.forca_real) + "x"


def cor():
    return MODOS[Q.sev][1]


corA = cor


def glow(ctx: Tela, c, b):
    ctx.shadowColor = c
    ctx.shadowBlur = b


def batida(s) -> bool:
    n = Q.ultima_chamada
    if s.get("ult") != n:
        s["ult"] = n
        return n > 0
    return False


def nucleo(ctx, cx, cy, r, k, a):
    g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
    g.addColorStop(0, "rgba(255,255,255,.95)")
    g.addColorStop(.4, hexa(k, .75))
    g.addColorStop(1, hexa(k, 0))
    ctx.fillStyle = g
    ctx.globalAlpha = max(0.0, min(1.0, a))
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, TAU)
    ctx.fill()
    ctx.globalAlpha = 1


def flash(s, dt, v=2.2):
    if batida(s):
        s["fl"] = 1.0
    s["fl"] = max(0.0, s.get("fl", 0.0) - dt * v)
    return s["fl"]


def _clear(ctx, w, h):
    ctx.clearRect(0, 0, w, h)


RAD_S_POR_FORCA = 2.6
RAD_S_PARADO = 0.12              # sem forca (ou a medir): devagar, so para se ver que esta vivo
RAD_S_MAX = 10.0


def velocidade_do_gasto(forca) -> float:
    """PURA (04/10 21:2x). Rad/s das pecas que rodam = a FORCA (gasto real / o que cabe ate ao reinicio). Antes era o %/h
    cru da janela que manda: na semana sao ~2%/h e a helice quase parava com a forca a 1,4x."""
    try:
        v = float(forca)
    except (TypeError, ValueError):
        v = 0.0
    if v != v or v < 0:
        v = 0.0
    return min(RAD_S_MAX, RAD_S_PARADO + v * RAD_S_POR_FORCA)


def barras_uso(usado_pct, n: int) -> int:
    """PURA (04/10). Quantas das n barrinhas acendem: o USADO, a encher ate 100%."""
    try:
        v = float(usado_pct or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v != v:
        v = 0.0
    return int(round(max(0.0, min(100.0, v)) / 100.0 * n))


def barras_tempo(restante_s, total_s: float, n: int) -> int:
    """PURA (04/10). Quantas das n barrinhas do TEMPO acendem: cheia com a janela inteira por gastar (5 h na sessao, 7 dias na
    semana), metade a meio, quase vazia nos ultimos minutos, 0 sem leitura. Arredonda para CIMA: enquanto falta tempo, ha luz."""
    try:
        frac = max(0.0, min(1.0, float(restante_s) / float(total_s)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0
    if frac != frac:
        return 0
    return int(math.ceil(frac * n - 1e-9))


COR_TEMPO_SESSAO = "#ffe7a3"
COR_TEMPO_SEMANA = "#bfefff"     # o anel fino de fora (reinicio da semana) e a sigla RM


def _seg_cheio(ctx, cx, cy, r, n, usado_pct, c_on, c_off, lw, gap, c_fim=None, fim_desde=.85):
    """04/10: as barrinhas enchem a medida que a janela se aproxima dos 100%; acesos = USADO (nao o livre);
    os segmentos depois de `fim_desde` (85%) acendem em `c_fim` - o fim da janela ve-se de longe."""
    on = barras_uso(usado_pct, n)
    ctx.lineWidth = lw
    for i in range(n):
        a0 = -math.pi / 2 + i / n * TAU + gap / 2
        ctx.strokeStyle = (c_fim if (c_fim and (i + 1) / n > fim_desde) else c_on) if i < on else c_off
        ctx.beginPath()
        ctx.arc(cx, cy, r, a0, a0 + TAU / n - gap)
        ctx.stroke()


def _seg_tempo(ctx, cx, cy, r, n, restante_s, total_s, c_on, c_off, lw, gap):
    """04/10: o anel duplo do horario (sessao/semana) funciona ao contrario do uso - cheio com 5 h pela frente, a
    metade com 2 h e meia, quase vazio nos ultimos 10 min; acesos = TEMPO QUE FALTA ate ao reinicio; esvazia a medida que o tempo passa."""
    on = barras_tempo(restante_s, total_s, n)
    ctx.lineWidth = lw
    for i in range(n):
        a0 = -math.pi / 2 + i / n * TAU + gap / 2
        ctx.strokeStyle = c_on if i < on else c_off
        ctx.beginPath()
        ctx.arc(cx, cy, r, a0, a0 + TAU / n - gap)
        ctx.stroke()


def mark2(ctx, w, h, t, dt, s):
    """Reator Mark II, o holograma do medidor. Por fora o anel DUPLO DO TEMPO (dentro: sessao de 5 h; fora: semana de 7 dias),
    a esvaziar ate ao reinicio; depois os aneis do USO a encher ate 100% (semana, sessao, Fable); no centro as pas (uma por
    agente em voo), o triangulo e o nucleo. Para ficar leve: sem desfoque (glow) nas
    pecas que rodam - o brilho e um traco largo translucido por baixo do traco fino, que nao custa quase nada."""
    cx, cy, R, f, k = w / 2, h / 2, min(w, h) * .45, forca(), corA()
    fl = flash(s, dt)
    # e a FORCA (21:2x: o %/h cru da semana deixava-a quase parada a 1,4x); muda suave (~0,5 s), nunca aos saltos
    alvo = velocidade_do_gasto(Q.forca_real)
    s["vrot"] = s.get("vrot", alvo) + (alvo - s.get("vrot", alvo)) * min(1.0, dt * 2.0)
    s["rot"] = s.get("rot", 0.0) + dt * s["vrot"]
    _clear(ctx, w, h)
    # os 5 aneis (tempo por fora, uso por dentro) so mudam quando um numero muda: UMA imagem guardada, copiada em cada
    # quadro (146 barrinhas desenhadas uma a uma custavam ~4 ms por quadro)
    chave = ("m9-aneis", round(w, 1), round(R, 2), barras_tempo(Q.reinicioW, 7 * 86400, 40),
             barras_tempo(Q.reinicioS, 5 * 3600, 40), barras_uso(Q.semana, 30), barras_uso(Q.sessao, 22), barras_uso(Q.fable, 14))

    def _aneis():
        _seg_tempo(ctx, cx, cy, R, 40, Q.reinicioW, 7 * 86400, COR_TEMPO_SEMANA, "rgba(191,239,255,.10)", 2.4, .05)
        _seg_tempo(ctx, cx, cy, R - 5, 40, Q.reinicioS, 5 * 3600, COR_TEMPO_SESSAO, "rgba(255,231,163,.10)", 2.4, .05)
        _seg_cheio(ctx, cx, cy, R * .82, 30, Q.semana, C["cy"], hexa(C["cy"], .1), 7, .05, C["mau"])
        _seg_cheio(ctx, cx, cy, R * .67, 22, Q.sessao, C["ouro"], hexa(C["ouro"], .1), 5, .06, C["mau"])
        _seg_cheio(ctx, cx, cy, R * .55, 14, Q.fable, C["vi"], hexa(C["vi"], .12), 3.5, .09, C["mau"])
    ctx.shadowBlur, ctx.globalAlpha = 0.0, 1.0
    caixa = skia.Rect.MakeLTRB(cx - R - 4, cy - R - 4, cx + R + 4, cy + R + 4)
    if not ctx._pela_memoria(False, _aneis, chave_extra=chave, caixa=caixa):
        _aneis()
    # as pas (uma por agente em voo, minimo 3)
    np_ = 3 + Q.agentes
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(s["rot"])
    ctx.strokeStyle = k
    ctx.lineWidth = 2.2
    ctx.globalAlpha = .75
    for _i in range(np_):
        ctx.rotate(TAU / np_)
        ctx.beginPath()
        ctx.moveTo(R * .30, 0)
        ctx.lineTo(R * .46, 0)
        ctx.stroke()
    ctx.restore()
    ctx.globalAlpha = 1
    # o triangulo do Mark II: halo = traco largo translucido (sem desfoque) + traco fino branco
    r = R * .25 * (1 + .1 * fl)
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(-s["rot"] * .35)
    ctx.lineJoin = "round"
    for largura, cor_, alfa in ((7 + 5 * fl, k, .28 + .3 * fl), (2.6, "#ffffff", 1.0)):
        ctx.globalAlpha = alfa
        ctx.strokeStyle = cor_
        ctx.lineWidth = largura
        ctx.beginPath()
        for i in range(3):
            a = -math.pi / 2 + i * TAU / 3
            (ctx.lineTo if i else ctx.moveTo)(math.cos(a) * r, math.sin(a) * r)
        ctx.closePath()
        ctx.stroke()
    ctx.restore()
    ctx.globalAlpha = 1
    ctx.shadowBlur = 0
    nucleo(ctx, cx, cy, R * .2, k, .6 + .3 * math.sin(t * (2 + f * 2)) ** 2 + .3 * fl)
    # numero na cor da forca (a mesma da sigla FC no painel)
    ctx.font_px, ctx.font_peso, ctx.textAlign = 12, 800, "center"
    tx_ = fx()
    lw_ = ctx.medir(tx_) + 8
    ctx.globalAlpha = 1
    ctx.fillStyle = "rgba(4,9,14,.96)"
    ctx.beginPath()
    ctx.roundRect(cx - lw_ / 2, cy - 8.5, lw_, 16, 5)
    ctx.fill()
    ctx.strokeStyle = k
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = k
    ctx.fillText(tx_, cx, cy + 4, halo=False)


# o unico holograma do medidor publico: o Reator Mark II (o id "m9" fica - e o mesmo do medidor original)
HOLOGRAMAS = {"m9": (mark2, "Reator Mark II")}
ORDEM = ["m9"]


# ======================================================================== o painel de numeros e a pilula
# velocidade sao da CONTA (a API, iguais nos dois PCs); a linha PC (MD SS AG TR), as pas e o clarao do holograma sao DESTE PC
LEGENDA = (("SE", "sessão de 5 h usada - conta (anel dourado)"), ("SM", "semana usada - conta (anel ciano)"),
           ("FB", "Fable usado - conta (anel violeta)"),
           ("FÇ", "força da conta: gasto real ÷ o que cabe até ao reinício (velocidade e cor do holograma)"),
           ("RT", "ritmo %/h da conta: real / o que cabe"),
           ("PC", "a linha do que é só DESTE PC - não entra na força, na cor nem na velocidade"),
           ("SS", "este PC: sessões do Claude Code abertas"),
           ("AG", "este PC: subagentes em voo - também as pás do holograma"),
           ("CM", "este PC: chamadas ao modelo no último minuto"),
           ("RS", "reinício da sessão - conta (anel fino de dentro)"), ("RM", "reinício da semana - conta (anel fino de fora)"),
           ("LT", "a última leitura da conta tem mais de 10 min"), ("*", "o clarão a cada chamada é deste PC"))
LINHAS_PAINEL = 5
ALTURA_LINHA = 14.0


def painel(ctx: Tela, w: float, y0: float, r: dict):
    """The panel under the hologram: 5-hour, week and per-model use, force, rate and what fits, this machine's sessions,
    agents and calls per minute, the two resets - and the age of the reading when it is old. Each line shrinks to fit."""
    import dados as D
    k = cor()
    pc = r.get("este_pc") or {}
    nm = D.nivel_do_modo(pc.get("modo"))
    cor_modo = MODOS[nm][1] if nm is not None else CINZA
    # (a legenda esta no menu do botao direito: LEGENDA) e numeros; o modo e a cor do ponto.
    def _usd(v):
        try:
            return "{:.2f}".format(float(v)).replace(".", ",")
        except (TypeError, ValueError):
            return "-"

    def _rit(a, b):
        try:
            return "{:.1f}/{:.1f}".format(float(a), float(b)).replace(".", ",")
        except (TypeError, ValueError):
            return "-"
    linhas = [
        [("SE ", C["ouro"]), (D.fmt_pct(r.get("sessao")), C["ouro"]), ("  SM ", C["cy"]), (D.fmt_pct(r.get("semana")), C["cy"]),
         ("  FB ", C["vi"]), (D.fmt_pct(r.get("fable")), C["vi"])],
        [("FÇ ", k), (D.fmt_forca(r.get("forca")), k), ("  RT ", C["dim"]), (_rit(r.get("ritmo"), r.get("cabe")), C["tx"])],
        [("PC ", C["am"]), ("SS ", C["dim"]), (str(pc.get("sessoes", 0)), C["tx"]),
         ("  AG ", C["dim"]), (str(pc.get("agentes", 0)), C["tx"]), ("  CM ", C["dim"]), (str(pc.get("chamadas_min", 0)), C["tx"])],
        [("RS ", COR_TEMPO_SESSAO), (D.fmt_dur(r.get("reinicio_sessao_s")), COR_TEMPO_SESSAO), ("  RM ", COR_TEMPO_SEMANA),
         (D.fmt_dur(r.get("reinicio_semana_s")), COR_TEMPO_SEMANA)],
    ]
    lt = r.get("leitura_ts")
    velha = (not lt) or ((r.get("t") or 0) - lt > 10 * 60) or bool(pc.get("erro_api"))
    rodape = ("LT " + (D.fmt_idade((r.get("t") or 0) - lt) if lt else "-")) if velha else ""
    # a memoria de desenho com a chave = o proprio texto; muda no maximo uma vez por minuto ou quando um numero muda.
    chave = ("painel", w, y0, k, cor_modo, tuple(tuple(p) for p in linhas), rodape)
    caixa = skia.Rect.MakeLTRB(0, y0 - 12, w, y0 + ALTURA_LINHA * len(linhas) + 6)
    ctx.shadowBlur, ctx.globalAlpha = 0.0, 1.0
    if not ctx._pela_memoria(False, lambda: _painel_linhas(ctx, w, y0, linhas, rodape), chave_extra=chave, caixa=caixa):
        _painel_linhas(ctx, w, y0, linhas, rodape)


def _painel_linhas(ctx, w, y0, linhas, rodape):
    # fica transparente
    alto = ALTURA_LINHA * len(linhas) + (12 if rodape else 0)
    ctx.fillStyle = "rgba(4,9,14,.82)"
    ctx.beginPath()
    ctx.roundRect(3, y0 - 12.5, w - 6, alto + 5, 7)
    ctx.fill()
    ctx.strokeStyle = "rgba(60,90,108,.55)"
    ctx.lineWidth = 1
    ctx.stroke()
    y = y0
    for partes in linhas:
        px = 10.5
        ctx.font_peso = 600
        ctx.font_px = px
        total = sum(ctx.medir(s_) for s_, _c in partes if s_)
        if total > w - 6:
            px = max(7.0, px * (w - 6) / total)
            ctx.font_px = px
            total = sum(ctx.medir(s_) for s_, _c in partes if s_)
        x = (w - total) / 2.0
        ctx.textAlign = "left"
        for s_, c in partes:
            if not s_:
                continue
            ctx.fillStyle = c or C["tx"]
            ctx.fillText(s_, x, y)
            x += ctx.medir(s_)
        y += ALTURA_LINHA
    if rodape:                                                 # so quando a leitura da cota esta velha (> 10 min)
        ctx.font_px = 9
        ctx.font_peso = 600
        ctx.textAlign = "center"
        ctx.fillStyle = C["am"]
        ctx.fillText(rodape, w / 2.0, y + 1)


def pilula(ctx: Tela, w: float, h: float, r: dict, hover: bool):
    """Minimizado: a pilula so com numeros (a da galeria), e o botao de abrir."""
    import dados as D
    k = cor()
    ctx.clearRect(0, 0, w, h)
    ctx.fillStyle = "rgba(4,8,12,.84)"
    ctx.beginPath()
    ctx.roundRect(1, 1, w - 2, h - 2, (h - 2) / 2)
    ctx.fill()
    ctx.strokeStyle = hexa(k, .55) if Q.sev else "rgba(42,64,80,1)"
    ctx.lineWidth = 1
    ctx.stroke()
    glow(ctx, k, 8)
    ctx.fillStyle = k
    ctx.beginPath()
    ctx.arc(16, h / 2, 4, 0, TAU)
    ctx.fill()
    ctx.shadowBlur = 0
    pc = r.get("este_pc") or {}
    partes = [("SE ", C["dim"]), (D.fmt_pct(r.get("sessao")), C["ouro"]), ("  SM ", C["dim"]), (D.fmt_pct(r.get("semana")), C["cy"]),
              ("  FÇ ", C["dim"]), (D.fmt_forca(r.get("forca")), k), ("  RS ", C["dim"]), (D.fmt_dur(r.get("reinicio_sessao_s")), C["tx"]),
              ("  PC ", C["am"]), ("AG ", C["dim"]), (str(pc.get("agentes", 0)), C["tx"])]
    ctx.font_px, ctx.font_peso, ctx.textAlign = 11.5, 700, "left"
    total = sum(ctx.medir(s_) for s_, _c in partes)
    livre = w - 28 - 34
    if total > livre:
        ctx.font_px = max(8.0, 11.5 * livre / total)
    x = 28
    for s_, c in partes:
        ctx.fillStyle = c or C["tx"]
        ctx.fillText(s_, x, h / 2 + 4, halo=False)
        x += ctx.medir(s_)
    botao(ctx, w - 18, h / 2, "abrir", hover)


def botao(ctx: Tela, x: float, y: float, tipo: str, hover: bool):
    """Os botoes redondos (minimizar, abrir, menu). Com o rato por cima ficam mais vivos."""
    ctx.fillStyle = "rgba(10,22,30,.92)" if hover else "rgba(10,22,30,.7)"
    ctx.beginPath()
    ctx.arc(x, y, 9, 0, TAU)
    ctx.fill()
    ctx.strokeStyle = "rgba(127,154,165,.9)" if hover else "rgba(127,154,165,.55)"
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.strokeStyle = "#e6f3f7"
    ctx.lineWidth = 1.6
    ctx.beginPath()
    if tipo == "minimizar":
        ctx.moveTo(x - 4, y + 2)
        ctx.lineTo(x + 4, y + 2)
    elif tipo == "abrir":
        ctx.rect(x - 4, y - 4, 8, 8)
    elif tipo == "numeros":
        for dy in (-3, 0, 3):
            ctx.moveTo(x - 4, y + dy)
            ctx.lineTo(x + 4, y + dy)
    else:                                                      # menu: tres pontos
        for dx in (-3.5, 0, 3.5):
            ctx.moveTo(x + dx, y)
            ctx.lineTo(x + dx + 0.01, y)
        ctx.lineWidth = 2.4
    ctx.stroke()
