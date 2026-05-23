"""
AFP page renderer.

AFP uses L-units (1/1440 inch).  Render at configurable DPI (default 150):
    pixels = l_units * dpi / 1440

Design:
  - Font family is detected from MCF2/CSD fields in the AFP file and mapped to
    system fonts (Times New Roman, Courier New, Helvetica, etc.).
  - Font size is estimated from median inter-line spacing (more reliable than
    advance-width estimation for proportional fonts).
  - Each text run is clipped to the space before the next run at the same Y,
    so columns can never visually overflow into each other.
  - Runs whose X start falls inside the extent of the previous run at the same
    Y are identified as overstrike (AFP bold simulation) and skipped.
  - Within-run character spacing uses actual PIL font metrics (getlength) so
    that proportional glyphs are placed at the correct relative positions.
"""

from __future__ import annotations

import statistics
import unicodedata
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .parser import AFPPage, TextRun

DEFAULT_DPI = 150

LETTER_WIDTH_LU  = int(8.5  * 1440)
LETTER_HEIGHT_LU = int(11.0 * 1440)
PAGE_MARGIN_LU   = int(0.5  * 1440)


# ── Font family registry ──────────────────────────────────────────────────────
#
# Each entry is a list of candidate paths tried in order; first hit wins.
# 'default' is used when the AFP typeface cannot be mapped to anything known.

_FAMILY_PATHS: Dict[str, List[str]] = {
    'times': [
        '/Library/Fonts/Times New Roman.ttf',
        '/System/Library/Fonts/Supplemental/Times New Roman.ttf',
        '/Library/Fonts/Georgia.ttf',
        '/System/Library/Fonts/Supplemental/Georgia.ttf',
    ],
    'times-bold': [
        '/Library/Fonts/Times New Roman Bold.ttf',
        '/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf',
        '/Library/Fonts/Georgia Bold.ttf',
        '/Library/Fonts/Times New Roman.ttf',       # fallback to regular
    ],
    'times-italic': [
        '/Library/Fonts/Times New Roman Italic.ttf',
        '/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf',
    ],
    'helvetica': [
        '/System/Library/Fonts/Helvetica.ttc',
        '/Library/Fonts/Arial.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
    ],
    'helvetica-bold': [
        '/Library/Fonts/Arial Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
    ],
    'courier': [
        '/System/Library/Fonts/Supplemental/Courier New.ttf',
        '/Library/Fonts/Courier New.ttf',
        '/System/Library/Fonts/Monaco.ttf',
        '/System/Library/Fonts/Menlo.ttc',
        '/System/Library/Fonts/Supplemental/Andale Mono.ttf',
    ],
    'courier-bold': [
        '/System/Library/Fonts/Supplemental/Courier New Bold.ttf',
        '/Library/Fonts/Courier New Bold.ttf',
        '/System/Library/Fonts/Supplemental/Courier New.ttf',
    ],
    'symbol': [
        '/System/Library/Fonts/Symbol.ttf',
        '/Library/Fonts/Symbol.ttf',
        # Symbol glyphs look better with a generic serif fallback
        '/Library/Fonts/Times New Roman.ttf',
    ],
    'default': [
        '/System/Library/Fonts/Supplemental/Courier New.ttf',
        '/Library/Fonts/Courier New.ttf',
        '/System/Library/Fonts/Monaco.ttf',
        '/System/Library/Fonts/Menlo.ttc',
    ],
}

# AFP typeface name substrings → family key
_TYPEFACE_FAMILY: List[Tuple[str, str]] = [
    # Order matters — more specific patterns first
    ('TIMES-BOLD',    'times-bold'),
    ('TIMES BOLD',    'times-bold'),
    ('TIMES-ITALIC',  'times-italic'),
    ('TIMES',         'times'),
    ('ROMAN-BOLD',    'times-bold'),
    ('ROMAN',         'times'),
    ('HELVETICA-BOLD','helvetica-bold'),
    ('HELVETICA',     'helvetica'),
    ('SWISS-BOLD',    'helvetica-bold'),
    ('SWISS',         'helvetica'),
    ('GOTHIC-BOLD',   'helvetica-bold'),
    ('GOTHIC',        'helvetica'),
    ('ARIAL-BOLD',    'helvetica-bold'),
    ('ARIAL',         'helvetica'),
    ('SANS-BOLD',     'helvetica-bold'),
    ('SANS',          'helvetica'),
    ('COURIER-BOLD',  'courier-bold'),
    ('COURIER',       'courier'),
    ('PRESTIGE',      'courier'),
    ('LETTER-GOTHIC', 'courier'),
    ('SYMBOL',        'symbol'),
]

