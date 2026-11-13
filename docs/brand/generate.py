"""Generate the INT logo SVGs (90s chrome, like the ShaderText wordmark).

Cinzel is the font the WebGL title (web/src/components/shader-text.tsx) loads; the
glyphs are converted to paths, so the SVGs need no font at runtime.

Then run export.py to refresh the PNG / ICO files the app serves.

Usage (from docs/brand/):
    curl -sSLo /tmp/Cinzel.ttf https://github.com/google/fonts/raw/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf
    uv run --with fonttools python generate.py /tmp/Cinzel.ttf .
"""

import math
import random
import sys
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONT_PATH, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

font = instantiateVariableFont(TTFont(FONT_PATH), {"wght": 900})
glyphset = font.getGlyphSet()
cmap = font.getBestCmap()
hmtx = font["hmtx"]
UPM = 1000
CAP = 700
SKEW = math.tan(math.radians(13))  # browser synthetic oblique, like the canvas title


class SvgPen(BasePen):
    """Path pen that maps font units to SVG units with scale, offset and oblique skew."""

    def __init__(self, gs, ox, oy, s):
        super().__init__(gs)
        self.ox, self.oy, self.s = ox, oy, s
        self.d = []

    def _p(self, pt):
        x, y = pt
        return f"{self.ox + self.s * (x + y * SKEW):.2f} {self.oy - self.s * y:.2f}"

    def _moveTo(self, p):
        self.d.append("M" + self._p(p))

    def _lineTo(self, p):
        self.d.append("L" + self._p(p))

    def _curveToOne(self, a, b, c):
        self.d.append("C" + " ".join(self._p(q) for q in (a, b, c)))

    def _qCurveToOne(self, a, b):
        self.d.append("Q" + " ".join(self._p(q) for q in (a, b)))

    def _closePath(self):
        self.d.append("Z")


def text_width(text, tracking=0):
    return sum(hmtx[cmap[ord(c)]][0] for c in text) + tracking * (len(text) - 1)


def text_path(text, x, baseline, size, tracking=0):
    """Outline of `text` with its left edge at x and cap baseline at `baseline`."""
    s = size / UPM
    d = []
    cx = 0
    for ch in text:
        g = cmap[ord(ch)]
        pen = SvgPen(glyphset, x + cx * s, baseline, s)
        glyphset[g].draw(pen)
        d.extend(pen.d)
        cx += hmtx[g][0] + tracking
    return "".join(d)


def ink_bounds(text, size, tracking=0):
    """Horizontal ink extent (left, right) in font-size units, skew included."""
    s = size / UPM
    xs = []
    cx = 0
    for ch in text:
        g = cmap[ord(ch)]
        bp = BoundsPen(glyphset)
        glyphset[g].draw(bp)
        if bp.bounds:
            x0, y0, x1, y1 = bp.bounds
            xs += [cx + x0 + y0 * SKEW, cx + x1 + y1 * SKEW]
        cx += hmtx[g][0] + tracking
    return min(xs) * s, max(xs) * s


# ---------------------------------------------------------------- palettes
# Face: the shader's sky / horizon / ground environment map as a vertical ramp,
# tinted with the INT segment colour (magenta / violet).
MAGENTA = [
    (0.00, "#7b3fe0"),
    (0.22, "#e3a6ff"),
    (0.42, "#fbe6ff"),
    (0.50, "#ffffff"),
    (0.515, "#2a0f3d"),
    (0.58, "#5b1f70"),
    (0.76, "#d050d8"),
    (1.00, "#4a1f86"),
]
SIDE_MAGENTA = ("#2a1446", "#8e5cc6")  # extrusion back -> front (shader sideGrad)


def lerp_hex(a, b, t):
    pa = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def grad(gid, stops, y0, y1):
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return (
        f'<linearGradient id="{gid}" x1="0" y1="{y0:.1f}" x2="0" y2="{y1:.1f}" '
        f'gradientUnits="userSpaceOnUse">{s}</linearGradient>'
    )


