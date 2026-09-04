"""Unit tests for multi-process communications, protocols, and worker lifecycle."""

import io
from pathlib import Path
import pickle
import tempfile
import time
import unittest

from PIL import Image

from comic_scroll_reader.communications.channel import (
    drain_queue,
    get_mp_context,
    safe_close_queue,
    safe_terminate_process,
)
from comic_scroll_reader.communications.messages import (
    RenderResponse,
    RenderTask,
    ShutdownSentinel,
)
from comic_scroll_reader.communications.pdf_bridge import (
    close_cached_documents,
    load_pdf_metadata,
    render_pdf_region_from_metadata,
)
from comic_scroll_reader.communications.worker import render_worker_main


class CommunicationsTests(unittest.TestCase):
    def test_messages_picklable(self) -> None:
        file = Path("/tmp/test_page.png")
        task = RenderTask(
            priority=1,
            sequence=42,
            generation=3,
            page_file=file,
            region_key=(file, 10, 20),
            source_box=(0.0, 0.0, 100.0, 100.0),
            target_size=(50, 50),
            resample_code=3,
        )
        serialized_task = pickle.dumps(task)
        restored_task = pickle.loads(serialized_task)
        self.assertEqual(task, restored_task)

        response = RenderResponse(
            generation=3,
            page_file=file,
            region_key=(file, 10, 20),
            webp_bytes=b"RIFFdummywebp",
            target_size=(50, 50),
        )
        serialized_resp = pickle.dumps(response)
        restored_resp = pickle.loads(serialized_resp)
        self.assertEqual(response, restored_resp)

        sentinel = ShutdownSentinel()
        self.assertEqual(type(pickle.loads(pickle.dumps(sentinel))), ShutdownSentinel)

    def test_context_is_spawn(self) -> None:
        ctx = get_mp_context()
        self.assertEqual(ctx.get_start_method(), "spawn")

    def test_drain_queue(self) -> None:
        ctx = get_mp_context()
        q = ctx.Queue()
        q.put(1)
        q.put(2)
        q.put("hello")
        items = drain_queue(q, timeout=0.5)
        self.assertEqual(items, [1, 2, "hello"])
        safe_close_queue(q)

    def test_worker_process_end_to_end(self) -> None:
        ctx = get_mp_context()
        task_queue = ctx.Queue()
        result_queue = ctx.Queue()
        stop_event = ctx.Event()

        proc = ctx.Process(
            target=render_worker_main,
            args=(task_queue, result_queue, stop_event),
            daemon=True,
        )
        proc.start()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                img_path = Path(temp_dir) / "test.png"
                Image.new("RGB", (200, 200), (0, 128, 255)).save(img_path)

                task = RenderTask(
                    priority=0,
                    sequence=1,
                    generation=1,
                    page_file=img_path,
                    region_key=(img_path, 0, 0),
                    source_box=(0.0, 0.0, 100.0, 100.0),
                    target_size=(50, 50),
                    resample_code=3,
                )
                task_queue.put(task)

                # Wait for response
                response = None
                for _ in range(50):
                    try:
                        response = result_queue.get(timeout=0.1)
                        break
                    except Exception:
                        time.sleep(0.02)

                self.assertIsNotNone(response)
                self.assertIsInstance(response, RenderResponse)
                self.assertEqual(response.generation, 1)
                self.assertEqual(response.page_file, img_path)
                self.assertIsNotNone(response.webp_bytes)
                self.assertTrue(response.webp_bytes.startswith(b"RIFF"))

                # Validate decoded image
                decoded = Image.open(io.BytesIO(response.webp_bytes))
                self.assertEqual(decoded.size, (50, 50))
                decoded.close()

                # Clean shutdown via sentinel
                task_queue.put(ShutdownSentinel())
                proc.join(timeout=2.0)
                self.assertFalse(proc.is_alive())
        finally:
            stop_event.set()
            safe_terminate_process(proc, timeout=1.0)
            safe_close_queue(task_queue)
            safe_close_queue(result_queue)

    def test_pdf_bridge_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meta_path = Path(temp_dir) / "csr_pdf_metadata.json"
            fake_pdf = Path(temp_dir) / "test.pdf"

            import json

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "pdf_path": str(fake_pdf),
                        "scale": 1.5,
                        "pages": [{"width": 100, "height": 200}],
                    },
                    f,
                )

            loaded = load_pdf_metadata(meta_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["scale"], 1.5)
            self.assertEqual(len(loaded["pages"]), 1)
            close_cached_documents()


if __name__ == "__main__":
    unittest.main()
