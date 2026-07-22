# -*- coding: utf-8 -*-
"""server.refresh() 挂传播档位/累计曲线到条目 + /api/public/propagation 接口。"""
import os
import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock


class TestRefreshAttachesPropagation(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig_items = server.ITEMS

    def tearDown(self):
        self.server.ITEMS = self._orig_items

    def test_propagation_attached_to_matching_group_only(self):
        import server, store, newsfetch
        rows = [
            {"id": "a", "published_at": "2026-06-10T10:00:00+08:00", "dedup_group": "g1"},
            {"id": "b", "published_at": "2026-06-10T09:00:00+08:00", "dedup_group": "g2"},
        ]
        prop = {"g1": {"level": 2, "sources": 5, "window_min": 90}}
        with mock.patch.object(newsfetch, "fetch_all", return_value=[]), \
             mock.patch.object(store, "recent_items", return_value=rows), \
             mock.patch.object(store, "propagation", return_value=prop), \
             mock.patch.object(server, "save_cache"):
            server.refresh(verbose=False)
        by_id = {it["id"]: it for it in server.ITEMS}
        self.assertEqual(by_id["a"]["propagation"],
                         {"level": 2, "sources": 5, "window_min": 90})
        self.assertNotIn("propagation", by_id["b"])      # 没档位的不塞键

    def test_propagation_failure_does_not_break_refresh(self):
        import server, store, newsfetch
        rows = [{"id": "a", "published_at": "2026-06-10T10:00:00+08:00", "dedup_group": "g1"}]
        with mock.patch.object(newsfetch, "fetch_all", return_value=[]), \
             mock.patch.object(store, "recent_items", return_value=rows), \
             mock.patch.object(store, "propagation", side_effect=RuntimeError("db locked")), \
             mock.patch.object(server, "save_cache"), \
             mock.patch.object(server, "load_cache", side_effect=Exception("no cache")):
            server.refresh(verbose=False)                # 不应抛异常
        self.assertEqual(self.server.ITEMS[0]["id"], "a")  # 工作集仍正常

    def test_prop_curve_attached_for_multi_source_only(self):
        import server, store, newsfetch
        rows = [
            {"id": "a", "published_at": "2026-06-12T10:00:00+08:00", "dedup_group": "g1"},
            {"id": "b", "published_at": "2026-06-12T09:00:00+08:00", "dedup_group": "g2"},
        ]
        series = {"g1": {"pts": [[0, 1], [30, 2]], "n": 2, "span_min": 30,
                         "first_ts": "2026-06-12T10:00:00+08:00", "first_source": "金十"}}
        with mock.patch.object(newsfetch, "fetch_all", return_value=[]), \
             mock.patch.object(store, "recent_items", return_value=rows), \
             mock.patch.object(store, "propagation", return_value={}), \
             mock.patch.object(store, "prop_series", return_value=series), \
             mock.patch.object(server, "save_cache"):
            server.refresh(verbose=False)
        by_id = {it["id"]: it for it in server.ITEMS}
        self.assertEqual(by_id["a"]["prop_curve"]["n"], 2)
        self.assertNotIn("prop_curve", by_id["b"])      # 无序列的不塞键


class TestPropagationEndpoint(unittest.TestCase):
    def setUp(self):
        import server, store
        self.server, self.store = server, store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig_db = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()
        con = store._conn()
        con.execute("INSERT INTO mentions (group_key, source, title_zh, ts) VALUES (?,?,?,?)",
                    ("g1", "金十", "t", "2026-06-12T10:00:00+08:00"))
        con.execute("INSERT INTO mentions (group_key, source, title_zh, ts) VALUES (?,?,?,?)",
                    ("g1", "Bloomberg", "t", "2026-06-12T10:30:00+08:00"))
        con.commit()
        con.close()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()                    # 释放监听 socket，避免 ResourceWarning
        self.store.DB_FILE = self._orig_db
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _get(self, qs):
        encoded = urllib.parse.quote(qs, safe="=&")
        url = "http://127.0.0.1:%d/api/public/propagation?%s" % (self.port, encoded)
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_returns_per_source_detail(self):
        status, body = self._get("group=g1")
        self.assertEqual(status, 200)
        self.assertEqual(body["n"], 2)
        self.assertEqual([p["cum"] for p in body["points"]], [1, 2])
        self.assertEqual(body["points"][0]["source"], "金十")

    def test_missing_group_empty(self):
        status, body = self._get("group=nope")
        self.assertEqual(status, 200)
        self.assertEqual(body["n"], 0)
        self.assertEqual(body["points"], [])


if __name__ == "__main__":
    unittest.main()
