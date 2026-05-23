"""
AFP (Advanced Function Presentation) binary file parser.

AFP structured field format:
  Byte 0    : 0x5A sync byte
  Bytes 1-2 : Length (big-endian, includes these 2 bytes + remaining header + data)
  Bytes 3-5 : Structured Field Identifier (SFID)
  Byte 6    : Flags
  Bytes 7-8 : Reserved (0x0000)
  Bytes 9+  : Data  (length - 8 bytes)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Generator

SYNC_BYTE = 0x5A

# Map 3-byte SFID → human name
SFID_NAMES: Dict[bytes, str] = {
    b'\xD3\xA8\xA8': 'BDT',   # Begin Document
    b'\xD3\xA9\xA8': 'EDT',   # End Document
    b'\xD3\xA8\xAF': 'BPG',   # Begin Page
    b'\xD3\xA9\xAF': 'EPG',   # End Page
    b'\xD3\xA8\x89': 'BAG',   # Begin Active Environment Group
    b'\xD3\xA9\x89': 'EAG',   # End Active Environment Group
    b'\xD3\xAB\xAF': 'PGD',   # Page Descriptor
    b'\xD3\xEE\x9B': 'PTX',   # Presentation Text Data
    b'\xD3\xAB\x8A': 'MCF',   # Map Coded Font
    b'\xD3\xA8\x87': 'BIM',   # Begin Image Object
    b'\xD3\xA9\x87': 'EIM',   # End Image Object
    b'\xD3\xEE\xFB': 'IRD',   # Image Raster Data (IOCA)
    b'\xD3\xA6\xFB': 'IDD',   # Image Data Descriptor
    b'\xD3\xA8\xC6': 'BRG',   # Begin Resource Group
    b'\xD3\xA9\xC6': 'ERG',   # End Resource Group
    b'\xD3\xA8\xD3': 'BMO',   # Begin Medium Overlay
    b'\xD3\xA9\xD3': 'EMO',   # End Medium Overlay
    b'\xD3\xA8\xDF': 'BPS',   # Begin Page Segment
    b'\xD3\xA9\xDF': 'EPS',   # End Page Segment
    b'\xD3\xAB\xD8': 'MPO',   # Map Page Overlay
    b'\xD3\xA8\x88': 'BCF',   # Begin Coded Font
    b'\xD3\xA9\x88': 'ECF',   # End Coded Font
    b'\xD3\xA8\x8A': 'BFN',   # Begin Font
    b'\xD3\xA9\x8A': 'EFN',   # End Font
    b'\xD3\xA8\xFB': 'BII',   # Begin Inline Image
    b'\xD3\xA9\xFB': 'EII',   # End Inline Image
    b'\xD3\xAB\xDF': 'MPS',   # Map Page Segment
    b'\xD3\xA8\x6D': 'BOC',   # Begin Object Container
    b'\xD3\xA9\x6D': 'EOC',   # End Object Container
    b'\xD3\xEE\x6D': 'OCD',   # Object Container Data
    b'\xD3\xA8\xAD': 'BNG',   # Begin Named Group (pages)
    b'\xD3\xA9\xAD': 'ENG',   # End Named Group
    b'\xD3\xAB\xA8': 'DXD',   # Document Index
    b'\xD3\xA4\x88': 'FGD',   # Font Global Descriptor
    b'\xD3\xA4\x8C': 'FND',   # Font Patterns
    b'\xD3\xA4\x8A': 'FCD',   # Font Character Index
    b'\xD3\xAB\x8C': 'MMT',   # Map Media Type
    # ── Font resource fields ──────────────────────────────────────────────────
    b'\xD3\xA8\xCE': 'BCF2',  # Begin Coded Font (resource wrapper)
    b'\xD3\xA9\xCE': 'ECF2',  # End Coded Font (resource wrapper)
    b'\xD3\xA6\x89': 'CSD',   # Character Set Descriptor (typeface name at bytes 0-31)
    b'\xD3\xB1\x8A': 'MCF2',  # Map Coded Font-2 (local-ID → coded-font table)
    # ── Presentation Text Object ──────────────────────────────────────────────
    b'\xD3\xA8\x9B': 'BPT',   # Begin Presentation Text Object
    b'\xD3\xA9\x9B': 'EPT',   # End Presentation Text Object
    b'\xD3\xAB\x9B': 'PTD',   # Presentation Text Descriptor
    # ── Page / medium layout ──────────────────────────────────────────────────
    b'\xD3\xA6\xAF': 'PPA',   # Page Position Attribute
    b'\xD3\xAB\xC3': 'CPD',   # Copy Page Descriptor
    b'\xD3\xAF\xC3': 'MDR',   # Medium Data Record (page mapping)
    b'\xD3\xB1\x9B': 'IMM',   # Invoke Medium Map
    # ── Object groups ─────────────────────────────────────────────────────────
    b'\xD3\xA8\xC9': 'BOG',   # Begin Object Group
    b'\xD3\xA9\xC9': 'EOG',   # End Object Group
    # ── Object area ───────────────────────────────────────────────────────────
    b'\xD3\xA8\xC4': 'BOA',   # Begin Object Area
    b'\xD3\xA9\xC4': 'EOA',   # End Object Area
    b'\xD3\xAB\xC4': 'OAD',   # Object Area Descriptor
    # ── Bar code ──────────────────────────────────────────────────────────────
    b'\xD3\xA8\xBB': 'BBC',   # Begin Bar Code Object
    b'\xD3\xA9\xBB': 'EBC',   # End Bar Code Object
    # ── Outer print-stream wrapper ────────────────────────────────────────────
    b'\xD3\xA8\xA5': 'BPP',   # Begin Print Profile / Stream
    b'\xD3\xA9\xA5': 'EPP',   # End Print Profile / Stream
    # ── Font character-set resource data (inside BCF2 blocks) ────────────────
    b'\xD3\xEE\x89': 'FCS',   # Font Character Shapes (extended bitmap data)
    b'\xD3\xAE\x89': 'FAD',   # Font Attribute Data
    b'\xD3\xA7\x89': 'FCR',   # Font Character Reference
    b'\xD3\xA2\x89': 'FCA',   # Font Character Attribute
    b'\xD3\x8C\x89': 'FMP',   # Font Metrics Parameters
    b'\xD3\xAC\x89': 'FCM',   # Font Character Migration data
    # ── Image-object glyph descriptors ────────────────────────────────────────
    b'\xD3\x8C\x87': 'IGD',   # Image Glyph Descriptor
    b'\xD3\xA7\x87': 'IGA',   # Image Glyph Attribute
    # ── Coded-font binary resource data (type 0x92) ───────────────────────────
    b'\xD3\xA8\x92': 'BCO',   # Begin Coded Object
    b'\xD3\xA9\x92': 'ECO',   # End Coded Object
    b'\xD3\xEE\x92': 'FRD',   # Font Resource Data (extended)
    # ── Inline-object and object-container variants ───────────────────────────
    b'\xD3\xA6\x6B': 'IOI',   # Include Object Inline
    b'\xD3\xAC\x6B': 'OCM',   # Object Container Migration
    # ── Image-object descriptors ──────────────────────────────────────────────
    b'\xD3\xA6\x87': 'IDA',   # Image Descriptor Attribute
    b'\xD3\xAB\xFB': 'IID',   # Image Input Descriptor
}

# PTX control sequence function codes (short form, 1 byte)
PTX_FUNC = {
    0xD8: 'BLN',   # Begin Line
    0xD9: 'ELN',   # End Line
    0xD4: 'AMB',   # Absolute Move Baseline  (2 bytes: Y in L-units)
    0xC6: 'AMI',   # Absolute Move Inline    (2 bytes: X in L-units)
    0x4C: 'RMI',   # Relative Move Inline    (2 bytes signed)
    0x52: 'RMB',   # Relative Move Baseline  (2 bytes signed)
    0xF1: 'SCFL',  # Set Coded Font Local    (1 byte: font local ID)
    0xDB: 'TRN',   # Transparent Data        (N bytes: text)
    0xF8: 'NOP',   # No Operation
    0xF6: 'STO',   # Set Text Orientation    (4 bytes)
    0xC0: 'SIM',   # Set Inline Margin       (2 bytes)
    0xC8: 'SIA',   # Set Interchar Adjust    (2 bytes)
    0xC4: 'SVI',   # Set Variable Space Incr (2 bytes)
    0x80: 'SEC',   # Set Extended Color
    0x74: 'BSU',   # Begin Suppression
    0x78: 'ESU',   # End Suppression
}


@dataclass
class StructuredField:
    sfid: bytes
    name: str
    flags: int
    data: bytes
    offset: int  # byte offset in file


@dataclass
class TextRun:
    x: int          # L-units (1/1440 inch) from left
    y: int          # L-units from top
    raw: bytes      # raw bytes from TRN
    font_idx: int   # SCFL font local ID
    encoding: str   # detected encoding


@dataclass
class AFPPage:
    number: int
    fields: List[StructuredField] = field(default_factory=list)
    text_runs: List[TextRun] = field(default_factory=list)
    # Page dimensions in L-units; 0 = use default (letter)
    width_lu: int = 0
    height_lu: int = 0
    # Cursor state carried across PTX fields on this page
    _cur_x: int = 0
    _cur_y: int = 0
    _cur_font: int = 0
    # local-font-id → typeface string, e.g. 2 → 'TIMES-ROMAN'
    font_typefaces: Dict[int, str] = field(default_factory=dict)


@dataclass
class AFPDocument:
    name: str = ''
    filepath: str = ''
    pages: List[AFPPage] = field(default_factory=list)
    all_fields: List[StructuredField] = field(default_factory=list)
    raw_field_count: int = 0
    # coded-font-name (8-char) → typeface string, e.g. 'C0AAAAN1' → 'TIMES-ROMAN'
    coded_font_typefaces: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_afp_file(filepath: str) -> AFPDocument:
    with open(filepath, 'rb') as fh:
        raw = fh.read()

    doc = AFPDocument(filepath=filepath)
    fields = list(_iter_structured_fields(raw))
    doc.raw_field_count = len(fields)
    doc.all_fields = fields
    _build_document(fields, doc)
    return doc


# ---------------------------------------------------------------------------
# Structured-field iterator
# ---------------------------------------------------------------------------

def _iter_structured_fields(data: bytes) -> Generator[StructuredField, None, None]:
    i = 0
    n = len(data)
    while i < n:
        # Skip to next sync byte
        if data[i] != SYNC_BYTE:
            i += 1
            continue

        if i + 9 > n:
            break

        length = struct.unpack_from('>H', data, i + 1)[0]
        if length < 8:
            i += 1
            continue

        end = i + 1 + length
        if end > n:
            break

        sfid = data[i + 3: i + 6]
        flags = data[i + 6]
        sf_data = data[i + 9: end]
        name = SFID_NAMES.get(sfid, f'X{sfid.hex().upper()}')

        yield StructuredField(sfid=sfid, name=name, flags=flags, data=sf_data, offset=i)
        i = end


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------

def _build_document(fields: List[StructuredField], doc: AFPDocument) -> None:
    current_page: Optional[AFPPage] = None
    page_num = 0
    _current_coded_font: str = ''   # name of the BCF2 resource being defined

    for sf in fields:
        if sf.name == 'BDT':
            doc.name = _decode_name(sf.data, 8)

        elif sf.name == 'BPG':
            page_num += 1
            current_page = AFPPage(number=page_num)
            doc.pages.append(current_page)

        elif sf.name == 'EPG':
            if current_page:
                _resolve_page_encoding(current_page)
                # Stamp font typefaces discovered at doc level onto this page
                _resolve_page_font_typefaces(current_page, doc)
            current_page = None

        elif sf.name == 'PGD' and current_page:
            _parse_pgd(sf.data, current_page)
            current_page.fields.append(sf)

        elif sf.name == 'PTX' and current_page:
            _parse_ptx(sf.data, current_page)
            current_page.fields.append(sf)

        # ── MCF2: local-font-id table (appears inside AEG on each page) ──────
        elif sf.name == 'MCF2':
            if current_page:
                _parse_mcf2(sf.data, doc, current_page)

        # ── BCF2 / CSD: font resource definitions (usually in resource group) ─
        elif sf.name == 'BCF2':
            _current_coded_font = _decode_name(sf.data, 8)

        elif sf.name == 'CSD' and _current_coded_font:
            typeface = _parse_typeface_from_csd(sf.data)
            if typeface:
                doc.coded_font_typefaces[_current_coded_font] = typeface

        elif sf.name == 'ECF2':
            _current_coded_font = ''

        elif current_page:
            current_page.fields.append(sf)


# ---------------------------------------------------------------------------
# Font resource helpers
# ---------------------------------------------------------------------------

def _parse_typeface_from_csd(data: bytes) -> str:
    """
    Extract the typeface name from a Character Set Descriptor (CSD / D3A689).

    The first 32 bytes are the typeface name in EBCDIC, space-padded.
    An '@' suffix (e.g. 'TIMES-ROMAN@0') is stripped — it indicates encoding.
    """
    if len(data) < 1:
        return ''
    name_bytes = data[:32]
    for enc in ('cp500', 'cp037', 'latin-1'):
        try:
            name = name_bytes.decode(enc).strip('\x00 ')
            # Strip encoding qualifier after '@'
            name = name.split('@')[0].strip()
            if name and any(c.isalpha() for c in name):
                return name.upper()
        except Exception:
            continue
    return ''


def _parse_mcf2(data: bytes, doc: AFPDocument, page: AFPPage) -> None:
    """
    Parse a Map Coded Font-2 (MCF2 / D3B18A) field.

    Structure:
      Bytes 0-3 : header — byte 0 is the per-record size (typically 0x1C = 28)
      Then N records of <record_size> bytes each:
        Byte  0   : local font ID
        Bytes 1-3 : padding
        Bytes 4-11: code-page name (EBCDIC, often 0xFF = not specified)
        Bytes 12-19: character-set name (EBCDIC, 8 chars)
        Bytes 20-27: coded-font name   (EBCDIC, 8 chars)

    We map  local_id → coded_font_name → typeface (from doc.coded_font_typefaces).
    """
    if len(data) < 4:
        return
    rec_size = data[0]
    if rec_size < 20:
        return                  # sanity check

    i = 4                       # skip 4-byte header
    while i + rec_size <= len(data):
        local_id = data[i]
        coded_font_name = _decode_name(data[i + 20:], 8) if len(data) >= i + 28 else ''
        if coded_font_name and coded_font_name in doc.coded_font_typefaces:
            page.font_typefaces[local_id] = doc.coded_font_typefaces[coded_font_name]
        i += rec_size


def _resolve_page_font_typefaces(page: AFPPage, doc: AFPDocument) -> None:
    """
    If the page's MCF2 was processed before the resource group was fully
    scanned (shouldn't happen but just in case), do a second pass.
    Also handles files where the MCF2 is absent — no-op in that case.
    """
    for local_id, cf_name in list(page.font_typefaces.items()):
        # If value is still a coded-font name (not yet resolved), try now
        if cf_name in doc.coded_font_typefaces:
            page.font_typefaces[local_id] = doc.coded_font_typefaces[cf_name]


# ---------------------------------------------------------------------------
# Page Descriptor (PGD)
# ---------------------------------------------------------------------------

def _parse_pgd(data: bytes, page: AFPPage) -> None:
    # PGD structure (simplified):
    #   Offset 0-1: X-axis extent (width)  in L-units
    #   Offset 2-3: Y-axis extent (height) in L-units
    if len(data) >= 4:
        x = struct.unpack_from('>H', data, 0)[0]
        y = struct.unpack_from('>H', data, 2)[0]
        if x > 0:
            page.width_lu = x
        if y > 0:
            page.height_lu = y


# ---------------------------------------------------------------------------
# Presentation Text Data (PTX)
# ---------------------------------------------------------------------------

def _parse_ptx(data: bytes, page: AFPPage) -> None:
    i = 0
    n = len(data)

    while i < n:
        # Long form: 0x2B 0xD3 ...
        if i + 1 < n and data[i] == 0x2B and data[i + 1] == 0xD3:
            if i + 6 > n:
                break
            cs_len = struct.unpack_from('>H', data, i + 2)[0]
            if cs_len < 4 or i + 2 + cs_len > n:
                break
            func = struct.unpack_from('>H', data, i + 4)[0]
            cs_data = data[i + 6: i + 2 + cs_len]
            i += 2 + cs_len
            _apply_ptx_cs_long(func, cs_data, page)
            continue

        # Short form: length (1 byte, inclusive) + function (1 byte) + data
        if i + 2 > n:
            break
        cs_len = data[i]
        if cs_len < 2:
            i += 1
            continue
        if i + cs_len > n:
            break
        func = data[i + 1]
        cs_data = data[i + 2: i + cs_len]
        i += cs_len

        _apply_ptx_cs_short(func, cs_data, page)


def _apply_ptx_cs_short(func: int, cs_data: bytes, page: AFPPage) -> None:
    # ── Absolute positioning ──────────────────────────────────────────────────
    # Different AFP generators use different codes for the same command.
    # We accept all known variants so real-world files render correctly.

    if func in (0xD8, 0xDA):  # BLN / ELN — Begin / End Line
        pass

    # AMB — Absolute Move Baseline (Y position)
    elif func in (0xD4, 0xD3):
        if len(cs_data) >= 2:
            page._cur_y = struct.unpack_from('>H', cs_data, 0)[0]

    # AMI — Absolute Move Inline (X position)
    elif func in (0xC6, 0xC7):
        if len(cs_data) >= 2:
            page._cur_x = struct.unpack_from('>H', cs_data, 0)[0]

    # RMI — Relative Move Inline (signed delta X)
    elif func in (0x4C, 0xC4):
        if len(cs_data) >= 2:
            page._cur_x += struct.unpack_from('>h', cs_data, 0)[0]

    # RMB — Relative Move Baseline (signed delta Y)
    elif func in (0x52, 0xD2):
        if len(cs_data) >= 2:
            page._cur_y += struct.unpack_from('>h', cs_data, 0)[0]

    # SCFL — Set Coded Font Local
    elif func in (0xF1, 0xD0):
        if len(cs_data) >= 1:
            page._cur_font = cs_data[0]

    # TRN — Transparent Data (actual text bytes)
    elif func == 0xDB:
        if cs_data:
            page.text_runs.append(TextRun(
                x=page._cur_x,
                y=page._cur_y,
                raw=cs_data,
                font_idx=page._cur_font,
                encoding='',        # resolved per-page after all PTX parsed
            ))
            # Advance X: approximate 120 L-units per character (≈ 10 CPI)
            page._cur_x += len(cs_data) * 120


def _apply_ptx_cs_long(func: int, cs_data: bytes, page: AFPPage) -> None:
    # Long-form function codes: low byte mirrors the short-form code.
    short_func = func & 0xFF
    _apply_ptx_cs_short(short_func, cs_data, page)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_name(data: bytes, length: int) -> str:
    chunk = data[:length] if len(data) >= length else data
    for enc in ('cp500', 'cp037', 'ascii', 'latin-1'):
        try:
            return chunk.decode(enc).strip('\x00 ')
        except Exception:
            continue
    return chunk.hex()


def _resolve_page_encoding(page: AFPPage) -> None:
    """
    Determine one encoding for the whole page by pooling all TRN bytes, then
    stamp every run.  This avoids single-byte runs flipping to the wrong codec.

    Strategy (in order):
    1. UCS-2 Big Endian — if ≥ 35 % of even-indexed bytes are 0x00, the data
       is 2-byte Unicode (common in modern AFP generators, e.g. IBM InfoPrint).
    2. Latin-1 — if ≥ 70 % of bytes fall in ASCII printable range (0x20–0x7E).
    3. EBCDIC cp500 — the mainframe default for everything else.
    """
    pool = b''.join(r.raw for r in page.text_runs if len(r.raw) > 2)
    if not pool:
        enc = 'cp500'
    else:
        # Test for UCS-2 BE: alternating 0x00 bytes at even positions
        even_nulls = sum(1 for i, b in enumerate(pool) if i % 2 == 0 and b == 0x00)
        if even_nulls / len(pool) >= 0.35:
            enc = 'utf-16-be'
        else:
            ascii_frac = sum(1 for b in pool if 0x20 <= b <= 0x7E) / len(pool)
            enc = 'latin-1' if ascii_frac > 0.70 else 'cp500'

    for run in page.text_runs:
        run.encoding = enc
