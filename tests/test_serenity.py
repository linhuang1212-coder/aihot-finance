# -*- coding: utf-8 -*-
import unittest
from tests._fixtures import SAMPLE_ITEMS


class TestBuildContext(unittest.TestCase):
    def test_selects_entity_items_sorted_by_score(self):
        import serenity
        ctx = serenity.build_context("英伟达", SAMPLE_ITEMS)
        # 只选命中英伟达的条目（a1,b2），不含白银(c3)
        self.assertEqual(ctx["based_on"], ["a1", "b2"])
        # 高分在前
        self.assertTrue(ctx["lines"].index("英伟达联手SK海力士")
                        < ctx["lines"].index("某基金增持英伟达3908股"))
        # sources 与 based_on 对齐
        self.assertEqual(len(ctx["sources"]), 2)
        self.assertEqual(ctx["sources"][0]["url"], "http://x/1")
        self.assertEqual(ctx["count"], 2)

    def test_no_match_returns_empty(self):
        import serenity
        ctx = serenity.build_context("茅台", SAMPLE_ITEMS)
        self.assertEqual(ctx["count"], 0)
        self.assertEqual(ctx["based_on"], [])


from unittest import mock
from tests._fixtures import SAMPLE_RSS


class TestFetchSupplement(unittest.TestCase):
    def test_parses_rss_titles_and_strips_source(self):
        import serenity
        with mock.patch("serenity._get_bytes", return_value=SAMPLE_RSS):
            out = serenity.fetch_supplement("英伟达", limit=5)
        self.assertEqual(out[0]["title"], "英伟达联手SK")
        self.assertEqual(out[0]["source"], "电子工程专辑")
        self.assertEqual(out[0]["url"], "http://g/1")
        self.assertEqual(len(out), 2)

    def test_network_failure_returns_empty(self):
        import serenity
        with mock.patch("serenity._get_bytes", side_effect=Exception("no vpn")):
            self.assertEqual(serenity.fetch_supplement("英伟达"), [])


import os
import tempfile
from tests._fixtures import SAMPLE_ITEMS as _ITEMS


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        import serenity
        self.serenity = serenity
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)  # 让缓存初始为"不存在"
        self._orig_cache = serenity.CACHE_FILE
        serenity.CACHE_FILE = self.tmp.name

    def tearDown(self):
        self.serenity.CACHE_FILE = self._orig_cache
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_ok_has_disclaimer_and_caches(self):
        s = self.serenity
        calls = {"n": 0}

        def fake_deepseek(system, user):
            calls["n"] += 1
            return "## 核心判断\n看上游 HBM，而非英伟达本身。"

        with mock.patch.object(s, "_deepseek", side_effect=fake_deepseek), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]), \
             mock.patch.object(s, "_gather_research", return_value={"text": "", "sources": []}):
            r1 = s.analyze("英伟达", _ITEMS)
            r2 = s.analyze("英伟达", _ITEMS)  # 第二次应命中缓存

        self.assertEqual(r1["status"], "ok")
        self.assertIn(s.DISCLAIMER, r1["analysis_md"])
        self.assertEqual(r1["based_on"], ["a1", "b2"])
        self.assertFalse(r1["cached"])
        self.assertTrue(r2["cached"])
        self.assertEqual(calls["n"], 1)  # 命中缓存，DeepSeek 只调一次

    def test_no_llm_when_key_missing(self):
        s = self.serenity
        with mock.patch.object(s.llm, "enabled", return_value=False):
            r = s.analyze("英伟达", _ITEMS)
        self.assertEqual(r["status"], "no_llm")
        self.assertIn("message", r)

    def test_no_data_when_no_items(self):
        s = self.serenity
        with mock.patch.object(s.llm, "enabled", return_value=True):
            r = s.analyze("茅台", _ITEMS)
        self.assertEqual(r["status"], "no_data")

    def test_error_when_deepseek_raises(self):
        s = self.serenity
        with mock.patch.object(s, "_deepseek", side_effect=Exception("timeout")), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]), \
             mock.patch.object(s, "_gather_research", return_value={"text": "", "sources": []}):
            r = s.analyze("英伟达", _ITEMS)
        self.assertEqual(r["status"], "error")

    def test_search_sources_footer_and_disclaimer_last(self):
        s = self.serenity
        research = {"text": "【联网搜索结果】\n[S1] 雪球HBM报告：62%",
                    "sources": [{"title": "雪球HBM报告", "url": "http://s/1", "source": "搜索"}],
                    "search": [{"title": "雪球HBM报告", "snippet": "62%", "url": "http://s/1"}]}
        with mock.patch.object(s, "_deepseek", return_value="## 核心判断\n看上游[S1]。"), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]), \
             mock.patch.object(s, "_gather_research", return_value=research):
            r = s.analyze("英伟达", _ITEMS)
        md = r["analysis_md"]
        self.assertIn("联网检索来源", md)         # 出处清单出现
        self.assertIn("http://s/1", md)           # 带链接
        self.assertIn("[S1]", md)                 # 编号引用
        self.assertTrue(md.rstrip().endswith(s.DISCLAIMER))  # 免责仍在最后

    def test_dedups_llm_paraphrased_disclaimer(self):
        s = self.serenity
        # LLM 自带一个措辞略不同的免责（不预测"价格"点位），最终应只保留一个规范免责
        body = ("## 核心判断\n看上游。\n\n---\n"
                "本分析为框架推理，非投资建议；不预测价格点位。来源真实 ≠ 内容已证实。")
        with mock.patch.object(s, "_deepseek", return_value=body), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]), \
             mock.patch.object(s, "_gather_research", return_value={"text": "", "sources": []}):
            r = s.analyze("英伟达", _ITEMS)
        md = r["analysis_md"]
        self.assertEqual(md.count("本分析为框架推理"), 1)     # 不重复
        self.assertTrue(md.rstrip().endswith(s.DISCLAIMER))


