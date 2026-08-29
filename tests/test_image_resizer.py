import unittest

from PIL import Image

from comic_scroll_reader.imaging.image_resizer import ImageResizer


class FakeGpuMat:
    def upload(self, source: object) -> None:
        self.source = source


class FakeCuda:
    def __init__(self, devices: int) -> None:
        self.devices = devices
        self.interpolation: int | None = None

    def getCudaEnabledDeviceCount(self) -> int:
        return self.devices

    def resize(
        self, source: FakeGpuMat, size: tuple[int, int], *, interpolation: int
    ) -> object:
        self.interpolation = interpolation

        class Result:
            def download(self) -> object:
                import numpy as np

                return np.zeros((size[1], size[0], 3), dtype=np.uint8)

        return Result()


class FakeCv2:
    INTER_LINEAR = 1
    INTER_CUBIC = 2
    cuda_GpuMat = FakeGpuMat
    error = RuntimeError

    def __init__(self, devices: int) -> None:
        self.cuda = FakeCuda(devices)


class ImageResizerTests(unittest.TestCase):
    def test_uses_cuda_for_a_supported_device(self) -> None:
        cv2 = FakeCv2(1)
        resizer = ImageResizer(cv2)

        result = resizer.resize(
            Image.new("RGB", (20, 30)), (10, 15), Image.Resampling.LANCZOS
        )

        self.assertEqual(resizer.backend_name, "CUDA")
        self.assertEqual(result.size, (10, 15))
        self.assertEqual(cv2.cuda.interpolation, cv2.INTER_CUBIC)

    def test_uses_cpu_when_no_cuda_device_is_available(self) -> None:
        resizer = ImageResizer(FakeCv2(0))

        result = resizer.resize(
            Image.new("RGB", (20, 30)), (10, 15), Image.Resampling.LANCZOS
        )

        self.assertEqual(resizer.backend_name, "CPU")
        self.assertEqual(result.size, (10, 15))


if __name__ == "__main__":
    unittest.main()
