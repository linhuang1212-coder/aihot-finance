# -*- coding: utf-8 -*-
"""telegram_bot.render_push_item 单条推送格式（贴图版结构）。

目标结构（用户 2026-06-10 指定，见那张「路透×彭博社快讯」图）：
    📊 快讯捕捉 | 来源: <source>            (heat -> 末尾 🔥)
    📝 翻译:/标题: <title_zh>               (英文源=翻译, 中文源=标题)
    💡 AI 独家解读:
    <summary_zh>                            (与标题相同则整块省略)
    🎯 涉及板块: <tgt 或 entities>
    🟢 利空 / 🔴 利多 / ⚪ 关注              (独立一行, A股语境)
    🕐 时间: <MM-DD HH:MM>
    🔤 原文: <title_orig>                    (仅英文源)
    🔗 查看详情                              (source_url 链接)
"""
import unittest

import telegram_bot as tb


def _en_item(**kw):
    it = {
        "id": "en1",
        "title_zh": "美国寻求快速修复以提升委内瑞拉石油产量",
        "summary_zh": "委内瑞拉增产或增全球供给，油价承压，利空能源股",
        "title_orig": "US seeks quick repairs to lift Venezuela oil output",
        "source": "Bloomberg", "source_url": "http://x/en1",
        "published_at": "2026-06-10T00:08:00+08:00", "lang": "en",
        "entities": [], "heat": "", "verified": "confirmed",
        "sentiment": {"dir": "利空", "tgt": "原油 / 能源股", "str": 3},
    }
    it.update(kw)
    return it


def _zh_item(**kw):
    it = {
        "id": "zh1",
        "title_zh": "三星HBM5首次展示，量产在即",
        "summary_zh": "三星HBM5进展，利好存储/HBM产业链",
        "source": "华尔街见闻", "source_url": "http://x/zh1",
        "published_at": "2026-06-10T09:15:00+08:00", "lang": "zh",
        "entities": [], "heat": "", "verified": "confirmed",
        "sentiment": {"dir": "利多", "tgt": "存储芯片", "str": 2},
    }
    it.update(kw)
    return it