GLINT_DEFS = [
    # Rays share one radial ramp; on a thin diamond the bounding box stretches it,
    # so each ray fades from a hot core to nothing at its tip (the shader's 1/x falloff).
    '<radialGradient id="glint-ray"><stop offset="0" stop-color="#fff"/>'
    '<stop offset="0.08" stop-color="#fff" stop-opacity="0.95"/>'
    '<stop offset="0.35" stop-color="#fbeaff" stop-opacity="0.55"/>'
    '<stop offset="1" stop-color="#e9d5ff" stop-opacity="0"/></radialGradient>',
    '<radialGradient id="glint-halo"><stop offset="0" stop-color="#fff" stop-opacity="0.95"/>'
    '<stop offset="0.12" stop-color="#fdf4ff" stop-opacity="0.6"/>'
    '<stop offset="0.4" stop-color="#e879f9" stop-opacity="0.18"/>'
    '<stop offset="1" stop-color="#a855f7" stop-opacity="0"/></radialGradient>',
    '<radialGradient id="glint-streak"><stop offset="0" stop-color="#f5d0fe" stop-opacity="0.7"/>'
    '<stop offset="1" stop-color="#c084fc" stop-opacity="0"/></radialGradient>',
    '<filter id="glint-soft" x="-50%" y="-50%" width="200%" height="200%">'
    '<feGaussianBlur stdDeviation="0.45"/></filter>',
]


def _ray(cx, cy, length, width, angle, fill="url(#glint-ray)", opacity=1.0):
    return (
        f'<path d="M{-length:.2f} 0 L0 {-width:.2f} L{length:.2f} 0 L0 {width:.2f}Z" '
        f'fill="{fill}" opacity="{opacity}" '
        f'transform="translate({cx} {cy}) rotate({angle})"/>'
    )


def sparkle(cx, cy, r, glow=True, opacity=1.0):
    """Specular glint: hot core, long thin cross rays, short diagonals, lavender halo
    and a faint anamorphic streak. Blended with `screen` so it only ever adds light."""
    out = f'<g opacity="{opacity}" style="mix-blend-mode:screen" filter="url(#glint-soft)">'
    if glow:
        out += f'<circle cx="{cx}" cy="{cy}" r="{r * 0.75:.1f}" fill="url(#glint-halo)"/>'
        out += _ray(cx, cy, r * 3.2, r * 0.03, 0, "url(#glint-streak)", 0.7)
    out += _ray(cx, cy, r * 1.5, r * 0.05, 0)
    out += _ray(cx, cy, r * 1.15, r * 0.045, 90)
    out += _ray(cx, cy, r * 0.5, r * 0.04, 45, opacity=0.8)
    out += _ray(cx, cy, r * 0.5, r * 0.04, -45, opacity=0.8)
    out += f'<circle cx="{cx}" cy="{cy}" r="{r * 0.16:.2f}" fill="url(#glint-halo)"/>'
    out += f'<circle cx="{cx}" cy="{cy}" r="{r * 0.055:.2f}" fill="#fff"/>'
    return out + "</g>"


def chrome_text(uid, segments, x, baseline, size, *, depth, steps, tracking=0,
                sweep=True, rim=None, shadow=True):
    """Extruded chrome text. segments: list of (text, face_stops, side_colors)."""
    s = size / UPM
    top = baseline - CAP * s
    defs, body = [], []
    rim = rim if rim is not None else max(1.0, size * 0.011)
    dx, dy = depth * 0.875, depth

    # Lay out segments
    seg_paths = []
    cx = x
    for i, (txt, stops, side) in enumerate(segments):
        d = text_path(txt, cx, baseline, size, tracking)
        seg_paths.append((d, stops, side, i))
        cx += (text_width(txt, tracking) + tracking) * s
    all_d = "".join(p[0] for p in seg_paths)
    defs.append(f'<path id="{uid}-all" d="{all_d}"/>')
    for d, stops, side, i in seg_paths:
        defs.append(f'<path id="{uid}-p{i}" d="{d}"/>')

    for d, stops, side, i in seg_paths:
        defs.append(grad(f"{uid}-face{i}", stops, top, baseline))
    defs.append(
        f'<linearGradient id="{uid}-rim" x1="0" y1="{top:.1f}" x2="0" y2="{baseline:.1f}" '
        f'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#ffffff"/>'
        f'<stop offset="0.45" stop-color="#d9dcf5"/><stop offset="1" stop-color="#4b4f72"/>'
        f"</linearGradient>"
    )

    if shadow:
        body.append(
            f'<use href="#{uid}-all" fill="#000" fill-opacity="0.7" filter="url(#{uid}-blur)" '
            f'transform="translate({dx + depth * 0.35:.1f} {dy + depth * 0.45:.1f})"/>'
        )
        defs.append(
            f'<filter id="{uid}-blur" x="-20%" y="-20%" width="140%" height="140%">'
            f'<feGaussianBlur stdDeviation="{depth * 0.55:.1f}"/></filter>'
        )

    # Extrusion, back to front
    for k in range(steps, 0, -1):
        t = k / steps
        for d, stops, side, i in seg_paths:
            col = lerp_hex(side[0], side[1], 1 - t)
            body.append(
                f'<use href="#{uid}-p{i}" fill="{col}" transform="translate({dx * t:.2f} {dy * t:.2f})"/>'
            )

    # Bevel rim, then the chrome face over its inner half
    body.append(
        f'<use href="#{uid}-all" fill="none" stroke="url(#{uid}-rim)" '
        f'stroke-width="{rim * 2:.2f}" stroke-linejoin="round"/>'
    )
    for d, stops, side, i in seg_paths:
        body.append(f'<use href="#{uid}-p{i}" fill="url(#{uid}-face{i})"/>')

    if sweep:
        w = x + text_width("".join(sg[0] for sg in segments), tracking) * s
        defs.append(f'<clipPath id="{uid}-clip"><use href="#{uid}-all"/></clipPath>')
        defs.append(
            f'<linearGradient id="{uid}-sweep" x1="{x:.0f}" y1="{top:.0f}" x2="{w:.0f}" '
            f'y2="{baseline:.0f}" gradientUnits="userSpaceOnUse">'
            '<stop offset="0.30" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset="0.40" stop-color="#fff" stop-opacity="0.55"/>'
            '<stop offset="0.43" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset="0.47" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset="0.49" stop-color="#fff" stop-opacity="0.30"/>'
            '<stop offset="0.51" stop-color="#fff" stop-opacity="0"/>'
            "</linearGradient>"
        )
        body.append(
            f'<rect x="{x - size:.0f}" y="{top - size:.0f}" width="{w - x + 2 * size:.0f}" '
            f'height="{3 * size:.0f}" fill="url(#{uid}-sweep)" clip-path="url(#{uid}-clip)"/>'
        )
    return defs, body


