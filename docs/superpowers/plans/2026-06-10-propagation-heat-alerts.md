# 传播热度曲线(发酵提醒) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把跨刷新被丢弃的"同事件再报道"信号留存为 mentions 流水,按滚动窗口算传播档位,事件发酵升档时通过 Telegram 推「📡 正在发酵」提醒(每档一次)。

**Architecture:** `store.py` 加 `mentions` 表((group_key, source) 唯一,记每信源首报时间),`upsert_items` 四条路径统一记提及;`store.propagation()` 按 90 分钟窗口算档位(3/5/7 家 → LV1/2/3);`server.refresh()` 把档位挂到条目随现有 API 带出;`telegram_bot.py` 推送循环里对比 `bot_state.json` 已提醒档位,升档且(重要度≥7 或命中自选)才推。

**Tech Stack:** Python 标准库(sqlite3 / unittest / mock),零第三方依赖。设计文档:`docs/specs/2026-06-10-传播热度曲线-design.md`。

**注意:** 本项目**不是 git 仓库**,各 Task 以"测试全绿 Checkpoint"代替 commit。测试命令统一在项目根 `c:\Users\Administrator\Desktop\新闻` 下运行。

---

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `store.py` | Modify | `mentions` 表 + `_record_mentions` + `upsert_items` 记提及 + `prune` 连删 + `propagation()` 档位计算 |
| `server.py` | Modify (refresh, 行 89-100) | 刷新后把 `propagation` 字段挂到工作集条目 |
| `telegram_bot.py` | Modify | `prop_alerted` 状态、`_prop_worthy`/`select_prop_alerts`/`render_prop_alert`、push_loop 接线 |
| `tests/test_store.py` | Modify | 新增 `TestMentions`、`TestPropagation` 两个测试类 |
| `tests/test_server_propagation.py` | Create | refresh 挂 propagation 的集成测试(mock store/newsfetch) |
| `tests/test_telegram.py` | Modify | 新增 `TestPropAlerts` 测试类 |

---

### Task 1: store — mentions 表与提及记录

**Files:**
- Modify: `store.py`(`init_db`、`upsert_items`、`prune`,新增 `_record_mentions`)
- Test: `tests/test_store.py`(追加 `TestMentions` 类)

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_store.py` 文件末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m unittest tests.test_store.TestMentions -v
```

预期:FAIL/ERROR(`no such table: mentions`)。

- [ ] **Step 3: 实现** —— `store.py` 三处改动:

(1) `init_db()` 里 `idx_group` 索引行之后、`cols = ...` 之前插入:

```python
    con.execute("""CREATE TABLE IF NOT EXISTS mentions (
        group_key TEXT, source TEXT, title_zh TEXT, ts TEXT,
        PRIMARY KEY (group_key, source))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_mention_ts ON mentions(ts)")
```

(2) 模块级新增函数(放在 `_from_row` 之后、`upsert_items` 之前):

```python
def _record_mentions(con, group, it, now):
    """记录信源提及:(group_key, source) 首见才记一行——同源重发/重复抓取天然幂等。
    代表条目带 also_sources(单轮聚类合并的各家)时逐家记,否则记 source 本身。"""
    if not group:
        return
    sources = it.get("also_sources") or [it.get("source") or ""]
    for s in sources:
        if s:
            con.execute(
                "INSERT OR IGNORE INTO mentions (group_key, source, title_zh, ts) "
                "VALUES (?,?,?,?)", (group, s, (it.get("title_zh") or "")[:200], now))
```

(3) `upsert_items()` 整体替换为(改动点:recent_feats 带 group;四条路径记提及):

