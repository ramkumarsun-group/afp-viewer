"""
AFP Viewer — PyQt6 GUI
Faithfully implements the HTML/JSX design: welcome screen, 3-pane page view,
inspector with hex dump, thumbnail rail, toolbar, light/dark theme.
"""

from __future__ import annotations

import io
import os
import traceback
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSize, QRect, QPoint, QTimer, QSettings,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDragEnterEvent, QDropEvent, QFont,
    QFontMetrics, QImage, QKeySequence, QPainter, QPainterPath,
    QPen, QPixmap, QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QStatusBar, QStyledItemDelegate,
    QStyleOptionViewItem, QStyle, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QProgressBar, QLineEdit, QAbstractItemView,
    QHeaderView, QPlainTextEdit,
)

from PIL import Image

from .parser import AFPDocument, StructuredField, parse_afp_file
from .renderer import render_page, DEFAULT_DPI


# ─────────────────────────────────────────────────────────────────────────────
# Theme colours — approximated from design's oklch values → RGB hex
# Light: styles.css :root   Dark: styles.css .dark
# ─────────────────────────────────────────────────────────────────────────────

_LIGHT = dict(
    bg          ='#FDFCFB',
    bg_sidebar  ='#F4F1EC',
    bg_viewer   ='#EEECE8',
    bg_toolbar  ='#F7F5F2',
    bg_page     ='#FFFFFF',
    bg_hover    ='#EAE8E3',
    bg_selected ='#D8EBFF',
    bg_elev     ='#FFFFFF',
    fg          ='#1C1B28',
    fg2         ='#4B4A58',
    fg3         ='#7A7988',
    fg4         ='#A8A7B5',
    line        ='#D6D5DA',
    line2       ='#E4E3E8',
    accent      ='#0A84FF',
    accent_soft ='#D8EBFF',
)

_DARK = dict(
    bg          ='#1B1A27',
    bg_sidebar  ='#201F2E',
    bg_viewer   ='#0F0E1A',
    bg_toolbar  ='#1E1D2C',
    bg_page     ='#FFFFFF',
    bg_hover    ='#2A2838',
    bg_selected ='#333052',
    bg_elev     ='#252333',
    fg          ='#F5F3FF',
    fg2         ='#B3B1C6',
    fg3         ='#8A8999',
    fg4         ='#5A596A',
    line        ='#37364A',
    line2       ='#2C2B3C',
    accent      ='#0A84FF',
    accent_soft ='#1E3555',
)

# ─────────────────────────────────────────────────────────────────────────────
# AFP record-type chip colours (matching --rt-* CSS vars)
# ─────────────────────────────────────────────────────────────────────────────

_RT_COLORS_LIGHT = {
    'doc':   '#7B5CAE',   # oklch(60% 0.13 280)  purple
    'page':  '#3272B8',   # oklch(58% 0.14 240)  blue
    'env':   '#2C8E8A',   # oklch(58% 0.10 180)  teal
    'text':  '#7A6A58',   # oklch(50% 0.05 60)   warm grey
    'image': '#B8643B',   # oklch(60% 0.14 30)   terracotta
    'bar':   '#2E9A4F',   # oklch(60% 0.14 140)  green
    'gfx':   '#A0359A',   # oklch(60% 0.14 320)  magenta
    'other': '#6E6C80',   # oklch(55% 0.005 250) neutral
}
_RT_COLORS_DARK = {
    'doc':   '#A78BDA',   # oklch(72% 0.14 280)
    'page':  '#6B9FE8',   # oklch(72% 0.14 240)
    'env':   '#5AB8B5',   # oklch(72% 0.10 180)
    'text':  '#B0A48E',   # oklch(72% 0.04 60)
    'image': '#E88D6B',   # oklch(72% 0.14 30)
    'bar':   '#5AC878',   # oklch(72% 0.14 140)
    'gfx':   '#D070CC',   # oklch(72% 0.14 320)
    'other': '#9898B5',   # oklch(70% 0.005 250)
}

# AFP field name → display category
_AFP_CATEGORY: dict[str, str] = {}
for _n in ['BDT', 'EDT', 'BNG', 'ENG', 'BPP', 'EPP', 'DXD']:
    _AFP_CATEGORY[_n] = 'doc'
for _n in ['BPG', 'EPG', 'BAG', 'EAG', 'PGD', 'PPA', 'BOG', 'EOG',
           'CPD', 'MDR', 'IMM', 'BOA', 'EOA', 'OAD', 'MPO', 'MPS']:
    _AFP_CATEGORY[_n] = 'page'
for _n in ['BCF', 'ECF', 'BCF2', 'ECF2', 'MCF', 'MCF2', 'CSD', 'BFN', 'EFN',
           'BRG', 'ERG', 'MMT', 'FGD', 'FND', 'FCD', 'FCS', 'FAD', 'FCR',
           'FCA', 'FMP', 'FCM', 'BCO', 'ECO', 'FRD', 'IOI', 'OCM']:
    _AFP_CATEGORY[_n] = 'env'
for _n in ['PTX', 'BPT', 'EPT', 'PTD']:
    _AFP_CATEGORY[_n] = 'text'
for _n in ['BIM', 'EIM', 'BII', 'EII', 'IRD', 'IDD', 'IDA', 'IGA', 'IGD', 'IID']:
    _AFP_CATEGORY[_n] = 'image'
for _n in ['BBC', 'EBC']:
    _AFP_CATEGORY[_n] = 'bar'


def _chip_qcolor(name: str, dark: bool) -> QColor:
    cat = _AFP_CATEGORY.get(name, 'other')
    palette = _RT_COLORS_DARK if dark else _RT_COLORS_LIGHT
    return QColor(palette.get(cat, palette['other']))


# ─────────────────────────────────────────────────────────────────────────────
# AFP structured-field display helpers
# ─────────────────────────────────────────────────────────────────────────────

_AFP_CLASS: dict[int, str] = {
    0xA2: 'Att', 0xA4: 'Par', 0xA6: 'Inc', 0xA7: 'InD',
    0xA8: 'Beg', 0xA9: 'End', 0xAA: 'Map', 0xAB: 'Dsc',
    0xAC: 'Mgr', 0xAE: 'Dat', 0xAF: 'Pos', 0xB0: 'Ctl',
    0xB1: 'Inv', 0xB4: 'Hdr', 0xEE: 'XDt', 0x8C: 'Mtr',
}
_AFP_TYPE: dict[int, str] = {
    0x6B: 'InlObj', 0x6D: 'ObjCnt', 0x87: 'Image',  0x88: 'CFont',
    0x89: 'CharSt', 0x8A: 'FontCS', 0x8B: 'CodePg', 0x8C: 'FntPat',
    0x92: 'FntDat', 0x9B: 'PText',  0xA5: 'Stream', 0xA8: 'Doc',
    0xAD: 'Group',  0xAF: 'Page',   0xBB: 'BarCod', 0xC3: 'Form',
    0xC4: 'ObjAr',  0xC6: 'ResGrp', 0xC7: 'CtlSq',  0xC9: 'ObjGrp',
    0xCE: 'CFont',  0xD3: 'Overly', 0xDF: 'PgSeg',  0xEE: 'XData',
    0xFB: 'ImgDat',
}

_SF_FULL_NAMES: dict[str, str] = {
    'BDT':'Begin Document', 'EDT':'End Document',
    'BNG':'Begin Named Group', 'ENG':'End Named Group', 'DXD':'Document Index',
    'BPG':'Begin Page', 'EPG':'End Page',
    'BAG':'Begin Active Environment Group', 'EAG':'End Active Environment Group',
    'PGD':'Page Descriptor', 'PPA':'Page Position Attribute',
    'BOG':'Begin Object Group', 'EOG':'End Object Group',
    'CPD':'Copy Page Descriptor', 'MDR':'Medium Data Record', 'IMM':'Invoke Medium Map',
    'BOA':'Begin Object Area', 'EOA':'End Object Area', 'OAD':'Object Area Descriptor',
    'MPO':'Map Page Overlay', 'MPS':'Map Page Segment',
    'PTX':'Presentation Text Data', 'BPT':'Begin Presentation Text',
    'EPT':'End Presentation Text', 'PTD':'Presentation Text Descriptor',
    'BIM':'Begin Image Object', 'EIM':'End Image Object',
    'BII':'Begin Inline Image', 'EII':'End Inline Image',
    'IRD':'Image Raster Data', 'IDD':'Image Data Descriptor',
    'IDA':'Image Descriptor Attribute', 'IGA':'Image Glyph Attribute',
    'IGD':'Image Glyph Descriptor', 'IID':'Image Input Descriptor',
    'BCO':'Begin Coded Object', 'ECO':'End Coded Object', 'FRD':'Font Resource Data',
    'BBC':'Begin Bar Code Object', 'EBC':'End Bar Code Object',
    'BCF':'Begin Coded Font', 'ECF':'End Coded Font',
    'BCF2':'Begin Coded Font Resource', 'ECF2':'End Coded Font Resource',
    'MCF':'Map Coded Font', 'MCF2':'Map Coded Font (type 2)',
    'CSD':'Character Set Descriptor',
    'BFN':'Begin Font', 'EFN':'End Font',
    'BRG':'Begin Resource Group', 'ERG':'End Resource Group', 'MMT':'Map Media Type',
    'FGD':'Font Global Descriptor', 'FND':'Font Patterns', 'FCD':'Font Character Index',
    'FCS':'Font Character Shapes', 'FAD':'Font Attribute Data',
    'FCR':'Font Character Reference', 'FCA':'Font Character Attribute',
    'FMP':'Font Metrics Parameters', 'FCM':'Font Character Migration Data',
    'BPP':'Begin Print Stream', 'EPP':'End Print Stream',
    'IOI':'Include Object Inline', 'OCM':'Object Container Migration',
    'BOC':'Begin Object Container', 'EOC':'End Object Container', 'OCD':'Object Container Data',
    'BPS':'Begin Page Segment', 'EPS':'End Page Segment',
    'BMO':'Begin Medium Overlay', 'EMO':'End Medium Overlay',
}