class TestRenderPushItem(unittest.TestCase):

    def test_en_has_all_labeled_blocks_in_order(self):
        out = tb.render_push_item(_en_item())
        # 头部含来源
        self.assertIn("📊 <b>快讯捕捉</b> | 来源: Bloomberg", out)
        # 英文源用「翻译」
        self.assertIn("📝 <b>翻译:</b> 美国寻求快速修复以提升委内瑞拉石油产量", out)
        # AI 解读块
        self.assertIn("💡 <b>AI 独家解读:</b>", out)
        self.assertIn("委内瑞拉增产或增全球供给，油价承压，利空能源股", out)
        # 涉及板块
        self.assertIn("🎯 <b>涉及板块:</b> 原油 / 能源股", out)
        # 独立方向行
        self.assertIn("🟢 <b>利空</b>", out)
        # 时间
        self.assertIn("🕐 <b>时间:</b> 06-10 00:08", out)
        # 原文（仅英文源）
        self.assertIn("🔤 <b>原文:</b> US seeks quick repairs to lift Venezuela oil output", out)
        # 查看详情链接
        self.assertIn('<a href="http://x/en1">🔗 查看详情</a>', out)
        # 顺序：翻译 在 解读 之前，解读 在 板块 之前，方向 在 时间 之前，原文 在 末尾
        self.assertLess(out.index("翻译:"), out.index("AI 独家解读"))
        self.assertLess(out.index("AI 独家解读"), out.index("涉及板块"))
        self.assertLess(out.index("利空</b>"), out.index("时间:"))
        self.assertLess(out.index("时间:"), out.index("原文:"))

    def test_blocks_separated_by_blank_line(self):
        # 用户 2026-06-10 反馈"有点挤"：块与块之间空一行
        out = tb.render_push_item(_en_item())
        blocks = out.split("\n\n")
        self.assertEqual(blocks[-1], "⠀")  # 末块是底部垫白(2026-06-12,见 TestCardBottomPadding)
        self.assertEqual(len(blocks) - 1, 8)   # 内容块:头/标题/解读/板块/方向/时间/原文/链接
        # 解读块内部（标签行与正文）仍是单换行，不拆成两块
        self.assertIn("AI 独家解读:</b>\n委内瑞拉", out)

    def test_li_duo_uses_red_circle(self):
        out = tb.render_push_item(_zh_item())
        self.assertIn("🔴 <b>利多</b>", out)
        self.assertNotIn("🟢", out)

    def test_zh_uses_biaoti_label_and_no_yuanwen(self):
        out = tb.render_push_item(_zh_item())
        self.assertIn("📝 <b>标题:</b> 三星HBM5首次展示，量产在即", out)
        self.assertNotIn("翻译:", out)
        self.assertNotIn("原文:", out)
        self.assertNotIn("🔤", out)

    def test_neutral_shows_guanzhu(self):
        out = tb.render_push_item(_en_item(sentiment={"dir": "中性", "tgt": "", "str": 0}))
        self.assertIn("⚪ <b>关注</b>", out)
        # 中性无 tgt -> 不出板块行
        self.assertNotIn("涉及板块", out)

    def test_missing_sentiment_falls_back_to_guanzhu(self):
        it = _en_item()
        it.pop("sentiment")
        out = tb.render_push_item(it)
        self.assertIn("⚪ <b>关注</b>", out)

    def test_heat_appends_fire_to_header(self):
        out = tb.render_push_item(_en_item(heat="🔥1234热度"))
        self.assertIn("来源: Bloomberg 🔥", out)

    def test_no_heat_no_fire(self):
        out = tb.render_push_item(_en_item())
        self.assertNotIn("🔥", out)

    def test_unverified_marks_direction_line(self):
        out = tb.render_push_item(_en_item(verified="unverified"))
        self.assertIn("⚠️未证实", out)

    def test_summary_equal_title_hides_jiedu_block(self):
        out = tb.render_push_item(_zh_item(summary_zh="三星HBM5首次展示，量产在即"))
        self.assertNotIn("AI 独家解读", out)

    def test_tgt_empty_falls_back_to_entities(self):
        it = _en_item(sentiment={"dir": "利空", "tgt": "", "str": 1},
                      entities=[{"name": "英伟达"}, {"name": "美光科技"}])
        out = tb.render_push_item(it)
        self.assertIn("🎯 <b>涉及板块:</b> 英伟达 美光科技", out)

    def test_html_escaped(self):
        out = tb.render_push_item(_zh_item(title_zh="A&B <已> 涨", summary_zh="x"))
        self.assertIn("A&amp;B &lt;已&gt; 涨", out)
        self.assertNotIn("<已>", out)

    def test_segments_rendered(self):
        out = tb.render_push_item(_zh_item(segments=["第二段陈述", "第三段陈述"]))
        self.assertIn("▫️ 第二段陈述", out)
        self.assertIn("▫️ 第三段陈述", out)

    def test_no_segments_no_bullet(self):
        out = tb.render_push_item(_zh_item())
        self.assertNotIn("▫️", out)


class _NoSleep:
    def sleep(self, *a):
        pass


