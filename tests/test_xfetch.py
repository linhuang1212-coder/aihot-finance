# -*- coding: utf-8 -*-
"""xfetch:X(twitterapi.io)信源。解析映射 / OSINT 标记 / 水位 / 护栏 / 无 key 降级。
全程 mock key 与 HTTP,绝不真实调用外部 API。"""
import json
import os
import tempfile
import unittest
from unittest import mock

import newsfetch
import xfetch

# 录制的 advanced_search 响应(简化字段,结构与 docs.twitterapi.io 一致)
RESP_ONE_PAGE = {
    "tweets": [
        {"id": "1990001", "text": "*FED CUTS RATES BY 25 BPS",
         "createdAt": "Wed Jun 11 02:00:30 +0000 2026",
         "author": {"userName": "DeItaone", "name": "Walter Bloomberg"}},
        {"id": "1990002", "text": "Explosions reported near Tehran refinery",
         "createdAt": "Wed Jun 11 02:01:00 +0000 2026",
         "author": {"userName": "IranObserver0", "name": "Iran Observer"}},
        {"id": "1990003", "text": "tweet from stranger account",
         "createdAt": "Wed Jun 11 02:02:00 +0000 2026",
         "author": {"userName": "NotInWatchlist", "name": "Nobody"}},
    ],
    "has_next_page": False, "next_cursor": "",
}

ACCOUNTS = [
    {"user": "DeItaone", "label": "Walter Bloomberg", "group": "finance"},
    {"user": "IranObserver0", "label": "Iran Observer", "group": "osint"},
]


