# Comic and Image Scroll Reader

A small Python desktop app that reads a folder of images as one continuous,
vertical strip.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

## Run

Open the folder picker:

```bash
python3 run_reader.py
```

Or open an image folder directly:

```bash
python3 run_reader.py /path/to/comic
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Use the mouse wheel or arrow keys to scroll, Ctrl+wheel to zoom, F11 for
fullscreen, and Escape to close the reader.