```python
def upsert_items(items):
    """累积入库。返回 (inserted, updated)。跨刷新去重：
    同 id -> 更新可变字段；新 id 但 dedup_group 已存在 -> 跳过；
    与近窗口已存条目标题高度相似（同事件不同源/重发）-> 跳过；全新 -> 插入。
    四条路径都把"信源提及"留痕进 mentions(传播热度信号,见 2026-06-10 spec)。"""
    import newsfetch  # 复用标题相似度（懒加载，避免模块级耦合）
    con = _conn()
    existing_ids = set(r[0] for r in con.execute("SELECT id FROM items"))
    existing_groups = set(r[0] for r in con.execute(
        "SELECT dedup_group FROM items WHERE dedup_group IS NOT NULL AND dedup_group != ''"))
    sim_cutoff = (datetime.now(CST) - timedelta(days=2)).isoformat(timespec="seconds")
    recent_feats = []                       # [(标题特征, dedup_group)]——相似命中要知道归谁
    for title, grp in con.execute(
            "SELECT title_zh, dedup_group FROM items WHERE published_at >= ? "
            "ORDER BY published_at DESC LIMIT 1500", (sim_cutoff,)):
        f = newsfetch._title_features({"title_zh": title})
        if f:
            recent_feats.append((f, grp))
    now = datetime.now(CST).isoformat(timespec="seconds")
    placeholders = ",".join("?" * len(_COLS))
    insert_sql = "INSERT INTO items (%s) VALUES (%s)" % (",".join(_COLS), placeholders)
    ins = upd = 0
    for it in items:
        iid = it.get("id")
        if not iid:
            continue
        g = it.get("dedup_group")
        if iid in existing_ids:
            sent = it.get("sentiment")
            con.execute(
                "UPDATE items SET score=?, selected=?, heat=?, summary_zh=?, dup_count=?, "
                "sentiment=? WHERE id=?",
                (it.get("score"), 1 if it.get("selected") else 0, it.get("heat"),
                 it.get("summary_zh"), it.get("dup_count"),
                 json.dumps(sent, ensure_ascii=False) if sent else None, iid))
            _record_mentions(con, g, it, now)   # 簇可能新增了信源(also_sources 增长)
            upd += 1
            continue
        if g and g in existing_groups:
            _record_mentions(con, g, it, now)
            continue
        f = newsfetch._title_features(it)
        hit = False
        if f:
            for rf, rg in recent_feats:
                if (len(f & rf) / (len(f | rf) or 1)) >= DEDUP_SIM:
                    hit = True
                    _record_mentions(con, rg or g, it, now)  # 归被命中条目的簇
                    break
        if hit:
            continue  # 与近窗口某条标题高度相似 -> 跨刷新重复，跳过
        con.execute(insert_sql, _to_row(it, now))
        _record_mentions(con, g, it, now)
        existing_ids.add(iid)
        if g:
            existing_groups.add(g)
        if f:
            recent_feats.append((f, g))
        ins += 1
    con.commit()
    con.close()
    return ins, upd
```

(4) `prune()` 里 `n = cur.rowcount` 之后追加一行:

```python
    con.execute("DELETE FROM mentions WHERE ts < ?", (cutoff,))
```

- [ ] **Step 4: 跑测试确认通过**

```
python -m unittest tests.test_store -v
```

预期:`TestMentions` 全 PASS,原 `TestStore` 11 个回归不破。

- [ ] **Step 5: Checkpoint** —— `tests.test_store` 全绿。

---

### Task 2: store — propagation() 传播档位

**Files:**
- Modify: `store.py`(新增模块常量与 `propagation()`)
- Test: `tests/test_store.py`(追加 `TestPropagation` 类)

- [ ] **Step 1: 写失败测试** —— `tests/test_store.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m unittest tests.test_store.TestPropagation -v
```

预期:ERROR(`module 'store' has no attribute 'propagation'`)。

- [ ] **Step 3: 实现** —— `store.py`:

(1) 模块顶部常量区(`DEDUP_SIM = 0.5` 之后)追加:

