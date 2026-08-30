#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="python3"
python_command_was_set=false
opencv_source=""

usage() {
    echo "Usage: $0 [--opencv-source /path/to/opencv-source] [python-command]"
}

while (( $# > 0 )); do
    case "$1" in
        --opencv-source)
            if (( $# < 2 )); then
                echo "--opencv-source requires a directory." >&2
                usage >&2
                exit 2
            fi
            opencv_source="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ "$python_command_was_set" == true ]]; then
                echo "Only one Python command may be supplied." >&2
                usage >&2
                exit 2
            fi
            python_command="$1"
            python_command_was_set=true
            shift
            ;;
    esac
done

python_executable="$(realpath -- "$(command -v "$python_command")")"
build_directory="$project_directory/build/minimal-opencv-cmake"
output_directory="$project_directory/build/minimal-opencv"
native_module="$build_directory/lib/python3/cv2.abi3.so"

if ! "$python_command" -c 'import cv2, numpy' >/dev/null; then
    echo "The selected Python environment needs OpenCV and NumPy." >&2
    exit 1
fi

if [[ -z "$opencv_source" ]]; then
    if ! opencv_source="$($python_command - <<'PY'
from pathlib import Path

import cv2

for parent in Path(cv2.__file__).resolve().parents:
    for candidate in (parent, parent / "opencv"):
        if (
            (candidate / "CMakeLists.txt").is_file()
            and (candidate / "modules" / "imgproc").is_dir()
        ):
            print(candidate)
            raise SystemExit(0)
raise SystemExit(1)
PY
    )"; then
        echo "Unable to locate the OpenCV source used by the selected environment." >&2
        echo "An installed wheel does not contain the C++ source needed for a minimal build." >&2
        echo "Use --opencv-source /path/to/opencv-source to provide its checkout." >&2
        exit 1
    fi
else
    opencv_source="$(realpath -- "$opencv_source")"
fi

if [[ ! -f "$opencv_source/CMakeLists.txt" || ! -d "$opencv_source/modules/imgproc" ]]; then
    echo "Not an OpenCV source directory: $opencv_source" >&2
    exit 1
fi

python_include="$($python_command -c 'import sysconfig; print(sysconfig.get_path("include"))')"
python_library="$($python_command -c 'import os, sysconfig; print(os.path.join(sysconfig.get_config_var("LIBDIR"), sysconfig.get_config_var("LDLIBRARY")))')"
numpy_include="$($python_command -c 'import numpy; print(numpy.get_include())')"

cmake -S "$opencv_source" -B "$build_directory" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_LIST=core,imgproc,python3 \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_TESTS=OFF \
    -DBUILD_PERF_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_opencv_apps=OFF \
    -DBUILD_JAVA=OFF \
    -DBUILD_opencv_python3=ON \
    -DPYTHON3_EXECUTABLE="$python_executable" \
    -DPYTHON3_INCLUDE_DIR="$python_include" \
    -DPYTHON3_LIBRARY="$python_library" \
    -DPYTHON3_NUMPY_INCLUDE_DIRS="$numpy_include" \
    -DPYTHON3_LIMITED_API=ON \
    -DPYTHON3_PACKAGES_PATH=lib/python3/site-packages \
    -DPYTHON_DEFAULT_EXECUTABLE="$python_executable" \
    -DWITH_CUDA=OFF \
    -DWITH_IPP=OFF \
    -DWITH_ITT=OFF \
    -DWITH_OPENCL=OFF \
    -DWITH_FFMPEG=OFF \
    -DWITH_GSTREAMER=OFF \
    -DWITH_GTK=OFF \
    -DWITH_QT=OFF \
    -DWITH_VTK=OFF \
    -DWITH_EIGEN=OFF \
    -DWITH_1394=OFF \
    -DWITH_V4L=OFF \
    -DWITH_OPENEXR=OFF \
    -DWITH_JASPER=OFF \
    -DWITH_WEBP=OFF \
    -DWITH_TIFF=OFF \
    -DWITH_JPEG=OFF \
    -DWITH_PNG=OFF

cmake --build "$build_directory" --parallel --target opencv_python3

if [[ ! -f "$native_module" ]]; then
    echo "OpenCV did not create the expected module: $native_module" >&2
    exit 1
fi

mkdir -p "$output_directory"
cp -- "$native_module" "$output_directory/cv2.so"
strip --strip-unneeded "$output_directory/cv2.so"

echo "Minimal OpenCV module created at: $output_directory/cv2.so"
du -h "$output_directory/cv2.so"