def _display_name(sf: StructuredField) -> str:
    name = sf.name
    if not name.startswith('X') or len(name) != 7:
        return name
    try:
        cb = int(name[3:5], 16)
        tb = int(name[5:7], 16)
    except ValueError:
        return name
    return f"{_AFP_CLASS.get(cb, f'{cb:02X}')}·{_AFP_TYPE.get(tb, f'{tb:02X}')}"


def _decode_name_bytes(chunk: bytes) -> str:
    if not chunk:
        return ''
    for enc in ('cp500', 'cp037', 'ascii', 'latin-1'):
        try:
            s = chunk.decode(enc).strip('\x00 ')
            if s and any(c.isalnum() for c in s):
                return s
        except Exception:
            pass
    return ''


def _sf_description(sf: StructuredField, page_num: int = 0) -> str:
    """Return a human-readable description for an AFP structured field.
    Shows the full English name from _SF_FULL_NAMES.
    BPG/EPG append the sequential page number (e.g. 'Begin Page · 0001').
    All other fields show the name only — no internal IDs or byte counts.
    """
    name = sf.name
    full = _SF_FULL_NAMES.get(name, '')

    if name == 'BPG':
        n = page_num if page_num > 0 else 1
        return f'{full or "Begin Page"} · {n:04d}'
    if name == 'EPG':
        n = page_num if page_num > 0 else 1
        return f'{full or "End Page"} · {n:04d}'

    return full if full else name


# ─────────────────────────────────────────────────────────────────────────────
# Hex dump formatter
# ─────────────────────────────────────────────────────────────────────────────

def _format_hex_dump(data: bytes, base_offset: int = 0, max_bytes: int = 256) -> str:
    lines = []
    chunk = data[:max_bytes]
    per = 16
    for i in range(0, len(chunk), per):
        row = chunk[i:i+per]
        addr = f'{base_offset + i:08X}'
        hex_l = ' '.join(f'{b:02X}' for b in row[:8])
        hex_r = ' '.join(f'{b:02X}' for b in row[8:])
        pad_l = '   ' * (8 - len(row[:8]))
        pad_r = '   ' * (8 - len(row[8:]))
        asc = ''.join(chr(b) if 32 <= b < 127 else '·' for b in row)
        lines.append(f'{addr}  {hex_l}{pad_l}  {hex_r}{pad_r}  {asc}')
    if len(data) > max_bytes:
        lines.append(f'… {len(data) - max_bytes} more bytes')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FullRowDelegate — paints AFP tree rows matching inspector.jsx RecordRow
# [TypeChip fixed-width] [description text, flex] [bytes, right-aligned muted]
# ─────────────────────────────────────────────────────────────────────────────

_SF_ROLE    = Qt.ItemDataRole.UserRole + 10
_DESC_ROLE  = Qt.ItemDataRole.UserRole + 11
_BYTES_ROLE = Qt.ItemDataRole.UserRole + 12


