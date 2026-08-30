#!/usr/bin/env python3
"""Friendly entry point for running the reader from its source folder."""

import importlib.util
import os
from pathlib import Path
import sys


def _use_project_environment_for_drag_and_drop() -> None:
    """Restart in the local environment when the caller lacks Tk drag/drop."""
    if importlib.util.find_spec("tkinterdnd2") is not None:
        return
    project_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if not project_python.is_file():
        return
    os.execv(
        str(project_python),
        [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]],
    )


_use_project_environment_for_drag_and_drop()

from comic_scroll_reader.app import main


if __name__ == "__main__":
    raise SystemExit(main())
