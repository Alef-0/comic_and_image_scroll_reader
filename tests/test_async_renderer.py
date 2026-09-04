"""Unit tests for the bounded asynchronous render pipeline."""

from pathlib import Path
import tempfile
import time
import unittest

from PIL import Image

from comic_scroll_reader.imaging.async_renderer import (
    AsyncRenderer,
    BoundedRequestQueue,
)


class AsyncRendererTests(unittest.TestCase):
    def test_queue_priority_and_deduplication(self) -> None:
        queue = BoundedRequestQueue(max_pending=4)
        file = Path("/tmp/test.png")

        # Submit key A with priority 5
        self.assertTrue(
            queue.submit(
                priority=5,
                generation=1,
                page_file=file,
                region_key=("A",),
                source_box=(0, 0, 10, 10),
                target_size=(10, 10),
            )
        )
        # Duplicate key A should be rejected
        self.assertFalse(
            queue.submit(
                priority=1,
                generation=1,
                page_file=file,
                region_key=("A",),
                source_box=(0, 0, 10, 10),
                target_size=(10, 10),
            )
        )
        # Submit key B with priority 0 (higher priority)
        self.assertTrue(
            queue.submit(
                priority=0,
                generation=1,
                page_file=file,
                region_key=("B",),
                source_box=(0, 0, 10, 10),
                target_size=(10, 10),
            )
        )

        # First popped must be B (priority 0)
        req1 = queue.get()
        self.assertIsNotNone(req1)
        self.assertEqual(req1.region_key, ("B",))

        # Second popped must be A (priority 5)
        req2 = queue.get()
        self.assertIsNotNone(req2)
        self.assertEqual(req2.region_key, ("A",))

        queue.close()

    def test_queue_eviction_when_full(self) -> None:
        queue = BoundedRequestQueue(max_pending=2)
        file = Path("/tmp/test.png")

        queue.submit(
            priority=10,
            generation=1,
            page_file=file,
            region_key=("low",),
            source_box=(0, 0, 10, 10),
            target_size=(10, 10),
        )
        queue.submit(
            priority=5,
            generation=1,
            page_file=file,
            region_key=("med",),
            source_box=(0, 0, 10, 10),
            target_size=(10, 10),
        )

        # Queue is full (2 items: priority 10 and priority 5).
        # Submitting priority 1 should evict priority 10 ("low").
        self.assertTrue(
            queue.submit(
                priority=1,
                generation=1,
                page_file=file,
                region_key=("high",),
                source_box=(0, 0, 10, 10),
                target_size=(10, 10),
            )
        )

        first = queue.get()
        self.assertEqual(first.region_key, ("high",))
        second = queue.get()
        self.assertEqual(second.region_key, ("med",))

        queue.close()

    def test_queue_generation_invalidation(self) -> None:
        queue = BoundedRequestQueue(max_pending=5)
        file = Path("/tmp/test.png")

        queue.submit(
            priority=1,
            generation=1,
            page_file=file,
            region_key=("old1",),
            source_box=(0, 0, 10, 10),
            target_size=(10, 10),
        )
        queue.submit(
            priority=2,
            generation=2,
            page_file=file,
            region_key=("new1",),
            source_box=(0, 0, 10, 10),
            target_size=(10, 10),
        )

        # Invalidate generation 1
        queue.cancel_stale_generations(current_generation=2)

        popped = queue.get()
        self.assertIsNotNone(popped)
        self.assertEqual(popped.region_key, ("new1",))

        queue.close()

    def test_async_renderer_end_to_end(self) -> None:
        renderer = AsyncRenderer(max_workers=2, max_pending=4)

        with tempfile.TemporaryDirectory() as temporary:
            img_path = Path(temporary) / "test.png"
            Image.new("RGB", (200, 200), (255, 0, 0)).save(img_path)

            submitted = renderer.submit(
                priority=0,
                generation=1,
                page_file=img_path,
                region_key=(img_path, 100, 100, 0, 0, 100, 100),
                source_box=(0.0, 0.0, 100.0, 100.0),
                target_size=(50, 50),
            )
            self.assertTrue(submitted)

            # Wait briefly for worker to complete
            results = []
            for _ in range(50):
                results = renderer.get_results()
                if results:
                    break
                time.sleep(0.01)

            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result.generation, 1)
            self.assertEqual(result.page_file, img_path)
            self.assertIsNotNone(result.image)
            self.assertEqual(result.image.size, (50, 50))
            result.image.close()

        renderer.shutdown()


if __name__ == "__main__":
    unittest.main()
