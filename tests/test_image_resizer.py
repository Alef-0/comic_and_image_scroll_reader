import unittest

from PIL import Image

from comic_scroll_reader.imaging.image_resizer import ImageResizer


class ImageResizerTests(unittest.TestCase):
    def test_resizes_image_to_target_dimensions(self) -> None:
        resizer = ImageResizer()
        image = Image.new("RGB", (200, 300), color="red")

        result = resizer.resize(image, (100, 150))

        self.assertEqual(result.size, (100, 150))
        self.assertEqual(result.mode, "RGB")

    def test_uses_specified_resampling_filter(self) -> None:
        resizer = ImageResizer()
        image = Image.new("RGBA", (100, 100), color="blue")

        result = resizer.resize(
            image, (50, 50), cpu_filter=Image.Resampling.BOX
        )

        self.assertEqual(result.size, (50, 50))
        self.assertEqual(result.mode, "RGBA")

    def test_upscaling(self) -> None:
        resizer = ImageResizer(default_filter=Image.Resampling.BICUBIC)
        image = Image.new("RGB", (50, 50), color="green")

        result = resizer.resize(image, (100, 100))

        self.assertEqual(result.size, (100, 100))


if __name__ == "__main__":
    unittest.main()
