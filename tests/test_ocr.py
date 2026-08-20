import asyncio
import unittest

from douyin_user_monitor.ocr import FakeOCRBackend, OCRResult, run_ocr


class OCRBackendTests(unittest.TestCase):
    def test_fake_backend_returns_text_and_counts_calls(self):
        backend = FakeOCRBackend(OCRResult("归墟 第32集", 0.95))
        result = run_ocr(backend, "https://example.invalid/cover.jpg", 1)
        self.assertEqual(result.text, "归墟 第32集")
        self.assertEqual(backend.calls, 1)

    def test_timeout_is_bounded(self):
        class Slow:
            async def extract_text(self, image_url):
                await asyncio.sleep(0.1)
                return OCRResult("late", 1)
        with self.assertRaises(asyncio.TimeoutError):
            run_ocr(Slow(), "image", 0.001)