# PIL font cache: (family_key, size_px) → Font object
_font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


# ── Font helpers ──────────────────────────────────────────────────────────────

def _typeface_to_family(typeface: str) -> str:
    """Map an AFP typeface name to one of the keys in _FAMILY_PATHS."""
    t = typeface.upper()
    for pattern, family in _TYPEFACE_FAMILY:
        if pattern in t:
            return family
    return 'default'


def _load_font_family(family: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font from the given family at the given pixel size, with caching."""
    key = (family, size)
    if key in _font_cache:
        return _font_cache[key]

    paths = _FAMILY_PATHS.get(family, _FAMILY_PATHS['default'])
    for path in paths:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue

    # Last resort
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the default (Courier) font — kept for backward-compat / fallback."""
    return _load_font_family('default', size)


def lu_to_px(lu: float, dpi: int) -> float:
    return lu * dpi / 1440


# ── Font size estimation ──────────────────────────────────────────────────────

def _estimate_line_height_lu(page: AFPPage) -> float:
    """
    Infer em size from the inter-line spacing.

    We look at all unique Y positions, take consecutive differences, filter to
    plausible line heights (6–40 pt ≈ 120–800 L-units), and return the median.
    The caller divides by 1.2 (standard 120 % leading) to get em size.
    """
    ys = sorted(set(r.y for r in page.text_runs))
    if len(ys) < 2:
        return 240.0        # fallback: ~12 pt line height (200 L-units em)

    diffs = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    plausible = [d for d in diffs if 100 < d < 900]
    if not plausible:
        return 240.0

    return statistics.median(plausible)


def _estimate_advance_lu(page: AFPPage) -> float:
    """
    Infer the typical character advance (in L-units) from inter-run gaps.

    IMPORTANT: divide by the CHARACTER count (decoded string length), NOT the
    raw byte count — UCS-2 BE uses 2 bytes per character so using bytes gives
    exactly half the correct advance.
    """
    samples: List[float] = []

    by_y: dict[int, List[TextRun]] = defaultdict(list)
    for r in page.text_runs:
        by_y[r.y].append(r)

    for runs in by_y.values():
        runs = sorted(runs, key=lambda r: r.x)
        for a, b in zip(runs, runs[1:]):
            text_a = _decode_and_filter(a)
            n = len(text_a)         # character count (not byte count)
            if n == 0:
                continue
            gap = b.x - a.x
            adv = gap / n
            # Plausible single-character advance: 40–400 L-units (3–25 CPI)
            if 40 < adv < 400:
                samples.append(adv)

    if not samples:
        return 96.0

    return max(50.0, min(300.0, statistics.median(samples)))


# ── Line layout: detect overstrike, compute clip widths ──────────────────────

@dataclass
class _PlacedRun:
    run: TextRun
    text: str
    x_px: float
    clip_px: float      # maximum pixel width this run may draw


def _layout_line(
    runs: List[TextRun],
    advance_lu: float,
    dpi: int,
    page_w_px: int,
) -> List[_PlacedRun]:
    """
    Given all runs at a single Y, produce placed, clipped runs.

    Overstrike detection (AFP bold simulation via double-printing) is handled
    passively: a run whose X falls within 1 L-unit of the previous run's X is
    a duplicate and skipped.  All other runs are placed at their AFP absolute X
    and clipped to the next run's X, so columns can never overflow visually.
    """
    placed: List[_PlacedRun] = []
    last_x: Optional[int] = None

    for run in sorted(runs, key=lambda r: r.x):
        text = _decode_and_filter(run)
        if not text:
            continue

        # Skip exact-position duplicates only (genuine overstrike / bold)
        if last_x is not None and abs(run.x - last_x) <= 1:
            continue

        x_px = lu_to_px(run.x, dpi)
        placed.append(_PlacedRun(run=run, text=text, x_px=x_px, clip_px=page_w_px))
        last_x = run.x

    # Set clip width = distance to the next run's start (or page edge)
    for i, p in enumerate(placed):
        if i + 1 < len(placed):
            p.clip_px = placed[i + 1].x_px
        else:
            p.clip_px = page_w_px

    return placed


# ── Glyph advance ─────────────────────────────────────────────────────────────

def _glyph_advance(font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
                   ch: str, fallback: float) -> float:
    """Return the pixel advance width for one character using font metrics."""
    try:
        w = font.getlength(ch)
        return w if w > 0 else fallback
    except AttributeError:
        pass
    try:
        bbox = font.getbbox(ch)
        w = float(bbox[2] - bbox[0])
        return w if w > 0 else fallback
    except Exception:
        return fallback


# ── Main render ───────────────────────────────────────────────────────────────

def render_page(page: AFPPage, dpi: int = DEFAULT_DPI) -> Image.Image:
    # ── Page canvas ──────────────────────────────────────────────────────────
    w_lu = page.width_lu or 0
    h_lu = page.height_lu or 0
    if not w_lu or not h_lu:
        w_lu, h_lu = _content_bounds(page)
    w_px = max(100, int(lu_to_px(w_lu, dpi)))
    h_px = max(100, int(lu_to_px(h_lu, dpi)))

    img  = Image.new('RGB', (w_px, h_px), 'white')
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w_px - 1, h_px - 1], outline='#cccccc', width=1)

    if not page.text_runs:
        _draw_no_content(draw, w_px, h_px)
        return img

    # ── Font sizing ───────────────────────────────────────────────────────────
    # We cross-check two independent signals and take the smaller one:
    #
    #  1. Line-height signal: median inter-baseline gap / 1.2 leading → em
    #  2. Advance signal: median inter-run advance / typical advance ratio
    #     For proportional fonts the average advance ≈ 0.50 × em; for
    #     monospace (Courier) it is ≈ 0.60 × em.  We use 0.50 as the common
    #     case because most business AFP uses proportional fonts.
    #
    # Taking the min prevents the font from growing larger than either signal
    # suggests, which is the main cause of within-run character overflow.
    line_h_lu   = _estimate_line_height_lu(page)
    em_from_lh  = line_h_lu / 1.2

    advance_lu  = _estimate_advance_lu(page)       # characters, not bytes
    em_from_adv = advance_lu / 0.50               # advance ≈ 0.50 × em

    em_lu      = min(em_from_lh, em_from_adv)
    font_size  = max(8, int(lu_to_px(em_lu, dpi)))

    # ── Build per-font-idx font objects ──────────────────────────────────────
    # When the AFP file has no MCF data we pick a sensible default:
    #   • UCS-2 BE files come from modern AFP generators that use proportional
    #     fonts (usually sans-serif), so Helvetica is a much better fit than
    #     Courier and avoids the over-wide-character clipping problem.
    #   • EBCDIC / Latin-1 files are typically traditional mainframe AFP where
    #     Courier is the conventional fallback.
    page_encoding = (page.text_runs[0].encoding if page.text_runs else '') or 'cp500'
    implicit_family = 'helvetica' if page_encoding == 'utf-16-be' else 'default'

    font_indices = set(r.font_idx for r in page.text_runs)
    idx_to_font: Dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
    for idx in font_indices:
        typeface = page.font_typefaces.get(idx, '')
        family   = _typeface_to_family(typeface) if typeface else implicit_family
        idx_to_font[idx] = _load_font_family(family, font_size)

    # Fallback font (used when idx not found in the map)
    fallback_font = _load_font_family(implicit_family, font_size)

    advance_lu = min(advance_lu, em_lu)   # keep for fallback glyph spacing

    # ── Approximate cap height for Y positioning ─────────────────────────────
    cap_h_px = font_size * 0.72

    # ── Group runs by baseline ────────────────────────────────────────────────
    by_y: dict[int, List[TextRun]] = defaultdict(list)
    for r in page.text_runs:
        by_y[r.y].append(r)

    for y_lu, runs in by_y.items():
        placed = _layout_line(runs, advance_lu, dpi, w_px)

        y_top = lu_to_px(y_lu, dpi) - cap_h_px
        y_top = max(0.0, y_top)
        if y_top >= h_px:
            continue

        prev_end_x = 0.0          # pixel X where the previous run's rendering ended
        snap_threshold = font_size * 0.45  # gaps smaller than ~half an em are snapped

        for p in placed:
            font = idx_to_font.get(p.run.font_idx, fallback_font)

            # Run-snapping: if the AFP X position leaves a tiny gap from where the
            # previous run ended (ligature splits, kerning, font-metric mismatch),
            # close it by starting at prev_end_x.  Word-level spaces are much larger
            # than snap_threshold and are left intact.
            gap = p.x_px - prev_end_x
            start_x = prev_end_x if (0 < gap < snap_threshold and prev_end_x > 0) else p.x_px

            x = start_x
            for ch in p.text:
                if x >= p.clip_px or x >= w_px:
                    break
                draw.text((int(x), int(y_top)), ch, fill='black', font=font)
                x += _glyph_advance(font, ch, lu_to_px(advance_lu, dpi))

            prev_end_x = x      # track where this run ended

    return img


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_bounds(page: AFPPage) -> tuple[int, int]:
    if not page.text_runs:
        return LETTER_WIDTH_LU, LETTER_HEIGHT_LU
    max_x = max(r.x + len(r.raw) * 96 for r in page.text_runs)
    max_y = max(r.y for r in page.text_runs)
    return (
        max(LETTER_WIDTH_LU, max_x + PAGE_MARGIN_LU),
        max(LETTER_HEIGHT_LU, max_y + int(1.0 * 1440)),
    )


