#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="python3"
python_command_was_set=false
external_opencv=""

usage() {
    echo "Usage: $0 [--external-opencv /path/to/cv2.so] [python-command]"
}

while (( $# > 0 )); do
    case "$1" in
        --external-opencv)
            if (( $# < 2 )); then
                echo "--external-opencv requires the path to a cv2 shared library." >&2
                usage >&2
                exit 2
            fi
            external_opencv="$2"
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

if [[ -n "$external_opencv" ]]; then
    if [[ ! -f "$external_opencv" ]]; then
        echo "External OpenCV library not found: $external_opencv" >&2
        exit 1
    fi
    external_opencv="$(realpath -- "$external_opencv")"
    if [[ "$external_opencv" == "$project_directory/run_reader.dist/"* ]]; then
        echo "External OpenCV must be outside run_reader.dist." >&2
        exit 1
    fi
fi

if ! "$python_command" -c 'import nuitka, numpy, PIL, FreeSimpleGUI' >/dev/null; then
    echo "The selected Python environment needs Nuitka and the reader dependencies." >&2
    echo "Follow the build steps in README.md, then run this script again." >&2
    exit 1
fi

cd "$project_directory"
nuitka_options=(
    --standalone
    --assume-yes-for-downloads
    --enable-plugin=tk-inter
    --include-package=FreeSimpleGUI
    --include-package=PIL
    --include-package=numpy
    --output-filename=comic-scroll-reader
)

if "$python_command" -c 'import cv2' >/dev/null 2>&1; then
    nuitka_options+=(--include-package=cv2)
    echo "Including OpenCV from the selected Python environment."
elif [[ -n "$external_opencv" ]]; then
    echo "The selected Python environment cannot import cv2." >&2
    echo "Link its Python loader into the environment before using --external-opencv." >&2
    exit 1
else
    echo "OpenCV is not installed; building with the Pillow CPU fallback."
fi

previous_opencv_link="$project_directory/run_reader.dist/cv2/cv2.so"
if [[ -L "$previous_opencv_link" ]]; then
    rm -- "$previous_opencv_link"
fi

"$python_command" -m nuitka "${nuitka_options[@]}" run_reader.py

if [[ -n "$external_opencv" ]]; then
    bundled_opencv="$project_directory/run_reader.dist/cv2/cv2.so"
    if [[ ! -f "$bundled_opencv" ]]; then
        echo "Nuitka did not create the expected OpenCV library: $bundled_opencv" >&2
        exit 1
    fi
    rm -- "$bundled_opencv"
    ln -s -- "$external_opencv" "$bundled_opencv"
    echo "Linked external OpenCV library: $external_opencv"
fi

echo "Standalone binary created in: $project_directory/run_reader.dist"
echo "Run it with: $project_directory/run_reader.dist/comic-scroll-reader"
