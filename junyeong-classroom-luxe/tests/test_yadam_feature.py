"""Offline regression tests; no YouTube network calls or API keys required."""
import importlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yadam_feature as yf


class YouTubeSpotlightTest(unittest.TestCase):
    def setUp(self):
        yf._CACHE.update(until=0.0, items=[], error="", updated=None)

    def test_no_key_does_not_fake_a_video(self):
        with patch.object(yf, "KEY", ""):
            result = yf.select_feature()
        self.assertEqual(result["status"], "setup_required")
        self.assertIsNone(result["video"])

    def test_excludes_previous_video_when_alternatives_exist(self):
        data = [{"id": "AAAAAAAAAAA"}, {"id": "BBBBBBBBBBB"}]
        with patch.object(yf, "KEY", "demo-key"), patch.object(yf, "_load", return_value=data):
            selected = yf.select_feature("AAAAAAAAAAA")
        self.assertEqual(selected["video"]["id"], "BBBBBBBBBBB")

    def test_candidates_are_sorted_by_views_relative_to_subscribers(self):
        posted = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        def fake_call(method, **params):
            if method == "search":
                return {"items": [
                    {"id": {"videoId": "AAAAAAAAAAA"}},
                    {"id": {"videoId": "BBBBBBBBBBB"}},
                ]}
            if method == "videos":
                return {"items": [
                    {"id": "AAAAAAAAAAA", "snippet": {
                        "title": "조선 야담 이야기", "description": "", "channelId": "chanA",
                        "channelTitle": "A", "publishedAt": posted},
                     "status": {"embeddable": True, "privacyStatus": "public"},
                     "contentDetails": {"duration": "PT25M0S"}, "statistics": {"viewCount": "25000"}},
                    {"id": "BBBBBBBBBBB", "snippet": {
                        "title": "옛날이야기 야담", "description": "", "channelId": "chanB",
                        "channelTitle": "B", "publishedAt": posted},
                     "status": {"embeddable": True, "privacyStatus": "public"},
                     "contentDetails": {"duration": "PT33M0S"}, "statistics": {"viewCount": "100000"}},
                ]}
            if method == "channels":
                return {"items": [
                    {"id": "chanA", "statistics": {"subscriberCount": "5000"}},
                    {"id": "chanB", "statistics": {"subscriberCount": "100000"}},
                ]}
            raise AssertionError(method)
        with patch.object(yf, "KEY", "demo-key"), patch.object(yf, "_call", side_effect=fake_call):
            results = yf._load()
        self.assertEqual([v["id"] for v in results], ["AAAAAAAAAAA", "BBBBBBBBBBB"])
        self.assertEqual(results[0]["ratio"], 5.0)
        self.assertEqual(results[1]["ratio"], 1.0)

    def test_short_or_unembeddable_items_are_rejected(self):
        posted = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        def fake_call(method, **params):
            if method == "search":
                return {"items": [{"id": {"videoId": "AAAAAAAAAAA"}}]}
            if method == "videos":
                return {"items": [{"id": "AAAAAAAAAAA", "snippet": {
                    "title": "야담", "channelId": "chanA", "publishedAt": posted},
                    "status": {"embeddable": False, "privacyStatus": "public"},
                    "contentDetails": {"duration": "PT55M"}, "statistics": {"viewCount": "99999"}}]}
            if method == "channels":
                return {"items": [{"id": "chanA", "statistics": {"subscriberCount": "100"}}]}
        with patch.object(yf, "KEY", "demo-key"), patch.object(yf, "_call", side_effect=fake_call):
            self.assertEqual(yf._load(), [])


if __name__ == "__main__":
    unittest.main()
