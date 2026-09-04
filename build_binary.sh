#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="python3"
python_command_was_set=false

usage() {
    echo "Usage: $0 [python-command]"
}

while (( $# > 0 )); do
    case "$1" in
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

if ! "$python_command" -c 'import nuitka, PIL, FreeSimpleGUI, tkinterdnd2, pypdfium2' >/dev/null; then
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
    --include-package=pypdfium2
    --include-data-files="$project_directory/comic_scroll_reader/assets/csr_logo.png=comic_scroll_reader/assets/csr_logo.png"
    --include-data-files="$project_directory/comic_scroll_reader/assets/csr_app_icon.png=comic_scroll_reader/assets/csr_app_icon.png"
    --linux-icon="$project_directory/comic_scroll_reader/assets/csr_app_icon.png"
    --nofollow-import-to=numpy
    --output-filename=comic-scroll-reader
)

"$python_command" -m nuitka "${nuitka_options[@]}" run_reader.py

echo "Compressed binary created at: $project_directory/comic-scroll-reader"
du -h "$project_directory/comic-scroll-reader"
echo "Run it with: $project_directory/comic-scroll-reader"

