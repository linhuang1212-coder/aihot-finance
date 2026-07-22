# -*- coding: utf-8 -*-
import unittest


def _it(iid, source, selected=True, verified="confirmed"):
    return {"id": iid, "channels": ["finance"], "source": source,
            "selected": selected, "verified": verified,
            "categories": [], "markets": [], "entities": [], "tags": [],
            "title_zh": "t" + iid, "summary_zh": "", "body_excerpt": ""}


class TestSourceFilter(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig = server.ITEMS
        server.ITEMS = [
            _it("a", "金十"),
            _it("b", "X·Walter Bloomberg"),
            _it("c", "X·GlobeEye News"),
            _it("d", "华尔街见闻"),
            _it("e", "XPeng快讯"),          # 以 X 开头但非 X·，不应命中
        ]

    def tearDown(self):
        self.server.ITEMS = self._orig

    def _ids(self, qs):
        return sorted(it["id"] for it in self.server.filter_items(qs))

    def test_jin10_exact(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "source": ["金十"]}),
            ["a"])

    def test_x_prefix_only(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "source": ["X"]}),
            ["b", "c"])

    def test_no_source_unaffected(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"]}),
            ["a", "b", "c", "d", "e"])

    def test_source_with_selected(self):
        self.server.ITEMS[1]["selected"] = False        # b 非精选
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["selected"], "source": ["X"]}),
            ["c"])


class TestPagination(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig = server.ITEMS
        server.ITEMS = [{"id": str(i), "channels": ["finance"], "source": "金十",
                         "selected": True, "verified": "confirmed", "categories": [],
                         "markets": [], "entities": [], "tags": [], "title_zh": "t",
                         "summary_zh": "", "body_excerpt": ""} for i in range(250)]

    def tearDown(self):
        self.server.ITEMS = self._orig

    def test_offset_paginates(self):
        q = {"channel": ["finance"], "mode": ["all"], "take": ["100"]}
        p1 = self.server.filter_items({**q, "offset": ["0"]})
        p2 = self.server.filter_items({**q, "offset": ["100"]})
        p3 = self.server.filter_items({**q, "offset": ["200"]})
        self.assertEqual([len(p1), len(p2), len(p3)], [100, 100, 50])
        self.assertEqual([x["id"] for x in p1[:2]], ["0", "1"])
        self.assertEqual([x["id"] for x in p2[:2]], ["100", "101"])   # 第二页不重叠

    def test_no_offset_is_page1(self):
        got = self.server.filter_items({"channel": ["finance"], "mode": ["all"], "take": ["100"]})
        self.assertEqual(got[0]["id"], "0")
        self.assertEqual(len(got), 100)


class TestStockFilter(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig = server.ITEMS

        def it(iid, source, ents):
            return {"id": iid, "channels": ["finance"], "source": source, "selected": True,
                    "verified": "confirmed", "categories": [], "markets": [], "entities": ents,
                    "tags": [], "title_zh": "t", "summary_zh": "", "body_excerpt": ""}

        server.ITEMS = [
            it("a", "金十", [{"name": "瑞华泰", "code": "688323", "market": "a-share"}]),
            it("b", "金十", [{"name": "WTI原油", "code": "CL", "market": "us"}]),         # 商品
            it("c", "金十", []),                                                          # 宏观无实体
            it("d", "X·Bloomberg", [{"name": "比亚迪", "code": "002594", "market": "a-share"}]),  # 非金十
        ]

    def tearDown(self):
        self.server.ITEMS = self._orig

    def _ids(self, qs):
        return sorted(x["id"] for x in self.server.filter_items(qs))

    def test_stock_keeps_a_share_only(self):
        self.assertEqual(self._ids({"channel": ["finance"], "mode": ["all"], "stock": ["1"]}),
                         ["a", "d"])

    def test_stock_with_jin10_source(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "stock": ["1"], "source": ["金十"]}),
            ["a"])

    def test_no_stock_unaffected(self):
        self.assertEqual(self._ids({"channel": ["finance"], "mode": ["all"]}),
                         ["a", "b", "c", "d"])


class TestStockOnlyFilter(unittest.TestCase):
    """金十=stock_only:主流视图隐藏,仅个股(stock=1)视图放行。"""

    def setUp(self):
        import server
        self.server = server
        self._orig = server.ITEMS

        def it(iid, source, stock_only, ents):
            return {"id": iid, "channels": ["finance"], "source": source, "selected": True,
                    "verified": "confirmed", "categories": [], "markets": [], "entities": ents,
                    "tags": [], "title_zh": "t", "summary_zh": "", "body_excerpt": "",
                    "stock_only": stock_only}

        server.ITEMS = [
            it("a", "DIGITIMES·涨价追踪", False, []),                                    # 主流
            it("b", "X·Bloomberg", False, []),                                          # 主流
            it("c", "金十", True, [{"name": "比亚迪", "code": "002594", "market": "a-share"}]),  # 个股
            it("d", "金十", True, [{"name": "WTI", "code": "CL", "market": "us"}]),       # 金十非A股
        ]

    def tearDown(self):
        self.server.ITEMS = self._orig

    def _ids(self, qs):
        return sorted(x["id"] for x in self.server.filter_items(qs))

    def test_main_feed_hides_stock_only(self):
        self.assertEqual(self._ids({"channel": ["finance"], "mode": ["all"]}), ["a", "b"])

    def test_stock_view_shows_jin10_a_share(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "stock": ["1"]}), ["c"])


class TestMainlineFilter(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._orig = server.ITEMS

        def it(iid, source, mainline):
            return {"id": iid, "channels": ["finance"], "source": source, "selected": True,
                    "verified": "confirmed", "categories": [], "markets": [], "entities": [],
                    "tags": [], "title_zh": "t", "summary_zh": "", "body_excerpt": "",
                    "mainline": mainline}

        server.ITEMS = [
            it("a", "金十", True),            # 算力链
            it("b", "X·Bloomberg", True),     # 算力链(X)
            it("c", "金十", False),           # 非主线
            it("d", "金十", None),            # 未打标(旧条目)
        ]

    def tearDown(self):
        self.server.ITEMS = self._orig

    def _ids(self, qs):
        return sorted(x["id"] for x in self.server.filter_items(qs))

    def test_mainline_keeps_tagged_only(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "mainline": ["1"]}),
            ["a", "b"])

    def test_mainline_with_source(self):
        self.assertEqual(
            self._ids({"channel": ["finance"], "mode": ["all"], "mainline": ["1"], "source": ["金十"]}),
            ["a"])

    def test_no_mainline_unaffected(self):
        self.assertEqual(self._ids({"channel": ["finance"], "mode": ["all"]}),
                         ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
