# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))


def _item(iid, group=None, days_ago=0, **kw):
    dt = datetime.now(CST) - timedelta(days=days_ago)
    it = {"id": iid, "published_at": dt.isoformat(timespec="seconds"),
          "title_zh": "标题" + iid, "summary_zh": "摘要", "body_excerpt": "",
          "source": "新浪财经", "source_url": "http://x/" + iid, "lang": "zh",
          "channels": ["finance"], "categories": ["macro"], "tags": [],
          "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}], "markets": [],
          "heat": "", "read_count": None, "verified": "confirmed",
          "dedup_group": group, "score": 5.0, "selected": True, "dup_count": 1}
    it.update(kw)
    return it


class TestStore(unittest.TestCase):
    def setUp(self):
        import store
        self.store = store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_insert_and_read_roundtrip(self):
        self.store.upsert_items([_item("a")])
        got = self.store.recent_items()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], "a")
        self.assertEqual(got[0]["entities"][0]["name"], "英伟达")   # JSON 列还原
        self.assertEqual(got[0]["categories"], ["macro"])
        self.assertIs(got[0]["selected"], True)                    # 0/1 -> bool

    def test_dedup_by_id_updates(self):
        ins1, _ = self.store.upsert_items([_item("a", score=5.0)])
        ins2, upd2 = self.store.upsert_items([_item("a", score=9.0)])
        self.assertEqual((ins1, ins2, upd2), (1, 0, 1))
        got = self.store.recent_items()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["score"], 9.0)                     # 同 id 更新

    def test_dedup_by_group_skips(self):
        self.store.upsert_items([_item("a", group="g1")])
        ins, _ = self.store.upsert_items([_item("b", group="g1")])  # 不同 id 同 group
        self.assertEqual(ins, 0)                                   # 同事件跳过
        self.assertEqual(len(self.store.recent_items()), 1)

    def test_dedup_by_title_similarity_across_fetches(self):
        # 不同 id、不同 group，但标题高度相似（同事件不同源/重发）-> 跨刷新视为重复
        self.store.upsert_items([_item("a", group="g1", title_zh="中韩半导体ETF华泰柏瑞触及涨停")])
        ins, _ = self.store.upsert_items([_item("b", group="g2", title_zh="中韩半导体ETF华泰柏瑞涨停")])
        self.assertEqual(ins, 0)
        self.assertEqual(len(self.store.recent_items()), 1)

    def test_distinct_titles_both_kept(self):
        # 标题不相似（不同新闻）-> 都保留，不误并
        self.store.upsert_items([_item("a", group="g1", title_zh="中韩半导体ETF华泰柏瑞触及涨停")])
        ins, _ = self.store.upsert_items([_item("c", group="g3", title_zh="美联储宣布维持基准利率不变")])
        self.assertEqual(ins, 1)
        self.assertEqual(len(self.store.recent_items()), 2)

    def test_sentiment_roundtrip(self):
        it = _item("s1")
        it["sentiment"] = {"dir": "利多", "tgt": "存储芯片", "str": 3}
        self.store.upsert_items([it])
        got = self.store.recent_items()[0]
        self.assertEqual(got["sentiment"], {"dir": "利多", "tgt": "存储芯片", "str": 3})

    def test_no_sentiment_absent(self):
        self.store.upsert_items([_item("s2")])      # _item 不带 sentiment
        self.assertNotIn("sentiment", self.store.recent_items()[0])

    def test_sentiment_updates_on_refetch(self):
        # 首存无情绪，后续刷新带上情绪 -> UPDATE 写入
        self.store.upsert_items([_item("s3")])
        it = _item("s3")
        it["sentiment"] = {"dir": "利空", "tgt": "存储", "str": 2}
        self.store.upsert_items([it])
        self.assertEqual(self.store.recent_items()[0]["sentiment"]["dir"], "利空")

    def test_recent_window_excludes_old(self):
        self.store.upsert_items([_item("recent", days_ago=1), _item("old", days_ago=40)])
        ids = {x["id"] for x in self.store.recent_items(30)}
        self.assertIn("recent", ids)
        self.assertNotIn("old", ids)

    def test_prune_deletes_old(self):
        self.store.upsert_items([_item("recent", days_ago=1), _item("old", days_ago=40)])
        self.assertEqual(self.store.prune(30), 1)
        self.assertEqual(len(self.store.recent_items(9999)), 1)    # 只剩 recent

    def test_migrate_only_when_empty(self):
        import json
        jf = self.tmp.name + ".json"
        with open(jf, "w", encoding="utf-8") as f:
            json.dump([_item("m1"), _item("m2")], f, ensure_ascii=False)
        self.assertEqual(self.store.migrate_from_json(jf), 2)       # 空库 -> 迁入 2
        self.assertEqual(self.store.migrate_from_json(jf), 0)       # 非空 -> 不再迁
        os.unlink(jf)


