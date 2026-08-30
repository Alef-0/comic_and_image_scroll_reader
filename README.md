# Comic and Image Scroll Reader

A small Python desktop app that reads a folder of images as one continuous,
vertical strip.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

The reader uses OpenCV bicubic resizing on the CPU. Pillow remains a fallback
if OpenCV cannot resize an image.

## Run

Open the folder picker:

```bash
python3 run_reader.py
```

Or open an image folder directly:

```bash
python3 run_reader.py /path/to/comic
```

The reader starts maximized by default. To start in a normal window instead:

```bash
python3 run_reader.py --windowed /path/to/comic
```

Start with paired pages, optionally using manga (right-to-left) order:

```bash
python3 run_reader.py --dual-page /path/to/comic
python3 run_reader.py --dual-page --manga /path/to/comic
```

## Build a compact binary

Run these commands from the project directory. They create an isolated Python
environment, install the application and compiler dependencies, and build the
compressed program:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install nuitka ordered-set zstandard
chmod +x build_binary.sh
chmod +x build_minimal_opencv.sh
./build_minimal_opencv.sh .venv/bin/python
./build_binary.sh \
  --minimal-opencv build/minimal-opencv/cv2.so \
  .venv/bin/python
```

The build can take several minutes. When it finishes, start the compiled
program with:

```bash
./comic-scroll-reader
```

To open an image folder directly, pass its path after the command:

```bash
./comic-scroll-reader /path/to/comic
```

The compact build compiles only OpenCV's core, image-processing, and Python
binding modules. It excludes CUDA, codecs, video, GUI, DNN, contrib modules,
and Intel IPP while retaining SIMD-optimized CPU resizing. Nuitka then excludes
unused NumPy packages and compresses the application into one executable.

`build_minimal_opencv.sh` automatically looks for the OpenCV source checkout
associated with the selected environment's `cv2` installation. If OpenCV came
from a normal precompiled wheel, that source is not present; download or clone
OpenCV and pass it explicitly with `--opencv-source /path/to/opencv-source`.

On the tested Linux system this reduced the distributable executable from
about 88 MB to about 33 MB. The exact size depends on the compiler and system
libraries. Running `build_binary.sh` without `--minimal-opencv` uses the OpenCV
installation from the selected Python environment and may produce a much
larger executable.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Use the mouse wheel or arrow keys to scroll. **Page Up** and **Page Down** move
one screen at a time, while **Home** and **End** jump to the beginning or end.
Ctrl+wheel zooms, F11 toggles fullscreen, and Escape closes the reader. Zoom
stops at the fitted window width by default; clear **Stop at fit width** to zoom
farther, or enable
**Don't enlarge images** to keep every page at or below its native width.
**Original Size** uses each image's native dimensions, reducing only images
that would exceed their available width while **Stop at fit width** is active.
In dual-page mode the cover remains alone and later pages are paired; **Manga
order** swaps the left and right pages in each pair. **Page spacing** leaves a
small background-colored gap between neighboring pages without adding outer
spacing before the first or after the last page.

The experimental **Detect double-page spreads** option is enabled by default.
It compares page proportions within the folder, scales unusually wide images
to the normal page height until they reach the width limit, and gives every
detected spread its own row in dual-page mode. Click and drag anywhere on the
reader to pan vertically or horizontally when an image is wider than the
window.

All page resizing uses bicubic interpolation.
Use **Options → Save Configs** to persist the current checkbox settings. They
are loaded automatically from `reader_config.json` beside `run_reader.py`, so
the configuration location does not depend on the directory used to launch
the reader.
