"""Generador de imágenes para compartir (fase F).

Crea una tarjeta cuadrada con el aspecto de la app y, SIEMPRE, el **logo translúcido pequeño en la
esquina inferior derecha** como marca de agua. La imagen es lo que se publica en redes; al pulsar la
publicación, el enlace lleva al perfil público del autor (lo gestiona el frontend con `?user=<id>`).
"""
import io
import os

# Pillow es OPCIONAL: si no está instalado, la app sigue funcionando; solo se desactiva la
# generación de imágenes de compartir (los endpoints devuelven 503 y las meta OG omiten la imagen).
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:  # noqa: BLE001
    Image = ImageDraw = ImageFont = None
    PIL_OK = False

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGO = os.path.join(_ROOT, "frontend", "media", "logo.png")

# Colores de la app (coinciden con :root de styles.css)
_BG = (10, 10, 31)
_BG2 = (22, 19, 58)
_TEXT = (236, 233, 255)
_MUTED = (148, 141, 196)
_GOLD = (245, 184, 42)
_CYAN = (63, 225, 255)
_BORDER = (58, 52, 112)

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_FONT_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(size: int, bold: bool = False):
    for p in (_FONT_BOLD if bold else _FONT_CANDIDATES):
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def render_card(eyebrow: str = "", title: str = "", subtitle: str = "",
                stats: list = None, accent=_CYAN) -> bytes:
    """Devuelve un PNG (1080x1080) con la tarjeta y la marca de agua del logo abajo-derecha, o None
    si Pillow no está instalado. `stats` = lista de (etiqueta, valor) que se pintan como pastillas."""
    if not PIL_OK:
        return None
    W = H = 1080
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    # fondo con degradado vertical sutil
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(_BG[0] + (_BG2[0] - _BG[0]) * t),
                                       int(_BG[1] + (_BG2[1] - _BG[1]) * t),
                                       int(_BG[2] + (_BG2[2] - _BG[2]) * t)))
    m = 64
    d.rounded_rectangle([m, m, W - m, H - m], radius=40, outline=_BORDER, width=3)
    x = m + 56
    # eyebrow
    d.text((x, m + 70), (eyebrow or "BRAWL SENSEI").upper(), font=_font(30, True), fill=accent)
    # título (envuelto)
    ft = _font(76, True)
    y = m + 130
    for ln in _wrap(d, title, ft, W - 2 * x)[:3]:
        d.text((x, y), ln, font=ft, fill=_TEXT)
        y += 92
    # subtítulo
    if subtitle:
        fs = _font(38)
        y += 8
        for ln in _wrap(d, subtitle, fs, W - 2 * x)[:2]:
            d.text((x, y), ln, font=fs, fill=_MUTED)
            y += 50
    # estadísticas como pastillas
    if stats:
        y = max(y + 40, 560)
        fl, fv = _font(30), _font(52, True)
        for label, value in stats[:4]:
            d.rounded_rectangle([x, y, W - m - 56, y + 108], radius=20, fill=(20, 18, 44), outline=_BORDER, width=2)
            d.text((x + 26, y + 20), str(label).upper(), font=fl, fill=_MUTED)
            d.text((x + 26, y + 50), str(value), font=fv, fill=accent)
            y += 128
    # marca de agua: logo translúcido pequeño abajo-derecha
    _paste_watermark(img, W, H, m)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _paste_watermark(img, W, H, m):
    try:
        logo = Image.open(_LOGO).convert("RGBA")
    except Exception:  # noqa: BLE001
        return
    lw = 150
    lh = max(1, int(logo.height * lw / logo.width))
    logo = logo.resize((lw, lh), Image.LANCZOS)
    # esquinas redondeadas + translúcido (~42%) para que quede como marca de agua, no como cuadro
    mask = Image.new("L", (lw, lh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, lw - 1, lh - 1], radius=22, fill=255)
    logo.putalpha(mask.point(lambda v: int(v * 0.42)))
    base = img.convert("RGBA")
    base.alpha_composite(logo, (W - m - lw, H - m - lh))
    img.paste(base.convert("RGB"), (0, 0))