class TestMentions(unittest.TestCase):
    """upsert 各路径把"同事件再报道"留痕进 mentions((group,source) 首见一行)。"""

    def setUp(self):
        import store
        self.store = store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _mentions(self, group):
        con = self.store._conn()
        rows = con.execute(
            "SELECT source, ts FROM mentions WHERE group_key=?", (group,)).fetchall()
        con.close()
        return rows

    def test_new_item_records_first_mention(self):
        self.store.upsert_items([_item("a", group="g1")])
        rows = self._mentions("g1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "新浪财经")          # _item 默认源

    def test_group_skip_still_records_mention(self):
        self.store.upsert_items([_item("a", group="g1")])
        self.store.upsert_items([_item("b", group="g1", source="金十",
                                       title_zh="完全不同的另一个标题写法")])
        self.assertEqual(len(self.store.recent_items()), 1)   # 条目仍跳过
        self.assertEqual(len(self._mentions("g1")), 2)        # 但提及记到 2 家

    def test_similar_title_skip_records_to_matched_group(self):
        self.store.upsert_items([_item("a", group="g1",
                                       title_zh="中韩半导体ETF华泰柏瑞触及涨停")])
        self.store.upsert_items([_item("b", group="g2", source="金十",
                                       title_zh="中韩半导体ETF华泰柏瑞涨停")])
        self.assertEqual(len(self._mentions("g1")), 2)        # 归被命中条目的簇
        self.assertEqual(len(self._mentions("g2")), 0)

    def test_same_source_counted_once(self):
        self.store.upsert_items([_item("a", group="g1")])
        self.store.upsert_items([_item("b", group="g1",
                                       title_zh="完全不同的另一个标题写法")])  # 同源(新浪财经)
        self.assertEqual(len(self._mentions("g1")), 1)        # (group,source) 幂等

    def test_id_update_records_grown_also_sources(self):
        # _cluster 单轮内并簇:跨轮新增信源体现为同 id 条目 also_sources 增长
        self.store.upsert_items([_item("a", group="g1", also_sources=["新浪财经"])])
        self.store.upsert_items([_item("a", group="g1",
                                       also_sources=["新浪财经", "金十", "CNBC"])])
        self.assertEqual(len(self._mentions("g1")), 3)

    def test_no_group_no_mention_no_crash(self):
        self.store.upsert_items([_item("a", group=None)])
        con = self.store._conn()
        n = con.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_prune_clears_old_mentions(self):
        old = (datetime.now(CST) - timedelta(days=40)).isoformat(timespec="seconds")
        con = self.store._conn()
        con.execute("INSERT INTO mentions VALUES (?,?,?,?)", ("gx", "旧源", "t", old))
        con.commit()
        con.close()
        self.store.upsert_items([_item("a", group="g1")])     # 近期提及
        self.store.prune(30)
        con = self.store._conn()
        rows = con.execute("SELECT group_key FROM mentions").fetchall()
        con.close()
        self.assertEqual([r[0] for r in rows], ["g1"])        # 旧的删了、新的还在


class TestPropagation(unittest.TestCase):
    """滚动窗口内首报信源数 -> 传播档位(3/5/7 -> LV1/2/3)。"""

    def setUp(self):
        import store
        self.store = store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _mention(self, group, source, minutes_ago=0):
        ts = (datetime.now(CST) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        con = self.store._conn()
        con.execute("INSERT OR IGNORE INTO mentions VALUES (?,?,?,?)",
                    (group, source, "t", ts))
        con.commit()
        con.close()

    def test_three_sources_is_level1(self):
        for s in ("源1", "源2", "源3"):
            self._mention("g1", s)
        p = self.store.propagation()
        self.assertEqual(p["g1"]["level"], 1)
        self.assertEqual(p["g1"]["sources"], 3)
        self.assertEqual(p["g1"]["window_min"], 90)     # 默认窗口,供 bot 渲染

    def test_five_sources_is_level2_seven_level3(self):
        for i in range(5):
            self._mention("g1", "源%d" % i)
        for i in range(7):
            self._mention("g2", "源%d" % i)
        p = self.store.propagation()
        self.assertEqual(p["g1"]["level"], 2)
        self.assertEqual(p["g2"]["level"], 3)

    def test_below_threshold_absent(self):
        self._mention("g1", "源1")
        self._mention("g1", "源2")
        self.assertEqual(self.store.propagation(), {})   # 2 家不到 LV1,不返回

    def test_window_excludes_old_mentions(self):
        for s in ("源1", "源2", "源3"):
            self._mention("g1", s, minutes_ago=999)      # 全在窗口外
        self.assertEqual(self.store.propagation(), {})
        # 放大窗口就能看到 -> 验证确为时间过滤,非数据丢失
        self.assertEqual(self.store.propagation(window_min=100000)["g1"]["sources"], 3)


class TestPropSeries(unittest.TestCase):
    """store.prop_series:从 mentions 算累计扩散紧凑序列。"""

    def setUp(self):
        import store
        self.store = store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _mention(self, group, source, ts):
        con = self.store._conn()
        con.execute("INSERT INTO mentions (group_key, source, title_zh, ts) VALUES (?,?,?,?)",
                    (group, source, "t", ts))
        con.commit()
        con.close()

    def test_cumulative_monotonic_first_last(self):
        self._mention("g1", "金十", "2026-06-12T10:00:00+08:00")
        self._mention("g1", "Bloomberg", "2026-06-12T10:30:00+08:00")
        self._mention("g1", "华尔街见闻", "2026-06-12T11:00:00+08:00")
        out = self.store.prop_series()
        self.assertIn("g1", out)
        s = out["g1"]
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["first_source"], "金十")
        self.assertEqual(s["span_min"], 60)
        cums = [c for _, c in s["pts"]]
        self.assertEqual(cums[0], 1)               # 首点累计=1
        self.assertEqual(cums[-1], 3)              # 末点累计=n
        self.assertEqual(cums, sorted(cums))       # 单调不减

    def test_single_source_excluded(self):
        self._mention("g2", "金十", "2026-06-12T10:00:00+08:00")
        out = self.store.prop_series()
        self.assertNotIn("g2", out)                # n<2 不返回

    def test_sampled_to_max_points(self):
        for i in range(40):
            self._mention("g3", "src%d" % i,
                          "2026-06-12T10:%02d:00+08:00" % i)
        out = self.store.prop_series(max_points=12)
        self.assertLessEqual(len(out["g3"]["pts"]), 12)
        self.assertEqual(out["g3"]["pts"][0][1], 1)
        self.assertEqual(out["g3"]["pts"][-1][1], 40)

    def test_groups_filter(self):
        self._mention("g1", "a", "2026-06-12T10:00:00+08:00")
        self._mention("g1", "b", "2026-06-12T10:10:00+08:00")
        self._mention("g4", "a", "2026-06-12T10:00:00+08:00")
        self._mention("g4", "b", "2026-06-12T10:10:00+08:00")
        out = self.store.prop_series(groups={"g1"})
        self.assertIn("g1", out)
        self.assertNotIn("g4", out)

    def test_zero_span_two_sources(self):
        # 两家信源同一秒到达 -> 首点 cum=1, 末点 cum=n
        self._mention("g9", "A", "2026-06-12T10:00:00+08:00")
        self._mention("g9", "B", "2026-06-12T10:00:00+08:00")
        s = self.store.prop_series()["g9"]
        self.assertEqual(s["pts"][0][1], 1)
        self.assertEqual(s["pts"][-1][1], 2)

    def test_groups_empty_set_returns_empty(self):
        self._mention("g1", "a", "2026-06-12T10:00:00+08:00")
        self._mention("g1", "b", "2026-06-12T10:10:00+08:00")
        self.assertEqual(self.store.prop_series(groups=set()), {})


class TestPropDetail(unittest.TestCase):
    """store.prop_detail:单事件逐家信源加入明细。"""

    def setUp(self):
        import store
        self.store = store
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._orig = store.DB_FILE
        store.DB_FILE = self.tmp.name
        store.init_db()

    def tearDown(self):
        self.store.DB_FILE = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _mention(self, group, source, ts):
        con = self.store._conn()
        con.execute("INSERT INTO mentions (group_key, source, title_zh, ts) VALUES (?,?,?,?)",
                    (group, source, "t", ts))
        con.commit()
        con.close()

    def test_per_source_cum_increasing(self):
        self._mention("g1", "金十", "2026-06-12T10:00:00+08:00")
        self._mention("g1", "Bloomberg", "2026-06-12T10:30:00+08:00")
        d = self.store.prop_detail("g1")
        self.assertEqual(d["n"], 2)
        self.assertEqual([p["cum"] for p in d["points"]], [1, 2])
        self.assertEqual(d["points"][0]["source"], "金十")
        self.assertEqual(d["first_ts"], "2026-06-12T10:00:00+08:00")

    def test_missing_group_empty(self):
        d = self.store.prop_detail("nope")
        self.assertEqual(d["n"], 0)
        self.assertEqual(d["points"], [])

    def test_none_group_empty(self):
        d = self.store.prop_detail(None)
        self.assertEqual(d["n"], 0)
        self.assertEqual(d["points"], [])