class TestSendCards(unittest.TestCase):
    """命令查询(/hot 等)也逐条发卡片：每条独立一条消息，不合并。"""

    def setUp(self):
        self.sent = []
        self._orig_send = tb.send
        self._orig_time = tb.time
        tb.send = lambda chat, text: self.sent.append(text)
        tb.time = _NoSleep()          # 跳过逐条节流 sleep，不碰真 time 模块

    def tearDown(self):
        tb.send = self._orig_send
        tb.time = self._orig_time

    def test_one_message_per_item_not_merged(self):
        items = [_zh_item(id="a", title_zh="标题A"),
                 _en_item(id="b", title_zh="标题B")]
        tb.send_cards(1, "📈 金融 · 精选", items)
        self.assertEqual(len(self.sent), 3)          # 1 标题 + 2 卡片
        self.assertIn("金融 · 精选", self.sent[0])     # 首条是小标题
        self.assertIn("标题A", self.sent[1])           # 卡片走 render_push_item
        self.assertIn("📊 <b>快讯捕捉</b>", self.sent[1])

    def test_caps_at_limit(self):
        items = [_zh_item(id=str(i)) for i in range(10)]
        tb.send_cards(1, "T", items, limit=4)
        self.assertEqual(len(self.sent), 1 + 4)        # 标题 + 上限4卡片

    def test_empty_sends_single_placeholder(self):
        tb.send_cards(1, "🔎 搜索：xyz", [])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("暂无", self.sent[0])
        self.assertIn("搜索：xyz", self.sent[0])


class TestRenderSpacing(unittest.TestCase):
    """bot 消息排版:所有块/条目之间必须空一行（用户 2026-06-12 反馈,延续 06-10 卡片块间空行约定）。"""

    def _assert_blank_line_separated(self, text):
        lines = text.split("\n")
        for a, b in zip(lines, lines[1:]):
            self.assertFalse(a.strip() and b.strip(),
                             "相邻两行都非空(缺空行): %r | %r" % (a, b))

    def test_daily_blocks_blank_line_separated(self):
        d = {"date": "2026-06-12", "sections": [
            {"label": "宏观", "items": [
                {"title_zh": "A", "source_url": "http://x/a", "source": "金十"},
                {"title_zh": "B", "source_url": "http://x/b", "source": "金十"},
            ]},
            {"label": "大宗", "items": [
                {"title_zh": "C", "source_url": "http://x/c", "source": "金十"},
            ]},
        ]}
        out = tb.render_daily(d)
        self._assert_blank_line_separated(out)
        self.assertIn("📰 金融日报 · 2026-06-12", out)
        for t in ("A", "B", "C"):
            self.assertIn(">%s</a>" % t, out)

    def test_daily_empty_placeholder_spaced(self):
        self._assert_blank_line_separated(tb.render_daily({"sections": []}))

    def test_quotes_blank_line_separated(self):
        from unittest import mock
        q = {"symbol": "NVDA", "price": 100.0, "change": 1.0, "pct": 1.0, "prev": 99.0}
        with mock.patch.object(tb.market, "enabled", return_value=True), \
             mock.patch.object(tb.market, "watchlist_symbols",
                               return_value=[("英伟达", "NVDA"), ("美光", "MU")]), \
             mock.patch.object(tb.market, "get_quote", return_value=q):
            out = tb.render_quotes()
        self._assert_blank_line_separated(out)
        self.assertIn("自选股实时报价", out)
        self.assertEqual(out.count("NVDA"), 2)   # 两个标的都在(mock 同一报价)

    def test_cards_empty_placeholder_spaced(self):
        sent = []
        orig = tb.send
        tb.send = lambda chat, text: sent.append(text)
        try:
            tb.send_cards(1, "🔎 搜索:xyz", [])
        finally:
            tb.send = orig
        self._assert_blank_line_separated(sent[0])


class TestCardBottomPadding(unittest.TestCase):
    """卡片底部垫空白(空行+盲文空白符U+2800),拉开 Telegram 相邻卡片的视觉距离。
    用户 2026-06-12 反馈:卡片之间太紧凑。普通空格/换行会被 Telegram 裁掉,U+2800 不会。"""

    def test_push_card_ends_with_padding(self):
        out = tb.render_push_item(_en_item())
        self.assertTrue(out.endswith("\n\n⠀"), "卡片末尾应为空行+U+2800 垫白")
        # 垫白在链接之后(链接仍是最后一个内容块)
        self.assertLess(out.index("查看详情"), out.index("⠀"))

    def test_prop_alert_inherits_padding(self):
        it = _en_item(propagation={"level": 1, "window_min": 90, "sources": 3},
                      llm_importance=8, dedup_group="g1")
        self.assertTrue(tb.render_prop_alert(it).endswith("\n\n⠀"))