class XFetchBase(unittest.TestCase):
    """公共脚手架:临时水位文件 + 注入账号表 + mock key/翻译。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig_state = xfetch.STATE_FILE
        xfetch.STATE_FILE = self.tmp.name
        self._orig_watch = xfetch._watch
        xfetch._watch = list(ACCOUNTS)
        self.p_key = mock.patch.object(xfetch, "_key", return_value="test-key")
        self.p_trans = mock.patch.object(newsfetch, "translate",
                                         side_effect=lambda t, **kw: "译:" + t[:30])
        self.p_key.start()
        self.p_trans.start()

    def tearDown(self):
        self.p_key.stop()
        self.p_trans.stop()
        xfetch.STATE_FILE = self._orig_state
        xfetch._watch = self._orig_watch
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)


class TestParse(XFetchBase):

    def test_maps_tweet_to_standard_item(self):
        with mock.patch.object(xfetch, "_call", return_value=RESP_ONE_PAGE):
            items = xfetch.fetch_x()
        it = next(x for x in items if "FED" in x["title_orig"])
        self.assertEqual(it["id"], newsfetch._hash_id("x:1990001"))
        self.assertEqual(it["source"], "X·Walter Bloomberg")
        self.assertEqual(it["source_url"], "https://x.com/DeItaone/status/1990001")
        self.assertEqual(it["published_at"], "2026-06-11T10:00:30+08:00")  # UTC02:00->CST10:00
        self.assertEqual(it["title_zh"], "译:*FED CUTS RATES BY 25 BPS")
        self.assertEqual(it["title_orig"], "*FED CUTS RATES BY 25 BPS")
        self.assertEqual(it["lang"], "en")
        self.assertEqual(it["channels"], ["finance"])
        self.assertEqual(it["dedup_group"],
                         newsfetch._hash_id(newsfetch._norm_title("*FED CUTS RATES BY 25 BPS")))

    def test_osint_group_unverified_finance_confirmed(self):
        with mock.patch.object(xfetch, "_call", return_value=RESP_ONE_PAGE):
            items = xfetch.fetch_x()
        by_src = {x["source"]: x for x in items}
        self.assertEqual(by_src["X·Iran Observer"]["verified"], "unverified")
        self.assertEqual(by_src["X·Walter Bloomberg"]["verified"], "confirmed")

    def test_unknown_author_skipped(self):
        with mock.patch.object(xfetch, "_call", return_value=RESP_ONE_PAGE):
            items = xfetch.fetch_x()
        self.assertEqual(len(items), 2)            # 白名单外的第 3 条被丢弃

    def test_query_contains_all_accounts_and_filters(self):
        seen = {}
        def fake_call(query, cursor=""):
            seen["q"] = query
            return {"tweets": [], "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", side_effect=fake_call):
            xfetch.fetch_x()
        self.assertIn("from:DeItaone", seen["q"])
        self.assertIn("from:IranObserver0", seen["q"])
        self.assertIn("-filter:retweets", seen["q"])
        self.assertIn("since_time:", seen["q"])

    def test_captures_conversation_id(self):
        resp = {"tweets": [{"id": "1", "text": "x",
                            "createdAt": "Wed Jun 11 02:00:30 +0000 2026",
                            "author": {"userName": "DeItaone"}, "conversationId": "999"}],
                "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", return_value=resp):
            items = xfetch.fetch_x()
        self.assertEqual(items[0]["conversation_id"], "999")


class TestWatermarkAndPaging(XFetchBase):

    def test_success_advances_watermark(self):
        with mock.patch.object(xfetch, "_call", return_value=RESP_ONE_PAGE):
            xfetch.fetch_x()
        with open(xfetch.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        self.assertIn("last_since_time", st)
        self.assertGreater(st["last_since_time"], 0)

    def test_failure_returns_empty_and_keeps_watermark(self):
        with open(xfetch.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_since_time": 1234567890}, f)
        with mock.patch.object(xfetch, "_call", side_effect=RuntimeError("503")):
            self.assertEqual(xfetch.fetch_x(), [])
        with open(xfetch.STATE_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_since_time"], 1234567890)  # 未推进

    def test_uses_stored_watermark_in_query(self):
        with open(xfetch.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_since_time": 1234567890}, f)
        seen = {}
        def fake_call(query, cursor=""):
            seen["q"] = query
            return {"tweets": [], "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", side_effect=fake_call):
            xfetch.fetch_x()
        self.assertIn("since_time:1234567890", seen["q"])

    def test_pagination_capped_at_max_pages(self):
        endless = dict(RESP_ONE_PAGE, has_next_page=True, next_cursor="c1")
        with mock.patch.object(xfetch, "_call", return_value=endless) as m_call:
            xfetch.fetch_x()
        self.assertEqual(m_call.call_count, xfetch.MAX_PAGES)

    def test_watermark_never_goes_backward(self):
        future = 9999999999            # 水位已在未来(时钟回拨等),不得倒退
        with open(xfetch.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_since_time": future}, f)
        with mock.patch.object(xfetch, "_call",
                               return_value={"tweets": [], "has_next_page": False,
                                             "next_cursor": ""}):
            xfetch.fetch_x()
        with open(xfetch.STATE_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_since_time"], future)  # max() 保护


class TestQueryChunking(XFetchBase):
    """账号多时单条查询会超 X 高级搜索 512 字符上限,API 不报错而是静默返回空
    (2026-06-12 生产事故:28 账号拼出 557 字符,抓取连续空转且水位照推)。
    账号表须按字符预算切片成多条短查询;任一片失败整轮放弃、水位不推进。"""

    BIG = [{"user": "account_%02d_xxxxx" % i, "label": "L%d" % i, "group": "finance"}
           for i in range(28)]

    def test_each_query_under_limit_and_covers_all_accounts(self):
        xfetch._watch = list(self.BIG)
        queries = []
        def fake_call(query, cursor=""):
            queries.append(query)
            return {"tweets": [], "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", side_effect=fake_call):
            xfetch.fetch_x()
        self.assertGreater(len(queries), 1)        # 28 账号必须切片
        for q in queries:
            self.assertLessEqual(len(q), 512, "单条查询超 X 上限: %d 字符" % len(q))
            self.assertIn("-filter:retweets", q)
            self.assertIn("since_time:", q)
        joined = " ".join(queries)
        for a in self.BIG:                          # 每个账号恰好覆盖一次
            self.assertEqual(joined.count("from:" + a["user"]), 1, a["user"])

    def test_results_from_all_chunks_merged(self):
        xfetch._watch = list(self.BIG)
        def fake_call(query, cursor=""):
            user = query.split("from:")[1].split(" ")[0]   # 该片第一个账号回一条推
            return {"tweets": [{"id": "id_" + user, "text": "t " + user,
                                "createdAt": "Wed Jun 11 02:00:30 +0000 2026",
                                "author": {"userName": user}}],
                    "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", side_effect=fake_call):
            items = xfetch.fetch_x()
        self.assertGreater(len(items), 1)           # 多片结果合并返回

    def test_chunk_failure_aborts_round_keeps_watermark(self):
        xfetch._watch = list(self.BIG)
        with open(xfetch.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_since_time": 1234567890}, f)
        calls = {"n": 0}
        def fake_call(query, cursor=""):
            calls["n"] += 1
            if calls["n"] >= 2:                     # 第二片起失败
                raise RuntimeError("503")
            return {"tweets": [], "has_next_page": False, "next_cursor": ""}
        with mock.patch.object(xfetch, "_call", side_effect=fake_call):
            self.assertEqual(xfetch.fetch_x(), [])
        with open(xfetch.STATE_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_since_time"], 1234567890)  # 未推进


class TestDisabled(unittest.TestCase):

    def test_no_key_returns_empty_without_http(self):
        with mock.patch.object(xfetch, "_key", return_value=None), \
             mock.patch.object(xfetch, "_call") as m_call:
            self.assertFalse(xfetch.enabled())
            self.assertEqual(xfetch.fetch_x(), [])
            m_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
