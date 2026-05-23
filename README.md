# AFP Viewer

A macOS desktop application for viewing IBM Advanced Function Presentation (AFP) print files.

## Features

- Open and render AFP/MO:DCA print files (`.afp`, `.listafp`, `.spl`)
- Multi-page navigation with thumbnail strip
- Zoom controls (Fit Page, Fit Width, custom zoom levels)
- AFP structured field inspector — browse the internal record tree (BDT, BPG, BRG, PTX, BIM, MCF, PGD, etc.)
- Search/filter records in the inspector panel
- Status bar showing page dimensions, DPI, zoom level
- Light and dark mode support
- macOS-native feel (squircle app icon, system fonts)

## Requirements

- macOS 12.0 or later
- Python 3.11+
- PyQt6
- Pillow

## Installation

```bash
# Clone the repo
git clone https://github.com/ramkumarsun-group/afp-viewer.git
cd afp-viewer

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running

```bash
source venv/bin/activate
python main.py
```

Or use the convenience script:

```bash
./run.sh
```

## Building the macOS App Bundle

```bash
source venv/bin/activate
pyinstaller afp_viewer.spec
```

The built app will be at `dist/AFP Viewer.app`.

## Project Structure

```
afp-viewer/
├── main.py              # Entry point
├── requirements.txt     # Python dependencies
├── run.sh               # Dev run script
├── afp_viewer.spec      # PyInstaller build spec
├── src/
│   ├── gui.py           # Main application window, toolbar, panels
│   ├── parser.py        # AFP/MO:DCA structured field parser
│   └── renderer.py      # AFP page renderer (PTX, BIM, graphics)
└── samples/             # Sample AFP files for testing
```

## AFP Format Support

Supports MO:DCA structured fields including:

| Code | Description |
|------|-------------|
| BDT  | Begin Document |
| EDT  | End Document |
| BNG  | Begin Named Page Group |
| BPG  | Begin Page |
| EPG  | End Page |
| BRG  | Begin Resource Group |
| PTX  | Presentation Text |
| BIM  | Begin Image |
| MCF  | Map Coded Font |
| PGD  | Page Descriptor |
| MDR  | Map Data Resource |

## License

MIT
