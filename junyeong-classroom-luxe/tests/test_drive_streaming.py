"""Offline regression tests for byte-range forwarding and Drive error triage."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_streaming import WINDOW_BYTES, capped_range, content_range_start, known_reason, should_retry


class DriveMediaRangeTests(unittest.TestCase):
    def test_normal_range(self):
        self.assertEqual(capped_range("bytes=100-200", 10000), (100, 200))

    def test_uncapped_browser_request_is_bounded(self):
        self.assertEqual(capped_range("bytes=0-", 2 * 1024**3), (0, WINDOW_BYTES-1))

    def test_continue_at_offset(self):
        self.assertEqual(capped_range("bytes=4194304-", 2 * 1024**3), (4194304, 2 * WINDOW_BYTES-1))

    def test_suffix_window(self):
        self.assertEqual(capped_range("bytes=-500", 2 * 1024**3), (2 * 1024**3-500, 2 * 1024**3-1))

    def test_large_suffix_still_bounded(self):
        start,end = capped_range("bytes=-10000000", 2 * 1024**3)
        self.assertEqual(end-start+1, WINDOW_BYTES)
        self.assertEqual(start, 2 * 1024**3 - 10000000)

    def test_no_range(self):
        self.assertIsNone(capped_range("", 4_000_000))

    def test_invalid_ranges(self):
        for bad in ("bytes=500-", "bytes=55-50", "bytes=0-3,8-10", "bytes=-0", "items=0-2", "bytes=-"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                capped_range(bad, 400)

    def test_content_range_validation(self):
        self.assertEqual(content_range_start("bytes 4096-8191/32768"), 4096)
        self.assertIsNone(content_range_start("invalid"))


class DriveErrorTests(unittest.TestCase):
    def test_known_error_is_bounded(self):
        self.assertEqual(known_reason({"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}), "userRateLimitExceeded")
        self.assertEqual(known_reason({"error":{"errors":[{"reason":"untrusted arbitrary message"}]}}), "unknown")
        self.assertEqual(known_reason(None), "unknown")

    def test_transient_quota_vs_permanent(self):
        self.assertTrue(should_retry(429, "unknown"))
        self.assertTrue(should_retry(503, "unknown"))
        self.assertTrue(should_retry(403, "userRateLimitExceeded"))
        self.assertFalse(should_retry(403, "downloadQuotaExceeded"))
        self.assertFalse(should_retry(404, "notFound"))


if __name__ == "__main__":
    unittest.main()
