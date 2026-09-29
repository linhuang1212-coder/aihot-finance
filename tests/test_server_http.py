# -*- coding: utf-8 -*-
"""HTTP 层加固(2026-09-29):gzip/ETag/HEAD、异常兜底、since 时区、静态路径、繁简多词搜索、
深度分析限额。起真实 Handler 走 http.client,这样能发 --path-as-is 式的原始路径。"""
import gzip
import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from unittest import mock


def _it(iid, title, published_at="2026-09-29T10:00:00+08:00", **kw):
    it = {"id": iid, "channels": ["finance"], "source": "X·Test", "selected": True,
          "verified": "confirmed", "categories": [], "markets": [], "entities": [],
          "tags": [], "title_zh": title, "summary_zh": "", "body_excerpt": "",
          "published_at": published_at}
    it.update(kw)
    return it


class _ServerCase(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig_items = server.ITEMS
        self._orig_public = server.PUBLIC_DIR
        self.tmp = tempfile.mkdtemp(prefix="aihot_http_")
        pub = os.path.join(self.tmp, "public")
        os.makedirs(pub)
        os.makedirs(os.path.join(self.tmp, "public_backup_x"))
        with open(os.path.join(pub, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>t</title>" + "x" * 3000)
        with open(os.path.join(pub, "index.html.bak-20260821"), "w", encoding="utf-8") as f:
            f.write("old")
        with open(os.path.join(self.tmp, "public_backup_x", "secret.html"), "w") as f:
            f.write("secret")
        server.PUBLIC_DIR = pub
        server.ITEMS = server._attach_dt([
            _it("a", "台積電2027年晶圓漲價3～6%"),
            _it("b", "英伟达回购授权增加1500亿美元", published_at="2026-09-28T09:00:00+08:00"),
            _it("c", "台积电产能满载", summary_zh="先进封装需求强"),
        ] + [_it("p%d" % i, "填充条目 %d " % i + "文" * 60) for i in range(30)])
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.ITEMS = self._orig_items
        self.server.PUBLIC_DIR = self._orig_public
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, path, method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def items_path(self, **params):
        base = {"channel": "finance", "mode": "all", "take": "100"}
        base.update(params)
        return "/api/public/items?" + urllib.parse.urlencode(base)

    def ids(self, **params):
        r, body = self.req(self.items_path(**params))
        self.assertEqual(r.status, 200)
        return [it["id"] for it in json.loads(body)["items"]]


class TestCompressionAndCaching(_ServerCase):
    def test_gzip_when_accepted(self):
        r, body = self.req(self.items_path(), headers={"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(r.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(int(r.getheader("Content-Length")), len(body))
        plain = json.loads(gzip.decompress(body))
        self.assertEqual(len(plain["items"]), 33)

    def test_identity_without_accept_encoding(self):
        """bot/run.py 健康检查用 urllib 不带 Accept-Encoding,必须拿到原文。"""
        r, body = self.req(self.items_path())
        self.assertIsNone(r.getheader("Content-Encoding"))
        self.assertEqual(r.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(len(json.loads(body)["items"]), 33)

    def test_gzip_q0_refused(self):
        r, _ = self.req(self.items_path(), headers={"Accept-Encoding": "gzip;q=0"})
        self.assertIsNone(r.getheader("Content-Encoding"))

    def test_small_body_not_compressed(self):
        r, body = self.req("/api/public/categories", headers={"Accept-Encoding": "gzip"})
        self.assertIsNone(r.getheader("Content-Encoding"))
        json.loads(body)

    def test_etag_304(self):
        r1, _ = self.req(self.items_path())
        etag = r1.getheader("ETag")
        self.assertTrue(etag and etag.startswith('W/"'))
        self.assertEqual(r1.getheader("Cache-Control"), "no-cache")
        r2, body2 = self.req(self.items_path(), headers={"If-None-Match": etag})
        self.assertEqual(r2.status, 304)
        self.assertEqual(body2, b"")
        self.assertEqual(r2.getheader("ETag"), etag)

    def test_etag_changes_with_content(self):
        r1, _ = self.req(self.items_path())
        self.server.ITEMS = self.server.ITEMS[1:]
        r2, _ = self.req(self.items_path(), headers={"If-None-Match": r1.getheader("ETag")})
        self.assertEqual(r2.status, 200)

    def test_strong_form_of_same_etag_is_304(self):
        """If-None-Match 按弱比较:客户端/代理把 W/ 去掉回传也算命中。"""
        r1, _ = self.req(self.items_path())
        strong = r1.getheader("ETag")[2:]
        r2, _ = self.req(self.items_path(), headers={"If-None-Match": 'x, ' + strong})
        self.assertEqual(r2.status, 304)

    def test_gzip_uppercase_q0_refused(self):
        r, _ = self.req(self.items_path(), headers={"Accept-Encoding": "gzip;Q=0"})
        self.assertIsNone(r.getheader("Content-Encoding"))

    def test_no_etag_on_errors(self):
        r, _ = self.req("/nope.html")
        self.assertEqual(r.status, 404)
        self.assertIsNone(r.getheader("ETag"))

    def test_nosniff(self):
        r, _ = self.req("/")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")


class TestHead(_ServerCase):
    def test_head_index(self):
        g, gbody = self.req("/")
        h, hbody = self.req("/", method="HEAD")
        self.assertEqual(h.status, 200)
        self.assertEqual(hbody, b"")
        self.assertEqual(h.getheader("Content-Length"), str(len(gbody)))

    def test_head_analysis_never_generates(self):
        import serenity
        with mock.patch.object(serenity, "analyze") as an:
            r, _ = self.req("/api/analysis?entity=abc", method="HEAD")
        self.assertEqual(r.status, 405)
        an.assert_not_called()


class TestStaticPaths(_ServerCase):
    def test_index_served(self):
        r, body = self.req("/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"<title>t</title>", body)

    def test_sibling_prefix_dir_blocked(self):
        """旧实现用 startswith(PUBLIC_DIR):public_backup_x 也以 public 开头,能被读到。"""
        r, body = self.req("/../public_backup_x/secret.html")
        self.assertEqual(r.status, 403)
        self.assertNotIn(b"secret", body)

    def test_backslash_traversal_blocked(self):
        r, body = self.req("/..\\public_backup_x\\secret.html")
        self.assertIn(r.status, (403, 404))
        self.assertNotIn(b"secret", body)

    def test_bak_file_not_served(self):
        r, _ = self.req("/index.html.bak-20260821")
        self.assertEqual(r.status, 404)

    def test_absolute_drive_path_not_500(self):
        r, _ = self.req("/C:/Windows/win.ini")
        self.assertIn(r.status, (403, 404))

    def test_inside_helper(self):
        pub = self.server.PUBLIC_DIR
        self.assertTrue(self.server._inside(pub, os.path.join(pub, "a", "b.js")))
        self.assertTrue(self.server._inside(pub, pub))
        self.assertFalse(self.server._inside(pub, pub + "_backup_x"))


class TestErrorBedrock(_ServerCase):
    def test_unexpected_exception_returns_500_json(self):
        with mock.patch.object(self.server, "filter_items", side_effect=RuntimeError("boom")), \
                mock.patch("builtins.print"):
            r, body = self.req(self.items_path())
        self.assertEqual(r.status, 500)
        self.assertEqual(json.loads(body), {"error": "internal error"})

    def test_error_after_headers_sent_does_not_append_500(self):
        """头已发出后再出错:只能断连接,不能把第二个状态行拼进正文。"""
        def route(handler):
            handler._send(b'{"ok": 1}', "application/json; charset=utf-8")
            raise RuntimeError("late failure")
        with mock.patch.object(self.server.Handler, "_route", route), \
                mock.patch("builtins.print"):
            r, body = self.req("/api/public/categories")
        self.assertEqual(r.status, 200)
        self.assertEqual(body, b'{"ok": 1}')

    def test_write_timeout_is_treated_as_client_gone(self):
        def route(handler):
            raise TimeoutError("timed out")
        with mock.patch.object(self.server.Handler, "_route", route), \
                mock.patch("builtins.print") as pr:
            try:
                self.req("/api/public/categories")
            except (http.client.HTTPException, OSError):
                pass
        pr.assert_not_called()

    def test_server_keeps_serving_after_error(self):
        with mock.patch.object(self.server, "filter_items", side_effect=RuntimeError("boom")), \
                mock.patch("builtins.print"):
            self.req(self.items_path())
        r, _ = self.req("/api/public/categories")
        self.assertEqual(r.status, 200)


class TestSince(_ServerCase):
    def test_naive_since_is_beijing_time(self):
        """原来不带时区的 since 会让比较抛 TypeError、连接直接断。"""
        ids = self.ids(since="2026-09-29T00:00:00")
        self.assertNotIn("b", ids)          # b 是 09-28
        self.assertIn("a", ids)

    def test_plus_decoded_as_space(self):
        # 客户端没编码 "+" -> parse_qs 解成空格
        r, body = self.req("/api/public/items?channel=finance&mode=all&take=100"
                           "&since=2026-09-29T00:00:00+08:00")
        self.assertEqual(r.status, 200)
        ids = [it["id"] for it in json.loads(body)["items"]]
        self.assertNotIn("b", ids)

    def test_parse_since_variants(self):
        ps = self.server.parse_since
        self.assertEqual(ps("2026-09-29T00:00:00").utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(ps("2026-09-29T00:00:00Z").utcoffset().total_seconds(), 0)
        self.assertEqual(ps("2026-09-29T00:00:00 08:00").utcoffset().total_seconds(), 8 * 3600)
        self.assertIsNotNone(ps("2026-09-29"))
        self.assertIsNone(ps("garbage"))
        self.assertIsNone(ps(""))

    def test_parse_since_py310_shapes(self):
        """云端是 3.10:fromisoformat 只认 +HH:MM 和 3/6 位小数秒,这些要先规整。"""
        ps = self.server.parse_since
        for v in ("2026-09-29T08:00:00+0800", "2026-09-29T08:00:00 0800",
                  "2026-09-29T08:00:00.1+08:00", "2026-09-29T08:00:00.1234567"):
            dt = ps(v)
            self.assertIsNotNone(dt, v)
            self.assertEqual((dt.hour, dt.utcoffset().total_seconds()), (8, 8 * 3600), v)
        self.assertEqual(ps("2026-09-29T00:00:00.5Z").microsecond, 500000)

    def test_naive_published_at_does_not_crash(self):
        self.server.ITEMS = self.server._attach_dt([_it("n", "t", published_at="2026-09-29T09:00:00")])
        self.assertEqual(self.ids(since="2026-09-29T08:00:00+08:00"), ["n"])


class TestSearch(_ServerCase):
    def test_simplified_query_hits_traditional_text(self):
        self.assertEqual(self.ids(q="台积电 涨价"), ["a"])

    def test_traditional_query_hits_simplified_text(self):
        self.assertEqual(sorted(self.ids(q="台積電")), ["a", "c"])

    def test_multi_word_is_and(self):
        self.assertEqual(self.ids(q="台积电 产能"), ["c"])
        self.assertEqual(self.ids(q="台积电 不存在的词"), [])

    def test_single_word_still_substring(self):
        self.assertEqual(self.ids(q="回购"), ["b"])

    def test_case_insensitive_and_regex_safe(self):
        self.server.ITEMS = self.server._attach_dt([_it("r", "NVDA (+3%) [盘前] a.b*c")])
        self.assertEqual(self.ids(q="nvda"), ["r"])
        self.assertEqual(self.ids(q="(+3%)"), ["r"])
        self.assertEqual(self.ids(q="[盘前]"), ["r"])
        self.assertEqual(self.ids(q="a.b*c"), ["r"])
        self.assertEqual(self.ids(q="a?b"), [])

    def test_ascii_query_keeps_phrase_semantics(self):
        """纯英文仍按整串短语:切词 AND 会让 "rate cut" 命中 moderate…consecutive,
        Telegram 里随手发的 "hi there" 也会被当搜索推一堆卡片。"""
        self.server.ITEMS = self.server._attach_dt([
            _it("x", "Fed signals a rate cut in December"),
            _it("y", "moderate growth for a consecutive quarter"),
        ])
        self.assertEqual(self.ids(q="rate cut"), ["x"])
        self.assertEqual(self.ids(q="hi there"), [])

    def test_whitespace_only_query_is_no_query(self):
        self.assertEqual(len(self.ids(q="   ")), 33)

    def test_fullwidth_space_splits(self):
        self.assertEqual(self.ids(q="台积电\u3000产能"), ["c"])


class TestZhVariant(unittest.TestCase):
    def test_pattern(self):
        import re
        import zhvariant
        p = re.compile(zhvariant.pattern("台积电"))
        for s in ("台积电", "台積電", "臺積電"):
            self.assertTrue(p.search(s), s)
        self.assertFalse(p.search("台积"))
        self.assertTrue(re.search(zhvariant.pattern("随着"), "隨著需求回溫"))
        self.assertTrue(re.search(zhvariant.pattern("广达"), "廣達AI伺服器"))
        self.assertTrue(re.search(zhvariant.pattern("关税"), "美國關稅"))

    def test_table_is_consistent(self):
        import zhvariant
        for t, s in zhvariant.T2S.items():
            self.assertNotEqual(t, s)
            self.assertIn(t, zhvariant.S2T[s])


class TestAnalysisGuard(unittest.TestCase):
    """深度分析会花钱:同时只生成一个 + 每天限额 + 入参限长。命中缓存不受限。"""

    ITEMS = [{"id": "a1", "title_zh": "英伟达联手SK海力士", "summary_zh": "", "body_excerpt": "",
              "source": "X·T", "source_url": "http://x/1", "published_at": "2026-09-29T10:00:00+08:00",
              "score": 9.0, "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}]}]

    def setUp(self):
        import serenity
        self.s = serenity
        self.cache = {}
        self.patches = [
            mock.patch.object(serenity.llm, "enabled", return_value=True),
            mock.patch.object(serenity, "_deepseek", return_value="## 核心判断\n看上游。"),
            mock.patch.object(serenity, "fetch_supplement", return_value=[]),
            mock.patch.object(serenity, "_gather_research",
                              return_value={"text": "", "sources": [], "search": []}),
            mock.patch.object(serenity, "_load_cache", side_effect=lambda: dict(self.cache)),
            mock.patch.object(serenity, "_save_cache", side_effect=self.cache.update),
            mock.patch.object(serenity, "_today", return_value="2026-09-29"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_quota_blocks_new_generation(self):
        for i in range(self.s.DAILY_MAX):
            self.cache["x%d@2026-09-29" % i] = {"status": "ok"}
        self.cache["old@2026-09-28"] = {"status": "ok"}
        r = self.s.analyze("英伟达", self.ITEMS)
        self.assertEqual(r["status"], "quota")
        self.s._deepseek.assert_not_called()

    def test_yesterday_does_not_count(self):
        for i in range(self.s.DAILY_MAX):
            self.cache["x%d@2026-09-28" % i] = {"status": "ok"}
        self.assertEqual(self.s.analyze("英伟达", self.ITEMS)["status"], "ok")

    def test_cached_still_served_when_quota_used_up(self):
        for i in range(self.s.DAILY_MAX):
            self.cache["x%d@2026-09-29" % i] = {"status": "ok"}
        self.cache["英伟达@2026-09-29"] = {"status": "ok", "entity": "英伟达", "analysis_md": "m"}
        r = self.s.analyze("英伟达", self.ITEMS)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["cached"])

    def test_busy_when_another_generation_running(self):
        self.assertTrue(self.s._GEN_LOCK.acquire(blocking=False))
        try:
            r = self.s.analyze("英伟达", self.ITEMS)
        finally:
            self.s._GEN_LOCK.release()
        self.assertEqual(r["status"], "busy")
        self.s._deepseek.assert_not_called()

    def test_lock_released_after_generation_and_failure(self):
        self.assertEqual(self.s.analyze("英伟达", self.ITEMS)["status"], "ok")
        self.assertFalse(self.s._GEN_LOCK.locked())
        self.s._deepseek.side_effect = RuntimeError("down")
        self.assertEqual(self.s.analyze("NVDA", self.ITEMS)["status"], "error")
        self.assertFalse(self.s._GEN_LOCK.locked())

    def test_entity_too_long(self):
        r = self.s.analyze("英" * (self.s.ENTITY_MAX_LEN + 1), self.ITEMS)
        self.assertEqual(r["status"], "bad_request")
        self.s._deepseek.assert_not_called()

    def test_failures_count_toward_quota(self):
        """DeepSeek 欠费/故障时每次失败仍会去抓外部站点,失败也要占额度。"""
        self.s._deepseek.side_effect = RuntimeError("402")
        with mock.patch.dict(self.s._FAILS, clear=True):
            for _ in range(self.s.DAILY_MAX):
                self.assertEqual(self.s.analyze("英伟达", self.ITEMS)["status"], "error")
            self.assertEqual(self.s.analyze("英伟达", self.ITEMS)["status"], "quota")
            self.assertEqual(self.s._deepseek.call_count, self.s.DAILY_MAX)


class TestSerenityIO(unittest.TestCase):
    def test_read_capped_enforces_total_deadline(self):
        """对端持续吐空行(DeepSeek 排队)时,单次读超时永远不触发,要靠总时限。"""
        import time
        import serenity

        class Dribble:
            def read1(self, n):
                return b"\n"
        with self.assertRaises(TimeoutError):
            serenity._read_capped(Dribble(), time.monotonic() + 0.05)

    def test_read_capped_max_bytes(self):
        import io
        import time
        import serenity
        r = io.BufferedReader(io.BytesIO(b"x" * 500000))
        self.assertLessEqual(len(serenity._read_capped(r, time.monotonic() + 5, 100000)), 100000 + 65536)

    def test_cache_atomic_write_and_corrupt_file_kept(self):
        import serenity
        tmp = tempfile.mkdtemp(prefix="aihot_sr_")
        try:
            path = os.path.join(tmp, "analysis_cache.json")
            with mock.patch.object(serenity, "CACHE_FILE", path):
                serenity._save_cache({"a@2026-09-29": {"status": "ok"}})
                self.assertEqual(serenity._load_cache(), {"a@2026-09-29": {"status": "ok"}})
                self.assertFalse(os.path.exists(path + ".tmp"))
                with open(path, "w", encoding="utf-8") as f:
                    f.write('{"a@2026-09-29": {"sta')           # 半截文件
                self.assertEqual(serenity._load_cache(), {})
                self.assertTrue(any(n.startswith("analysis_cache.json.corrupt-")
                                    for n in os.listdir(tmp)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
