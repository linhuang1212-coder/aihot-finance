# -*- coding: utf-8 -*-
"""2026-10-06 口径放宽 + 模型判重 + 断供追补:
- 模型在 dup 字段里指认「与近期已推送的某条是同一件事」-> 不再推送;
- 被判非财经且极低分的 X 碎片不入库;DeepSeek 的拒绝回话不当译文;只有链接的推文跳过;
- X 水位落后太多时按时间窗一段段补;回补脚本产出的补丁缓存在 server 启动时并入。"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

CST = timezone(timedelta(hours=8))


def _now(minutes_ago=0):
    return (datetime.now(CST) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class TestDupResolve(unittest.TestCase):
    def test_refs(self):
        import llm
        recent = [{"id": "r1"}, {"id": "r2"}]
        batch = [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}]
        self.assertEqual(llm._resolve_dup("R2", recent, batch, 0), "r2")
        self.assertEqual(llm._resolve_dup(" r1 ", recent, batch, 0), "r1")
        self.assertEqual(llm._resolve_dup("B1", recent, batch, 2), "b1")
        self.assertIsNone(llm._resolve_dup("B3", recent, batch, 2))     # 指向自己
        self.assertIsNone(llm._resolve_dup("B3", recent, batch, 1))     # 指向后面的
        self.assertIsNone(llm._resolve_dup("R9", recent, batch, 0))     # 越界
        for bad in ("", None, "重复", "R", "3", "R0"):
            self.assertIsNone(llm._resolve_dup(bad, recent, batch, 1), bad)

    def test_recent_block_format(self):
        import llm
        blk = llm._recent_block([{"id": "a", "title": "美债收益率\n创新高"}, {"id": "b", "title": "x" * 200}])
        self.assertIn("R1. 美债收益率 创新高", blk)
        self.assertIn("R2. " + "x" * llm.RECENT_TITLE_CHARS, blk)
        self.assertNotIn("x" * (llm.RECENT_TITLE_CHARS + 1), blk)
        self.assertEqual(llm._recent_block([]), "")


class TestEnrichDedup(unittest.TestCase):
    def _run(self, items, replies, recent=None):
        import llm
        seen_blocks = []

        def fake_call(lines, recent_block=""):
            seen_blocks.append(recent_block)
            return replies.pop(0)

        cache = {}
        with mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value=cache), \
                mock.patch.object(llm, "_save"), \
                mock.patch.object(llm, "_call", side_effect=fake_call), \
                mock.patch.object(llm, "BATCH", 2):
            llm.enrich(items, recent=recent, push_imp=6)
        return cache, seen_blocks

    def test_dup_of_recent_and_of_earlier_in_batch(self):
        items = [{"id": "a", "title_zh": "10年期美债收益率升至5.33%"},
                 {"id": "b", "title_zh": "美国基准收益率升至2002年以来最高"}]
        cache, blocks = self._run(items, [[{"i": 1, "imp": 8, "dup": "R1"}, {"i": 2, "imp": 8, "dup": "B1"}]],
                                  recent=[{"id": "old", "title": "美国10年期国债收益率触及24年高位"}])
        self.assertEqual(cache["a"]["dup"], "old")
        self.assertEqual(cache["b"]["dup"], "a")
        self.assertEqual(items[0]["llm_dup"], "old")
        self.assertIn("R1. 美国10年期国债收益率触及24年高位", blocks[0])

    def test_pushworthy_items_roll_into_recent_for_later_batches(self):
        items = [{"id": "a", "title_zh": "博通将向Anthropic提供420亿美元贷款"},
                 {"id": "b", "title_zh": "某条低分闲聊"},
                 {"id": "c", "title_zh": "博通拟贷款420亿美元给Anthropic"}]
        cache, blocks = self._run(items, [
            [{"i": 1, "imp": 7, "dup": ""}, {"i": 2, "imp": 3, "dup": ""}],
            [{"i": 1, "imp": 7, "dup": "R1"}]])
        self.assertEqual(blocks[0], "")                                     # 起初没有已推送
        self.assertIn("R1. 博通将向Anthropic提供420亿美元贷款", blocks[1])    # 第一批过线的进了列表
        self.assertNotIn("低分闲聊", blocks[1])                              # 没过线的不进
        self.assertEqual(cache["c"]["dup"], "a")
        self.assertNotIn("dup", cache["a"])

    def test_dup_is_not_pushed_but_kept(self):
        import llm
        import newsfetch as nf
        it = {"id": "x", "source": "X·a", "llm_importance": 8, "llm_pv": llm.prompt_version(), "llm_dup": "old"}
        nf._finalize(it)
        self.assertFalse(it["selected"])
        ok = {"id": "y", "source": "X·a", "llm_importance": 6, "llm_pv": llm.prompt_version()}
        nf._finalize(ok)
        self.assertTrue(ok["selected"])                                      # 6 = 相关且有新信息 -> 推


class TestFetchAllBroad(unittest.TestCase):
    def _fetch(self, fake_enrich, xitems):
        import newsfetch as nf
        import llm
        import xfetch
        import store
        with mock.patch.object(nf, "fetch_digitimes", return_value=[]), \
                mock.patch.object(nf, "fetch_jin10", return_value=[]), \
                mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(xfetch, "fetch_x", return_value=xitems), \
                mock.patch.object(store, "recent_selected", return_value=[{"id": "old", "title": "旧闻"}]), \
                mock.patch.object(llm, "enrich", side_effect=fake_enrich):
            return {it["id"]: it for it in nf.fetch_all()}

    def _x(self, iid, title):
        return {"id": iid, "source": "X·Test", "title_zh": title, "summary_zh": "", "body_excerpt": "",
                "title_orig": "", "lang": "zh", "channels": ["finance"], "categories": [], "markets": [],
                "tags": [], "entities": [], "heat": "", "read_count": None, "verified": "confirmed",
                "published_at": _now(), "dedup_group": "g_" + iid, "score": 0.0}

    def test_recent_selected_passed_to_model_and_junk_dropped(self):
        seen = {}

        def fake_enrich(items, *a, **k):
            seen.update(k)
            for it in items:
                it["llm_pv"] = "pv"
                if it["id"] == "junk":
                    it["llm_fin"], it["llm_importance"] = False, 1
                elif it["id"] == "odd":
                    it["llm_fin"], it["llm_importance"] = False, 6
                elif it["id"] == "dup":
                    it["llm_fin"], it["llm_importance"], it["llm_dup"] = True, 8, "old"
                else:
                    it["llm_fin"], it["llm_importance"] = True, 6
            return len(items)

        out = self._fetch(fake_enrich, [self._x("junk", "@某人 是的"), self._x("odd", "某条被误判的"),
                                        self._x("dup", "同一件事的另一种说法"), self._x("ok", "某车企交付量增长")])
        self.assertEqual(seen["recent"], [{"id": "old", "title": "旧闻"}])
        import newsfetch as nf
        self.assertEqual(seen["push_imp"], nf.SELECT_IMP)
        self.assertNotIn("junk", out)                    # 非财经 + 极低分:不入库
        self.assertIn("odd", out)                        # 非财经但分不低:留着,不进精选(防误判丢数据)
        self.assertFalse(out["odd"]["selected"])
        self.assertFalse(out["dup"]["selected"])         # 重复:留在全量,不推
        self.assertTrue(out["ok"]["selected"])


class TestRecentSelected(unittest.TestCase):
    def test_only_selected_within_window_newest_first(self):
        import store
        tmp = tempfile.mkdtemp(prefix="aihot_rs_")
        orig = store.DB_FILE
        store.DB_FILE = os.path.join(tmp, "news.db")
        try:
            store.init_db()

            def it(iid, sel, mins):
                return {"id": iid, "published_at": _now(mins), "title_zh": "标题" + iid * 8, "summary_zh": "",
                        "body_excerpt": "", "source": "X·" + iid, "source_url": "", "lang": "zh",
                        "channels": ["finance"], "categories": [], "tags": [], "entities": [], "markets": [],
                        "heat": "", "read_count": None, "verified": "confirmed", "dedup_group": "g" + iid,
                        "score": 6, "selected": sel, "dup_count": 1}
            store.upsert_items([it("a", True, 10), it("b", False, 5), it("c", True, 1), it("d", True, 60 * 30)])
            self.assertEqual([r["id"] for r in store.recent_selected()], ["c", "a"])
            # 断供追补:按那一批的时间点取(只看它之前的)
            self.assertEqual([r["id"] for r in store.recent_selected(before=_now(8))], ["a"])
            self.assertEqual([r["id"] for r in store.recent_selected(before=_now(60 * 29))], ["d"])
        finally:
            store.DB_FILE = orig
            shutil.rmtree(tmp, ignore_errors=True)


class TestTranslateRefusal(unittest.TestCase):
    def test_refusal_text_is_not_a_translation(self):
        """DeepSeek 对只有链接的输入回「抱歉,我无法访问该链接…」,曾被当成译文写进标题。"""
        import newsfetch as nf
        import llm
        cache = {}
        with mock.patch.object(nf, "_load_trans", return_value=cache), \
                mock.patch.object(llm, "translate",
                                  return_value="抱歉，我无法访问该链接。请提供需要翻译的文本内容。"), \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}):
            nf.reset_translate_round()
            self.assertEqual(nf.translate("https://t.co/abc"), "https://t.co/abc")
            self.assertEqual(cache, {})
            self.assertEqual(nf._trans_fail, 0)          # 不算服务故障,不触发熔断

    def test_normal_text_mentioning_sorry_is_fine(self):
        import newsfetch as nf
        import llm
        zh = "特朗普谈加拿大：他们会进来说“先生，我们很抱歉”"
        with mock.patch.object(nf, "_load_trans", return_value={}), \
                mock.patch.object(llm, "translate", return_value=zh), \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}):
            nf.reset_translate_round()
            self.assertEqual(nf.translate("Trump on Canada: ..."), zh)


class TestXfetchCatchup(unittest.TestCase):
    def _run(self, since_ago_min, tweets=None):
        import xfetch
        calls, saved = [], {}
        now = int(datetime.now(CST).timestamp())

        def fake_call(query, cursor=""):
            calls.append(query)
            return {"tweets": tweets or [], "has_next_page": False}

        with mock.patch.object(xfetch, "enabled", return_value=True), \
                mock.patch.object(xfetch, "_accounts", return_value=[{"user": "a", "label": "A", "group": "kol"}]), \
                mock.patch.object(xfetch, "_load_state", return_value={"last_since_time": now - since_ago_min * 60}), \
                mock.patch.object(xfetch, "_save_state", side_effect=saved.update), \
                mock.patch.object(xfetch, "_call", side_effect=fake_call), \
                mock.patch("builtins.print"):
            items = xfetch.fetch_x()
        return calls, saved, now, items

    def test_normal_round_has_no_until(self):
        calls, saved, now, _ = self._run(5)
        self.assertNotIn("until_time", calls[0])
        self.assertGreaterEqual(saved["last_since_time"], now - 61)

    def test_big_gap_fetches_one_window_and_advances_only_that_far(self):
        """欠费两天后恢复:每轮只补水位之后的一段,水位只推进到这一段的末尾(原来直接跳到现在,中间全丢)。"""
        import xfetch
        calls, saved, now, _ = self._run(60 * 50)
        since = now - 60 * 50 * 60
        until = since + xfetch.CATCHUP_WINDOW_MIN * 60
        self.assertIn("since_time:%d until_time:%d" % (since, until), calls[0])
        self.assertEqual(saved["last_since_time"], until - xfetch.OVERLAP_SEC)

    def test_last_window_is_clamped_to_now(self):
        import xfetch
        calls, saved, now, _ = self._run(xfetch.CATCHUP_GAP_MIN + 10)
        self.assertRegex(calls[0], r"until_time:\d+")
        self.assertLessEqual(saved["last_since_time"], now)
        self.assertGreaterEqual(saved["last_since_time"], now - 61)

    def test_link_only_tweets_skipped(self):
        tw = [{"id": "1", "text": "https://t.co/abc", "author": {"userName": "a"}, "createdAt": ""},
              {"id": "2", "text": "@someone https://t.co/x", "author": {"userName": "a"}, "createdAt": ""},
              {"id": "3", "text": "美联储加息25个基点", "author": {"userName": "a"}, "createdAt": ""}]
        import newsfetch
        with mock.patch.object(newsfetch, "translate", side_effect=lambda t, *a, **k: t):
            _, _, _, items = self._run(5, tweets=tw)
        self.assertEqual([it["title_zh"] for it in items], ["美联储加息25个基点"])


class TestCachePatchMerge(unittest.TestCase):
    def test_backfill_patch_merged_on_load(self):
        import llm
        tmp = tempfile.mkdtemp(prefix="aihot_patch_")
        path = os.path.join(tmp, "llm_cache.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"a": {"imp": 5, "pv": "old"}, "keep": {"imp": 3}}, f)
            with open(path + ".patch", "w", encoding="utf-8") as f:
                json.dump({"a": {"imp": 7, "pv": "new"}, "b": {"imp": 6, "pv": "new"}}, f)
            with mock.patch.object(llm, "CACHE_FILE", path), mock.patch.object(llm, "_cache", None), \
                    mock.patch("builtins.print"):
                cache = llm._load()
                self.assertEqual(cache["a"]["imp"], 7)
                self.assertEqual(cache["b"]["imp"], 6)
                self.assertEqual(cache["keep"]["imp"], 3)
            self.assertFalse(os.path.exists(path + ".patch"))
            self.assertTrue(os.path.exists(path + ".patch.applied"))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["a"]["pv"], "new")           # 已落盘
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
