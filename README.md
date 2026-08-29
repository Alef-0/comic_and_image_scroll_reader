# Comic and Image Scroll Reader

A small Python desktop app that reads a folder of images as one continuous,
vertical strip.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

OpenCV is intentionally not installed from PyPI: the standard wheels do not
include CUDA. The reader automatically uses CUDA resizing when a CUDA-enabled
`cv2` module is visible to the active Python environment, and otherwise uses
Pillow on the CPU. The active backend appears at the right of the toolbar.

### Use a custom OpenCV build in a virtual environment

First remove any wheel that could take precedence over the custom build:

```bash
python -m pip uninstall opencv-python opencv-python-headless \
  opencv-contrib-python opencv-contrib-python-headless
```

The preferred override is to install your locally built wheel into each active
environment. This does not contact PyPI:

```bash
python -m pip install --force-reinstall /path/to/numpy-wheel.whl
python -m pip install --force-reinstall --no-deps /path/to/opencv-wheel.whl
```

The wheel's Python and platform tags must match the environment (for example,
`cp312` requires CPython 3.12).

If you have no wheel and OpenCV was installed into a system prefix such as
`/usr/local`, add the directory that contains its `cv2` package to the
environment with a `.pth` file. Replace the example source path with the one
printed by your custom Python outside the environment:

```bash
python3 -c 'import cv2; print(cv2.__file__)'
python -c 'import site; print(site.getsitepackages()[0])'
printf '%s\n' '/usr/local/lib/python3.12/site-packages' \
  > .venv/lib/python3.12/site-packages/custom-opencv.pth
```

Use the Python version in your own paths. Repeat the `.pth` step for each
virtual environment. The path can also be the build tree's `python_loader`
directory if you have not installed the compiled build yet. The OpenCV build
and virtual environment must use the same Python major/minor version.

Alternatively, install the already-built OpenCV tree directly into the active
environment if its install layout is relocatable:

```bash
cmake --install /path/to/opencv-build --prefix "$VIRTUAL_ENV"
```

Confirm that the environment loads the intended binary and sees CUDA:

```bash
python -c 'import cv2; print(cv2.__file__); print(cv2.cuda.getCudaEnabledDeviceCount())'
```

The final number must be greater than zero for this reader to select CUDA.

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

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Use the mouse wheel or arrow keys to scroll, Ctrl+wheel to zoom, F11 for
fullscreen, and Escape to close the reader. Zoom stops at the fitted window
width by default; clear **Stop at fit width** to zoom farther, or enable
**Don't enlarge images** to keep every page at or below its native width.
**Original Size** uses each image's native dimensions, reducing only images
that would exceed their available width while **Stop at fit width** is active.
In dual-page mode the cover remains alone and later pages are paired; **Manga
order** swaps the left and right pages in each pair.
