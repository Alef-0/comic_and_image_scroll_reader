#!/usr/bin/env python3
"""Experimental Qt entry point for comparing image rendering performance."""

import importlib.util
import os
from pathlib import Path
import sys


def _use_project_environment() -> None:
    """Restart in the local environment used by the main reader."""
    if importlib.util.find_spec("PySide6") is not None:
        return
    project_environment = Path(__file__).resolve().parent / ".venv"
    project_python = project_environment / "bin" / "python"
    if (
        not project_python.is_file()
        or Path(sys.prefix).resolve() == project_environment.resolve()
    ):
        return
    os.execv(
        str(project_python),
        [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def main() -> int:
    _use_project_environment()
    try:
        from comic_scroll_reader.qt_app import main as qt_main
    except ModuleNotFoundError as error:
        if error.name == "PySide6":
            print(
                "The experimental Qt reader needs PySide6. Install the optional "
                "Qt dependencies first with:\n"
                "  .venv/bin/python -m pip install 'PySide6>=6.8'",
                file=sys.stderr,
            )
            return 2
        raise
    return qt_main()


if __name__ == "__main__":
    raise SystemExit(main())
