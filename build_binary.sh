#!/usr/bin/env bash

set -euo pipefail

python_command="${1:-python3}"
project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! "$python_command" -c 'import nuitka, cv2, numpy, PIL, FreeSimpleGUI' >/dev/null; then
    echo "The selected Python environment needs Nuitka and the reader dependencies." >&2
    echo "Install Nuitka there and make sure it loads your CUDA-enabled cv2 build." >&2
    exit 1
fi

cd "$project_directory"
"$python_command" -m nuitka \
    --standalone \
    --enable-plugin=tk-inter \
    --include-package=FreeSimpleGUI \
    --include-package=PIL \
    --include-package=numpy \
    --include-package=cv2 \
    --output-filename=comic-scroll-reader \
    run_reader.py

echo "Standalone binary created in: $project_directory/run_reader.dist"