def svg(size, defs, body, comment, w=None, h=None):
    w, h = w or size, h or size
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
        f'height="{h}" role="img" aria-label="INTellect">\n'
        f"  <!-- {comment} -->\n"
        f"  <defs>{''.join(GLINT_DEFS + defs)}</defs>\n  " + "\n  ".join(body) + "\n</svg>\n"
    )


def centered_x(text, size, area_w, depth, tracking=0):
    left, right = ink_bounds(text, size, tracking)
    ink_w = right - left + depth * 0.875
    return (area_w - ink_w) / 2 - left


# ---------------------------------------------------------------- INT on the nebula
def _frame(S, rx):
    return [f'<clipPath id="frame"><rect width="{S}" height="{S}" rx="{rx}"/></clipPath>']


def _grain(S):
    """Film grain like the login page (.login-grain), plus a corner vignette."""
    defs = [
        '<filter id="grain" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3" seed="3" '
        'stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>',
        f'<radialGradient id="vignette" cx="{S / 2}" cy="{S * 0.45}" r="{S * 0.75}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0.55" stop-color="#000" stop-opacity="0"/>'
        '<stop offset="1" stop-color="#000" stop-opacity="0.65"/></radialGradient>',
    ]
    body = [
        f'<rect width="{S}" height="{S}" fill="url(#vignette)"/>',
        f'<rect width="{S}" height="{S}" filter="url(#grain)" opacity="0.07" '
        'style="mix-blend-mode:overlay"/>',
    ]
    return defs, body


def _edge(S, rx):
    return (
        f'<rect x="1" y="1" width="{S - 2}" height="{S - 2}" rx="{rx - 1}" fill="none" '
        f'stroke="url(#edge)" stroke-width="2"/>'
    ), (
        f'<linearGradient id="edge" x1="0" y1="0" x2="0" y2="{S}" gradientUnits="userSpaceOnUse">'
        '<stop offset="0" stop-color="#f5d0fe" stop-opacity="0.35"/>'
        '<stop offset="0.5" stop-color="#fff" stop-opacity="0.06"/>'
        '<stop offset="1" stop-color="#a855f7" stop-opacity="0.25"/></linearGradient>'
    )


def _stars(S, n, seed, avoid=None):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        x, y = rnd.uniform(8, S - 8), rnd.uniform(8, S - 8)
        if avoid and avoid[0] < x < avoid[2] and avoid[1] < y < avoid[3]:
            continue
        r = rnd.choice((0.5, 0.6, 0.7, 0.8, 1.0, 1.3))
        o = rnd.uniform(0.25, 0.9)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#fff" fill-opacity="{o:.2f}"/>')
    return "<g>" + "".join(out) + "</g>"


