"""Benchmark the image-resize pipelines used by the comic reader."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
from PIL import Image, ImageOps

try:
    import cv2
except (ImportError, OSError):
    cv2 = None


IMAGE_SUFFIXES = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".jp2", ".png", ".tif", ".tiff", ".webp"}
)
Resize = Callable[[Image.Image, tuple[int, int]], Image.Image]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Pillow and OpenCV/NumPy resizing, including transfers to and "
            "from an OpenCV CUDA device when one is available."
        )
    )
    parser.add_argument("folder", type=Path, help="folder containing images to test")
    parser.add_argument(
        "--widths",
        type=int,
        nargs="+",
        default=[640, 960, 1280],
        help="target widths to test (default: 640 960 1280)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=24,
        help="number of evenly distributed images to test; 0 uses all (default: 24)",
    )
    parser.add_argument(
        "--repeat", type=int, default=3, help="timed repetitions (default: 3)"
    )
    return parser.parse_args()


def choose_even_sample(files: list[Path], limit: int) -> list[Path]:
    if limit <= 0 or len(files) <= limit:
        return files
    if limit == 1:
        return [files[len(files) // 2]]
    return [files[round(index * (len(files) - 1) / (limit - 1))] for index in range(limit)]


def load_images(folder: Path, limit: int) -> list[tuple[Path, Image.Image]]:
    files = sorted(
        (
            file
            for file in folder.iterdir()
            if file.is_file() and file.suffix.casefold() in IMAGE_SUFFIXES
        ),
        key=lambda file: file.name.casefold(),
    )
    images: list[tuple[Path, Image.Image]] = []
    for file in choose_even_sample(files, limit):
        try:
            with Image.open(file) as source:
                images.append(
                    (file, ImageOps.exif_transpose(source).convert("RGB").copy())
                )
        except (OSError, ValueError):
            print(f"Skipping unreadable image: {file}")
    return images


def target_size(image: Image.Image, width: int) -> tuple[int, int]:
    return width, max(1, round(image.height * width / image.width))


def pillow_resize(filter_: Image.Resampling) -> Resize:
    return lambda image, size: image.resize(size, filter_)


def opencv_resize(interpolation: int) -> Resize:
    def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        result = cv2.resize(np.asarray(image), size, interpolation=interpolation)
        return Image.fromarray(result, mode="RGB")

    return resize


def cuda_resize(interpolation: int) -> Resize:
    def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        source = cv2.cuda_GpuMat()
        source.upload(np.asarray(image))
        result = cv2.cuda.resize(source, size, interpolation=interpolation)
        return Image.fromarray(result.download(), mode="RGB")

    return resize


def time_pipeline(
    resize: Resize,
    cases: list[tuple[Image.Image, tuple[int, int]]],
    repeat: int,
) -> float:
    samples: list[float] = []
    for _ in range(repeat):
        started = perf_counter()
        for image, size in cases:
            resized = resize(image, size)
            resized.getpixel((0, 0))
        samples.append(perf_counter() - started)
    return median(samples)


def main() -> int:
    arguments = parse_arguments()
    if not arguments.folder.is_dir():
        raise SystemExit(f"Image folder does not exist: {arguments.folder}")
    if arguments.repeat < 1 or any(width < 1 for width in arguments.widths):
        raise SystemExit("--repeat and every target width must be positive")

    images = load_images(arguments.folder, arguments.limit)
    if not images:
        raise SystemExit(f"No readable images found in: {arguments.folder}")
    cases = [
        (image, target_size(image, width))
        for _file, image in images
        for width in arguments.widths
    ]

    cuda_devices = 0
    if cv2 is not None:
        try:
            cuda_devices = cv2.cuda.getCudaEnabledDeviceCount()
        except (AttributeError, cv2.error):
            pass

    print(f"Folder: {arguments.folder}")
    print(f"Images: {len(images)}; resize operations per run: {len(cases)}")
    print(f"Widths: {', '.join(map(str, arguments.widths))}; repetitions: {arguments.repeat}")
    print(f"Pillow: {Image.__version__}; NumPy: {np.__version__}")
    if cv2 is None:
        print("OpenCV: unavailable; CUDA devices: 0")
    else:
        print(f"OpenCV: {cv2.__version__}; CUDA devices: {cuda_devices}")

    pipelines: list[tuple[str, Resize]] = [
        ("Pillow bicubic", pillow_resize(Image.Resampling.BICUBIC)),
        ("Pillow Lanczos", pillow_resize(Image.Resampling.LANCZOS)),
    ]
    if cv2 is not None:
        pipelines.extend(
            [
                ("OpenCV CPU bicubic", opencv_resize(cv2.INTER_CUBIC)),
                ("OpenCV CPU Lanczos", opencv_resize(cv2.INTER_LANCZOS4)),
            ]
        )
    if cuda_devices:
        pipelines.extend(
            [
                ("OpenCV CUDA bicubic", cuda_resize(cv2.INTER_CUBIC)),
                ("OpenCV CUDA Lanczos", cuda_resize(cv2.INTER_LANCZOS4)),
            ]
        )

    print("\nMedian complete conversion-and-resize time:")
    results: list[tuple[str, float]] = []
    for name, resize in pipelines:
        try:
            elapsed = time_pipeline(resize, cases, arguments.repeat)
        except Exception as error:
            print(f"  {name:<23} unsupported ({error})")
            continue
        results.append((name, elapsed))
        milliseconds = elapsed * 1000 / len(cases)
        print(f"  {name:<23} {elapsed:8.3f} s  ({milliseconds:7.2f} ms/image)")

    if results:
        winner, elapsed = min(results, key=lambda result: result[1])
        print(f"\nFastest: {winner} ({elapsed:.3f} s)")
    if not cuda_devices:
        print("CUDA was not benchmarked because OpenCV reported no available device.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
