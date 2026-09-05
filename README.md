# Comic and Scroll Reader

A small Python desktop app that reads a folder of images as one continuous,
vertical strip. It supports single-page and paired-page layouts, manga order,
double-spread detection, per-folder reading progress, and persistent window and
toolbar state.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

The reader uses Pillow SIMD-accelerated resizing directly on the CPU.


## Run

Open the launcher, where a folder can be dropped or chosen with the desktop's
native folder dialog:

```bash
python3 run_reader.py
```

The launcher's basic settings are loaded from the per-user config file and can
be edited and saved before opening a comic. Drag and drop requires the
`tkinterdnd2` dependency from `requirements.txt`; when the project `.venv`
contains it, `run_reader.py` automatically uses that environment. Folder
browsing uses Zenity on GTK desktops or KDialog on KDE.

Or open an image folder directly:

```bash
python3 run_reader.py /path/to/comic
```

The Qt frontend provides the same continuous-reading controls with its own
single-process, background image loader. It progressively builds a small
retained fallback for every page, while visible pages receive priority and a
larger bounded cache keeps recently viewed sharp pages. Install its optional
dependency once, then run it with an image folder, selected images, or a PDF:

```bash
.venv/bin/python -m pip install 'PySide6>=6.8'
python3 run_reader_qt.py /path/to/comic
python3 run_reader_qt.py /path/to/comic.pdf
```

When no path is supplied, the Qt version opens its folder picker. It also
accepts folders, images, and PDFs by drag and drop.

The reader restores its last window size, position, and maximized state. To
override that saved state and start in a normal window:

```bash
python3 run_reader.py --windowed /path/to/comic
```

Start with paired pages, optionally using manga (right-to-left) order:

```bash
python3 run_reader.py --dual-page /path/to/comic
python3 run_reader.py --dual-page --manga /path/to/comic
```

For UI inspection, open every collapsible toolbar menu at once:

```bash
python3 run_reader.py --windowed --expand-all /path/to/comic
```

If the initially selected folder is invalid or contains no readable images,
the error is shown and the launcher opens again. An invalid folder selected
from an active reader leaves the current comic open.

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
./build_binary.sh .venv/bin/python
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

The build compiles the application with Nuitka into a single compact executable
without heavy third-party C++ dependencies like OpenCV, resulting in a lightweight
standalone binary (~18–20 MB).


## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Reader controls

Use the mouse wheel or arrow keys to scroll. **Page Up** and **Page Down** move
one screen at a time, while **Home** and **End** jump to the beginning or end.
Ctrl+wheel, **+**, and **-** zoom; F11 toggles fullscreen; and Escape closes the
reader. The underlined **-**, zoom percentage, **+**, and **Pages** counter at
the right of the toolbar are clickable. Clicking **Pages** opens a page-jump
dialog. Zoom stops at the fitted window width by default; clear **Stop at fit
width** to zoom farther, or enable
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

The experimental **Remember folder** option controls per-folder reading
positions. When disabled, no page is saved and the reader never asks whether
to continue. Zoom is global: every folder and reader window uses the same last
saved zoom level.

All page resizing uses bicubic interpolation.
Use **Save Configs** to persist the current checkbox settings. Toolbar
visibility and the last window geometry/state are saved automatically when
they change or the reader closes. They are loaded from
`~/.config/comic-scroll-reader/reader_config.json`; if `XDG_CONFIG_HOME` is
set, its value replaces `~/.config`.

On close, the active folder and last page are saved separately in
`~/.config/comic-scroll-reader/reading_progress.csv`. Reopening that folder
offers to restore the saved page. The CSV stores no other configuration; the
shared zoom level remains in `reader_config.json`.

## Credits and license

Developed by Alef-0. Vibecoded using OpenAI GPT-5.6 Codex and Google Antigravity.
Distributed under the MIT License; see `LICENSE` for the full terms.

## Build a Debian package

First build the onefile program and place the resulting `comic-scroll-reader`
binary in an artifact folder. The folder may also contain Nuitka's
`run_reader.onefile-build` directory; it is compiler output and is not added to
the package.

Create the version 0.2 package with:

```bash
chmod +x build_deb.sh
./build_deb.sh /path/to/artifact-folder
```

The package is written to `dist/` and can be installed with:

```bash
sudo apt install ./dist/comic-scroll-reader_0.2_amd64.deb
```

Set `DEB_MAINTAINER` before running the builder to replace the default local
maintainer entry in the package metadata.
