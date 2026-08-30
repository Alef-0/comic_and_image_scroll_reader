#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="python3"
python_command_was_set=false
minimal_opencv=""

usage() {
    echo "Usage: $0 [--minimal-opencv /path/to/cv2.so] [python-command]"
}

while (( $# > 0 )); do
    case "$1" in
        --minimal-opencv)
            if (( $# < 2 )); then
                echo "--minimal-opencv requires the path to a native cv2 module." >&2
                usage >&2
                exit 2
            fi
            minimal_opencv="$2"
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

if [[ -n "$minimal_opencv" ]]; then
    if [[ ! -f "$minimal_opencv" ]]; then
        echo "Minimal OpenCV module not found: $minimal_opencv" >&2
        exit 1
    fi
    minimal_opencv="$(realpath -- "$minimal_opencv")"
fi

if ! "$python_command" -c 'import nuitka, numpy, PIL, FreeSimpleGUI' >/dev/null; then
    echo "The selected Python environment needs Nuitka and the reader dependencies." >&2
    echo "Follow the build steps in README.md, then run this script again." >&2
    exit 1
fi

cd "$project_directory"
nuitka_options=(
    --onefile
    --assume-yes-for-downloads
    --enable-plugin=tk-inter
    --python-flag=no_docstrings
    --include-package=FreeSimpleGUI
    --include-package=PIL
    --include-module=cv2
    --nofollow-import-to=numpy.random
    --nofollow-import-to=numpy.fft
    --output-filename=comic-scroll-reader
)

if [[ -n "$minimal_opencv" ]]; then
    cv2_loader="$($python_command -c 'import cv2; print(cv2.__file__)')"
    cv2_loader_directory="$(dirname -- "$cv2_loader")"
    wrapper_directory="$(mktemp -d /tmp/comic-reader-cv2-wrapper.XXXXXX)"
    mkdir -p "$wrapper_directory/cv2"
    cp -- "$cv2_loader_directory/__init__.py" "$wrapper_directory/cv2/"
    cp -- "$cv2_loader_directory/load_config_py3.py" "$wrapper_directory/cv2/"
    cp -- "$cv2_loader_directory/config.py" "$wrapper_directory/cv2/"
    cp -- "$cv2_loader_directory/config-3.py" "$wrapper_directory/cv2/"
    cp -- "$minimal_opencv" "$wrapper_directory/cv2/cv2.abi3.so"
    PYTHONPATH="$wrapper_directory${PYTHONPATH:+:$PYTHONPATH}" \
        "$python_command" -m nuitka "${nuitka_options[@]}" run_reader.py
else
    "$python_command" -m nuitka "${nuitka_options[@]}" run_reader.py
fi

echo "Compressed binary created at: $project_directory/comic-scroll-reader"
du -h "$project_directory/comic-scroll-reader"
echo "Run it with: $project_directory/comic-scroll-reader"
