# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest import mock


class TestSentimentEnrich(unittest.TestCase):
    def setUp(self):
        import llm
        self.llm = llm
        llm._cache = None
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = llm.CACHE_FILE
        llm.CACHE_FILE = self.tmp.name

    def tearDown(self):
        self.llm.CACHE_FILE = self._orig
        self.llm._cache = None
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _items(self):
        return [{"id": "x1", "title_zh": "三星HBM5首次展示"}]

    def _run(self, fake):
        with mock.patch.object(self.llm, "_key", return_value="k"), \
             mock.patch.object(self.llm, "_call", return_value=fake):
            items = self._items()
            n = self.llm.enrich(items)
        return items, n

    def test_applies_sentiment(self):
        fake = [{"i": 1, "s": "存储链景气", "c": ["sector"], "imp": 7,
                 "fin": True, "rumor": False, "dir": "利多", "tgt": "存储芯片", "str": 2}]
        items, n = self._run(fake)
        self.assertEqual(n, 1)
        self.assertEqual(items[0]["sentiment"], {"dir": "利多", "tgt": "存储芯片", "str": 2})

    def test_neutral_kept_with_empty_tgt(self):
        fake = [{"i": 1, "s": "数据公布", "c": ["macro"], "imp": 5,
                 "fin": True, "rumor": False, "dir": "中性", "tgt": "", "str": 0}]
        items, _ = self._run(fake)
        self.assertEqual(items[0]["sentiment"]["dir"], "中性")
        self.assertEqual(items[0]["sentiment"]["tgt"], "")

    def test_missing_dir_is_graceful(self):
        fake = [{"i": 1, "s": "x", "c": [], "imp": 3, "fin": True, "rumor": False}]
        items, _ = self._run(fake)
        self.assertNotIn("sentiment", items[0])     # 模型没给 dir -> 不硬塞，不报错

    def test_disabled_no_sentiment(self):
        with mock.patch.object(self.llm, "_key", return_value=None):
            items = self._items()
            n = self.llm.enrich(items)
        self.assertEqual(n, 0)
        self.assertNotIn("sentiment", items[0])

    def test_sentiment_persists_in_cache(self):
        fake = [{"i": 1, "s": "存储链景气", "c": ["sector"], "imp": 7,
                 "fin": True, "rumor": False, "dir": "利多", "tgt": "存储芯片", "str": 2}]
        self._run(fake)
        # 第二次：缓存命中，_call 不应被调用，仍能还原 sentiment
        self.llm._cache = None
        with mock.patch.object(self.llm, "_key", return_value="k"), \
             mock.patch.object(self.llm, "_call",
                               side_effect=AssertionError("should not call")) as c:
            items = self._items()
            self.llm.enrich(items)
            c.assert_not_called()
        self.assertEqual(items[0]["sentiment"]["dir"], "利多")
