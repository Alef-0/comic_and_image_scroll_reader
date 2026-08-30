import unittest

from PIL import Image

from comic_scroll_reader.imaging.image_resizer import ImageResizer


class FakeCv2:
    INTER_CUBIC = 2
    error = RuntimeError

    def __init__(self) -> None:
        self.interpolation: int | None = None

    def resize(
        self, source: object, size: tuple[int, int], *, interpolation: int
    ) -> object:
        import numpy as np

        self.interpolation = interpolation
        return np.zeros((size[1], size[0], 3), dtype=np.uint8)


class FailingCv2(FakeCv2):
    def resize(
        self, source: object, size: tuple[int, int], *, interpolation: int
    ) -> object:
        raise RuntimeError("OpenCV resize failed")


class ImageResizerTests(unittest.TestCase):
    def test_uses_opencv_bicubic_resize(self) -> None:
        cv2 = FakeCv2()
        resizer = ImageResizer(cv2)

        result = resizer.resize(
            Image.new("RGB", (20, 30)), (10, 15), Image.Resampling.BICUBIC
        )

        self.assertEqual(result.size, (10, 15))
        self.assertEqual(cv2.interpolation, cv2.INTER_CUBIC)

    def test_falls_back_to_pillow_after_an_opencv_failure(self) -> None:
        resizer = ImageResizer(FailingCv2())

        result = resizer.resize(
            Image.new("RGB", (20, 30)), (10, 15), Image.Resampling.BICUBIC
        )

        self.assertEqual(result.size, (10, 15))


if __name__ == "__main__":
    unittest.main()