```python
PROP_WINDOW_MIN = int(os.environ.get("PROP_WINDOW_MIN", "90"))   # 传播统计滚动窗口(分钟)
PROP_LEVELS = [int(x) for x in os.environ.get("PROP_LEVELS", "3,5,7").split(",")]  # LV1/2/3 信源数阈值
```

(2) `prune()` 之后新增:

```python
def _prop_level(n):
    lvl = 0
    for i, th in enumerate(PROP_LEVELS, 1):
        if n >= th:
            lvl = i
    return lvl


def propagation(window_min=None):
    """滚动窗口内各事件簇的首报信源数 -> 传播档位。
    返回 {group_key: {"level": 1-3, "sources": n, "window_min": w}};不足 LV1 的不返回。
    发酵=加速:统计的是窗口内**新加入**的信源,老事件无新跟进则档位自然回落。"""
    w = PROP_WINDOW_MIN if window_min is None else window_min
    cutoff = (datetime.now(CST) - timedelta(minutes=w)).isoformat(timespec="seconds")
    con = _conn()
    rows = con.execute(
        "SELECT group_key, COUNT(DISTINCT source) FROM mentions "
        "WHERE ts >= ? GROUP BY group_key", (cutoff,)).fetchall()
    con.close()
    out = {}
    for g, n in rows:
        lvl = _prop_level(n)
        if lvl:
            out[g] = {"level": lvl, "sources": n, "window_min": w}
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```
python -m unittest tests.test_store -v
```

- [ ] **Step 5: Checkpoint** —— `tests.test_store` 全绿。

---

### Task 3: server — refresh 挂 propagation 字段

**Files:**
- Modify: `server.py:89-100`(`refresh()` 的工作集读取段)
- Create: `tests/test_server_propagation.py`

- [ ] **Step 1: 写失败测试** —— 新建 `tests/test_server_propagation.py`:

```python
# -*- coding: utf-8 -*-
"""server.refresh() 把 store.propagation() 的档位挂到对应 dedup_group 的条目上。"""
import unittest
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
             mock.patch.object(server, "save_cache"):
            server.refresh(verbose=False)                # 不应抛异常
        self.assertEqual(self.server.ITEMS[0]["id"], "a")  # 工作集仍正常


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m unittest tests.test_server_propagation -v
```

预期:第一个用例 FAIL(条目上无 `propagation` 键)。

- [ ] **Step 3: 实现** —— `server.py` `refresh()` 中,把

```python
    try:
        items = store.recent_items()        # 工作集 = 累积库近 N 天
        if items:
            ITEMS = _attach_dt(items)
```

改为

```python
    try:
        items = store.recent_items()        # 工作集 = 累积库近 N 天
        if items:
            try:                            # 传播档位:由 mentions 滚动窗口重算,挂到代表条目
                prop = store.propagation()
                for it in items:
                    p = prop.get(it.get("dedup_group"))
                    if p:
                        it["propagation"] = p
            except Exception as e:
                if verbose:
                    print("[prop] failed:", e)
            ITEMS = _attach_dt(items)
```

(`public_item` 原样透传所有非下划线键,API 端**无需改动**。)

- [ ] **Step 4: 跑测试确认通过**

```
python -m unittest tests.test_server_propagation -v
```

- [ ] **Step 5: Checkpoint** —— 该文件全绿。

---

### Task 4: bot — 发酵提醒(门槛/选取/渲染)

**Files:**
- Modify: `telegram_bot.py`(常量、`_load_state`、`_select_fresh` 之后新增三个函数)
- Test: `tests/test_telegram.py`(追加 `TestPropAlerts` 类)

- [ ] **Step 1: 写失败测试** —— `tests/test_telegram.py` 末尾(`TestSendCards` 类之后、`if __name__` 之前)追加:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m unittest tests.test_telegram.TestPropAlerts -v
```

预期:ERROR(`module 'telegram_bot' has no attribute 'WATCHLIST_NAMES'`)。

- [ ] **Step 3: 实现** —— `telegram_bot.py` 四处:

(1) 常量区,`WATCH_ENTITY = "英伟达"` 之后追加:

```python
PROP_IMP_MIN = int(os.environ.get("PROP_IMP_MIN", "7"))      # 发酵提醒的重要度门槛
PROP_ALERTED_CAP = 2000                                       # 已提醒记账上限(防状态膨胀)


def _watchlist_names():
    try:
        with open(os.path.join(BASE, "watchlist.json"), encoding="utf-8") as f:
            return {e.get("name") for e in json.load(f).get("entities", []) if e.get("name")}
    except Exception:
        return set()


WATCHLIST_NAMES = _watchlist_names()
```

(2) `_load_state()` 里 `s.setdefault("baseline", False)` 之后追加:

```python
    s.setdefault("prop_alerted", {})    # dedup_group -> 已提醒到的传播档位
```

(3) `_select_fresh` 函数之后追加:

```python
def _prop_worthy(it):
    """发酵提醒门槛:重要(llm_importance>=PROP_IMP_MIN) 或 命中自选(实体/情绪标的)。
    LLM 关闭(无 llm_importance)时退化为仅自选命中,不报错。"""
    imp = it.get("llm_importance")
    if imp is not None and imp >= PROP_IMP_MIN:
        return True
    if any(e.get("name") in WATCHLIST_NAMES for e in (it.get("entities") or [])):
        return True
    tgt = (it.get("sentiment") or {}).get("tgt") or ""
    return any(n in tgt for n in WATCHLIST_NAMES if n)


def select_prop_alerts(cur, alerted):
    """挑出该发「📡 正在发酵」的条目,返回 (条目列表, 更新后的记账)。
    每个事件簇每档最多提醒一次,降档不提醒;未过门槛不记账(LLM 晚到补上重要度仍可触发)。"""
    out = []
    for it in cur:
        lvl = (it.get("propagation") or {}).get("level") or 0
        g = it.get("dedup_group")
        if not g or lvl <= int(alerted.get(g, 0)):
            continue
        if not _prop_worthy(it):
            continue
        alerted[g] = lvl
        out.append(it)
    if len(alerted) > PROP_ALERTED_CAP:          # 按插入顺序裁掉最老的记账
        for k in list(alerted.keys())[:len(alerted) - PROP_ALERTED_CAP]:
            del alerted[k]
    return out, alerted


def render_prop_alert(it):
    """发酵提醒 = 「📡 正在发酵」头 + 标准推送卡(块间空行,与现有排版一致)。"""
    p = it.get("propagation") or {}
    head = "📡 <b>正在发酵 · LV%d</b>（%d分钟内%d家信源跟进）" % (
        p.get("level") or 0, p.get("window_min") or 90, p.get("sources") or 0)
    return head + "\n\n" + render_push_item(it)
```

- [ ] **Step 4: 跑测试确认通过**

```
python -m unittest tests.test_telegram -v
```

预期:`TestPropAlerts` 全 PASS,原有卡片/发送测试回归不破。

- [ ] **Step 5: Checkpoint** —— `tests.test_telegram` 全绿。

---

### Task 5: bot — push_loop 接线

**Files:**
- Modify: `telegram_bot.py`(`push_loop()` 主循环段)

- [ ] **Step 1: 接线** —— `push_loop()` 中把

```python
            cur = fetch_items({"channel": "finance", "mode": "all", "take": "60"})
            with _LOCK:
                fresh, STATE["seen"], STATE["seen_groups"] = _select_fresh(
                    cur, STATE["seen"], STATE["seen_groups"])
                _save_state()
            if not fresh:
                continue
            # 英伟达优先，其余按时间正序（旧->新）；逐条独立发送（用户要求不合并）
            fresh.sort(key=lambda x: (not is_nvidia(x), x.get("published_at", "")))
            fresh = fresh[:PUSH_MAX]
            for chat in subs:
                for it in fresh:
                    try:
                        send(chat, render_push_item(it))
                        time.sleep(0.5)        # 逐条发，避免触发 Telegram 单聊限流
                    except Exception as e:
                        print("[push] send to %s error: %s" % (chat, e))
            print("[push] pushed %d items to %d subs" % (len(fresh), len(subs)))
```

