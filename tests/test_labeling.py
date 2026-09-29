# -*- coding: utf-8 -*-
"""精选校准标注页的接口:密钥、抽样(覆盖未精选/已标不再出)、追加写入、入参校验、限速。
服务器唯一的写接口,经隧道公网可达,所以安全约束都要有测试。文件都指向临时目录。"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

CST = timezone(timedelta(hours=8))


def _it(iid, selected, mainline, minutes_ago=10, source="X·Test"):
    return {"id": iid, "channels": ["finance"], "source": source, "selected": selected,
            "mainline": mainline, "verified": "confirmed", "categories": [], "markets": [],
            "entities": [], "tags": [], "title_zh": "标题" + iid, "summary_zh": "摘要",
            "body_excerpt": "body " + iid, "llm_importance": 7 if selected else 3,
            "published_at": (datetime.now(CST) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")}


class TestLabeling(unittest.TestCase):
    def setUp(self):
        import labeling
        import server
        import llm
        self.lab, self.server = labeling, server
        self.tmp = tempfile.mkdtemp(prefix="aihot_label_")
        self.patches = [
            mock.patch.object(labeling, "KEY_FILE", os.path.join(self.tmp, "label_key.txt")),
            mock.patch.object(labeling, "LABELS_FILE", os.path.join(self.tmp, "labels.jsonl")),
            mock.patch.object(labeling, "_key", None),
            mock.patch.object(labeling, "_labeled", None),
            mock.patch.object(labeling, "_writes", []),
            mock.patch.object(llm, "_load", return_value={"s1": {"pv": "abc12345"}}),
        ]
        for p in self.patches:
            p.start()
        self._orig_items = server.ITEMS
        server.ITEMS = [_it("s1", True, True), _it("s2", True, False), _it("n1", False, True),
                        _it("n2", False, False), _it("j1", False, False, source="金十"),
                        _it("old", False, False, minutes_ago=60 * 24 * 10)]
        self.key = labeling.get_key()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.ITEMS = self._orig_items
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if key is True:
            h["X-Label-Key"] = self.key
        elif key:
            h["X-Label-Key"] = key
        h.update(headers or {})
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, (json.loads(raw) if raw else None)

    def post(self, iid, label, key=True):
        return self.req("POST", "/api/label", {"id": iid, "label": label}, key=key)

    def test_key_generated_once_and_stored(self):
        with open(self.lab.KEY_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), self.key)
        self.assertGreaterEqual(len(self.key), 20)

    def test_wrong_or_missing_key_forbidden(self):
        self.assertEqual(self.req("GET", "/api/label/next", key=False)[0], 403)
        self.assertEqual(self.req("GET", "/api/label/next", key="nope")[0], 403)
        self.assertEqual(self.req("GET", "/api/label/next?key=" + self.key, key=False)[0], 403)  # 不再收查询串
        self.assertEqual(self.post("s1", 1, key="nope")[0], 403)
        self.assertEqual(self.post("s1", 1, key=False)[0], 403)
        self.assertFalse(os.path.exists(self.lab.LABELS_FILE))

    def test_key_file_with_bom(self):
        with open(self.lab.KEY_FILE, "w", encoding="utf-8-sig") as f:
            f.write("MYKEY123")
        self.lab._key = None
        self.assertTrue(self.lab.check_key("MYKEY123"))

    def test_next_hides_ai_score_and_skips_hidden_old_and_labeled(self):
        seen = set()
        for _ in range(60):
            st, d = self.req("GET", "/api/label/next")
            self.assertEqual(st, 200)
            it = d["item"]
            self.assertNotIn("llm_importance", it)
            self.assertNotIn("selected", it)
            seen.add(it["id"])
        self.assertEqual(seen, {"s1", "s2", "n1", "n2"})       # 未精选的也会出;金十/超 7 天不出
        for iid in ("s1", "s2", "n1", "n2"):
            self.assertEqual(self.post(iid, 1)[0], 200)
        st, d = self.req("GET", "/api/label/next")
        self.assertIsNone(d["item"])
        self.assertEqual(d["stats"], {"labeled": 4, "keep": 4})

    def test_post_appends_snapshot_line(self):
        st, d = self.post("s1", 1)
        self.assertEqual(st, 200)
        self.assertEqual(d["stats"], {"labeled": 1, "keep": 1})
        self.post("n2", 0)
        with open(self.lab.LABELS_FILE, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual([(r["id"], r["label"]) for r in rows], [("s1", 1), ("n2", 0)])
        self.assertEqual(rows[0]["imp"], 7)
        self.assertEqual(rows[0]["pv"], "abc12345")
        self.assertTrue(rows[0]["selected"])

    def test_labels_survive_reload(self):
        self.post("s1", 1)
        self.lab._labeled = None                     # 模拟重启:从文件重建已标集合
        self.assertEqual(self.lab.stats(), {"labeled": 1, "keep": 1})

    def test_bad_requests_rejected(self):
        self.assertEqual(self.post("nope", 1)[0], 404)                          # id 不在工作集
        for bad in (5, True, 1.0, "1", None, 1e400):                            # 只收整数 0/1
            self.assertEqual(self.post("s1", bad)[0], 400, bad)
        self.assertEqual(self.req("POST", "/api/label", b"not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/label", b"[" * 3000)[0], 400)    # 深嵌套不 500
        self.assertEqual(self.req("POST", "/api/label", b'{"id": 1, "label": 1}')[0], 400)
        self.assertEqual(self.req("POST", "/api/label", b"x" * 5000)[0], 413)
        self.assertEqual(self.req("POST", "/api/label", b"")[0], 400)
        self.assertFalse(os.path.exists(self.lab.LABELS_FILE))

    def test_corrupt_labels_file_does_not_break_api(self):
        with open(self.lab.LABELS_FILE, "wb") as f:
            f.write('{"id": "s1", "label": 1}\n{"id": "s2", "la'.encode("utf-8") + b"\xe4\xb8\n")
        self.lab._labeled = None
        self.assertEqual(self.req("GET", "/api/label/stats")[0], 200)
        st, d = self.post("n1", 0)
        self.assertEqual(st, 200)
        self.assertEqual(d["stats"]["labeled"], 2)

    def test_rate_limited(self):
        with mock.patch.object(self.lab, "RATE_PER_MIN", 3):
            codes = [self.post("s1", 1)[0] for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])

    def test_other_posts_not_allowed(self):
        self.assertEqual(self.req("POST", "/api/public/items", {"a": 1})[0], 405)
        self.assertEqual(self.req("POST", "/api/analysis?entity=x", {"a": 1})[0], 405)

    def test_label_page_served(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/label.html")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        c.close()
        self.assertEqual(r.status, 200)
        self.assertNotIn(self.key, body)                                         # 密钥不在页面源码里
        self.assertIn("location.hash", body)                                     # 密钥从 # 后面读


if __name__ == "__main__":
    unittest.main()
