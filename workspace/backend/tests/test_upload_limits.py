"""Regression coverage for bounded upload reads."""

import asyncio
import unittest
from unittest.mock import patch

from app.routers.files import _read_upload_limited


class _Upload:
    def __init__(self, content: bytes, declared_size=None):
        self.content = content
        self.size = declared_size
        self.position = 0
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        result = self.content[self.position:self.position + size]
        self.position += len(result)
        return result


class UploadLimitTests(unittest.TestCase):
    def test_oversize_is_rejected_without_unbounded_read(self):
        upload = _Upload(b"123456789")
        with patch("app.routers.files.config.MAX_FILE_SIZE", 8):
            self.assertIsNone(asyncio.run(_read_upload_limited(upload)))
        self.assertTrue(all(0 < size <= 9 for size in upload.read_sizes))

    def test_exact_limit_is_allowed(self):
        upload = _Upload(b"12345678")
        with patch("app.routers.files.config.MAX_FILE_SIZE", 8):
            self.assertEqual(asyncio.run(_read_upload_limited(upload)), b"12345678")

    def test_declared_oversize_skips_read(self):
        upload = _Upload(b"123456789", declared_size=9)
        with patch("app.routers.files.config.MAX_FILE_SIZE", 8):
            self.assertIsNone(asyncio.run(_read_upload_limited(upload)))
        self.assertEqual(upload.read_sizes, [])


if __name__ == "__main__":
    unittest.main()