import os as _os
_FIX = _os.path.join(_os.path.dirname(__file__), "fixtures")


class TestFetchArticle(unittest.TestCase):
    def test_strips_html_and_truncates(self):
        import serenity
        html = b"<html><head><style>x{}</style><script>bad()</script></head><body><p>\xe6\xad\xa3\xe6\x96\x87\xe5\x86\x85\xe5\xae\xb9</p></body></html>"
        with mock.patch.object(serenity, "_get_bytes", return_value=html):
            txt = serenity._fetch_article("http://ex.com/a")
        self.assertIn("正文内容", txt)
        self.assertNotIn("bad()", txt)
        self.assertNotIn("<p>", txt)

    def test_skips_google_news_redirect(self):
        import serenity
        called = {"n": 0}

        def boom(*a, **k):
            called["n"] += 1
            raise AssertionError("should not fetch")

        with mock.patch.object(serenity, "_get_bytes", side_effect=boom):
            self.assertEqual(serenity._fetch_article("https://news.google.com/rss/articles/xyz"), "")
        self.assertEqual(called["n"], 0)

    def test_failure_returns_empty(self):
        import serenity
        with mock.patch.object(serenity, "_get_bytes", side_effect=Exception("timeout")):
            self.assertEqual(serenity._fetch_article("http://ex.com/a"), "")


class TestDdgSearch(unittest.TestCase):
    def test_parses_results(self):
        import serenity
        with open(_os.path.join(_FIX, "ddg.html"), "rb") as f:
            html = f.read()
        with mock.patch.object(serenity, "_get_bytes", return_value=html):
            out = serenity._ddg_search("英伟达 HBM")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["url"], "https://ex.com/1")
        self.assertIn("62%", out[0]["snippet"])

    def test_failure_returns_empty(self):
        import serenity
        with mock.patch.object(serenity, "_get_bytes", side_effect=Exception("blocked")):
            self.assertEqual(serenity._ddg_search("x"), [])


