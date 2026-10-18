"""Offline R2 free-budget and 2GB multipart upload planning tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import r2_storage as storage


class R2PlanningTests(unittest.TestCase):
    def test_two_gib_in_16_mib_chunks(self):
        self.assertEqual(storage.part_count(2*1024**3),128)
        self.assertEqual(storage.expected_part_bytes(2*1024**3,1),storage.CHUNK_SIZE)
        self.assertEqual(storage.expected_part_bytes(2*1024**3,128),storage.CHUNK_SIZE)

    def test_partial_final_chunk(self):
        n=2*storage.CHUNK_SIZE+1024
        self.assertEqual(storage.part_count(n),3)
        self.assertEqual(storage.expected_part_bytes(n,3),1024)

    def test_invalid_part_numbers(self):
        for n in (0,2,-1):
            with self.subTest(n=n),self.assertRaises(ValueError):
                storage.expected_part_bytes(100,n)

    def test_free_budget_clamped(self):
        self.assertEqual(storage.budget_for({}),storage.DEFAULT_BUDGET)
        self.assertEqual(storage.budget_for({"R2_FREE_BUDGET_BYTES":"999999999999"}),storage.DEFAULT_BUDGET)
        self.assertEqual(storage.budget_for({"R2_FREE_BUDGET_BYTES":"5000000000"}),5_000_000_000)
        self.assertEqual(storage.budget_for({"R2_FREE_BUDGET_BYTES":"abc"}),storage.DEFAULT_BUDGET)

    def test_requires_four_credentials(self):
        self.assertFalse(storage.configured({}))
        self.assertFalse(storage.configured({"R2_ACCOUNT_ID":"x"}))
        self.assertTrue(storage.configured({
            "R2_ACCOUNT_ID":"a"*32, "R2_BUCKET":"example",
            "R2_ACCESS_KEY_ID":"example", "R2_SECRET_ACCESS_KEY":"example"
        }))

    def test_key_pattern_cannot_use_other_buckets(self):
        self.assertTrue(storage._KEY_PATTERN.fullmatch("lessons/"+"a"*32+".mp4"))
        self.assertFalse(storage._KEY_PATTERN.fullmatch("../../sensitive"))
        self.assertFalse(storage._KEY_PATTERN.fullmatch("lessons/"+"a"*32+".exe"))


if __name__=="__main__":
    unittest.main()
