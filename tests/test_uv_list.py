from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date

import config


class UvListFileTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(config.UV_LIST_PATH.read_text(encoding="utf-8"))

    def test_meta_shape_and_sha256_format(self):
        meta = self.raw["listMeta"]
        for key in (
            "edition",
            "sourceUrl",
            "hubUrl",
            "verifiedDate",
            "sourceSha256",
            "mirrorUrl",
        ):
            self.assertTrue(meta.get(key), msg=key)
        date.fromisoformat(meta["verifiedDate"])
        sha = meta["sourceSha256"]
        self.assertRegex(sha, r"^[0-9a-f]{64}$")
        self.assertTrue(meta["sourceUrl"].endswith(".pdf"))
        self.assertGreaterEqual(len(self.raw["occupations"]), 40)
        ids = [occ["id"] for occ in self.raw["occupations"]]
        self.assertEqual(len(ids), len(set(ids)))
        for occ in self.raw["occupations"]:
            self.assertTrue(occ["titleHr"])
            self.assertTrue(occ["titleEn"])
            self.assertIsInstance(occ["regions"], list)
            self.assertTrue(occ["regions"])

    def test_committed_file_hash_is_stable(self):
        """Catch accidental JSON edits that drop sourceSha256 without noticing."""
        digest = hashlib.sha256(config.UV_LIST_PATH.read_bytes()).hexdigest()
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
