# -*- coding: utf-8 -*-
"""服务启动顺序:必须先能监听、再抓数据。

2026-09-10 事故:main() 里 refresh() 是同步的,首轮抓取(金十 + X/KOL + LLM 增强)耗时数分钟,
端口在那之前根本没开——每次重启公网 /news/ 都白吐几分钟 502(实测 09:46:59 起进程、
09:50:08 才就绪),run.py 的健康检查还顺带误报一次"数据服务无法访问"。
修法与 goldhot 对齐:main() 用 hot_start() 读磁盘缓存(毫秒级),首轮真实抓取挪进后台线程。
"""
import json
import os
import tempfile
import unittest


class TestHotStart(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig_items = server.ITEMS
        self._orig_file = server.DATA_FILE
        self.tmp = tempfile.mkdtemp(prefix="aihot_startup_")

    def tearDown(self):
        import shutil
        self.server.ITEMS = self._orig_items
        self.server.DATA_FILE = self._orig_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cache(self, items):
        p = os.path.join(self.tmp, "items.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        self.server.DATA_FILE = p

    def test_hot_start_loads_cache_without_network(self):
        """热启动只读磁盘:即便把 fetch_all 换成会炸的桩,也必须能拿到数据。"""
        import newsfetch
        self._write_cache([
            {"id": "a", "published_at": "2026-09-10T09:00:00+08:00", "title_zh": "t",
             "source": "金十", "stock_only": True, "channels": ["finance"]},
            {"id": "b", "published_at": "2026-09-10T10:00:00+08:00", "title_zh": "u",
             "source": "X·KOL", "stock_only": False, "channels": ["finance"]},
        ])
        orig = newsfetch.fetch_all
        newsfetch.fetch_all = lambda *a, **k: self.fail("热启动不该联网抓取")
        try:
            n = self.server.hot_start()
        finally:
            newsfetch.fetch_all = orig
        self.assertEqual(n, 2)
        self.assertEqual(len(self.server.ITEMS), 2)
        # 倒序 + _dt 已挂好,可直接进过滤/渲染
        self.assertEqual(self.server.ITEMS[0]["id"], "b")
        self.assertIn("_dt", self.server.ITEMS[0])

    def test_hot_start_keeps_derived_fields(self):
        """items.json 保留了上一轮的派生字段(stock_only 等),热启动后过滤口径立即正确,
        不必等首轮 refresh 跑完——否则金十条目会在主流视图里露出几分钟。"""
        self._write_cache([
            {"id": "a", "published_at": "2026-09-10T09:00:00+08:00", "title_zh": "t",
             "source": "金十", "stock_only": True, "channels": ["finance"]},
        ])
        self.server.hot_start()
        self.assertIs(self.server.ITEMS[0]["stock_only"], True)

    def test_hot_start_survives_missing_cache(self):
        """缓存文件不存在也要能开服务(空列表 > 502)。"""
        self.server.DATA_FILE = os.path.join(self.tmp, "nope.json")
        n = self.server.hot_start()
        self.assertEqual(n, len(self.server.ITEMS))
        self.assertIsInstance(self.server.ITEMS, list)

    def test_hot_start_survives_corrupt_cache(self):
        """缓存被写坏(断电/半截写入,本机 9-10 就非正常关机两次)时不得抛。"""
        p = os.path.join(self.tmp, "items.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('[{"id": "a", "published_at":')     # 截断的 JSON
        self.server.DATA_FILE = p
        n = self.server.hot_start()
        self.assertIsInstance(n, int)
        self.assertIsInstance(self.server.ITEMS, list)

    def test_bg_refresh_runs_first_round_itself(self):
        """首轮抓取的责任已移交后台线程:first_refresh=True 时必须自己先跑一轮,
        否则热启动之后数据会停在缓存上、直到 REFRESH_SECONDS 之后才更新。"""
        calls = []
        orig = self.server.refresh
        self.server.refresh = lambda verbose=True: calls.append(verbose)
        # 让 while 循环第一次 sleep 就退出,只验证"循环前那一轮"
        orig_sleep = self.server.time.sleep

        def boom(_s):
            raise KeyboardInterrupt

        self.server.time.sleep = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.server._bg_refresh(first_refresh=True)
        finally:
            self.server.refresh = orig
            self.server.time.sleep = orig_sleep
        self.assertEqual(len(calls), 1, "first_refresh=True 应在进入循环前先抓一轮")

    def test_bg_refresh_can_skip_first_round(self):
        calls = []
        orig = self.server.refresh
        self.server.refresh = lambda verbose=True: calls.append(verbose)
        orig_sleep = self.server.time.sleep

        def boom(_s):
            raise KeyboardInterrupt

        self.server.time.sleep = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.server._bg_refresh(first_refresh=False)
        finally:
            self.server.refresh = orig
            self.server.time.sleep = orig_sleep
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
