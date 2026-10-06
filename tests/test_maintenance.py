# -*- coding: utf-8 -*-
"""2026-09-29 一批修复:字段入库、金十不吞可见事件、主线判定、翻译熔断+补救轮、
付费服务断供告警、原子写、每日备份。所有文件路径都指向临时目录,不碰生产数据。"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

CST = timezone(timedelta(hours=8))


def _now(minutes_ago=0):
    return (datetime.now(CST) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _item(iid, title, source="X·Test", group=None, minutes_ago=0, **kw):
    it = {"id": iid, "published_at": _now(minutes_ago), "title_zh": title, "summary_zh": "",
          "body_excerpt": "", "source": source, "source_url": "http://x/" + iid, "lang": "zh",
          "channels": ["finance"], "categories": [], "tags": [], "entities": [], "markets": [],
          "heat": "", "read_count": None, "verified": "confirmed",
          "dedup_group": group or ("g_" + iid), "score": 5.0, "selected": True, "dup_count": 1}
    it.update(kw)
    return it


class _TmpDB(unittest.TestCase):
    def setUp(self):
        import store
        self.store = store
        self.tmpdir = tempfile.mkdtemp(prefix="aihot_maint_")
        self._orig = store.DB_FILE
        store.DB_FILE = os.path.join(self.tmpdir, "news.db")
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def mentions(self, group):
        con = self.store._conn()
        rows = con.execute("SELECT source FROM mentions WHERE group_key=?", (group,)).fetchall()
        con.close()
        return sorted(r[0] for r in rows)

    def ids(self):
        return sorted(it["id"] for it in self.store.recent_items())


# ---------------- ① 字段入库 ----------------
class TestPersistedFields(_TmpDB):
    def test_roundtrip_llm_importance_title_orig_segments(self):
        self.store.upsert_items([_item("a", "英伟达发布新芯片", llm_importance=7,
                                       title_orig="Nvidia unveils new chip",
                                       segments=["第二段", "第三段"])])
        it = self.store.recent_items()[0]
        self.assertEqual(it["llm_importance"], 7)
        self.assertEqual(it["title_orig"], "Nvidia unveils new chip")
        self.assertEqual(it["segments"], ["第二段", "第三段"])

    def test_absent_fields_not_injected(self):
        self.store.upsert_items([_item("a", "标题")])
        it = self.store.recent_items()[0]
        for k in ("llm_importance", "title_orig", "segments"):
            self.assertNotIn(k, it)

    def test_zero_importance_kept(self):
        self.store.upsert_items([_item("a", "标题", llm_importance=0)])
        self.assertEqual(self.store.recent_items()[0]["llm_importance"], 0)

    def test_old_db_gets_columns_migrated(self):
        path = os.path.join(self.tmpdir, "old.db")
        con = sqlite3.connect(path)
        con.execute("""CREATE TABLE items (id TEXT PRIMARY KEY, published_at TEXT, title_zh TEXT,
            summary_zh TEXT, body_excerpt TEXT, source TEXT, source_url TEXT, lang TEXT,
            channels TEXT, categories TEXT, tags TEXT, entities TEXT, markets TEXT, heat TEXT,
            read_count INTEGER, verified TEXT, dedup_group TEXT, score REAL, selected INTEGER,
            dup_count INTEGER, fetched_at TEXT)""")
        con.execute("INSERT INTO items (id, published_at, title_zh, channels) VALUES ('o', ?, 't', '[]')",
                    (_now(),))
        con.commit()
        con.close()
        self.store.DB_FILE = path
        self.store.init_db()
        cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(items)")}
        for c in ("sentiment", "llm_importance", "title_orig", "segments"):
            self.assertIn(c, cols)
        self.assertEqual(self.store.recent_items()[0]["id"], "o")

    def test_update_does_not_blank_existing_enrichment(self):
        """同 id 再抓(DIGITIMES 列表每轮都回来):本轮增强没拿到的字段不能把库里的覆盖成空。"""
        self.store.upsert_items([_item("a", "標題", summary_zh="好摘要", llm_importance=6,
                                       sentiment={"dir": "利多", "tgt": "存储", "str": 2})])
        self.store.upsert_items([_item("a", "標題", summary_zh="", llm_importance=None)])
        it = self.store.recent_items()[0]
        self.assertEqual(it["summary_zh"], "好摘要")
        self.assertEqual(it["llm_importance"], 6)
        self.assertEqual(it["sentiment"]["dir"], "利多")

    def test_chinese_original_not_shown_as_translation(self):
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-test-token")
        import telegram_bot
        card = telegram_bot.render_push_item({
            "source": "X·联合早报", "title_zh": "英伟达扩大回购计划",
            "title_orig": "英伟达已将股票回购计划扩大 https://t.co/x", "published_at": _now()})
        self.assertNotIn("原文", card)
        self.assertIn("标题:", card)

    def test_push_card_shows_original_after_roundtrip(self):
        """06-10 定的推送卡「🔤原文」行:字段入库后才会真的出现。"""
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-test-token")
        import telegram_bot
        self.store.upsert_items([_item("a", "英伟达发布新芯片", title_orig="Nvidia unveils new chip")])
        card = telegram_bot.render_push_item(self.store.recent_items()[0])
        self.assertIn("原文", card)
        self.assertIn("Nvidia unveils new chip", card)


# ---------------- ② 金十不吞可见事件 ----------------
class TestHiddenSourceCluster(unittest.TestCase):
    def test_jin10_never_represents_visible_event(self):
        import newsfetch as nf
        x = _item("x", "英伟达回购授权增加1500亿美元", source="X·Walter Bloomberg")
        j = _item("j", "英伟达回购授权增加1500亿美元", source="金十", summary_zh="更长的摘要" * 5)
        reps = nf._cluster([x, j])
        by = {r["id"]: r for r in reps}
        self.assertEqual(set(by), {"x", "j"})              # 两边都保留:X 进主流,金十进个股栏
        self.assertIn("金十", by["x"]["also_sources"])     # 金十计入可见事件的印证/传播
        self.assertEqual(by["x"]["dup_count"], 2)

    def test_visible_sources_still_merge(self):
        import newsfetch as nf
        a = _item("a", "英伟达回购授权增加1500亿美元", source="X·Bloomberg")
        b = _item("b", "英伟达回购授权增加1500亿美元", source="X·Walter Bloomberg")
        self.assertEqual(len(nf._cluster([a, b])), 1)


class TestHiddenSourceStore(_TmpDB):
    def test_visible_after_hidden_is_inserted_and_takes_over_mentions(self):
        self.store.upsert_items([_item("j", "中韩半导体ETF华泰柏瑞触及涨停", source="金十", group="gj")])
        self.store.upsert_items([_item("x", "中韩半导体ETF华泰柏瑞涨停", source="X·Test", group="gx")])
        self.assertEqual(self.ids(), ["j", "x"])                   # 原来 X 被跳过、从主流消失
        self.assertEqual(self.mentions("gx"), ["X·Test", "金十"])    # 金十那一簇的提及并过来

    def test_hidden_after_visible_is_inserted_and_counts_for_visible(self):
        self.store.upsert_items([_item("x", "中韩半导体ETF华泰柏瑞触及涨停", source="X·Test", group="gx")])
        self.store.upsert_items([_item("j", "中韩半导体ETF华泰柏瑞涨停", source="金十", group="gj")])
        self.assertEqual(self.ids(), ["j", "x"])                   # 个股栏要用,照常入库
        self.assertEqual(self.mentions("gx"), ["X·Test", "金十"])
        self.assertEqual(self.mentions("gj"), [])

    def test_same_side_duplicates_still_skipped(self):
        self.store.upsert_items([_item("a", "中韩半导体ETF华泰柏瑞触及涨停", source="X·A", group="ga")])
        self.store.upsert_items([_item("b", "中韩半导体ETF华泰柏瑞涨停", source="X·B", group="gb")])
        self.assertEqual(self.ids(), ["a"])
        self.assertEqual(self.mentions("ga"), ["X·A", "X·B"])

    def test_takeover_keeps_jin10_first_seen_time(self):
        """X 在之后某轮才加入金十事件:接管时金十的首见时间要保住(先搬旧提及再记本轮)。"""
        self.store.upsert_items([_item("j", "英伟达回购授权增加1500亿美元", source="金十", group="gj")])
        con = self.store._conn()
        con.execute("UPDATE mentions SET ts='2026-09-29T10:00:00+08:00' WHERE group_key='gj'")
        con.commit()
        con.close()
        self.store.upsert_items([_item("x", "英伟达回购授权增加1500亿美元", source="X·a", group="gx",
                                       also_sources=["X·a", "金十"])])
        con = self.store._conn()
        ts = con.execute("SELECT ts FROM mentions WHERE group_key='gx' AND source='金十'").fetchone()[0]
        con.close()
        self.assertEqual(ts, "2026-09-29T10:00:00+08:00")

    def test_same_batch_both_sides(self):
        self.store.upsert_items([
            _item("x", "英伟达回购授权增加1500亿美元", source="X·Test", group="gx",
                  also_sources=["X·Test", "金十"]),
            _item("j", "英伟达回购授权增加1500亿美元", source="金十", group="gj"),
        ])
        self.assertEqual(self.ids(), ["j", "x"])
        self.assertEqual(self.mentions("gx"), ["X·Test", "金十"])


# ---------------- ③ 主线判定 ----------------
class TestMainlineRule(unittest.TestCase):
    def tag(self, **kw):
        import newsfetch as nf
        it = {"title_zh": "", "summary_zh": "", "body_excerpt": "", "title_orig": "",
              "entities": [], "source": "X·Test"}
        it.update(kw)
        nf._tag_mainline(it)
        return it["mainline"]

    def test_llm_summary_alone_does_not_count(self):
        self.assertFalse(self.tag(title_zh="OpenAI 取消发布新模型", summary_zh="利好AI算力链"))

    def test_core_words(self):
        for t in ["台积电计划在美国建第二座园区", "TSMC raises prices", "DRAM spot price falls",
                  "英伟达回购授权增加1500亿美元", "LPDDR6 路线图"]:
            self.assertTrue(self.tag(title_zh=t), t)

    def test_broad_words_need_industry_context(self):
        self.assertFalse(self.tag(title_zh="豆腐都变成肉价了,涨价太猛"))
        self.assertFalse(self.tag(title_zh="欧盟面临柴油短缺"))
        self.assertTrue(self.tag(title_zh="被动元件全面涨价"))

    def test_gaming_gpu_needs_ai_context(self):
        self.assertFalse(self.tag(title_zh="又一块显卡GPU电源接口烧毁"))
        self.assertTrue(self.tag(title_zh="AI训练集群GPU交期拉长"))

    def test_ascii_word_boundaries(self):
        self.assertFalse(self.tag(title_zh="迪拜王储 Hamdan 出席活动"))     # 不是 AMD
        self.assertFalse(self.tag(title_orig="a drama about markets"))      # 不是 DRAM
        self.assertTrue(self.tag(title_orig="AMD to acquire World Labs"))
        self.assertTrue(self.tag(title_orig="GPUs sold out"
                                            " at AI data centers"))

    def test_source_and_watchlist_rules(self):
        self.assertTrue(self.tag(source="DIGITIMES·涨价追踪", title_zh="某公司营收"))
        self.assertTrue(self.tag(source="X·SemiAnalysis", title_zh="集群测试第二部分"))
        self.assertTrue(self.tag(title_zh="随便一条", entities=[{"name": "美光科技", "market": "us"}]))

    def test_gaming_veto(self):
        self.assertFalse(self.tag(title_zh="NVIDIA发布GeForce热修复驱动程序616.86"))
        self.assertFalse(self.tag(title_zh="NVIDIA发布GeForce热修复驱动",
                                  entities=[{"name": "英伟达", "market": "us"}]))
        self.assertFalse(self.tag(title_zh="《巫师3》PC配置要求:12GB内存"))
        self.assertFalse(self.tag(title_zh="任天堂Switch 2服务器错误导致误封"))
        self.assertTrue(self.tag(title_zh="显卡全线涨价,游戏玩家叫苦"))      # 强信号放行
        self.assertTrue(self.tag(title_zh="IQE与量子点领域顶级玩家签署采购协议,晶圆代工"))

    def test_existing_exclusions_kept(self):
        self.assertFalse(self.tag(title_zh="国际原子能机构:扎波罗热核电站关键电力线路修复工作仍在进行"))


# ---------------- ④ 翻译熔断 + 增强熔断 + 补救轮 ----------------
class TestTranslateBreaker(unittest.TestCase):
    def test_breaks_after_consecutive_failures(self):
        import newsfetch as nf
        import llm
        with mock.patch.object(nf, "_load_trans", return_value={}), \
                mock.patch.object(llm, "translate", side_effect=RuntimeError("down")) as tr, \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}), \
                mock.patch("builtins.print"):
            nf.reset_translate_round()
            for i in range(10):
                self.assertEqual(nf.translate("text %d" % i), "text %d" % i)
        self.assertEqual(tr.call_count, nf.TRANS_FAIL_BREAK)

    def test_success_resets_failure_count_and_marks_dirty(self):
        import newsfetch as nf
        import llm
        cache = {}
        with mock.patch.object(nf, "_load_trans", return_value=cache), \
                mock.patch.object(llm, "translate", side_effect=[RuntimeError("x"), "好", "好2"]), \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}), \
                mock.patch("builtins.print"):
            nf.reset_translate_round()
            nf._trans_dirty = False
            nf.translate("a")
            self.assertEqual(nf.translate("b"), "好")
            self.assertEqual(nf._trans_fail, 0)
            self.assertTrue(nf._trans_dirty)
            self.assertEqual(nf.translate("c"), "好2")


class TestEnrichBreaker(unittest.TestCase):
    def test_stops_after_two_failed_batches(self):
        import llm
        items = [{"id": "e%d" % i, "title_zh": "t%d" % i} for i in range(40)]
        with mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value={}), \
                mock.patch.object(llm, "_save") as sv, \
                mock.patch.object(llm, "_call", side_effect=RuntimeError("402")) as call, \
                mock.patch("builtins.print"):
            self.assertEqual(llm.enrich(items), 0)
        self.assertEqual(call.call_count, 2)
        sv.assert_not_called()                       # 没新结果不重写缓存


class TestEnrich400Split(unittest.TestCase):
    def test_rejected_item_does_not_sink_its_batch(self):
        """一条被内容风控拒(HTTP 400)的推文,原来会让同批 7 条陪着失败、永远补不上。"""
        import llm
        import urllib.error
        items = [{"id": "i%d" % k, "title_zh": ("毒" if k == 3 else "正常%d" % k)} for k in range(8)]

        def fake_call(lines, recent_block=""):
            if "毒" in lines:
                raise urllib.error.HTTPError("http://x", 400, "bad", {}, None)
            n = len(lines.split("\n"))
            return [{"i": j + 1, "s": "摘要", "imp": 5, "dir": "中性"} for j in range(n)]

        cache = {}
        with mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value=cache), \
                mock.patch.object(llm, "_save") as sv, \
                mock.patch.object(llm, "_call", side_effect=fake_call), \
                mock.patch("builtins.print"):
            done = llm.enrich(items)
        self.assertEqual(done, 7)
        self.assertTrue(cache["i3"].get("rejected"))            # 墓碑:以后不再提交
        self.assertEqual(sum(1 for it in items if it.get("sentiment")), 7)
        sv.assert_called_once()


class TestRepairRound(_TmpDB):
    def setUp(self):
        super().setUp()
        import newsfetch as nf
        nf.reset_translate_round()
        nf._repair_tries.clear()

    def test_outage_rounds_do_not_use_up_tries(self):
        """DeepSeek 故障期间(熔断)不扣补救次数——否则故障超过 15 分钟,恢复后一条也补不回来。"""
        import newsfetch as nf
        import llm
        rows = [_item("e%d" % i, "Nvidia soars %d" % i, body_excerpt="Nvidia soars %d" % i,
                      sentiment={"dir": "中性"}) for i in range(5)]
        with mock.patch.object(nf, "_load_trans", return_value={}), mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(llm, "translate", side_effect=RuntimeError("402")), \
                mock.patch.object(llm, "enabled", return_value=False), \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}), \
                mock.patch("builtins.print"):
            for _ in range(6):
                nf.reset_translate_round()          # 模拟每轮主抓取开头清零
                self.assertEqual(nf.repair_items([dict(r) for r in rows]), [])
        self.assertEqual(nf._repair_tries, {})
        nf.reset_translate_round()
        with mock.patch.object(nf, "_load_trans", return_value={}), mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(llm, "translate", return_value="英伟达大涨"), \
                mock.patch.object(llm, "enabled", return_value=False), \
                mock.patch.dict(os.environ, {"TRANSLATE_BACKEND": "deepseek"}):
            fixed = nf.repair_items([dict(r) for r in rows])
        self.assertEqual(len(fixed), 5)

    def test_skipped_when_main_round_breaker_tripped(self):
        import newsfetch as nf
        nf._trans_fail = nf.TRANS_FAIL_BREAK
        with mock.patch.object(nf, "translate") as tr:
            self.assertEqual(nf.repair_items([_item("e", "Nvidia")]), [])
        tr.assert_not_called()
        nf.reset_translate_round()

    def test_exhausted_ids_excluded_before_limit(self):
        self.store.upsert_items([_item("e%d" % i, "Nvidia %d" % i, minutes_ago=i) for i in range(5)])
        got = self.store.repair_candidates(limit=2, exclude={"e0", "e1"})
        self.assertEqual([it["id"] for it in got], ["e2", "e3"])

    def test_cached_sentiment_applied_without_api_call(self):
        """缓存里已有增强结果、库里却没情绪的行:直接用缓存补上,不再调 API。"""
        import newsfetch as nf
        import llm
        cache = {"c": {"s": "摘要", "c": [], "imp": 5, "fin": True, "rumor": False,
                       "dir": "利多", "tgt": "英伟达", "str": 1}}
        with mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value=cache), \
                mock.patch.object(llm, "_call") as call:
            fixed = nf.repair_items([_item("c", "英伟达大涨")])
        call.assert_not_called()
        self.assertEqual(fixed[0]["sentiment"]["dir"], "利多")

    def test_candidates_are_x_items_untranslated_or_without_sentiment(self):
        self.store.upsert_items([
            _item("en", "Nvidia soars", body_excerpt="Nvidia soars", sentiment={"dir": "中性"}),
            _item("ns", "英伟达大涨"),                                               # 没情绪
            _item("ok", "英伟达大涨了", sentiment={"dir": "利多", "tgt": "", "str": 1}),
            _item("jin", "Nvidia", source="金十"),                                   # 金十不补
            _item("old", "Nvidia old", minutes_ago=60 * 72),                        # 超窗口
        ])
        self.assertEqual(sorted(it["id"] for it in self.store.repair_candidates()), ["en", "ns"])

    def test_repair_translates_enriches_and_persists(self):
        import newsfetch as nf
        import llm
        self.store.upsert_items([
            _item("en", "Nvidia soars", body_excerpt="Nvidia soars", summary_zh="Nvidia soars",
                  sentiment={"dir": "中性", "tgt": "", "str": 0}),
            _item("ns", "英伟达大涨"),
        ])

        def fake_enrich(items, *a, **k):
            for it in items:
                it["sentiment"] = {"dir": "利多", "tgt": "英伟达", "str": 2}
                it["llm_importance"] = 7
            return len(items)

        nf._repair_tries.clear()
        with mock.patch.object(nf, "_load_trans", return_value={"Nvidia soars": "英伟达大涨"}), \
                mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(llm, "enabled", return_value=True), \
                mock.patch.object(llm, "_load", return_value={}), \
                mock.patch.object(llm, "enrich", side_effect=fake_enrich):
            fixed = nf.repair_items(self.store.repair_candidates())
        self.store.apply_repairs(fixed)
        by = {it["id"]: it for it in self.store.recent_items()}
        self.assertEqual(by["en"]["title_zh"], "英伟达大涨")
        self.assertEqual(by["en"]["summary_zh"], "英伟达大涨")
        self.assertEqual(by["ns"]["sentiment"]["dir"], "利多")
        self.assertEqual(by["ns"]["llm_importance"], 7)
        self.assertTrue(by["ns"]["selected"])                  # 重新打分:imp 7 >= 6

    def test_gives_up_after_max_tries(self):
        import newsfetch as nf
        import llm
        rows = [_item("en", "Nvidia soars", body_excerpt="Nvidia soars", sentiment={"dir": "中性"})]
        nf._repair_tries.clear()
        with mock.patch.object(nf, "translate", return_value="Nvidia soars") as tr, \
                mock.patch.object(nf, "_save_trans"), \
                mock.patch.object(llm, "enabled", return_value=False):
            for _ in range(nf.REPAIR_MAX_TRIES + 2):
                nf.repair_items([dict(r) for r in rows])
        self.assertEqual(tr.call_count, nf.REPAIR_MAX_TRIES)


# ---------------- ⑤ 付费服务断供告警 ----------------
class TestHealth(unittest.TestCase):
    def setUp(self):
        import health
        self.h = health
        self._state = (health._enabled, dict(health._streak), dict(health._seen_ok), dict(health._alerted))
        health._streak.clear(); health._seen_ok.clear(); health._alerted.clear()
        health._enabled = True

    def tearDown(self):
        h = self.h
        h._enabled = self._state[0]
        for d, orig in ((h._streak, self._state[1]), (h._seen_ok, self._state[2]), (h._alerted, self._state[3])):
            d.clear(); d.update(orig)

    def _err(self, code):
        import urllib.error
        return urllib.error.HTTPError("http://x", code, "err", {}, None)

    def _deliver(self):
        msgs = self.h.evaluate()
        for service, state, _ in msgs:
            self.h.mark(service, state)
        return [m for _, _, m in msgs]

    def test_three_402_in_a_row_alerts_once_then_recovers(self):
        self.h.note_ok("twitterapi")
        for _ in range(3):
            self.h.note_error("twitterapi", self._err(402))
        msgs = self._deliver()
        self.assertEqual(len(msgs), 1)
        self.assertIn("402", msgs[0])
        self.assertIn("充值", msgs[0])
        self.h.note_error("twitterapi", self._err(402))
        self.assertEqual(self._deliver(), [])                              # 不刷屏
        self.h.note_ok("twitterapi")
        self.assertIn("恢复", self._deliver()[0])

    def test_no_false_402_right_after_recovery(self):
        """充值恢复后再有一次零星失败,不能又报「请去充值」。"""
        for _ in range(3):
            self.h.note_error("deepseek", self._err(402))
        self._deliver()
        self.h.note_ok("deepseek")
        self._deliver()
        self.h.note_error("deepseek", self._err(400))
        self.assertEqual(self._deliver(), [])

    def test_intermittent_failures_do_not_flap(self):
        """网络抖动:失败夹着成功,不算断供,不来回刷屏。"""
        out = []
        for i in range(36):
            if i % 3 == 0:
                self.h.note_error("twitterapi", self._err(503))
            else:
                self.h.note_ok("twitterapi")
            out += self._deliver()
        self.assertEqual(out, [])

    def test_non_payment_errors_need_longer_streak(self):
        for i in range(5):
            self.h.note_error("deepseek", self._err(503))
        self.assertEqual(self._deliver(), [])
        self.h.note_error("deepseek", self._err(503))
        self.assertEqual(len(self._deliver()), 1)

    def test_undelivered_alert_is_retried(self):
        for _ in range(3):
            self.h.note_error("twitterapi", self._err(402))
        self.assertEqual(len(self.h.evaluate()), 1)        # 没 mark = 没送达
        self.assertEqual(len(self.h.evaluate()), 1)        # 下一轮还会给出

    def test_disabled_is_noop(self):
        self.h._enabled = False
        self.h.note_error("deepseek", self._err(402))
        self.assertEqual(self.h._streak, {})
        self.assertEqual(self.h.evaluate(), [])

    def test_xfetch_records_failures(self):
        import xfetch
        with mock.patch.object(xfetch, "enabled", return_value=True),                 mock.patch.object(xfetch, "_accounts", return_value=[{"user": "a", "group": "kol"}]),                 mock.patch.object(xfetch, "_load_state", return_value={}),                 mock.patch.object(xfetch, "_call", side_effect=self._err(402)),                 mock.patch("builtins.print"):
            self.assertEqual(xfetch.fetch_x(), [])
        self.assertEqual(self.h._streak["twitterapi"][-1][1], 402)


# ---------------- ⑥ 原子写 ----------------
class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aihot_aio_")
        self.p = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_and_no_temp_left(self):
        import atomicio
        self.assertTrue(atomicio.write_json(self.p, {"a": "中"}))
        self.assertEqual(atomicio.read_json(self.p), {"a": "中"})
        self.assertEqual(os.listdir(self.tmp), ["state.json"])

    def test_failed_write_keeps_old_file(self):
        import atomicio
        atomicio.write_json(self.p, {"a": 1})
        with mock.patch("builtins.print"):
            self.assertFalse(atomicio.write_json(self.p, {"a": object()}))   # 序列化失败
        self.assertEqual(atomicio.read_json(self.p), {"a": 1})
        self.assertEqual(os.listdir(self.tmp), ["state.json"])

    def test_corrupt_file_quarantined_not_overwritten(self):
        import atomicio
        with open(self.p, "w", encoding="utf-8") as f:
            f.write('{"subscribers": [1, 2')
        with mock.patch("builtins.print"):
            self.assertEqual(atomicio.read_json(self.p, {}), {})
        self.assertTrue(any(n.startswith("state.json.corrupt-") for n in os.listdir(self.tmp)))

    def test_prev_fallback(self):
        import atomicio
        atomicio.write_json(self.p, {"subscribers": [1, 2]}, keep_prev=True)
        atomicio.write_json(self.p, {"subscribers": [1, 2, 3]}, keep_prev=True)
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("{broken")
        with mock.patch("builtins.print"):
            self.assertEqual(atomicio.read_json(self.p, {}, try_prev=True), {"subscribers": [1, 2]})

    def test_bot_state_survives_corruption(self):
        """bot_state 读坏:原来订阅者清空、推送悄悄停;现在回退到上一份。"""
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-test-token")
        import atomicio
        import telegram_bot
        atomicio.write_json(self.p, {"subscribers": [111]}, keep_prev=True)
        atomicio.write_json(self.p, {"subscribers": [111, 222]}, keep_prev=True)
        with open(self.p, "w", encoding="utf-8") as f:
            f.write('{"subscri')
        with mock.patch.object(telegram_bot, "STATE_FILE", self.p), mock.patch("builtins.print"):
            s = telegram_bot._load_state()
        self.assertEqual(s["subscribers"], [111])

    def test_items_json_written_compact_and_atomic(self):
        import server
        p = os.path.join(self.tmp, "items.json")
        with mock.patch.object(server, "DATA_FILE", p):
            server.save_cache([{"id": "a", "_dt": 1, "title_zh": "t"}])
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        self.assertEqual(json.loads(raw), [{"id": "a", "title_zh": "t"}])
        self.assertNotIn("\n", raw)


class TestLlmPrune(unittest.TestCase):
    def test_prune_keeps_window_and_refuses_tiny_keep_set(self):
        import llm
        cache = {"k%d" % i: {} for i in range(2000)}
        with mock.patch.object(llm, "_load", return_value=cache), mock.patch.object(llm, "_save") as sv:
            self.assertEqual(llm.prune(["k1"]), 0)                     # 库读失败式的小集合:不动
            sv.assert_not_called()
            self.assertEqual(llm.prune(["k%d" % i for i in range(1500)]), 500)
        self.assertEqual(len(cache), 1500)


# ---------------- ⑦ 每日备份 + 维护只在后台循环里跑 ----------------
class TestBackup(_TmpDB):
    def test_daily_snapshot_is_readable_and_rotated(self):
        self.store.upsert_items([_item("a", "标题")])
        d = os.path.join(self.tmpdir, "backups")
        for i in range(9):
            self.store.backup(d, keep=7, day="2026-09-%02d" % (i + 1))
        snaps = sorted(os.listdir(d))
        self.assertEqual(len(snaps), 7)
        self.assertEqual(snaps[0], "news-2026-09-03.db")
        con = sqlite3.connect(os.path.join(d, snaps[-1]))
        self.assertEqual(con.execute("SELECT id FROM items").fetchall(), [("a",)])
        con.close()
        self.assertIsNone(self.store.backup(d, day="2026-09-09"))     # 当天已有 -> 跳过
        with open(os.path.join(d, "news-2026-09-10.db.tmp"), "w") as f:
            f.write("half")                                           # 上次中途失败的半成品
        self.store.backup(d, keep=7, day="2026-09-10")
        self.assertFalse(any(n.endswith(".tmp") for n in os.listdir(d)))


class TestMaintenanceOnlyInBackgroundLoop(unittest.TestCase):
    def test_refresh_does_not_run_maintenance(self):
        import server
        import store
        import newsfetch
        orig = server.ITEMS
        try:
            with mock.patch.object(newsfetch, "fetch_all", return_value=[]), \
                    mock.patch.object(store, "recent_items", return_value=[
                        {"id": "a", "published_at": _now(), "dedup_group": "g"}]), \
                    mock.patch.object(store, "propagation", return_value={}), \
                    mock.patch.object(store, "prop_series", return_value={}), \
                    mock.patch.object(server, "save_cache"), \
                    mock.patch.object(server, "after_refresh") as ar, \
                    mock.patch.object(store, "backup") as bk, \
                    mock.patch.object(newsfetch, "repair_items") as rp:
                server.refresh(verbose=False)
            ar.assert_not_called()
            bk.assert_not_called()
            rp.assert_not_called()
        finally:
            server.ITEMS = orig

    def test_after_refresh_repairs_alerts_and_backs_up_once_a_day(self):
        import server
        import store
        import newsfetch
        import health
        import notify
        orig_items, orig_last, orig_gate = server.ITEMS, server._last_daily, server._maintenance_enabled
        server.ITEMS = [{"id": "x", "title_zh": "Nvidia", "selected": False}]
        server._last_daily = None
        server._maintenance_enabled = True
        fixed = [{"id": "x", "title_zh": "英伟达", "selected": True, "score": 7.0}]
        try:
            with mock.patch.object(store, "repair_candidates", return_value=[{"id": "x"}]), \
                    mock.patch.object(newsfetch, "repair_items", return_value=fixed), \
                    mock.patch.object(store, "apply_repairs") as ap, \
                    mock.patch.object(health, "evaluate", return_value=[("twitterapi", True, "X 断供")]), \
                    mock.patch.object(health, "mark") as mk, \
                    mock.patch.object(notify, "send_alert", return_value=True) as sa, \
                    mock.patch.object(store, "backup", return_value=None) as bk, \
                    mock.patch("llm.prune", return_value=0), \
                    mock.patch("builtins.print"):
                server.after_refresh()
                server.after_refresh()
            ap.assert_called()
            sa.assert_called_with("X 断供")
            mk.assert_called_with("twitterapi", True)                 # 送达才记账
            self.assertEqual(bk.call_count, 1)                          # 一天一次
            self.assertEqual(server.ITEMS[0]["title_zh"], "英伟达")      # 工作集就地更新
            self.assertTrue(server.ITEMS[0]["selected"])
        finally:
            server.ITEMS, server._last_daily = orig_items, orig_last
            server._maintenance_enabled = orig_gate

    def test_after_refresh_is_noop_unless_started_by_main(self):
        """测试里调 _bg_refresh(test_server_startup)不能碰生产 data/:没经 main() 打开就什么都不做。"""
        import server
        import store
        self.assertFalse(server._maintenance_enabled)
        with mock.patch.object(store, "repair_candidates") as rc, mock.patch.object(store, "backup") as bk:
            server.after_refresh()
        rc.assert_not_called()
        bk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
