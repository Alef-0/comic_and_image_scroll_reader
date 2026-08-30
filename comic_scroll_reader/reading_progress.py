"""Per-folder reading positions stored separately from reader preferences."""

import csv
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_DIRECTORY


PROGRESS_PATH = CONFIG_DIRECTORY / "reading_progress.csv"
FIELDNAMES = ("folder", "last_page")


@dataclass(frozen=True)
class ReadingProgress:
    folder: Path
    last_page: int


def _folder_key(folder: Path) -> str:
    return str(folder.expanduser().resolve())


def load_reading_progress(path: Path = PROGRESS_PATH) -> dict[str, ReadingProgress]:
    """Load valid progress rows, ignoring malformed or incomplete entries."""
    records: dict[str, ReadingProgress] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as progress_file:
            rows = csv.DictReader(progress_file)
            for row in rows:
                folder_text = row.get("folder", "").strip()
                try:
                    last_page = int(row.get("last_page", ""))
                except (TypeError, ValueError):
                    continue
                if not folder_text or last_page < 1:
                    continue
                folder = Path(folder_text)
                records[_folder_key(folder)] = ReadingProgress(
                    folder=folder,
                    last_page=last_page,
                )
    except OSError:
        return records
    return records


def progress_for_folder(
    folder: Path, path: Path = PROGRESS_PATH
) -> ReadingProgress | None:
    """Return the saved position for one folder, if it has been seen before."""
    return load_reading_progress(path).get(_folder_key(folder))


def save_reading_progress(
    progress: ReadingProgress, path: Path = PROGRESS_PATH
) -> None:
    """Create or replace the row for a folder without storing other settings."""
    records = load_reading_progress(path)
    key = _folder_key(progress.folder)
    records[key] = ReadingProgress(
        folder=Path(key),
        last_page=max(1, int(progress.last_page)),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as progress_file:
        writer = csv.DictWriter(progress_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for folder_key in sorted(records):
            record = records[folder_key]
            writer.writerow(
                {
                    "folder": folder_key,
                    "last_page": record.last_page,
                }
            )
