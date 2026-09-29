# -*- coding: utf-8 -*-
"""精选重做:打分输入带原文、缓存记 prompt 版本、新门槛只作用于新版 prompt 打的分、校准工具的指标。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))


class TestBuildLine(unittest.TestCase):
    def test_includes_original_but_not_source(self):
        import llm
        line = llm.build_line({"title_zh": "格罗方德新加坡产能吃紧", "source": "DIGITIMES·涨价追踪",
                               "body_excerpt": "矽光子擴產卡設備交期,先進封裝2027揭布局"})
        self.assertIn("产能吃紧", line)
        self.assertIn("矽光子擴產", line)
        self.assertNotIn("DIGITIMES", line)

    def test_prefers_title_orig_and_truncates(self):
        import llm
        line = llm.build_line({"title_zh": "英伟达大涨", "title_orig": "Nvidia soars " + "x" * 500,
                               "body_excerpt": "ignored"})
        self.assertIn("Nvidia soars", line)
        self.assertNotIn("ignored", line)
        self.assertLessEqual(len(line), 20 + 100 + llm.EXCERPT_CHARS)

    def test_no_duplicate_when_excerpt_equals_title(self):
        import llm
        self.assertEqual(llm.build_line({"title_zh": "同一句话", "body_excerpt": "同一句话"}), "标题:同一句话")

    def test_chinese_tweet_uses_longer_original(self):
        """中文推文:title_zh 是截到 120 字的改写,原文更长——给模型看原文,而不是只剩标题前 100 字。"""
        import llm
        orig = "英伟达将股票回购计划扩大1500亿美元," + "后续细节" * 40
        line = llm.build_line({"title_zh": orig[:120], "title_orig": orig})
        self.assertEqual(line, "标题:" + orig[:llm.EXCERPT_CHARS])


class TestPromptVersionAndThreshold(unittest.TestCase):
    def test_enrich_records_pv(self):
        import llm
        cache = {}
        with mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value=cache), \
                mock.patch.object(llm, "_save"), \
                mock.patch.object(llm, "_call", return_value=[{"i": 1, "imp": 8, "dir": "利多"}]):
            items = [{"id": "a", "title_zh": "t"}]
            llm.enrich(items)
        self.assertEqual(cache["a"]["pv"], llm.prompt_version())
        self.assertEqual(items[0]["llm_pv"], llm.prompt_version())

    def test_any_new_prompt_version_uses_new_threshold(self):
        """以后微调 prompt(pv 变了),7 分制的分也不能被退回旧刻度;库行没有 llm_pv 时按 id 回查缓存。"""
        import llm
        import newsfetch as nf
        it = {"id": "x1", "llm_importance": nf.LEGACY_SELECT_IMP, "source": "X·a"}
        with mock.patch.object(llm, "_load", return_value={"x1": {"imp": 6, "pv": "oldver01"}}):
            nf._finalize(it)
        self.assertEqual(it["selected"], nf.LEGACY_SELECT_IMP >= nf.SELECT_IMP)

    def test_new_threshold_only_for_current_prompt(self):
        import llm
        import newsfetch as nf
        cur = {"llm_importance": nf.LEGACY_SELECT_IMP, "llm_pv": llm.prompt_version(), "source": "X·a"}
        old = {"llm_importance": nf.LEGACY_SELECT_IMP, "llm_pv": None, "source": "X·a"}
        nf._finalize(cur)
        nf._finalize(old)
        self.assertEqual(cur["selected"], nf.LEGACY_SELECT_IMP >= nf.SELECT_IMP)
        self.assertTrue(old["selected"])                 # 旧版 prompt 的分沿用旧门槛
        top = {"llm_importance": nf.SELECT_IMP, "llm_pv": llm.prompt_version(), "source": "X·a"}
        nf._finalize(top)
        self.assertTrue(top["selected"])


class TestFinFalseNotDropped(unittest.TestCase):
    def test_x_items_judged_non_finance_are_kept_unselected(self):
        """新 prompt 初版把约 30% 的地缘推文判成 fin=false;X 推文只抓一次,丢了回不来。"""
        import newsfetch as nf
        import llm
        import xfetch
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

        def mk(iid, source, title):
            return {"id": iid, "source": source, "title_zh": title, "summary_zh": "", "body_excerpt": "",
                    "title_orig": "", "lang": "zh", "channels": ["finance"], "categories": [],
                    "markets": [], "tags": [], "entities": [], "heat": "", "read_count": None,
                    "verified": "confirmed", "published_at": now, "dedup_group": "g_" + iid, "score": 0.0}

        def fake_enrich(items, *a, **k):
            for it in items:
                it["llm_fin"] = False
                it["llm_importance"] = 8
            return len(items)

        with mock.patch.object(nf, "fetch_digitimes", return_value=[mk("d", "DIGITIMES·涨价追踪", "某新闻")]), \
                mock.patch.object(nf, "fetch_jin10", return_value=[]), \
                mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(xfetch, "fetch_x", return_value=[mk("x", "X·Clash Report", "伊朗无人机飞越埃尔比勒")]), \
                mock.patch.object(llm, "enrich", side_effect=fake_enrich):
            out = {it["id"]: it for it in nf.fetch_all()}
        self.assertIn("x", out)
        self.assertFalse(out["x"]["selected"])
        self.assertNotIn("d", out)                      # 非白名单源仍按原规则丢弃


class TestNvidiaBypass(unittest.TestCase):
    def test_bypass_needs_imp_5_on_new_scale(self):
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-test-token")
        import telegram_bot as tb
        nv = [{"name": tb.WATCH_ENTITY}]
        self.assertFalse(tb._worth_push({"selected": False, "entities": nv, "llm_importance": 4}))
        self.assertTrue(tb._worth_push({"selected": False, "entities": nv, "llm_importance": 5}))
        self.assertTrue(tb._worth_push({"selected": False, "entities": nv}))


class TestSelectionEvalMetrics(unittest.TestCase):
    def test_precision_recall_f1_and_volume(self):
        import selection_eval as se
        labels = {"a": 1, "b": 1, "c": 0, "d": 0}
        scores = {"a": 8, "b": 5, "c": 7, "d": 2}
        rows = {r["t"]: r for r in se.metrics(labels, scores)["rows"]}
        self.assertEqual((rows[7]["sel"], rows[7]["tp"]), (2, 1))
        self.assertAlmostEqual(rows[7]["p"], 0.5)
        self.assertAlmostEqual(rows[7]["r"], 0.5)
        self.assertAlmostEqual(rows[5]["r"], 1.0)
        vol = se.volume({"x": 8, "y": 6, "z": 3, "w": 7}, 400)
        self.assertAlmostEqual(vol[7], 200)

    def test_labels_jsonl_last_wins(self):
        import tempfile
        import selection_eval as se
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write('{"id": "a", "label": 1}\n{"id": "a", "label": 0}\n\n{"id": "b", "label": 1}\n{"id": "c", "la')
        try:
            self.assertEqual(se.load_labels(f.name), {"a": 0, "b": 1})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
