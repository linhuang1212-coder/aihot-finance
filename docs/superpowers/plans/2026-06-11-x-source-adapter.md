# X(Twitter)信息源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 twitterapi.io 高级搜索把 16 个精选 X 账号的推文接入现有新闻管线(翻译/打标/情绪/发酵/推送自动生效),OSINT 组一律标未证实。

**Architecture:** 新模块 `xfetch.py`(模式同 llm.py/market.py:无 key 静默跳过)+ 账号配置 `x_watch.json` + 增量水位 `data/x_state.json`。一条 `from:A OR from:B ... since_time:N` 查询扫全名单,每轮最多 3 页。`newsfetch.py` 仅两处小改:`fetch_all` 追加抓取、`_keep` 对 `X·` 前缀源直通(否则英文无实体推文被 newsfetch.py:597 误杀)。

**Tech Stack:** Python 标准库(urllib/json/unittest/mock),零第三方依赖。设计文档:`docs/specs/2026-06-11-X信源接入-design.md`。

**注意:** 项目**不是 git 仓库**,各 Task 以"测试全绿 Checkpoint"代替 commit。测试命令在项目根 `c:\Users\Administrator\Desktop\新闻` 下运行。`bot_config.json` 已存有真实 `twitterapi_key`(已 gitignore)——测试中必须 mock key 与 HTTP,**绝不真实调用**。

---

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `x_watch.json` | Create | 4 组 16 账号白名单(user/label/group) |
| `xfetch.py` | Create | key 读取、账号加载、HTTP 调用、推文→条目映射、增量抓取 |
| `newsfetch.py` | Modify (两处) | `_keep` X 直通;`fetch_all` 接 xfetch |
| `tests/test_xfetch.py` | Create | 解析/标记/水位/护栏/降级测试 |
| `tests/test_newsfetch.py` | Modify | 追加 `_keep` X 直通测试 |

---

### Task 1: x_watch.json + xfetch.py 解析层

**Files:**
- Create: `x_watch.json`
- Create: `xfetch.py`
- Test: `tests/test_xfetch.py`(新建)

- [ ] **Step 1: 写账号配置** `x_watch.json`(照搬,这是数据不是代码,先落盘):

```json
{
  "_note": "X 监控账号。group: finance/osint/mover/kol;osint 组推文一律 verified=unverified。改完重启 server 生效。",
  "accounts": [
    {"user": "DeItaone",        "label": "Walter Bloomberg", "group": "finance"},
    {"user": "ChineseWSJ",      "label": "华尔街日报中文",   "group": "finance"},
    {"user": "zaobaosg",        "label": "联合早报",         "group": "finance"},
    {"user": "KobeissiLetter",  "label": "Kobeissi Letter",  "group": "finance"},
    {"user": "BRICSinfo",       "label": "BRICS News",       "group": "osint"},
    {"user": "clashreport",     "label": "Clash Report",     "group": "osint"},
    {"user": "IranObserver0",   "label": "Iran Observer",    "group": "osint"},
    {"user": "DailyIranNews",   "label": "Daily Iran News",  "group": "osint"},
    {"user": "GlobeEyeNews",    "label": "GlobeEye News",    "group": "osint"},
    {"user": "OSINTWarfare",    "label": "OSINT Warfare",    "group": "osint"},
    {"user": "DI313_",          "label": "DI313",            "group": "osint"},
    {"user": "elonmusk",        "label": "Elon Musk",        "group": "mover"},
    {"user": "realDonaldTrump", "label": "Donald Trump",     "group": "mover"},
    {"user": "dnystedt",        "label": "Dan Nystedt",      "group": "kol"},
    {"user": "dylan522p",       "label": "Dylan Patel",      "group": "kol"},
    {"user": "Jukanlosreve",    "label": "Jukan",            "group": "kol"}
  ]
}
```

- [ ] **Step 2: 写失败测试** —— 新建 `tests/test_xfetch.py`:

```python
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


class TestDisabled(unittest.TestCase):

    def test_no_key_returns_empty_without_http(self):
        with mock.patch.object(xfetch, "_key", return_value=None), \
             mock.patch.object(xfetch, "_call") as m_call:
            self.assertFalse(xfetch.enabled())
            self.assertEqual(xfetch.fetch_x(), [])
            m_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

```
python -m unittest tests.test_xfetch -v
```

预期:ERROR(`No module named 'xfetch'`)。

- [ ] **Step 4: 写实现** —— 新建 `xfetch.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) 信源(twitterapi.io,可选)。
精选账号推文 -> 标准条目,进 newsfetch 管线(翻译/打标/情绪/发酵全自动)。
- key: bot_config.json 的 twitterapi_key 或环境变量 TWITTERAPI_KEY;无 key 静默跳过。
- 账号白名单: x_watch.json(4 组;osint 组一律 verified=unverified)。
- 增量水位: data/x_state.json;抓取失败不推进水位,下轮重试。
设计文档: docs/specs/2026-06-11-X信源接入-design.md
"""

import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCH_FILE = os.path.join(BASE_DIR, "x_watch.json")
STATE_FILE = os.path.join(BASE_DIR, "data", "x_state.json")
API_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
CST = timezone(timedelta(hours=8))
MAX_PAGES = 3                      # 每轮成本护栏(每页约 20 条)
FIRST_RUN_BACK_MIN = 30            # 首跑只回看 30 分钟,避免历史灌爆
OVERLAP_SEC = 60                   # 水位重叠 1 分钟防边界丢推(id 去重兜底)

_watch = None


def _key():
    k = os.environ.get("TWITTERAPI_KEY")
    if k:
        return k
    try:
        with open(os.path.join(BASE_DIR, "bot_config.json"), encoding="utf-8") as f:
            return json.load(f).get("twitterapi_key")
    except Exception:
        return None


def enabled():
    return bool(_key())


def _accounts():
    global _watch
    if _watch is None:
        try:
            with open(WATCH_FILE, encoding="utf-8") as f:
                _watch = json.load(f).get("accounts", [])
        except Exception:
            _watch = []
    return _watch


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(s):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception as e:
        print("[xfetch] state save error:", e)


def _call(query, cursor=""):
    params = urllib.parse.urlencode(
        {"query": query, "queryType": "Latest", "cursor": cursor})
    req = urllib.request.Request(API_URL + "?" + params,
                                 headers={"X-API-Key": _key()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _parse_time(s):
    try:  # createdAt 形如 "Tue Dec 10 07:00:30 +0000 2024"
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(CST)
    except Exception:
        return datetime.now(CST)


def _to_item(tw, acct):
    import newsfetch                # 懒导入复用翻译/哈希(同 store->newsfetch 先例)
    text = (tw.get("text") or "").strip()
    zh = newsfetch.translate(text[:300])
    user = (tw.get("author") or {}).get("userName") or acct.get("user", "")
    dt = _parse_time(tw.get("createdAt") or "")
    return {
        "id": newsfetch._hash_id("x:" + str(tw.get("id"))),
        "published_at": dt.isoformat(timespec="seconds"),
        "title_zh": zh[:120], "summary_zh": zh[:120],
        "body_excerpt": text[:500], "title_orig": text[:300],
        "source": "X·" + (acct.get("label") or user),
        "source_url": "https://x.com/%s/status/%s" % (user, tw.get("id")),
        "lang": "en", "channels": ["finance"], "categories": [], "tags": [],
        "entities": [], "markets": [], "heat": "", "read_count": None,
        "verified": "unverified" if acct.get("group") == "osint" else "confirmed",
        "dedup_group": newsfetch._hash_id(newsfetch._norm_title(text)),
        "score": 0.0,
    }


def fetch_x():
    """抓白名单账号新推文(增量)。无 key/无账号 -> [];失败 -> [] 且水位不推进。"""
    if not enabled():
        return []
    accts = _accounts()
    if not accts:
        return []
    by_user = {a["user"].lower(): a for a in accts if a.get("user")}
    now_ts = int(datetime.now(CST).timestamp())
    state = _load_state()
    since = int(state.get("last_since_time") or (now_ts - FIRST_RUN_BACK_MIN * 60))
    query = "(%s) -filter:retweets since_time:%d" % (
        " OR ".join("from:" + a["user"] for a in accts if a.get("user")), since)
    items, cursor = [], ""
    try:
        for _ in range(MAX_PAGES):
            d = _call(query, cursor)
            for tw in d.get("tweets") or []:
                user = ((tw.get("author") or {}).get("userName") or "").lower()
                acct = by_user.get(user)
                if acct and tw.get("id"):
                    items.append(_to_item(tw, acct))
            if not d.get("has_next_page") or not d.get("next_cursor"):
                break
            cursor = d["next_cursor"]
    except Exception as e:
        print("[xfetch] fetch error:", e)
        return []                       # 失败不推进水位,下轮重试
    state["last_since_time"] = max(since, now_ts - OVERLAP_SEC)
    _save_state(state)
    if items:
        print("[xfetch] %d new tweets" % len(items))
    return items
```

- [ ] **Step 5: 跑测试确认通过**

```
python -m unittest tests.test_xfetch -v
```

预期:5 个测试全 PASS(TestParse 4 + TestDisabled 1)。

- [ ] **Step 6: Checkpoint** —— 全绿,且全套回归 `python -m unittest discover -s tests`(115 + 5 = 120)。

---

### Task 2: 水位推进与分页护栏

**Files:**
- Modify: `tests/test_xfetch.py`(追加测试类)
- `xfetch.py` 已在 Task 1 实现该逻辑——本 Task 用测试钉死行为,若有偏差修 `xfetch.py`

- [ ] **Step 1: 追加失败/验证测试** 到 `tests/test_xfetch.py`(`TestDisabled` 类之前):

```python
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
```

- [ ] **Step 2: 跑测试**

```
python -m unittest tests.test_xfetch -v
```

预期:若 Task 1 实现无偏差则全 PASS(10 个);有 FAIL 则按测试语义修 `xfetch.py`(行为以本测试为准)。

- [ ] **Step 3: Checkpoint** —— `tests.test_xfetch` 全绿。

---

### Task 3: newsfetch 集成(_keep 直通 + fetch_all 接线)

**Files:**
- Modify: `newsfetch.py`(`_keep` 函数 + `fetch_all` 抓取段)
- Modify: `tests/test_newsfetch.py`(追加测试类)

- [ ] **Step 1: 写失败测试** —— `tests/test_newsfetch.py` 末尾(`if __name__` 之前,若无该块则文件末尾)追加:

```python
class TestKeepXWhitelist(unittest.TestCase):
    """X 白名单源直通降噪;普通英文无实体条目仍被丢(回归)。"""

    def _en_item(self, source):
        return {"source": source, "lang": "en", "entities": [],
                "title_zh": "random text", "summary_zh": "", "body_excerpt": "",
                "title_orig": "some random non-finance text", "categories": [],
                "markets": [], "tags": []}

    def test_x_source_passes_keep(self):
        import newsfetch
        self.assertTrue(newsfetch._keep(self._en_item("X·Walter Bloomberg")))

    def test_plain_english_no_entity_still_dropped(self):
        import newsfetch
        self.assertFalse(newsfetch._keep(self._en_item("Some Random Blog")))
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m unittest tests.test_newsfetch.TestKeepXWhitelist -v
```

预期:`test_x_source_passes_keep` FAIL(当前英文无实体被丢)。

- [ ] **Step 3: 实现** —— `newsfetch.py` 两处:

(1) `_keep()` 中 `if src in BLOCKED_SOURCES:` 块之后、`if _source_tier(src) == 3:` 之前插入:

```python
    if src.startswith("X·"):          # X 白名单 = 人工精选,直通(spec 2026-06-11)
        return True
```

(2) `fetch_all()` 中 RSS 循环之后、`_save_trans()` 之前(xfetch 翻译要赶在缓存落盘前)插入:

```python
    try:                              # X 信源(可选,无 key 自动跳过)
        import xfetch
        items.extend(xfetch.fetch_x())
    except Exception as e:
        print("[xfetch] skipped:", e)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

```
python -m unittest tests.test_newsfetch -v
python -m unittest discover -s tests
```

预期:全部 PASS(125 + 2 = 127)。

- [ ] **Step 5: Checkpoint** —— 全绿。

---

### Task 4: 端到端验证(真实调用,花真钱,小心控制)

**Files:** 无代码改动;操作 run.py 服务。

- [ ] **Step 1: 先单独干跑一次 xfetch**(只此一次真实调用,验证 key/查询/解析):

```
python -c "import xfetch; items = xfetch.fetch_x(); print(len(items), 'tweets'); [print(' ', i['source'], '|', i['verified'], '|', i['title_zh'][:40]) for i in items[:5]]"
```

预期:不报错;返回 0~60 条(取决于近 30 分钟白名单发推量);OSINT 条目 verified=unverified。
若 401/403 → 检查 key;若 0 条且无报错 → 正常(近 30 分钟无新推)。

- [ ] **Step 2: 重启服务**

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory "C:\Users\Administrator\Desktop\新闻" -WindowStyle Hidden
```

- [ ] **Step 3: 等首轮抓取后验证 X 条目入库**(约 2 分钟)

```
python -c "import sqlite3; con=sqlite3.connect(r'C:\Users\Administrator\Desktop\新闻\data\news.db'); print(con.execute('SELECT COUNT(*) FROM items WHERE source LIKE \"X·%\"').fetchone()); print(con.execute('SELECT source, title_zh FROM items WHERE source LIKE \"X·%\" ORDER BY published_at DESC LIMIT 5').fetchall())"
```

预期:计数 ≥0 且随时间增长;server.log 出现 `[xfetch] N new tweets`。

- [ ] **Step 4: 验证 API 与推送链路** —— `/api/public/items?channel=finance&q=X·` 能查到;OSINT 推文带 ⚠️;命中自选(如 dnystedt 提美光)走优先推送。

- [ ] **Step 5: 成本观察** —— twitterapi.io 控制台看当日消耗,应在 $0.2/天 以内;异常则查 server.log 调用频率。

- [ ] **Step 6: Checkpoint** —— 端到端通过,子项目 3 完成。