def _cloud(fid, seed, freq, rgb, gain, bias):
    r, g, b = rgb
    return (
        f'<filter id="{fid}" x="0" y="0" width="100%" height="100%">'
        f'<feTurbulence type="fractalNoise" baseFrequency="{freq}" numOctaves="5" seed="{seed}"/>'
        f'<feColorMatrix type="matrix" values="0 0 0 0 {r}  0 0 0 0 {g}  0 0 0 0 {b}  '
        f'{gain} 0 0 0 {bias}"/></filter>'
    )


def bg_nebula(S, rx):
    """CD-ROM intro space: violet/magenta nebula, starfield, spotlight behind the text."""
    defs = _frame(S, rx) + [
        f'<radialGradient id="neb-base" cx="{S / 2}" cy="{S * 0.46}" r="{S * 0.72}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2b0f4d"/>'
        '<stop offset="0.5" stop-color="#12072a"/><stop offset="1" stop-color="#040208"/>'
        "</radialGradient>",
        _cloud("neb-a", 11, 0.0055, (0.85, 0.22, 0.95), 2.6, -1.15),
        _cloud("neb-b", 29, 0.008, (0.30, 0.35, 1.0), 2.4, -1.1),
        f'<radialGradient id="neb-mask-g" cx="{S / 2}" cy="{S / 2}" r="{S * 0.6}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0.2" stop-color="#fff"/>'
        '<stop offset="1" stop-color="#fff" stop-opacity="0.15"/></radialGradient>',
        f'<mask id="neb-mask"><rect width="{S}" height="{S}" fill="url(#neb-mask-g)"/></mask>',
        f'<radialGradient id="spot" cx="{S / 2}" cy="{S * 0.47}" r="{S * 0.42}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#f0abfc" stop-opacity="0.38"/>'
        '<stop offset="0.45" stop-color="#c026d3" stop-opacity="0.14"/>'
        '<stop offset="1" stop-color="#7c3aed" stop-opacity="0"/></radialGradient>',
    ]
    body = [
        f'<rect width="{S}" height="{S}" fill="url(#neb-base)"/>',
        f'<g mask="url(#neb-mask)"><rect width="{S}" height="{S}" filter="url(#neb-b)" opacity="0.55"/>'
        f'<rect width="{S}" height="{S}" filter="url(#neb-a)" opacity="0.75" '
        'style="mix-blend-mode:screen"/></g>',
        _stars(S, 90, 5),
        sparkle(88, 96, 14, opacity=0.7) + sparkle(430, 420, 11, opacity=0.6),
        f'<rect width="{S}" height="{S}" fill="url(#spot)"/>',
    ]
    return defs, body


def int_nebula(rx=112, scale=1.0, edge=True):
    """The approved INT logo. rx=0 gives a full-bleed square for launchers, which apply
    their own mask; scale < 1 shrinks the letters into a maskable icon's safe zone."""
    S = 512
    size, depth, tr = 230, 18, -10
    x = centered_x("INT", size, S, depth, tracking=tr)
    base = (S + CAP * size / UPM) / 2 - 6
    d0, b0 = bg_nebula(S, rx)
    dg, bg_ = _grain(S)
    edge_body, edge_def = _edge(S, rx)
    d1, b1 = chrome_text("int", [("INT", MAGENTA, SIDE_MAGENTA)], x, base, size,
                         depth=depth, steps=30, tracking=tr)
    # Glints sit on the bevel's specular corners: top of the T bar, foot of the I.
    glints = sparkle(399, 172, 58) + sparkle(127, 329, 28, opacity=0.85)
    c = S / 2
    fg = (f'<g transform="translate({c} {c}) scale({scale}) translate({-c} {-c})">'
          + "".join(b1) + glints + "</g>")
    body = ['<g clip-path="url(#frame)">'] + b0 + [fg] + bg_ + ["</g>"]
    if edge:
        body.append(edge_body)
    return svg(S, d0 + dg + [edge_def] + d1, body,
               "INT, the magenta half of the wordmark, on a 90s CD-ROM nebula.")


files = {
    "logo-int.svg": int_nebula(),
    # Home-screen icons (PWA, apple-touch): opaque, full bleed. The maskable one keeps
    # the letters inside the central 80 % circle Android may crop to.
    "app-icon.svg": int_nebula(rx=0, edge=False),
    "app-icon-maskable.svg": int_nebula(rx=0, scale=0.74, edge=False),
}
for name, content in files.items():
    (OUT / name).write_text(content)
    print(name, len(content))