class TestQuoteBlock(unittest.TestCase):
    def test_us_stock_quote(self):
        import serenity
        items = [{"entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}]}]
        q = {"symbol": "NVDA", "price": 180.0, "change": 2.0, "pct": 1.1, "prev": 178.0}
        with mock.patch.object(serenity.market, "get_quote", return_value=q), \
             mock.patch.object(serenity.market, "next_earnings", return_value="2026-08-20"):
            out = serenity._quote_block("英伟达", items)
        self.assertIn("NVDA", out)
        self.assertIn("2026-08-20", out)

    def test_non_us_entity_empty(self):
        import serenity
        items = [{"entities": [{"name": "茅台", "code": "600519", "market": "a-share"}]}]
        self.assertEqual(serenity._quote_block("英伟达", items), "")


class TestGatherResearch(unittest.TestCase):
    def test_assembles_blocks_and_sources(self):
        import serenity
        items = [{"source_url": "http://ex.com/a", "entities": []}]
        with mock.patch.object(serenity, "_fetch_article", return_value="某篇正文摘录"), \
             mock.patch.object(serenity, "_ddg_search",
                               return_value=[{"title": "T1", "snippet": "SK海力士62%", "url": "http://s/1"}]), \
             mock.patch.object(serenity, "_quote_block", return_value="NVDA $180"):
            r = serenity._gather_research("英伟达", items)
        self.assertIn("某篇正文摘录", r["text"])
        self.assertIn("SK海力士62%", r["text"])
        self.assertIn("NVDA $180", r["text"])
        self.assertIn("[S1]", r["text"])               # 搜索结果带编号
        self.assertEqual(len(r["search"]), 1)          # 回传搜索清单供附出处
        self.assertEqual(r["sources"][0]["url"], "http://s/1")

    def test_disabled_returns_empty(self):
        import serenity
        with mock.patch.object(serenity, "DEEP_RESEARCH", False):
            r = serenity._gather_research("英伟达", [{"source_url": "http://ex.com/a"}])
        self.assertEqual(r["text"], "")


class TestRenderAnalysis(unittest.TestCase):
    def test_ok_renders_title_and_body(self):
        import telegram_bot
        r = {"status": "ok", "entity": "英伟达",
             "analysis_md": "## 核心判断\n看上游。", "sources": [], "based_on": []}
        out = telegram_bot.render_analysis(r)
        self.assertIn("英伟达", out)
        self.assertIn("看上游", out)

    def test_non_ok_renders_message(self):
        import telegram_bot
        r = {"status": "no_llm", "entity": "英伟达", "message": "未配置分析能力。"}
        out = telegram_bot.render_analysis(r)
        self.assertIn("未配置分析能力", out)


class TestWorthPush(unittest.TestCase):
    def test_selected_always_pushes(self):
        import telegram_bot as tb
        self.assertTrue(tb._worth_push({"selected": True, "entities": []}))

    def test_nvidia_gossip_below_threshold_not_pushed(self):
        # 回归：黄仁勋花边（importance=2）不该被推
        import telegram_bot as tb
        it = {"selected": False, "entities": [{"name": "英伟达"}], "llm_importance": 2}
        self.assertFalse(tb._worth_push(it))

    def test_nvidia_real_news_pushed(self):
        import telegram_bot as tb
        it = {"selected": False, "entities": [{"name": "英伟达"}], "llm_importance": 7}
        self.assertTrue(tb._worth_push(it))

    def test_nvidia_unjudged_still_pushed(self):
        # LLM 还没打分（imp 为空）→ 维持原行为照推
        import telegram_bot as tb
        it = {"selected": False, "entities": [{"name": "英伟达"}]}
        self.assertTrue(tb._worth_push(it))

    def test_non_nvidia_unselected_not_pushed(self):
        import telegram_bot as tb
        it = {"selected": False, "entities": [{"name": "茅台"}], "llm_importance": 8}
        self.assertFalse(tb._worth_push(it))


class TestPushRender(unittest.TestCase):
    """逐条独立推送：每条自带方向信号头(利多🔴/利空🟢/关注⚪)。"""

    def _it(self, title, sdir=None, tgt="", strn=0, source="财联社",
            heat="", entities=None, verified="confirmed"):
        it = {"id": title, "title_zh": title, "summary_zh": title + "摘要",
              "source": source, "source_url": "http://x/" + title,
              "published_at": "2026-06-09T19:05:00+08:00",
              "heat": heat, "verified": verified, "entities": entities or []}
        if sdir:
            it["sentiment"] = {"dir": sdir, "tgt": tgt, "str": strn}
        return it

    def test_bullish_header_with_target(self):
        import telegram_bot as tb
        out = tb.render_push_item(self._it("苹果AI升级周期", "利多", "苹果", 2))
        self.assertIn("🔴", out)
        self.assertIn("利多", out)
        self.assertIn("苹果", out)
        self.assertIn("苹果AI升级周期", out)

    def test_bearish_header(self):
        import telegram_bot as tb
        out = tb.render_push_item(self._it("半导体大跌", "利空", "半导体", 3))
        self.assertIn("🟢", out)
        self.assertIn("利空", out)

    def test_neutral_or_unjudged_is_guanzhu(self):
        import telegram_bot as tb
        self.assertIn("⚪", tb.render_push_item(self._it("美联储决议", "中性")))
        self.assertIn("关注", tb.render_push_item(self._it("某条无情绪")))

    def test_single_item_not_combined(self):
        # 单条渲染：不含分组分隔线/计数头（确认不是合并格式）
        import telegram_bot as tb
        out = tb.render_push_item(self._it("利多A", "利多", "存储", 2))
        self.assertNotIn("━━━", out)
        self.assertNotIn("新消息 ·", out)

    def test_markers_heat_unverified_sector(self):
        # 贴图版结构：热度->头部🔥，未证实->方向行⚠️，涉及板块取 tgt(优先于实体)
        import telegram_bot as tb
        out = tb.render_push_item(self._it(
            "三星HBM5", "利多", "存储", 3, heat="沸",
            entities=[{"name": "美光", "code": "MU"}], verified="unverified"))
        self.assertIn("🔥", out)                       # 热度
        self.assertIn("⚠️未证实", out)                 # 未证实
        self.assertIn("🎯 <b>涉及板块:</b> 存储", out)  # tgt 优先于实体


class TestSelectFresh(unittest.TestCase):
    def test_cap_keeps_most_recent_in_order(self):
        import telegram_bot as tb
        # 喂入超过 cap 的新条目：最近的应保留、最早的被裁（旧实现用 set->list 顺序随机会丢最近）
        cur = [{"id": "id%d" % i, "selected": False, "dedup_group": None, "entities": []}
               for i in range(tb.SEEN_CAP + 100)]
        _, new_seen, _ = tb._select_fresh(cur, [], [])
        self.assertEqual(len(new_seen), tb.SEEN_CAP)
        self.assertIn("id%d" % (tb.SEEN_CAP + 99), new_seen)   # 最新的保留
        self.assertNotIn("id0", new_seen)                      # 最早的被裁

    def test_seen_id_not_repushed(self):
        import telegram_bot as tb
        cur = [{"id": "x", "selected": True, "dedup_group": "g", "entities": []}]
        fresh, new_seen, _ = tb._select_fresh(cur, ["x"], ["g"])
        self.assertEqual(fresh, [])                            # 已读 -> 不重推
        self.assertIn("x", new_seen)

    def test_group_dedup_pushes_once(self):
        import telegram_bot as tb
        cur = [{"id": "a", "selected": True, "dedup_group": "g", "entities": []},
               {"id": "b", "selected": True, "dedup_group": "g", "entities": []}]
        fresh, _, _ = tb._select_fresh(cur, [], [])
        self.assertEqual(len(fresh), 1)                        # 同事件只推一条


class TestSplitText(unittest.TestCase):
    def test_short_stays_one_chunk(self):
        import telegram_bot as tb
        self.assertEqual(tb._split_text("短文本"), ["短文本"])

    def test_long_splits_on_newlines_lossless(self):
        import telegram_bot as tb
        text = "\n".join(("第%d段" % i) * 30 for i in range(200))  # 远超单条上限
        chunks = tb._split_text(text, limit=1000)
        self.assertTrue(len(chunks) > 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("\n".join(chunks), text)  # 段落边界切，无损重组

    def test_single_overlong_paragraph_hard_split(self):
        import telegram_bot as tb
        chunks = tb._split_text("X" * 2500, limit=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("".join(chunks), "X" * 2500)