class FullRowDelegate(QStyledItemDelegate):
    """Paints full AFP tree rows: chip | description | bytes.
    TypeChip: text=base, bg=base@18% alpha, border=base@35% alpha.
    Row height: 26px. No column separators, no header."""

    def __init__(self, is_dark_fn, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark_fn
        self._mono = QFont('Menlo')
        self._mono.setStyleHint(QFont.StyleHint.Monospace)
        self._mono.setPointSizeF(9.5)
        self._mono.setBold(True)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        dark = self._is_dark()
        t = _DARK if dark else _LIGHT
        r = option.rect

        # Row background
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, QColor(t['bg_selected']))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(r, QColor(t['bg_hover']))

        sf: Optional[StructuredField] = index.data(_SF_ROLE)
        chip_label = index.data(Qt.ItemDataRole.DisplayRole) or ''
        desc_text  = index.data(_DESC_ROLE)  or ''
        bytes_text = index.data(_BYTES_ROLE) or ''

        # Chip colours: text=base, bg=base@18%, border=base@35%
        base     = _chip_qcolor(sf.name if sf else '', dark)
        bg_c     = QColor(base.red(), base.green(), base.blue(), 46)   # ~18%
        border_c = QColor(base.red(), base.green(), base.blue(), 89)   # ~35%

        fm_mono = QFontMetrics(self._mono)
        chip_w  = max(fm_mono.horizontalAdvance(chip_label) + 10, 32)
        chip_h  = 16
        cx = r.x() + 4
        cy = r.y() + (r.height() - chip_h) // 2

        bytes_font = QFont('Menlo')
        bytes_font.setStyleHint(QFont.StyleHint.Monospace)
        bytes_font.setPointSize(9)
        fm_bytes = QFontMetrics(bytes_font)
        bytes_w  = fm_bytes.horizontalAdvance(bytes_text) + 6

        name_x = cx + chip_w + 6
        name_w = r.right() - bytes_w - 10 - name_x

        # Draw chip
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(float(cx), float(cy), float(chip_w), float(chip_h), 3.0, 3.0)
        painter.fillPath(path, bg_c)
        painter.setPen(QPen(border_c, 0.5))
        painter.drawPath(path)

        painter.setFont(self._mono)
        painter.setPen(base)
        painter.drawText(QRect(cx, cy, chip_w, chip_h),
                         Qt.AlignmentFlag.AlignCenter, chip_label)

        # Draw description (flex, elided)
        if name_w > 10:
            sans = QApplication.font()
            sans.setPointSize(11)
            fm_sans = QFontMetrics(sans)
            elided  = fm_sans.elidedText(desc_text, Qt.TextElideMode.ElideRight, max(name_w, 0))
            painter.setFont(sans)
            painter.setPen(QColor(t['fg']))
            painter.drawText(
                QRect(name_x, r.y(), name_w, r.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided)

        # Draw byte count (right-aligned, muted)
        if bytes_text:
            painter.setFont(bytes_font)
            painter.setPen(QColor(t['fg3']))
            painter.drawText(
                QRect(r.right() - bytes_w - 4, r.y(), bytes_w + 4, r.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                bytes_text)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, 26)


# ─────────────────────────────────────────────────────────────────────────────
# Background load thread
# ─────────────────────────────────────────────────────────────────────────────

class LoadThread(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object, list)
    error = pyqtSignal(str)

    def __init__(self, filepath: str, dpi: int = DEFAULT_DPI):
        super().__init__()
        self.filepath = filepath
        self.dpi = dpi

    def run(self) -> None:
        try:
            doc = parse_afp_file(self.filepath)
            pixmaps: List[QPixmap] = []
            for i, page in enumerate(doc.pages):
                img = render_page(page, self.dpi)
                pixmaps.append(_pil_to_pixmap(img))
                self.progress.emit(i + 1, len(doc.pages))
            self.done.emit(doc, pixmaps)
        except Exception as exc:
            self.error.emit(f'{type(exc).__name__}: {exc}\n{traceback.format_exc()}')


# ─────────────────────────────────────────────────────────────────────────────
# AFP file icon (drawn with QPainter, matching FileIcon SVG in welcome.jsx)
# ─────────────────────────────────────────────────────────────────────────────

def _make_afp_icon(size: int) -> QPixmap:
    h = int(size * 1.2)
    pix = QPixmap(size, h)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    fold = size // 5

    body = QPainterPath()
    body.moveTo(2, 0)
    body.lineTo(size - fold - 2, 0)
    body.lineTo(size - 2, fold)
    body.lineTo(size - 2, h - 2)
    body.lineTo(2, h - 2)
    body.closeSubpath()
    p.fillPath(body, QColor('#FFFFFF'))
    p.setPen(QPen(QColor('#C8C7D8'), 1.0))
    p.drawPath(body)

    fold_path = QPainterPath()
    fold_path.moveTo(size - fold - 2, 0)
    fold_path.lineTo(size - fold - 2, fold)
    fold_path.lineTo(size - 2, fold)
    p.fillPath(fold_path, QColor('#E0DFF0'))
    p.setPen(QPen(QColor('#C8C7D8'), 1.0))
    p.drawPath(fold_path)

    # Content lines (matching rect elements in FileIcon SVG)
    accent = QColor('#0A84FF')
    line_h = max(2, size // 22)
    y_positions = [int(h * 0.46), int(h * 0.58), int(h * 0.71)]
    widths_frac  = [1.0, 0.75, 0.92]
    alphas       = [178, 102, 140]
    lx = int(size * 0.20)
    lw_full = int(size * 0.60)
    for yy, wf, alpha in zip(y_positions, widths_frac, alphas):
        c = QColor(accent)
        c.setAlpha(alpha)
        p.fillRect(lx, yy, int(lw_full * wf), line_h, c)

    # AFP label
    f = QFont('Menlo')
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(max(6, size // 9))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor('#0A84FF'))
    lbl_rect = QRect(0, h - size // 3, size, size // 4)
    p.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, 'AFP')
    p.end()
    return pix


# ─────────────────────────────────────────────────────────────────────────────
# WelcomeWidget — matches welcome.jsx exactly
# Left: flex stretch drop-zone  |  Right: fixed 340px recents + about box
# ─────────────────────────────────────────────────────────────────────────────

class WelcomeWidget(QWidget):
    open_requested = pyqtSignal()
    file_opened    = pyqtSignal(str)   # path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark = False
        self.setAcceptDrops(True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── LEFT: drop-zone panel ─────────────────────────────────────────
        left = QWidget()
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(40, 40, 40, 40)
        lv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._drop_frame = QFrame()
        self._drop_frame.setObjectName('drop_frame')
        self._drop_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drop_frame.mousePressEvent = lambda _: self.open_requested.emit()
        self._drop_frame.setMaximumWidth(420)
        df_layout = QVBoxLayout(self._drop_frame)
        df_layout.setContentsMargins(36, 36, 36, 36)
        df_layout.setSpacing(14)
        df_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_make_afp_icon(56))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet('background: transparent;')

        drop_title = QLabel('Drop an AFP file here')
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_title.setStyleSheet('font-size: 15px; font-weight: 600; background: transparent;')

        self._drop_sub = QLabel('or click to browse — supports .afp, .mod, .lst')
        self._drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_sub.setStyleSheet('font-size: 12px; color: #7A7988; background: transparent;')

        df_layout.addWidget(icon_lbl)
        df_layout.addWidget(drop_title)
        df_layout.addWidget(self._drop_sub)

        self._hint = QLabel('⌘O to open')
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet('font-size: 11px; color: #7A7988; background: transparent;')

        lv.addWidget(self._drop_frame)
        lv.addSpacing(16)
        lv.addWidget(self._hint)
        root.addWidget(left, 12)

        # ── Separator line on left of right panel ─────────────────────────
        self._vsep = QFrame()
        self._vsep.setFrameShape(QFrame.Shape.VLine)
        self._vsep.setFixedWidth(1)
        root.addWidget(self._vsep)

        # ── RIGHT: recents panel (fixed 340px) ────────────────────────────
        self._right = QWidget()
        self._right.setObjectName('right_panel')
        self._right.setFixedWidth(340)
        rv = QVBoxLayout(self._right)
        rv.setContentsMargins(16, 30, 16, 16)
        rv.setSpacing(0)

        self._recents_hdr = QLabel('RECENT FILES')
        self._recents_hdr.setStyleSheet(
            'font-size: 11px; font-weight: 700; letter-spacing: 1px; padding: 0 8px 12px;')
        rv.addWidget(self._recents_hdr)

        self._recents_container = QWidget()
        self._recents_vbox = QVBoxLayout(self._recents_container)
        self._recents_vbox.setContentsMargins(0, 0, 0, 0)
        self._recents_vbox.setSpacing(2)
        rv.addWidget(self._recents_container)

        rv.addStretch()

        # About AFP files info box
        self._about_box = QFrame()
        self._about_box.setObjectName('about_box')
        ab_layout = QVBoxLayout(self._about_box)
        ab_layout.setContentsMargins(12, 10, 12, 10)
        ab_layout.setSpacing(3)

        self._about_title = QLabel('About AFP files')
        self._about_title.setStyleSheet('font-size: 10.5px; font-weight: 600;')

        self._about_text = QLabel(
            'AFP (Advanced Function Presentation) is a print-stream format '
            'built from structured fields like BPG, PTX, BIM and BBC. '
            'This viewer renders the visual page and exposes the underlying record tree.')
        self._about_text.setWordWrap(True)
        self._about_text.setStyleSheet('font-size: 10.5px;')

        ab_layout.addWidget(self._about_title)
        ab_layout.addWidget(self._about_text)
        rv.addWidget(self._about_box)

        root.addWidget(self._right)
        self._populate_recents()

    def _populate_recents(self) -> None:
        while self._recents_vbox.count():
            item = self._recents_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        settings = QSettings('AFPViewer', 'RecentFiles')
        recents = settings.value('recent_files', []) or []
        if not recents:
            no_lbl = QLabel('No recent files')
            no_lbl.setStyleSheet(f'font-size: 12px; padding: 8px; color: #A8A7B5;')
            self._recents_vbox.addWidget(no_lbl)
            return

        for path in recents[:10]:
            p = Path(path)
            if not p.exists():
                continue
            row = self._make_recent_row(path, p.name, str(p.parent))
            self._recents_vbox.addWidget(row)

    def _make_recent_row(self, path: str, filename: str, parent_path: str) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_make_afp_icon(22))
        hl.addWidget(icon_lbl)

        info = QWidget()
        iv = QVBoxLayout(info)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(1)
        name_lbl = QLabel(filename)
        name_lbl.setStyleSheet('font-size: 13px; font-weight: 600; background: transparent;')
        path_lbl = QLabel(parent_path)
        path_lbl.setStyleSheet('font-size: 10px; background: transparent;')
        path_lbl.setMaximumWidth(260)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # Elide via font metrics at paint time
        fm = QFontMetrics(path_lbl.font())
        elided = fm.elidedText(parent_path, Qt.TextElideMode.ElideMiddle, 240)
        path_lbl.setText(elided)
        iv.addWidget(name_lbl)
        iv.addWidget(path_lbl)
        hl.addWidget(info, 1)

        t = _DARK if self._dark else _LIGHT
        row.mousePressEvent = lambda _, p=path: self.file_opened.emit(p)
        row.enterEvent  = lambda _, r=row: r.setStyleSheet(
            f'background: {t["bg_hover"]}; border-radius: 8px;')
        row.leaveEvent  = lambda _, r=row: r.setStyleSheet('')
        return row

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(('.afp', '.mod', '.lst')):
                self.file_opened.emit(path)

    def refresh_recents(self) -> None:
        self._populate_recents()

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(f'background: {t["bg_viewer"]};')
        self._right.setStyleSheet(
            f'#right_panel {{ background: {t["bg_sidebar"]}; }}'
            f'QWidget {{ background: transparent; }}'
        )
        self._about_box.setStyleSheet(
            f'background: {t["bg_elev"]}; border: 0.5px solid {t["line"]}; border-radius: 8px;')
        self._vsep.setStyleSheet(f'background: {t["line"]};')
        self._drop_frame.setStyleSheet(
            f'#drop_frame {{ background: {t["bg_elev"]}; border: 2px dashed {t["line2"]}; border-radius: 14px; }}'
            f'#drop_frame:hover {{ background: {t["accent_soft"]}; border-color: {t["accent"]}; }}')
        self._drop_sub.setStyleSheet(f'font-size: 12px; color: {t["fg3"]};')
        self._hint.setStyleSheet(f'font-size: 11px; color: {t["fg3"]};')
        self._recents_hdr.setStyleSheet(
            f'font-size: 11px; font-weight: 700; letter-spacing: 1px; '
            f'padding: 0 8px 12px; color: {t["fg3"]};')
        self._about_title.setStyleSheet(f'font-size: 10.5px; font-weight: 600; color: {t["fg2"]};')
        self._about_text.setStyleSheet(f'font-size: 10.5px; color: {t["fg3"]};')


# ─────────────────────────────────────────────────────────────────────────────
# ThumbnailPanel — matches viewer.jsx Thumbnails component
# Fixed 168px wide, 124px thumbnail width, accent border when selected
# ─────────────────────────────────────────────────────────────────────────────

_THUMB_W = 124


class ThumbnailPanel(QWidget):
    page_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(168)
        self._dark = False
        self._current = 0
        self._thumb_labels: list[QLabel] = []
        self._num_labels: list[QLabel]   = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # "PAGES" header — 11px bold, fg3, letterSpacing 0.8, uppercase
        self._hdr = QLabel('PAGES')
        self._hdr.setContentsMargins(14, 10, 14, 8)
        self._hdr.setStyleSheet(
            'font-size: 11px; font-weight: 700; letter-spacing: 1px; color: #7A7988;')
        root.addWidget(self._hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll)

        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(14, 0, 14, 14)
        self._vbox.setSpacing(0)
        self._vbox.addStretch()
        self._scroll.setWidget(self._container)

    def set_pages(self, pixmaps: List[QPixmap]) -> None:
        # Clear existing thumbnails
        for lbl in self._thumb_labels:
            lbl.parent().deleteLater() if lbl.parent() else lbl.deleteLater()
        self._thumb_labels.clear()
        self._num_labels.clear()

        # Remove all items including stretch
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, pm in enumerate(pixmaps):
            th = pm.scaledToWidth(_THUMB_W, Qt.TransformationMode.SmoothTransformation)

            wrapper = QWidget()
            wl = QVBoxLayout(wrapper)
            wl.setContentsMargins(0, 0, 0, 14)
            wl.setSpacing(4)
            wrapper.setCursor(Qt.CursorShape.PointingHandCursor)

            lbl = QLabel()
            lbl.setPixmap(th)
            lbl.setFixedSize(th.width(), th.height())
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setProperty('page_idx', i)

            num = QLabel(str(i + 1))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet('font-size: 11px; color: #7A7988;')

            wl.addWidget(lbl, 0, Qt.AlignmentFlag.AlignCenter)
            wl.addWidget(num, 0, Qt.AlignmentFlag.AlignCenter)

            wrapper.mousePressEvent = (lambda e, n=i: self.page_selected.emit(n))
            self._thumb_labels.append(lbl)
            self._num_labels.append(num)
            self._vbox.addWidget(wrapper)

        self._vbox.addStretch()
        if pixmaps:
            self.set_current(0)

    def set_current(self, idx: int) -> None:
        self._current = idx
        t = _DARK if self._dark else _LIGHT
        for i, (lbl, num) in enumerate(zip(self._thumb_labels, self._num_labels)):
            if i == idx:
                lbl.setStyleSheet(
                    f'border: 1.5px solid {t["accent"]}; border-radius: 3px;')
                num.setStyleSheet(
                    f'font-size: 11px; font-weight: 600; color: {t["accent"]};')
            else:
                lbl.setStyleSheet(
                    f'border: 1.5px solid {t["line"]}; border-radius: 3px;')
                num.setStyleSheet(
                    f'font-size: 11px; color: {t["fg2"]};')

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(f'QWidget {{ background: {t["bg_sidebar"]}; }}'
                           f'QScrollBar:vertical {{ background: transparent; width: 8px; }}'
                           f'QScrollBar::handle:vertical {{ background: {t["line"]}; border-radius: 4px; }}')
        self._hdr.setStyleSheet(
            f'font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {t["fg3"]};')
        self.set_current(self._current)


# ─────────────────────────────────────────────────────────────────────────────
# PageCanvas + PageViewerPanel — bg_viewer, centred page with shadow
# ─────────────────────────────────────────────────────────────────────────────

class PageCanvas(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source: Optional[QPixmap] = None
        self._zoom = 1.0

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._redraw()

    def set_zoom(self, zoom: float) -> None:
        self._zoom = zoom
        self._redraw()

    def _redraw(self) -> None:
        if not self._source:
            return
        w = int(self._source.width() * self._zoom)
        h = int(self._source.height() * self._zoom)
        scaled = self._source.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)
        self.resize(scaled.width(), scaled.height())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            win = self.window()
            if delta > 0:
                win._zoom_in()
            elif delta < 0:
                win._zoom_out()
            event.accept()
        else:
            event.ignore()


class PageViewerPanel(QWidget):
    """Page display area — bg_viewer background, 28px padding, centred page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll)

        self.canvas = PageCanvas()
        self.scroll.setWidget(self.canvas)

    def apply_theme(self, dark: bool) -> None:
        t = _DARK if dark else _LIGHT
        self.scroll.setStyleSheet(
            f'QScrollArea {{ background: {t["bg_viewer"]}; border: none; }}'
            f'QScrollBar:vertical {{ background: transparent; width: 8px; }}'
            f'QScrollBar::handle:vertical {{ background: {t["line"]}; border-radius: 4px; }}'
            f'QScrollBar:horizontal {{ background: transparent; height: 8px; }}'
            f'QScrollBar::handle:horizontal {{ background: {t["line"]}; border-radius: 4px; }}'
        )
        self.canvas.setStyleSheet(f'background: {t["bg_viewer"]}; padding: 28px;')


# ─────────────────────────────────────────────────────────────────────────────
# InspectorTree — single-column QTreeWidget with FullRowDelegate
# ─────────────────────────────────────────────────────────────────────────────

class InspectorTree(QTreeWidget):
    def __init__(self, is_dark_fn, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark_fn
        self._delegate = FullRowDelegate(is_dark_fn, self)

        self.setColumnCount(1)
        self.header().hide()
        self.setItemDelegate(self._delegate)
        self.setIndentation(14)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)

    def populate(self, doc: 'AFPDocument', filter_text: str = '') -> None:
        self.clear()
        stack: list[QTreeWidgetItem] = []
        page_num = 0
        fl = filter_text.lower()

        for sf in doc.all_fields:
            is_begin = (len(sf.name) > 1 and sf.name[0] == 'B' and sf.name[1].isupper())
            is_end   = (len(sf.name) > 1 and sf.name[0] == 'E' and sf.name[1].isupper())

            if sf.name == 'BPG':
                page_num += 1

            disp   = _display_name(sf)
            desc   = _sf_description(sf, page_num if sf.name in ('BPG', 'EPG') else 0)
            size_s = f'{len(sf.data)}b' if sf.data else '0b'

            if fl and fl not in disp.lower() and fl not in sf.name.lower() \
                    and fl not in desc.lower():
                if is_begin:
                    stack.append(QTreeWidgetItem())
                elif is_end and stack:
                    stack.pop()
                continue

            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.DisplayRole, disp)
            item.setData(0, _SF_ROLE, sf)
            item.setData(0, _DESC_ROLE, desc)
            item.setData(0, _BYTES_ROLE, size_s)
            item.setToolTip(0, _SF_FULL_NAMES.get(sf.name, '')
                            or f'SFID 0x{sf.sfid.hex().upper()}')

            if stack and stack[-1].treeWidget() is not None:
                stack[-1].addChild(item)
            elif stack:
                # dummy parent — add at top level
                self.addTopLevelItem(item)
            else:
                self.addTopLevelItem(item)

            if is_begin:
                stack.append(item)
                item.setExpanded(True)
            elif is_end and stack:
                stack.pop()

        self.expandToDepth(2)

    def apply_theme(self, dark: bool) -> None:
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(f'''
            QTreeWidget {{
                background: {t["bg_sidebar"]};
                color: {t["fg"]};
                border: none;
                font-size: 12px;
                outline: 0;
            }}
            QTreeWidget::item {{ padding: 0; }}
            QTreeWidget::item:hover {{ background: {t["bg_hover"]}; }}
            QTreeWidget::item:selected {{
                background: {t["bg_selected"]};
                color: {t["fg"]};
            }}
            QScrollBar:vertical {{
                background: transparent; width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {t["line"]}; border-radius: 4px;
            }}
        ''')


# ─────────────────────────────────────────────────────────────────────────────
# RecordDetailsPanel — matches inspector.jsx RecordDetails
# Header chip + full name, offset/length grid, Parameters, Hex Dump
# ─────────────────────────────────────────────────────────────────────────────

class RecordDetailsPanel(QWidget):
    def __init__(self, is_dark_fn, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark_fn

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 14)
        root.setSpacing(8)

        # Header: TypeChip + full name (13px bold)
        self._hdr = QWidget()
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self._chip_lbl = QLabel()
        self._chip_lbl.setFont(_mono_font(9))
        self._chip_lbl.setFixedHeight(20)
        self._chip_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chip_lbl.setMinimumWidth(50)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet('font-size: 13px; font-weight: 600;')
        self._name_lbl.setWordWrap(True)
        hl.addWidget(self._chip_lbl)
        hl.addWidget(self._name_lbl, 1)
        root.addWidget(self._hdr)

        # Metadata grid: Offset 0xHEXADDR | Length N bytes (11px mono fg3)
        meta_w = QWidget()
        ml = QHBoxLayout(meta_w)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(16)
        self._offset_lbl = QLabel()
        self._length_lbl = QLabel()
        for lbl in (self._offset_lbl, self._length_lbl):
            lbl.setFont(_mono_font(10))
            ml.addWidget(lbl)
        ml.addStretch()
        root.addWidget(meta_w)

        # PARAMETERS section
        root.addWidget(_section_label('Parameters'))
        self._params_lbl = QLabel()
        self._params_lbl.setWordWrap(True)
        self._params_lbl.setFont(_mono_font(10))
        root.addWidget(self._params_lbl)

        # HEX DUMP section
        root.addWidget(_section_label('Hex dump'))
        self._hex_edit = QPlainTextEdit()
        self._hex_edit.setReadOnly(True)
        self._hex_edit.setFont(_mono_font(10))
        self._hex_edit.setMinimumHeight(80)
        self._hex_edit.setMaximumHeight(200)
        self._hex_edit.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._hex_edit)

        root.addStretch()
        self._show_empty()

    def show_record(self, sf: StructuredField) -> None:
        dark = self._is_dark()
        t = _DARK if dark else _LIGHT
        base     = _chip_qcolor(sf.name, dark)
        text_c   = base.name()
        bg_c     = f'rgba({base.red()},{base.green()},{base.blue()},46)'
        border_c = f'rgba({base.red()},{base.green()},{base.blue()},89)'

        disp = _display_name(sf)
        self._chip_lbl.setText(disp)
        self._chip_lbl.setStyleSheet(
            f'background: {bg_c}; color: {text_c};'
            f'border: 0.5px solid {border_c}; border-radius: 3px;'
            f'padding: 0 5px; font-weight: 700;')

        self._name_lbl.setText(_SF_FULL_NAMES.get(sf.name, '') or sf.name)

        self._offset_lbl.setText(f'Offset  0x{sf.offset:08X}')
        self._offset_lbl.setStyleSheet(f'color: {t["fg3"]}; font-size: 11px;')
        self._length_lbl.setText(f'Length  {len(sf.data)} bytes')
        self._length_lbl.setStyleSheet(f'color: {t["fg3"]}; font-size: 11px;')

        desc = _sf_description(sf)
        self._params_lbl.setText(desc or '—')
        self._params_lbl.setStyleSheet(f'color: {t["fg2"]}; font-size: 11px;')

        dump = _format_hex_dump(sf.data, sf.offset) if sf.data else '(no data)'
        self._hex_edit.setPlainText(dump)
        self._hex_edit.setStyleSheet(
            f'background: {t["bg_elev"]}; color: {t["fg"]};'
            f'border: 0.5px solid {t["line"]}; border-radius: 6px; padding: 6px;')

    def _show_empty(self) -> None:
        self._chip_lbl.clear()
        self._chip_lbl.setStyleSheet('')
        self._name_lbl.setText('')
        self._offset_lbl.setText('')
        self._length_lbl.setText('')
        self._params_lbl.setText('Select a record from the tree above to inspect its parsed fields and raw bytes.')
        self._hex_edit.clear()

    def apply_theme(self, dark: bool) -> None:
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(f'background: {t["bg"]};')
        self._hex_edit.setStyleSheet(
            f'background: {t["bg_elev"]}; color: {t["fg"]};'
            f'border: 0.5px solid {t["line"]}; border-radius: 6px; padding: 6px;')
        self._params_lbl.setStyleSheet(f'color: {t["fg2"]}; font-size: 11px;')


# ─────────────────────────────────────────────────────────────────────────────
# InspectorPanel — matches inspector.jsx Inspector component
# Fixed 360px, bg_sidebar, header + search, tree (50%), details (50%)
# ─────────────────────────────────────────────────────────────────────────────

class InspectorPanel(QWidget):
    collapse_requested = pyqtSignal()

    def __init__(self, is_dark_fn, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark_fn
        self._dark = False
        self.setFixedWidth(360)
        self._doc: Optional['AFPDocument'] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setObjectName('inspector_hdr')
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(12, 10, 12, 8)
        hl.setSpacing(6)

        top_row = QWidget()
        tr = QHBoxLayout(top_row)
        tr.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel('Structured records')
        self._title.setStyleSheet('font-size: 13px; font-weight: 700; background: transparent;')
        tr.addWidget(self._title)
        tr.addStretch()
        # Collapse toggle button
        self._btn_collapse = QPushButton('›')
        self._btn_collapse.setFixedSize(20, 20)
        self._btn_collapse.setStyleSheet(
            'QPushButton { font-size: 13px; background: transparent; border: none;'
            '  color: #7A7988; padding: 0; }'
            'QPushButton:hover { color: #1C1B28; }')
        self._btn_collapse.setToolTip('Collapse panel')
        self._btn_collapse.clicked.connect(self.collapse_requested.emit)
        tr.addWidget(self._btn_collapse)
        hl.addWidget(top_row)

        # "N records · N bytes"
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet('font-size: 11px; background: transparent;')
        hl.addWidget(self._count_lbl)

        # Search input with magnifier icon
        self._search = QLineEdit()
        self._search.setPlaceholderText('Filter by type (BPG, PTX, BIM…)')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter_changed)
        # Add magnifier icon on the left
        from PyQt6.QtGui import QIcon as _QIcon
        search_icon_pix = QPixmap(14, 14)
        search_icon_pix.fill(Qt.GlobalColor.transparent)
        _sp = QPainter(search_icon_pix)
        _sp.setRenderHint(QPainter.RenderHint.Antialiasing)
        _sp.setPen(QPen(QColor('#9E9DAB'), 1.3))
        _sp.setBrush(Qt.BrushStyle.NoBrush)
        _sp.drawEllipse(1, 1, 8, 8)
        _sp.drawLine(8, 8, 13, 13)
        _sp.end()
        self._search.addAction(
            _QIcon(search_icon_pix),
            QLineEdit.ActionPosition.LeadingPosition
        )
        self._search.setFixedHeight(30)
        hl.addWidget(self._search)

        root.addWidget(hdr)

        self._hdr_sep = QFrame()
        self._hdr_sep.setFrameShape(QFrame.Shape.HLine)
        self._hdr_sep.setFixedHeight(1)
        root.addWidget(self._hdr_sep)

        # ── Splitter: tree (flex 1) / details (flex 1) ────────────────────
        self._split = QSplitter(Qt.Orientation.Vertical)
        self._split.setHandleWidth(1)
        self._split.setChildrenCollapsible(False)

        self._tree = InspectorTree(is_dark_fn)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        self._split.addWidget(self._tree)

        self._details_scroll = QScrollArea()
        self._details_scroll.setWidgetResizable(True)
        self._details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._details = RecordDetailsPanel(is_dark_fn)
        self._details_scroll.setWidget(self._details)
        self._split.addWidget(self._details_scroll)
        self._split.setSizes([300, 300])

        root.addWidget(self._split, 1)

    def populate(self, doc: 'AFPDocument') -> None:
        self._doc = doc
        n = len(doc.all_fields)
        total = sum(len(sf.data) for sf in doc.all_fields)
        self._count_lbl.setText(f'{n} records · {total:,} bytes')
        self._tree.populate(doc)
        self._details._show_empty()

    def _on_filter_changed(self, text: str) -> None:
        if self._doc:
            self._tree.populate(self._doc, text)

    def _on_selection(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        sf = items[0].data(0, _SF_ROLE)
        if sf is not None:
            self._details.show_record(sf)

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(
            f'#inspector_hdr {{ background: {t["bg_sidebar"]}; }}'
            f'QWidget {{ background: {t["bg_sidebar"]}; color: {t["fg"]}; }}'
        )
        self._hdr_sep.setStyleSheet(f'background: {t["line"]};')
        self._count_lbl.setStyleSheet(f'font-size: 11px; color: {t["fg3"]}; background: transparent;')
        self._title.setStyleSheet(f'font-size: 13px; font-weight: 700; color: {t["fg"]}; background: transparent;')
        self._btn_collapse.setStyleSheet(
            f'QPushButton {{ font-size: 13px; background: transparent; border: none;'
            f'  color: {t["fg3"]}; padding: 0; }}'
            f'QPushButton:hover {{ color: {t["fg"]}; }}'
        )
        self._search.setStyleSheet(
            f'QLineEdit {{ background: {t["bg_elev"]}; color: {t["fg"]};'
            f'  border: 0.5px solid {t["line"]}; border-radius: 6px;'
            f'  padding: 5px 8px 5px 28px; font-size: 12px; }}'
            f'QLineEdit:focus {{ border-color: {t["accent"]}; }}'
        )
        self._split.setStyleSheet(
            f'QSplitter::handle {{ background: {t["line"]}; }}')
        self._tree.apply_theme(dark)
        self._details.apply_theme(dark)
        self._details_scroll.setStyleSheet(
            f'QScrollArea {{ background: {t["bg"]}; border: none; }}'
            f'QScrollBar:vertical {{ background: transparent; width: 8px; }}'
            f'QScrollBar::handle:vertical {{ background: {t["line"]}; border-radius: 4px; }}'
        )


# ─────────────────────────────────────────────────────────────────────────────
# StructurePanel — full-width inspector (Structure view, index 1 in stack)
# ─────────────────────────────────────────────────────────────────────────────

class StructurePanel(QWidget):
    def __init__(self, is_dark_fn, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark_fn

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QWidget()
        hdr.setObjectName('struct_hdr')
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 8, 14, 8)

        self._title = QLabel('AFP Structure')
        self._title.setStyleSheet('font-size: 12px; font-weight: 700;')
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet('font-size: 10px;')

        self._search = QLineEdit()
        self._search.setPlaceholderText('Filter by type or name…')
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(260)
        self._search.textChanged.connect(self._on_filter)

        hl.addWidget(self._title)
        hl.addSpacing(12)
        hl.addWidget(self._count_lbl)
        hl.addStretch()
        hl.addWidget(self._search)
        root.addWidget(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        root.addWidget(sep)
        self._sep = sep

        self._tree = InspectorTree(is_dark_fn)
        root.addWidget(self._tree, 1)
        self._doc: Optional['AFPDocument'] = None

    def populate(self, doc: 'AFPDocument') -> None:
        self._doc = doc
        n = len(doc.all_fields)
        total = sum(len(sf.data) for sf in doc.all_fields)
        self._count_lbl.setText(f'{n} structured fields  ·  {total:,} bytes total')
        self._tree.populate(doc)

    def _on_filter(self, text: str) -> None:
        if self._doc:
            self._tree.populate(self._doc, text)

    def apply_theme(self, dark: bool) -> None:
        t = _DARK if dark else _LIGHT
        self.setStyleSheet(
            f'#struct_hdr {{ background: {t["bg_toolbar"]}; }}'
            f'QWidget {{ background: {t["bg_sidebar"]}; }}'
        )
        self._title.setStyleSheet(f'font-size: 12px; font-weight: 700; color: {t["fg"]};')
        self._sep.setStyleSheet(f'background: {t["line"]};')
        self._count_lbl.setStyleSheet(f'font-size: 10px; color: {t["fg3"]};')
        self._search.setStyleSheet(
            f'QLineEdit {{ background: {t["bg_elev"]}; color: {t["fg"]};'
            f'  border: 0.5px solid {t["line"]}; border-radius: 6px;'
            f'  padding: 4px 8px; font-size: 12px; }}'
        )
        self._tree.apply_theme(dark)


# ─────────────────────────────────────────────────────────────────────────────
# MainWindow — native macOS window, matches app.jsx shell
# Toolbar: Open | nav (⏮◀ N/M ▶⏭) | zoom (− 100% +) | Page/Structure |
#          Inspector | Thumbnails | Dark
# Body stack: 0=3-pane, 1=structure, 2=welcome
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    _VIEW_PAGE      = 0
    _VIEW_STRUCTURE = 1
    _VIEW_WELCOME   = 2

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('AFP Viewer')
        self.setMinimumSize(960, 700)
        self.resize(1280, 840)
        self.setAcceptDrops(True)

        self._dark = False
        self._doc: Optional[AFPDocument] = None
        self._pixmaps: List[QPixmap] = []
        self._current = 0
        self._zoom = 1.0
        self._loader: Optional[LoadThread] = None
        self._current_view = self._VIEW_WELCOME

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._apply_theme()
        self._set_nav_enabled(False)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._view_stack = QStackedWidget()

        # Index 0: 3-pane (ThumbnailPanel | PageViewerPanel | InspectorPanel)
        page_view = QWidget()
        pv = QHBoxLayout(page_view)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self._thumb = ThumbnailPanel()
        self._thumb.page_selected.connect(self._on_thumb_select)
        pv.addWidget(self._thumb)

        self._thumb_sep = _vline()
        pv.addWidget(self._thumb_sep)

        self._viewer = PageViewerPanel()
        pv.addWidget(self._viewer, 1)

        self._inspector_sep = _vline()
        pv.addWidget(self._inspector_sep)

        self._inspector = InspectorPanel(lambda: self._dark)
        self._inspector.collapse_requested.connect(self._toggle_inspector)
        pv.addWidget(self._inspector)

        self._view_stack.addWidget(page_view)        # 0

        # Index 1: full-width structure panel
        self._structure = StructurePanel(lambda: self._dark)
        self._view_stack.addWidget(self._structure)  # 1

        # Index 2: welcome screen
        self._welcome = WelcomeWidget()
        self._welcome.open_requested.connect(self.open_file_dialog)
        self._welcome.file_opened.connect(self._open_file)
        self._view_stack.addWidget(self._welcome)    # 2

        self._view_stack.setCurrentIndex(self._VIEW_WELCOME)
        root.addWidget(self._view_stack, 1)

        # Status bar — single permanent content widget owns the full layout.
        # This avoids showMessage/addWidget overlap issues entirely.
        self._status = QStatusBar()
        self._status.setSizeGripEnabled(False)
        self.setStatusBar(self._status)

        # One widget fills 100% of the status bar
        _sb_content = QWidget()
        _sb_layout = QHBoxLayout(_sb_content)
        _sb_layout.setContentsMargins(8, 0, 8, 0)
        _sb_layout.setSpacing(0)

        # Left group: message / format · dpi · producer
        self._sb_msg = QLabel('Drop an AFP file here or use File → Open')
        _sb_layout.addWidget(self._sb_msg)

        self._sb_format = QLabel('')
        self._sb_dpi    = QLabel('')
        self._sb_producer = QLabel('')
        for lbl in (self._sb_format, self._sb_dpi, self._sb_producer):
            lbl.setVisible(False)
            _sb_layout.addWidget(lbl)

        # Progress bar (shown while rendering)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        self._progress.setFixedWidth(200)
        _sb_layout.addWidget(self._progress)

        # Elastic spacer
        _sb_layout.addStretch(1)

        # Right group: dim · zoom · page (always on the right)
        self._sb_dim       = QLabel('')
        self._sb_zoom_lbl  = QLabel('')
        self._page_pos_lbl = QLabel('')
        self._zoom_lbl = self._sb_zoom_lbl  # alias used elsewhere
        for lbl in (self._sb_dim, self._sb_zoom_lbl, self._page_pos_lbl):
            lbl.setVisible(False)
            _sb_layout.addWidget(lbl)

        self._status.addPermanentWidget(_sb_content, 1)  # stretch=1 fills all space

    def _build_menus(self) -> None:
        mb = self.menuBar()
        mb.setNativeMenuBar(True)

        file_menu = mb.addMenu('&File')
        open_act = QAction('&Open File…', self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        exit_act = QAction('E&xit', self)
        exit_act.setShortcut(QKeySequence.StandardKey.Quit)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        view_menu = mb.addMenu('&View')
        for label, key, fn in [
            ('&Page View',     Qt.Key.Key_1, lambda: self._switch_view(self._VIEW_PAGE)),
            ('AFP &Structure', Qt.Key.Key_2, lambda: self._switch_view(self._VIEW_STRUCTURE)),
        ]:
            a = QAction(label, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            view_menu.addAction(a)
        view_menu.addSeparator()

        dark_act = QAction('Toggle &Dark Mode', self)
        dark_act.triggered.connect(self._toggle_dark)
        view_menu.addAction(dark_act)
        view_menu.addSeparator()

        for label, key, fn in [
            ('Zoom &In',    Qt.Key.Key_Equal, self._zoom_in),
            ('Zoom &Out',   Qt.Key.Key_Minus, self._zoom_out),
            ('&Reset Zoom', Qt.Key.Key_0,     self._zoom_reset),
            ('&Fit Window', Qt.Key.Key_F,     self._zoom_fit),
        ]:
            a = QAction(label, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            view_menu.addAction(a)

        nav_menu = mb.addMenu('&Navigate')
        for label, key, fn in [
            ('&Previous Page', Qt.Key.Key_Left,  self.prev_page),
            ('&Next Page',     Qt.Key.Key_Right, self.next_page),
            ('&First Page',    Qt.Key.Key_Home,  self.first_page),
            ('&Last Page',     Qt.Key.Key_End,   self.last_page),
        ]:
            a = QAction(label, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            nav_menu.addAction(a)

    def _build_toolbar(self) -> None:
        tb = QToolBar('Main')
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setFixedHeight(52)
        self.addToolBar(tb)
        self._tb = tb

        # ── Open (folder icon) ────────────────────────────────────────────
        self._btn_open = _icon_btn('open')
        self._btn_open.setToolTip('Open file  ⌘O')
        self._btn_open.clicked.connect(self.open_file_dialog)
        tb.addWidget(self._btn_open)
        tb.addWidget(_tb_sep())

        # ── DocSwitcher (file icon + name + info + chevron) ───────────────
        self._doc_switcher = _DocSwitcher(self)
        self._doc_switcher.open_new.connect(self.open_file_dialog)
        tb.addWidget(self._doc_switcher)

        # ── Flex spacer → pushes nav/zoom to centre ───────────────────────
        spacer_l = QWidget()
        spacer_l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer_l)

        # ── Page nav: ‹ [N / M] › ────────────────────────────────────────
        self._btn_prev = _icon_btn('prev')
        self._btn_prev.setToolTip('Previous page  ←')
        self._btn_prev.clicked.connect(self.prev_page)
        tb.addWidget(self._btn_prev)

        self._page_ind = QLabel('—')
        self._page_ind.setFixedWidth(70)
        self._page_ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_ind.setStyleSheet(
            'font-size: 12px; font-variant-numeric: tabular-nums;'
            'color: #4B4A58; background: transparent; border: none;')
        tb.addWidget(self._page_ind)

        self._btn_next = _icon_btn('next')
        self._btn_next.setToolTip('Next page  →')
        self._btn_next.clicked.connect(self.next_page)
        tb.addWidget(self._btn_next)
        tb.addWidget(_tb_sep())

        # ── Zoom: − [Fit Page ▾] + ───────────────────────────────────────
        self._btn_zoom_out = _icon_btn('minus')
        self._btn_zoom_out.setToolTip('Zoom out  ⌘−')
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        tb.addWidget(self._btn_zoom_out)

        self._btn_zoom_label = QPushButton('Fit Page ▾')
        self._btn_zoom_label.setFixedSize(96, 28)
        self._btn_zoom_label.setToolTip('Zoom preset')
        self._btn_zoom_label.clicked.connect(self._show_zoom_menu)
        self._btn_zoom_label.setStyleSheet(
            'QPushButton { font-size: 12px; border-radius: 6px; padding: 0 8px;'
            '  border: 0.5px solid rgba(0,0,0,0.18); }'
            'QPushButton:hover { background: rgba(0,0,0,0.06); }'
        )
        tb.addWidget(self._btn_zoom_label)

        self._btn_zoom_in = _icon_btn('plus')
        self._btn_zoom_in.setToolTip('Zoom in  ⌘=')
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        tb.addWidget(self._btn_zoom_in)

        spacer_r = QWidget()
        spacer_r.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer_r)

        tb.addWidget(_tb_sep())

        # ── View toggles: Page | Structure ───────────────────────────────
        self._btn_page_view = _icon_btn('page_view', checkable=True)
        self._btn_page_view.setToolTip('Page view  1')
        self._btn_page_view.clicked.connect(lambda: self._switch_view(self._VIEW_PAGE))
        tb.addWidget(self._btn_page_view)

        self._btn_struct_view = _icon_btn('structure', checkable=True)
        self._btn_struct_view.setToolTip('AFP structure  2')
        self._btn_struct_view.clicked.connect(lambda: self._switch_view(self._VIEW_STRUCTURE))
        tb.addWidget(self._btn_struct_view)
        tb.addWidget(_tb_sep())

        # ── Inspector + Thumbnails toggles ────────────────────────────────
        self._btn_thumbs = _icon_btn('thumbs', checkable=True)
        self._btn_thumbs.setToolTip('Toggle thumbnails')
        self._btn_thumbs.setChecked(True)
        self._btn_thumbs.clicked.connect(self._toggle_thumbs)
        tb.addWidget(self._btn_thumbs)

        self._btn_inspector = _icon_btn('inspector', checkable=True)
        self._btn_inspector.setToolTip('Toggle inspector')
        self._btn_inspector.setChecked(True)
        self._btn_inspector.clicked.connect(self._toggle_inspector)
        tb.addWidget(self._btn_inspector)
        tb.addWidget(_tb_sep())

        # ── Dark mode ─────────────────────────────────────────────────────
        self._btn_dark = _icon_btn('dark', checkable=True)
        self._btn_dark.setToolTip('Toggle dark mode')
        self._btn_dark.clicked.connect(self._toggle_dark)
        tb.addWidget(self._btn_dark)

    def _show_zoom_menu(self) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for label, fn in [
            ('50%',      lambda: self._set_zoom(0.5)),
            ('75%',      lambda: self._set_zoom(0.75)),
            ('100%',     lambda: self._set_zoom(1.0)),
            ('125%',     lambda: self._set_zoom(1.25)),
            ('150%',     lambda: self._set_zoom(1.5)),
            ('200%',     lambda: self._set_zoom(2.0)),
            ('Fit Page',  lambda: self._zoom_fit('Fit Page')),
            ('Fit Width', lambda: self._zoom_fit('Fit Width')),
        ]:
            menu.addAction(label, fn)
        menu.exec(self._btn_zoom_label.mapToGlobal(
            self._btn_zoom_label.rect().bottomLeft()))

    def _set_zoom(self, z: float) -> None:
        self._zoom = z
        if self._pixmaps:
            self._viewer.canvas.set_zoom(z)
        self._update_zoom_label()

    # ── Theme ─────────────────────────────────────────────────────────────

    def _toggle_dark(self) -> None:
        self._dark = not self._dark
        self._btn_dark.setChecked(self._dark)
        self._apply_theme()

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        self._btn_dark.setChecked(dark)
        self._apply_theme()

    def _apply_theme(self) -> None:
        dark = self._dark
        t = _DARK if dark else _LIGHT

        app_ss = f'''
            QMainWindow, QWidget {{
                background: {t["bg"]};
                color: {t["fg"]};
                font-family: -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif;
            }}
            QMenuBar {{
                background: {t["bg_toolbar"]};
                color: {t["fg"]};
                border-bottom: 0.5px solid {t["line"]};
                font-size: 13px;
            }}
            QMenuBar::item:selected {{ background: {t["bg_hover"]}; }}
            QMenu {{
                background: {t["bg_elev"]};
                color: {t["fg"]};
                border: 0.5px solid {t["line"]};
            }}
            QMenu::item:selected {{ background: {t["accent"]}; color: #fff; }}
            QToolBar {{
                background: {t["bg_toolbar"]};
                border-bottom: 0.5px solid {t["line"]};
                spacing: 2px;
                padding: 4px 6px;
            }}
            QStatusBar {{
                background: {t["bg_toolbar"]};
                color: {t["fg3"]};
                border-top: 0.5px solid {t["line"]};
                font-size: 11px;
            }}
            QProgressBar {{
                background: {t["bg_elev"]};
                border: 0.5px solid {t["line"]};
                border-radius: 4px;
                height: 14px;
                text-align: center;
                font-size: 10px;
            }}
            QProgressBar::chunk {{
                background: {t["accent"]};
                border-radius: 3px;
            }}
            QSplitter::handle {{ background: {t["line"]}; }}
        '''
        QApplication.instance().setStyleSheet(app_ss)

        btn_ss = (
            f'QPushButton {{'
            f'  background: transparent; color: {t["fg2"]};'
            f'  border: none; border-radius: 6px;'
            f'  font-size: 12px;'
            f'}}'
            f'QPushButton:hover {{ background: {t["bg_hover"]}; }}'
            f'QPushButton:checked {{ background: {t["accent_soft"]}; color: {t["accent"]}; }}'
            f'QPushButton:disabled {{ color: {t["fg4"]}; }}'
        )
        for btn in self.findChildren(QPushButton):
            btn.setStyleSheet(btn_ss)

        self._page_ind.setStyleSheet(
            f'font-size: 12px; color: {t["fg2"]}; font-variant-numeric: tabular-nums;'
            'background: transparent; border: none;')
        sb_lbl_ss = f'color: {t["fg3"]}; font-size: 11px; background: transparent;'
        for lbl in (self._sb_msg, self._sb_format, self._sb_dpi, self._sb_producer,
                    self._sb_dim, self._sb_zoom_lbl, self._page_pos_lbl):
            lbl.setStyleSheet(sb_lbl_ss)

        for sep in self.findChildren(QFrame):
            if sep.frameShape() == QFrame.Shape.VLine:
                sep.setStyleSheet(f'background: {t["line"]};')

        self._thumb.apply_theme(dark)
        self._viewer.apply_theme(dark)
        self._inspector.apply_theme(dark)
        self._structure.apply_theme(dark)
        self._welcome.apply_theme(dark)
        self._doc_switcher.apply_theme(dark)

        # Re-render SVG icon buttons for new theme
        for btn in self.findChildren(QPushButton):
            if hasattr(btn, '_icon_name'):
                _render_icon_btn(btn, dark)

        self._status.setStyleSheet(
            f'QStatusBar {{ background: {t["bg_toolbar"]}; color: {t["fg3"]}; '
            f'border-top: 0.5px solid {t["line"]}; font-size: 11px; }}'
        )

    # ── View switching ────────────────────────────────────────────────────

    def _switch_view(self, view_idx: int) -> None:
        if not self._doc:
            return
        self._current_view = view_idx
        self._view_stack.setCurrentIndex(view_idx)
        self._btn_page_view.setChecked(view_idx == self._VIEW_PAGE)
        self._btn_struct_view.setChecked(view_idx == self._VIEW_STRUCTURE)

    def _toggle_inspector(self) -> None:
        vis = self._btn_inspector.isChecked()
        self._inspector.setVisible(vis)
        self._inspector_sep.setVisible(vis)

    def _toggle_thumbs(self) -> None:
        vis = self._btn_thumbs.isChecked()
        self._thumb.setVisible(vis)
        self._thumb_sep.setVisible(vis)

    # ── File loading ──────────────────────────────────────────────────────

    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open AFP File', str(Path.home()),
            'AFP Files (*.afp *.AFP *.listafp *.lst *.mod *.spl);;All Files (*)'
        )
        if path:
            self._open_file(path)

    def _open_file(self, path: str) -> None:
        self._save_recent(path)
        self.load_file(path)

    def _save_recent(self, path: str) -> None:
        settings = QSettings('AFPViewer', 'RecentFiles')
        recents = list(settings.value('recent_files', []) or [])
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        settings.setValue('recent_files', recents[:20])

    def load_file(self, filepath: str) -> None:
        if not filepath or not os.path.isfile(filepath):
            self._sb_msg.setText(f'File not found: {filepath!r}')
            return

        if self._loader and self._loader.isRunning():
            self._loader.terminate()
            self._loader.wait()

        self.setWindowTitle(f'AFP Viewer — {Path(filepath).name}')
        self._sb_msg.setText(f'Loading …')
        self._sb_msg.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        # Hide metadata labels while loading
        for lbl in (self._sb_format, self._sb_dpi, self._sb_producer,
                    self._sb_dim, self._sb_zoom_lbl, self._page_pos_lbl):
            lbl.setVisible(False)
        self._set_nav_enabled(False)
        self._thumb.set_pages([])
        self._pixmaps = []
        self._doc = None

        self._loader = LoadThread(filepath)
        self._loader.progress.connect(self._on_load_progress)
        self._loader.done.connect(self._on_load_done)
        self._loader.error.connect(self._on_load_error)
        self._loader.start()

    def _on_load_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._sb_msg.setText(f'Rendering page {current} / {total} …')

    def _on_load_done(self, doc: AFPDocument, pixmaps: list) -> None:
        self._doc = doc
        self._pixmaps = pixmaps
        self._progress.setVisible(False)
        self._sb_msg.setVisible(False)  # hide loading message

        if pixmaps:
            self._current = 0
            self._thumb.set_pages(pixmaps)
            self._show_page(0)
            self._set_nav_enabled(True)

        self._inspector.populate(doc)
        self._structure.populate(doc)
        self._welcome.refresh_recents()

        fname = Path(doc.filepath).name
        size_bytes = os.path.getsize(doc.filepath) if os.path.isfile(doc.filepath) else 0
        self._doc_switcher.set_doc(fname, len(doc.pages), size_bytes)

        # Left: format · dpi
        dot = '  ·  '
        from src.renderer import DEFAULT_DPI
        self._sb_format.setText('AFP/MO:DCA-PS')
        self._sb_dpi.setText(dot + f'{DEFAULT_DPI} dpi')
        self._sb_format.setVisible(True)
        self._sb_dpi.setVisible(True)
        self._sb_producer.setVisible(False)  # BDT name is internal, not meaningful

        # Right: dim · zoom · page (populated by _show_page)
        for lbl in (self._sb_dim, self._sb_zoom_lbl, self._page_pos_lbl):
            lbl.setVisible(True)

        self._view_stack.setCurrentIndex(self._VIEW_PAGE)
        self._btn_page_view.setChecked(True)
        self._btn_struct_view.setChecked(False)
        self._current_view = self._VIEW_PAGE

        # Defer fit-page so the layout has settled and viewport size is correct
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._zoom_fit('Fit Page'))

    def _on_load_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._sb_msg.setText('Error loading file')
        QMessageBox.critical(self, 'Load Error', msg)

    # ── Page display ──────────────────────────────────────────────────────

    def _show_page(self, idx: int) -> None:
        if not self._pixmaps or idx < 0 or idx >= len(self._pixmaps):
            return
        self._current = idx
        self._viewer.canvas.set_pixmap(self._pixmaps[idx])
        self._viewer.canvas.set_zoom(self._zoom)
        self._thumb.set_current(idx)

        total = len(self._pixmaps)
        self._page_ind.setText(f'  {idx + 1} / {total}  ')
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < total - 1)

        # Status bar right: dimensions · zoom · page
        dot = '  ·  '
        page = self._doc.pages[idx] if self._doc and idx < len(self._doc.pages) else None
        if page and (page.width_lu or page.height_lu):
            # AFP L-units: 1440 per inch
            w_in = page.width_lu / 1440
            h_in = page.height_lu / 1440
            dim_str = f'{w_in:.1f} × {h_in:.1f} in'
        elif self._pixmaps:
            # Fallback: use rendered pixel size at 150 dpi
            px = self._pixmaps[idx]
            dim_str = f'{px.width() / 150:.1f} × {px.height() / 150:.1f} in'
        else:
            dim_str = ''
        zoom_label = self._btn_zoom_label.text().replace(' ▾', '').strip()
        self._sb_dim.setText(dim_str)
        self._sb_zoom_lbl.setText(dot + zoom_label if zoom_label else '')
        self._page_pos_lbl.setText(dot + f'Page {idx + 1} of {total}')

    def _on_thumb_select(self, idx: int) -> None:
        if idx != self._current:
            self._show_page(idx)

    # ── Navigation ────────────────────────────────────────────────────────

    def prev_page(self) -> None:
        if self._current > 0:
            self._show_page(self._current - 1)

    def next_page(self) -> None:
        if self._doc and self._current < len(self._pixmaps) - 1:
            self._show_page(self._current + 1)

    def first_page(self) -> None:
        if self._pixmaps:
            self._show_page(0)

    def last_page(self) -> None:
        if self._pixmaps:
            self._show_page(len(self._pixmaps) - 1)

    # ── Zoom ──────────────────────────────────────────────────────────────

    def _zoom_in(self) -> None:
        self._zoom = min(4.0, round(self._zoom + 0.1, 2))
        self._apply_zoom()

    def _zoom_out(self) -> None:
        self._zoom = max(0.2, round(self._zoom - 0.1, 2))
        self._apply_zoom()

    def _zoom_reset(self) -> None:
        self._zoom = 1.0
        self._apply_zoom()

    def _zoom_fit(self, label: str = 'Fit Page') -> None:
        if not self._pixmaps:
            return
        vp = self._viewer.scroll.viewport()
        px = self._pixmaps[self._current]
        # Subtract 56px padding (28px each side) so the page has breathing room
        avail_w = max(1, vp.width()  - 56)
        avail_h = max(1, vp.height() - 56)
        scale = min(avail_w / px.width(), avail_h / px.height())
        self._zoom = round(max(0.05, scale), 3)
        self._viewer.canvas.set_zoom(self._zoom)
        self._btn_zoom_label.setText(label + ' ▾')
        dot = '  ·  '
        self._sb_zoom_lbl.setText(dot + label)

    def _update_zoom_label(self) -> None:
        label = f'{int(self._zoom * 100)}%'
        self._btn_zoom_label.setText(label + ' ▾')
        # Update status bar right zoom segment
        dot = '  ·  '
        self._sb_zoom_lbl.setText(dot + label)

    def _apply_zoom(self) -> None:
        self._update_zoom_label()
        if self._pixmaps:
            self._viewer.canvas.set_zoom(self._zoom)

    # ── Drag and drop ─────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path and os.path.isfile(path):
                    self._open_file(path)
                    return

    # ── Keyboard shortcuts ────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-apply fit if in a fit mode so the page tracks the new window size
        if self._pixmaps:
            label = self._btn_zoom_label.text().replace(' ▾', '').strip()
            if label in ('Fit Page', 'Fit Width'):
                self._zoom_fit(label)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.prev_page()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self.next_page()
        elif key == Qt.Key.Key_Home:
            self.first_page()
        elif key == Qt.Key.Key_End:
            self.last_page()
        elif key == Qt.Key.Key_1:
            self._switch_view(self._VIEW_PAGE)
        elif key == Qt.Key.Key_2:
            self._switch_view(self._VIEW_STRUCTURE)
        else:
            super().keyPressEvent(event)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_nav_enabled(self, enabled: bool) -> None:
        for w in (self._btn_prev, self._btn_next,
                  self._btn_zoom_in, self._btn_zoom_out):
            w.setEnabled(enabled)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mono_font(pt: int = 10) -> QFont:
    f = QFont('Menlo')
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(pt)
    return f


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        'font-size: 10px; font-weight: 700; letter-spacing: 1px;'
        'color: #7A7988; padding: 6px 0 2px 0;')
    return lbl


def _tb_btn(text: str, wide: bool = False, checkable: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(checkable)
    btn.setFixedHeight(28)
    if wide:
        btn.setMinimumWidth(52)
        btn.setMaximumWidth(110)
    else:
        btn.setFixedWidth(28)
    return btn


# SVG path data for toolbar icons (16x16 viewBox) — no arc commands
_ICON_PATHS: dict[str, str] = {
    # Folder: tab on left (M=tab top-left, diagonal to body, body rect)
    'open':      'M3,3 H6 L7.5,5 H13 V13 H3 Z',
    # Chevrons
    'prev':      'M10,3 L5,8 L10,13',
    'next':      'M6,3 L11,8 L6,13',
    # Zoom
    'minus':     'M4,8 L12,8',
    'plus':      'M4,8 L12,8 M8,4 L8,12',
    # Two-panel page view
    'page_view': 'M2,2 H9 V14 H2 Z M11,2 H14 V14 H11 Z',
    # Indented list lines (structure tree)
    'structure': 'M2,4 H14 M2,8 H11 M2,12 H13',
    # Thumbnail strip + main area
    'thumbs':    'M2,3 H5 V13 H2 Z M7,3 H14 V13 H7 Z',
    # Inspector: panel + detail lines
    'inspector': 'M2,2 H14 V14 H2 Z M10,2 V14 M11,5 H13 M11,8 H13',
    # Dark-mode toggle: sun circle + rays (polygon circle approx)
    'dark': (
        'M8,5 L6.3,5.5 L5,6.8 L4.5,8.5 L5,10.2 L6.3,11.5 L8,12 '
        'L9.7,11.5 L11,10.2 L11.5,8.5 L11,6.8 L9.7,5.5 Z '
        'M8,2 V3.5 M8,13.5 V12 M2,8.5 H3.5 M12.5,8.5 H14 '
        'M4.1,4.1 L5.2,5.2 M10.8,11.9 L11.9,10.8 '
        'M11.9,4.1 L10.8,5.2 M5.2,11.9 L4.1,10.8'
    ),
    'rulers':    'M2,5 H14 V11 H2 Z M4,5 V8 M6,5 V7 M8,5 V8 M10,5 V7 M12,5 V8',
}


def _icon_btn(name: str, checkable: bool = False) -> QPushButton:
    """Create a 32x32 icon-only QPushButton. Icon drawn from SVG path."""
    btn = QPushButton()
    btn.setFixedSize(32, 32)
    btn.setCheckable(checkable)
    btn.setToolTip(name)
    btn._icon_name = name  # type: ignore[attr-defined]
    _render_icon_btn(btn, dark=False)
    return btn


def _render_icon_btn(btn: QPushButton, dark: bool) -> None:
    """Draw the SVG path as a QIcon on the button (1× and 2× for Retina)."""
    from PyQt6.QtGui import QIcon, QPainterPath as _PP
    path_str = _ICON_PATHS.get(btn._icon_name, '')  # type: ignore[attr-defined]
    color = QColor('#F5F3FF' if dark else '#4B4A58')
    icon = QIcon()
    for sz in (16, 32):
        pix = QPixmap(sz, sz)
        pix.fill(Qt.GlobalColor.transparent)
        if path_str:
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            scale = sz / 16.0
            p.scale(scale, scale)
            pen = QPen(color, 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            pp = _PP()
            pp.setFillRule(Qt.FillRule.WindingFill)
            _parse_svg_path(pp, path_str)
            p.drawPath(pp)
            p.end()
        icon.addPixmap(pix)
    btn.setIcon(icon)
    btn.setIconSize(QSize(16, 16))


def _parse_svg_path(pp, d: str) -> None:
    """SVG path parser: M/L/H/V/Z/a/C/c commands."""
    import re
    tokens = re.findall(r'[MLHVZaCcz]|[-+]?[0-9]*\.?[0-9]+', d)
    i = 0
    cx, cy = 0.0, 0.0
    cmd = 'M'
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t; i += 1
            if cmd in ('Z', 'z'):
                pp.closeSubpath()
            continue
        if cmd == 'M':
            cx, cy = float(tokens[i]), float(tokens[i+1]); i += 2
            pp.moveTo(cx, cy)
            cmd = 'L'  # implicit lineTo after moveTo
        elif cmd == 'L':
            cx, cy = float(tokens[i]), float(tokens[i+1]); i += 2
            pp.lineTo(cx, cy)
        elif cmd == 'H':
            cx = float(tokens[i]); i += 1
            pp.lineTo(cx, cy)
        elif cmd == 'V':
            cy = float(tokens[i]); i += 1
            pp.lineTo(cx, cy)
        elif cmd in ('Z', 'z'):
            pp.closeSubpath()
        elif cmd == 'a':
            # relative arc — approximate corner arcs with quadratic bezier
            i += 5  # skip: rx, ry, rotation, large-arc, sweep
            dx, dy = float(tokens[i]), float(tokens[i+1]); i += 2
            ex, ey = cx + dx, cy + dy
            # Control point at the corner intersection (works for 90° corner arcs)
            if abs(dx) >= abs(dy):
                qx, qy = ex, cy   # move horizontally first
            else:
                qx, qy = cx, ey   # move vertically first
            pp.quadTo(qx, qy, ex, ey)
            cx, cy = ex, ey
        elif cmd == 'C':
            # absolute cubic bezier
            x1, y1 = float(tokens[i]), float(tokens[i+1])
            x2, y2 = float(tokens[i+2]), float(tokens[i+3])
            cx, cy = float(tokens[i+4]), float(tokens[i+5]); i += 6
            pp.cubicTo(x1, y1, x2, y2, cx, cy)
        elif cmd == 'c':
            # relative cubic bezier
            x1 = cx + float(tokens[i]); y1 = cy + float(tokens[i+1])
            x2 = cx + float(tokens[i+2]); y2 = cy + float(tokens[i+3])
            nx = cx + float(tokens[i+4]); ny = cy + float(tokens[i+5]); i += 6
            pp.cubicTo(x1, y1, x2, y2, nx, ny)
            cx, cy = nx, ny
        else:
            i += 1


class _DocSwitcher(QWidget):
    """Toolbar widget: mini AFP icon + filename + page/size info + chevron.
    Matches app.jsx DocSwitcher. Hidden when no file is open."""
    open_new = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(False)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(6, 0, 8, 0)
        hl.setSpacing(8)

        # Mini AFP file icon (22x26)
        self._icon = QLabel()
        self._icon.setPixmap(_make_afp_icon(22))
        self._icon.setStyleSheet('background: transparent;')
        hl.addWidget(self._icon)

        # Text column
        info = QWidget()
        info.setStyleSheet('background: transparent;')
        iv = QVBoxLayout(info)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            'font-size: 13px; font-weight: 600; background: transparent;')
        self._info_lbl = QLabel()
        self._info_lbl.setStyleSheet(
            'font-size: 10.5px; background: transparent;')
        iv.addWidget(self._name_lbl)
        iv.addWidget(self._info_lbl)
        hl.addWidget(info)

        # Chevron
        chev = QLabel('⌄')
        chev.setStyleSheet('font-size: 11px; background: transparent;')
        hl.addWidget(chev)
        self._chev = chev

        self.mousePressEvent = lambda _: self.open_new.emit()

    def set_doc(self, name: str, pages: int, size_bytes: int) -> None:
        self._name_lbl.setText(name)
        kb = size_bytes / 1024
        self._info_lbl.setText(f'{pages} page{"s" if pages != 1 else ""} · {kb:.1f} KB')
        self.setVisible(True)

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        t = _DARK if dark else _LIGHT
        self._info_lbl.setStyleSheet(
            f'font-size: 10.5px; color: {t["fg3"]}; background: transparent;')
        self._chev.setStyleSheet(
            f'font-size: 11px; color: {t["fg3"]}; background: transparent;')


def _tb_sep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(18)
    return sep


def _vline() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    return sep


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    qimg = QImage.fromData(buf.read())
    return QPixmap.fromImage(qimg)
