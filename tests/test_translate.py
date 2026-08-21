from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import StateStore
from translate import Translator, _useful_translation, fetch_mymemory


class UsefulTranslationTests(unittest.TestCase):
    def test_rejects_same_text_and_quota(self):
        self.assertIsNone(_useful_translation("Kuhar", "Kuhar"))
        self.assertIsNone(_useful_translation("Kuhar", "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY. NEXT AVAILABLE IN 21 HOURS"))
        self.assertEqual(_useful_translation("Kuhar", "Cook"), "Cook")


class TranslatorCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "t.sqlite3")
        self.calls: list[str] = []

        def fake_fetch(text: str) -> str | None:
            self.calls.append(text)
            return {"Kuhar": "Cook", "Bolnica": "Hospital"}.get(text)

        self.translator = Translator(store=self.store, fetch=fake_fetch, delay=0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_caches_hits_and_misses(self):
        self.assertEqual(self.translator.hr_en("Kuhar"), "Cook")
        self.assertIsNone(self.translator.hr_en("Unknown"))
        self.assertEqual(self.translator.hr_en("Kuhar"), "Cook")
        self.assertIsNone(self.translator.hr_en("Unknown"))
        self.assertEqual(self.calls, ["Kuhar", "Unknown"])

    def test_empty_input(self):
        self.assertIsNone(self.translator.hr_en("  "))
        self.assertEqual(self.calls, [])


class MyMemoryFetchTests(unittest.TestCase):
    def test_parses_response(self):
        class FakeResp:
            status_code = 200
            def json(self):
                return {"responseData": {"translatedText": "Cook"}}

        with patch("translate.requests.get", return_value=FakeResp()):
            self.assertEqual(fetch_mymemory("Kuhar"), "Cook")