改为

```python
            cur = fetch_items({"channel": "finance", "mode": "all", "take": "60"})
            with _LOCK:
                fresh, STATE["seen"], STATE["seen_groups"] = _select_fresh(
                    cur, STATE["seen"], STATE["seen_groups"])
                alerts, STATE["prop_alerted"] = select_prop_alerts(
                    cur, STATE.get("prop_alerted", {}))
                _save_state()
            if not fresh and not alerts:
                continue
            # 英伟达优先，其余按时间正序（旧->新）；逐条独立发送（用户要求不合并）
            fresh.sort(key=lambda x: (not is_nvidia(x), x.get("published_at", "")))
            fresh = fresh[:PUSH_MAX]
            for chat in subs:
                for it in alerts:              # 发酵提醒先发(事件升级,优先级最高)
                    try:
                        send(chat, render_prop_alert(it))
                        time.sleep(0.5)
                    except Exception as e:
                        print("[push] prop alert to %s error: %s" % (chat, e))
                for it in fresh:
                    try:
                        send(chat, render_push_item(it))
                        time.sleep(0.5)        # 逐条发，避免触发 Telegram 单聊限流
                    except Exception as e:
                        print("[push] send to %s error: %s" % (chat, e))
            print("[push] pushed %d items + %d prop alerts to %d subs"
                  % (len(fresh), len(alerts), len(subs)))
```

- [ ] **Step 2: 语法与导入自检**

```
python -c "import ast; ast.parse(open('telegram_bot.py', encoding='utf-8').read()); print('syntax ok')"
```

预期:`syntax ok`(push_loop 无单测——依赖长轮询与真实 Telegram,由 Task 6 端到端验证)。

- [ ] **Step 3: 回归全绿**

```
python -m unittest discover -s tests -v
```

预期:全部 PASS(此前 92 个 + 本期新增 ≈ 115 个)。

- [ ] **Step 4: Checkpoint** —— 全绿。

---

### Task 6: 端到端验证(真实环境)

**Files:** 无代码改动;操作 `run.py` 服务与 `data/news.db`。

- [ ] **Step 1: 重启服务**(单实例锁,先停旧进程)

PowerShell:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory "C:\Users\Administrator\Desktop\新闻" -WindowStyle Hidden
```

- [ ] **Step 2: 等首轮抓取完成后确认 mentions 在积累**(约 1-2 分钟)

```
python -c "import sqlite3; con=sqlite3.connect(r'C:\Users\Administrator\Desktop\新闻\data\news.db'); print('mentions:', con.execute('SELECT COUNT(*) FROM mentions').fetchone()[0]); print(con.execute('SELECT group_key, COUNT(DISTINCT source) n FROM mentions GROUP BY group_key ORDER BY n DESC LIMIT 5').fetchall())"
```

预期:计数 > 0;头部簇有多信源记录。

- [ ] **Step 3: 确认 API 带出 propagation 字段**(需有事件达到 3 家信源;刚启动可能为空,等几轮刷新)

```
python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://localhost:8910/api/public/items?channel=finance&mode=all&take=60')); hits=[(i['title_zh'][:30], i.get('propagation')) for i in d['items'] if i.get('propagation')]; print(len(hits), hits[:3])"
```

预期:能跑通;有发酵事件时打印 `{'level':…,'sources':…,'window_min':90}`。

- [ ] **Step 4: Telegram 观察** —— 保持订阅,出现达标事件时应收到「📡 正在发酵 · LVn」卡;同一事件同档不重复。`bot_state.json` 出现 `prop_alerted` 键。

- [ ] **Step 5: Checkpoint** —— 端到端通过,子项目 2 完成。