class TestPropAlerts(unittest.TestCase):
    """发酵提醒:升档且(重要 或 自选命中)才推;每档一次,降档不提醒。"""

    def setUp(self):
        self._orig_wl = tb.WATCHLIST_NAMES
        tb.WATCHLIST_NAMES = {"英伟达", "闪迪"}

    def tearDown(self):
        tb.WATCHLIST_NAMES = self._orig_wl

    def _prop_item(self, level=1, **kw):
        it = _zh_item(**kw)
        it.setdefault("dedup_group", "gp1")
        it["propagation"] = {"level": level, "sources": 1 + 2 * level, "window_min": 90}
        return it

    def test_first_level_with_watchlist_hit_alerts(self):
        it = self._prop_item(level=1, entities=[{"name": "英伟达"}])
        out, alerted = tb.select_prop_alerts([it], {})
        self.assertEqual(len(out), 1)
        self.assertEqual(alerted, {"gp1": 1})

    def test_same_level_not_repeated(self):
        it = self._prop_item(level=1, entities=[{"name": "英伟达"}])
        out, _ = tb.select_prop_alerts([it], {"gp1": 1})
        self.assertEqual(out, [])

    def test_level_up_alerts_again(self):
        it = self._prop_item(level=2, entities=[{"name": "英伟达"}])
        out, alerted = tb.select_prop_alerts([it], {"gp1": 1})
        self.assertEqual(len(out), 1)
        self.assertEqual(alerted["gp1"], 2)

    def test_downgrade_no_alert(self):
        it = self._prop_item(level=1, entities=[{"name": "英伟达"}])
        out, alerted = tb.select_prop_alerts([it], {"gp1": 2})
        self.assertEqual(out, [])
        self.assertEqual(alerted["gp1"], 2)              # 记账不回退

    def test_unworthy_not_alerted_and_not_recorded(self):
        # 不重要且不沾自选:不推,也不记账(之后 LLM 补上重要度仍可触发)
        it = self._prop_item(level=1, entities=[], sentiment=None)
        out, alerted = tb.select_prop_alerts([it], {})
        self.assertEqual(out, [])
        self.assertEqual(alerted, {})

    def test_importance_gate(self):
        hi = self._prop_item(level=1, entities=[], sentiment=None, llm_importance=7)
        lo = self._prop_item(level=1, entities=[], sentiment=None, llm_importance=5,
                             dedup_group="gp2")
        out, _ = tb.select_prop_alerts([hi, lo], {})
        self.assertEqual([x["dedup_group"] for x in out], ["gp1"])

    def test_sentiment_tgt_hits_watchlist(self):
        it = self._prop_item(level=1, entities=[],
                             sentiment={"dir": "利多", "tgt": "闪迪/存储产业链", "str": 2})
        out, _ = tb.select_prop_alerts([it], {})
        self.assertEqual(len(out), 1)

    def test_no_propagation_field_ignored(self):
        it = _zh_item()
        it["dedup_group"] = "gp1"
        out, alerted = tb.select_prop_alerts([it], {})
        self.assertEqual((out, alerted), ([], {}))

    def test_render_prop_alert_header_plus_card(self):
        it = self._prop_item(level=2, entities=[{"name": "英伟达"}])
        out = tb.render_prop_alert(it)
        self.assertIn("📡 <b>正在发酵 · LV2</b>（90分钟内5家信源跟进）", out)
        self.assertIn("📊 <b>快讯捕捉</b>", out)           # 下半部分复用推送卡
        self.assertLess(out.index("正在发酵"), out.index("快讯捕捉"))

    def test_state_defaults_include_prop_alerted(self):
        orig = tb.STATE_FILE
        tb.STATE_FILE = orig + ".nonexistent"
        try:
            self.assertEqual(tb._load_state()["prop_alerted"], {})
        finally:
            tb.STATE_FILE = orig


if __name__ == "__main__":
    unittest.main()