def _char_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    try:
        bbox = font.getbbox('M')
        return float(bbox[2] - bbox[0])
    except Exception:
        return 0.0


# ── Groff AFP special-font ligature table ─────────────────────────────────────
# groff's AFP driver encodes typographic characters in a "special" charset
# (usually the T1AAAAAA or similar user-defined charset).  When decoded with
# cp500, these bytes fall in the U+00E0–U+00E9 range and must be mapped to the
# actual ligature or typographic character they represent.
#
# Mapping derived from groff's devlj4/devps special font character tables:
#   0x44 → U+00E0 'à' → fi  ligature
#   0x45 → U+00E1 'á' → fl  ligature
#   0x46 → U+00E2 'â' → ff  ligature
#   0x47 → U+00E3 'ã' → ffi ligature
#   0x48 → U+00E4 'ä' → ffl ligature
#   0x49 → U+00E5 'å' → ft  (f + t)
#   0x43 → U+00DF 'ß' → ss  (German sharp-s as "ss")
#
_GROFF_LIGATURES: Dict[str, str] = {
    '\xe0': 'fi',   # à → fi
    '\xe1': 'fl',   # á → fl
    '\xe2': 'ff',   # â → ff
    '\xe3': 'ffi',  # ã → ffi
    '\xe4': 'ffl',  # ä → ffl
    '\xe5': 'ft',   # å → ft
    '\xdf': 'ss',   # ß → ss
}


def _decode_and_filter(run: TextRun) -> str:
    enc = run.encoding or 'cp500'
    for e in (enc, 'utf-16-be', 'cp500', 'cp037', 'latin-1'):
        try:
            raw = run.raw.decode(e)
            break
        except Exception:
            continue
    else:
        raw = run.raw.decode('ascii', errors='replace')

    out = []
    for ch in raw:
        # Apply groff ligature substitution for special-font characters
        if ch in _GROFF_LIGATURES:
            out.append(_GROFF_LIGATURES[ch])
            continue
        cat = unicodedata.category(ch)
        if cat.startswith(('L', 'N', 'P', 'S')):
            out.append(ch)
        elif ch == '\xa0' or ch == ' ':
            out.append(' ')
        elif 0x20 <= ord(ch) <= 0x7E:
            out.append(ch)
        # Characters that are non-printable extended (e.g. groff special marks
        # that didn't match a ligature) are silently dropped — they typically
        # render as □ boxes and are typographic control marks, not content.
    return ''.join(out)


def _draw_no_content(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    msg = ('No renderable text found on this page.\n'
           '(Page may contain graphics, overlays, or unsupported AFP resources.)')
    draw.text((20, 20), msg, fill='#888888', font=_load_font(14))
