# -*- coding: utf-8 -*-
import json
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock
from tests._fixtures import SAMPLE_ITEMS


class TestAnalysisEndpoint(unittest.TestCase):
    def setUp(self):
        import server, serenity
        self.server, self.serenity = server, serenity
        server.ITEMS = list(SAMPLE_ITEMS)  # /api/analysis 不依赖 _dt
        self.p_enabled = mock.patch.object(serenity.llm, "enabled", return_value=True)
        self.p_deep = mock.patch.object(serenity, "_deepseek",
                                        return_value="## 核心判断\n看上游。")
        self.p_supp = mock.patch.object(serenity, "fetch_supplement", return_value=[])
        self.p_cache = mock.patch.object(serenity, "_load_cache", return_value={})
        self.p_save = mock.patch.object(serenity, "_save_cache")
        for p in (self.p_enabled, self.p_deep, self.p_supp, self.p_cache, self.p_save):
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()                    # 释放监听 socket，避免 ResourceWarning
        for p in (self.p_enabled, self.p_deep, self.p_supp, self.p_cache, self.p_save):
            p.stop()

    def _get(self, qs):
        # percent-encode non-ASCII chars so http.client can send the URL
        encoded = urllib.parse.quote(qs, safe="=&")
        url = "http://127.0.0.1:%d/api/analysis?%s" % (self.port, encoded)
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_ok(self):
        status, body = self._get("channel=finance&entity=英伟达")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn(self.serenity.DISCLAIMER, body["analysis_md"])
        self.assertEqual(body["based_on"], ["a1", "b2"])

    def test_no_data_for_unknown_entity(self):
        status, body = self._get("channel=finance&entity=茅台")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "no_data")
