# Serenity 深度分析层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 AIHOT 金融板块加一个按需的「Serenity 深度分析层」——对单个标的产出产业链/机构/风险视角的中文分析，经 Telegram `/serenity` 与 `/api/analysis` 对外。

**Architecture:** 新增 `analysis_pack.md`（提示词）+ `serenity.py`（分析逻辑：取本地条目 → Google News RSS 补充 → DeepSeek，按 `(标的,当天)` 缓存）；`server.py` 加 `/api/analysis` 端点调它；`telegram_bot.py` 加 `/serenity` 命令调端点。按需触发，非自动管线。

**Tech Stack:** 纯 Python 标准库 + 现有 DeepSeek key（复用 `llm.py`）。测试用 stdlib `unittest`（不引入 pytest，保持零依赖）。

参考：`docs/superpowers/specs/2026-06-08-serenity-analysis-layer-design.md`

**Note（git）:** 本项目当前不在 git 仓库下。每个 Task 末尾的「Checkpoint」= 跑测试全绿。如需版本管理，先在项目根 `git init`，再把每个 Checkpoint 当作一次 commit。

**Note（运行环境）:** 解释器用 `python`（已在 PATH）。所有命令在项目根 `c:\Users\Administrator\Desktop\新闻` 下执行。改了 `server.py` 需重启守护（`python run.py` 已带单实例锁，先停旧进程再起）。

---

## 数据契约（全计划共用，先定死）

### `serenity.analyze()` 返回结构
```
{
  "status":   "ok" | "no_llm" | "no_data" | "error",
  "entity":   str,
  "analysis_md": str,        # status=ok 时有
  "message":  str,           # status!=ok 时有（友好提示）
  "sources":  [{ "title": str, "url": str, "source": str }],
  "based_on": [str],         # 用到的本地条目 id
  "generated_at": str,       # ISO8601 +08:00
  "cached":   bool
}
```

### 关键常量（定义在 `serenity.py`）
```
DISCLAIMER = "本分析为框架推理，非投资建议；不预测点位。来源真实 ≠ 内容已证实。"
CONTEXT_ITEMS = 12
MODEL = "deepseek-chat"
API_URL = "https://api.deepseek.com/chat/completions"
```
`DISCLAIMER` 的字符串必须与 `analysis_pack.md` 结尾免责行**逐字一致**（analyze 会检查是否已含，避免重复追加）。

---

## File Structure

| 文件 | 职责 | 新建/修改 |
|---|---|---|
| `analysis_pack.md` | Serenity 框架系统提示词（改它即调教，无需动代码） | 新建 |
| `serenity.py` | `build_context` / `fetch_supplement` / `analyze` + 缓存 | 新建 |
| `server.py` | `do_GET` 加 `/api/analysis` 路由 | 修改 |
| `telegram_bot.py` | 加 `/serenity`(`/deep`) 命令 + HELP | 修改 |
| `tests/__init__.py` | 让 tests 成为包 | 新建 |
| `tests/test_serenity.py` | serenity 单元测试 | 新建 |
| `tests/test_api_analysis.py` | 端点契约测试 | 新建 |

---

## Task 0: 测试脚手架 + 基线

**Files:**
- Create: `tests/__init__.py`（空文件）
- Create: `tests/_fixtures.py`

- [ ] **Step 1: 建空包文件** `tests/__init__.py`（内容为空）。

- [ ] **Step 2: 写共享夹具** `tests/_fixtures.py`：

```python
# -*- coding: utf-8 -*-
"""测试共享样本条目。"""

SAMPLE_ITEMS = [
    {
        "id": "a1", "title_zh": "重磅！英伟达联手SK海力士",
        "summary_zh": "英伟达联手SK海力士，利好HBM与AI芯片产业链",
        "body_excerpt": "英伟达与SK海力士签署下一代AI内存多年合作",
        "source": "电子工程专辑", "source_url": "http://x/1",
        "published_at": "2026-06-08T15:04:00+08:00", "score": 9.0,
        "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}],
    },
    {
        "id": "b2", "title_zh": "某基金增持英伟达3908股",
        "summary_zh": "机构小幅增持英伟达，对股价影响有限",
        "body_excerpt": "", "source": "MarketBeat", "source_url": "http://x/2",
        "published_at": "2026-06-08T15:36:00+08:00", "score": 3.0,
        "entities": [{"name": "英伟达", "code": "NVDA", "market": "us"}],
    },
    {
        "id": "c3", "title_zh": "白银上涨", "summary_zh": "贵金属走强",
        "body_excerpt": "", "source": "新浪财经", "source_url": "http://x/3",
        "published_at": "2026-06-08T14:00:00+08:00", "score": 5.0,
        "entities": [],
    },
]

SAMPLE_RSS = (
    b"<?xml version='1.0'?><rss><channel>"
    b"<item><title>\xe8\x8b\xb1\xe4\xbc\x9f\xe8\xbe\xbe\xe8\x81\x94\xe6\x89\x8bSK - "
    b"\xe7\x94\xb5\xe5\xad\x90\xe5\xb7\xa5\xe7\xa8\x8b\xe4\xb8\x93\xe8\xbe\x91</title>"
    b"<link>http://g/1</link></item>"
    b"<item><title>NV memory deal - Reuters</title><link>http://g/2</link></item>"
    b"</channel></rss>"
)
```

- [ ] **Step 3: 跑空发现确认 unittest 可用**

Run: `python -m unittest discover -s tests -v`
Expected: `Ran 0 tests` 且 OK（无报错，证明发现机制正常）。

- [ ] **Step 4: Checkpoint** — 上面命令无报错。

---

## Task 1: `analysis_pack.md`（提示词）

**Files:**
- Create: `analysis_pack.md`

- [ ] **Step 1: 写文件** `analysis_pack.md`（照搬以下内容，结尾免责行须与 `serenity.DISCLAIMER` 逐字一致）：

```markdown
# Serenity 深度分析器 · 指令包（analysis pack）
# 给 DeepSeek 深度分析器的"领域规范"。改这个文件即可调教质量，无需动代码；改完重启 server 生效。

你是一名以**产业链分析 + 机构资金视角**见长的买方研究者。你不做短线、不做技术分析、不预测点位。
任务：针对给定标的，结合提供的新闻，输出**专业、客观、上游导向、风险清醒**的中文分析。

## 思维框架（4 个镜片）
1. 卡脖子/上游优先：真正的价值常在产业链上游"卡脖子/瓶颈"环节，而非终端品牌。先问：这件事里，谁是被抢的稀缺环节？
2. 机构资金行为（信号 vs 噪音）：
   - 真信号：下游龙头公开锁定上游、产能售罄/排产到数年后、龙头高管对供需的明确表态、产业链订单验证。
   - 噪音：零散小基金"增持 N 股"这类标题、蹭概念的涨停板、缺乏产业逻辑的情绪。
3. 长线价值：看 3–5 年产业趋势与范式转移，而非 3–5 天股价波动。
4. 估值重置（re-rating）：识别可能被市场"重新评级"的环节，通常是定价权上移的上游瓶颈。

## 决策纪律
- 上游优先、瓶颈识别、机构验证、稀缺性定价。
- 风险面：技术颠覆、多源（multi-source）稀释供应商定价权、估值下行/周期反转、地缘。
- 拿不准就少说，别编数据；没有的硬数据（如具体机构持仓份额）就明说"手上无此数据"。

## 输出结构（固定五段，中文为主、专业英文术语点缀）
**核心判断**：一句话点出该看什么（常常不是这只票本身，而是它牵动的上游）。
**卡脖子 / 上游**：这件事的稀缺/瓶颈环节在哪、为什么有定价权。
**信号 vs 噪音**：把提供的新闻分成"真信号"和"噪音/froth"，并说明理由。
**风险**：至少 2 条，必须包含多源/周期/估值之一。
**底线**：长线视角的取舍判断（不喊点位、不喊买卖）。

## 硬性纪律
- 地缘/传闻类信息标「未证实」。
- 不输出投资建议、不预测价格点位、不喊买卖时点。
- 结尾必须保留一行免责：本分析为框架推理，非投资建议；不预测点位。来源真实 ≠ 内容已证实。
```

- [ ] **Step 2: 健全性检查**

Run: `python -c "open('analysis_pack.md',encoding='utf-8').read().index('信号 vs 噪音'); print('pack ok')"`
Expected: 打印 `pack ok`（文件存在且含关键段落）。

- [ ] **Step 3: Checkpoint** — 文件就位、检查通过。

---

## Task 2: `serenity.build_context`

**Files:**
- Create: `serenity.py`
- Test: `tests/test_serenity.py`

- [ ] **Step 1: 写失败测试** `tests/test_serenity.py`：

```python
# -*- coding: utf-8 -*-
import unittest
from tests._fixtures import SAMPLE_ITEMS


class TestBuildContext(unittest.TestCase):
    def test_selects_entity_items_sorted_by_score(self):
        import serenity
        ctx = serenity.build_context("英伟达", SAMPLE_ITEMS)
        # 只选命中英伟达的条目（a1,b2），不含白银(c3)
        self.assertEqual(ctx["based_on"], ["a1", "b2"])
        # 高分在前
        self.assertTrue(ctx["lines"].index("英伟达联手SK海力士")
                        < ctx["lines"].index("某基金增持英伟达3908股"))
        # sources 与 based_on 对齐
        self.assertEqual(len(ctx["sources"]), 2)
        self.assertEqual(ctx["sources"][0]["url"], "http://x/1")
        self.assertEqual(ctx["count"], 2)

    def test_no_match_returns_empty(self):
        import serenity
        ctx = serenity.build_context("茅台", SAMPLE_ITEMS)
        self.assertEqual(ctx["count"], 0)
        self.assertEqual(ctx["based_on"], [])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_serenity -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'serenity'`）。

- [ ] **Step 3: 写最小实现** `serenity.py`（先只到 build_context；后续 Task 往同文件追加）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serenity 深度分析层（按需）。对单个标的产出产业链/机构/风险视角的中文分析。
数据：传入的本地条目 + Google News RSS 补充头条（零依赖）。
DeepSeek key 复用 llm.py。缓存按 (标的,当天) 存 data/analysis_cache.json。
没 key / 无数据 / 调用失败 时返回带 status 的友好结果，不抛异常。
"""

import os
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import llm  # 复用 _key / enabled

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACK_FILE = os.path.join(BASE_DIR, "analysis_pack.md")
CACHE_FILE = os.path.join(BASE_DIR, "data", "analysis_cache.json")
CST = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_TOKENS = int(os.environ.get("ANALYSIS_MAX_TOKENS", "1200"))
CONTEXT_ITEMS = 12
DISCLAIMER = "本分析为框架推理，非投资建议；不预测点位。来源真实 ≠ 内容已证实。"


def _entity_match(it, entity):
    ent = entity.strip().lower()
    if not ent:
        return False
    for e in it.get("entities", []):
        if ent in (e.get("name") or "").lower() or ent == (e.get("code") or "").lower():
            return True
    hay = " ".join([it.get("title_zh", ""), it.get("summary_zh", ""),
                    it.get("body_excerpt", "")]).lower()
    return ent in hay


def build_context(entity, items):
    """挑该标的最相关的 N 条（实体命中 + 按 score、时间倒序），返回上下文。"""
    rel = [it for it in items if _entity_match(it, entity)]
    rel.sort(key=lambda x: ((x.get("score") or 0), x.get("published_at") or ""),
             reverse=True)
    rel = rel[:CONTEXT_ITEMS]
    lines, based_on, sources = [], [], []
    for i, it in enumerate(rel, 1):
        t = it.get("title_zh") or ""
        s = it.get("summary_zh") or ""
        src = it.get("source") or ""
        when = (it.get("published_at") or "")[:16]
        lines.append("%d. [%s|%s] %s —— %s" % (i, when, src, t, s))
        based_on.append(it.get("id"))
        sources.append({"title": t, "url": it.get("source_url") or "", "source": src})
    return {"lines": "\n".join(lines), "based_on": based_on,
            "sources": sources, "count": len(rel)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_serenity -v`
Expected: PASS（2 个测试）。

- [ ] **Step 5: Checkpoint** — 测试全绿。

---

## Task 3: `serenity.fetch_supplement`

**Files:**
- Modify: `serenity.py`（追加 `_get_bytes` + `fetch_supplement`）
- Test: `tests/test_serenity.py`（追加用例）

- [ ] **Step 1: 追加失败测试** 到 `tests/test_serenity.py`：

```python
from unittest import mock
from tests._fixtures import SAMPLE_RSS


class TestFetchSupplement(unittest.TestCase):
    def test_parses_rss_titles_and_strips_source(self):
        import serenity
        with mock.patch("serenity._get_bytes", return_value=SAMPLE_RSS):
            out = serenity.fetch_supplement("英伟达", limit=5)
        self.assertEqual(out[0]["title"], "英伟达联手SK")
        self.assertEqual(out[0]["source"], "电子工程专辑")
        self.assertEqual(out[0]["url"], "http://g/1")
        self.assertEqual(len(out), 2)

    def test_network_failure_returns_empty(self):
        import serenity
        with mock.patch("serenity._get_bytes", side_effect=Exception("no vpn")):
            self.assertEqual(serenity.fetch_supplement("英伟达"), [])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_serenity.TestFetchSupplement -v`
Expected: FAIL（`AttributeError: module 'serenity' has no attribute 'fetch_supplement'`）。

- [ ] **Step 3: 追加实现** 到 `serenity.py`：

```python
def _get_bytes(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_supplement(entity, limit=6):
    """用 Google News RSS 拉该标的最新头条做补充背景。失败返回 []，不抛。"""
    q = urllib.parse.quote(entity)
    url = ("https://news.google.com/rss/search?q=%s%%20when:3d"
           "&hl=zh-CN&gl=CN&ceid=CN:zh" % q)
    out = []
    try:
        root = ET.fromstring(_get_bytes(url))
    except Exception:
        return []
    for n in root.iterfind(".//item"):
        title = (n.findtext("title") or "").strip()
        link = (n.findtext("link") or "").strip()
        if not title:
            continue
        src = "Google News"
        if " - " in title:
            title, src = title.rsplit(" - ", 1)
        out.append({"title": title.strip(), "source": src.strip(), "url": link})
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_serenity -v`
Expected: PASS（4 个测试）。

- [ ] **Step 5: Checkpoint** — 测试全绿。

---

## Task 4: `serenity.analyze`（缓存 + status 分支 + 免责）

**Files:**
- Modify: `serenity.py`（追加 `_pack` / `_now_iso` / `_today` / `_load_cache` / `_save_cache` / `_deepseek` / `analyze`）
- Test: `tests/test_serenity.py`（追加用例）

- [ ] **Step 1: 追加失败测试** 到 `tests/test_serenity.py`：

```python
import os
import tempfile
from tests._fixtures import SAMPLE_ITEMS as _ITEMS


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        import serenity
        self.serenity = serenity
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)  # 让缓存初始为"不存在"
        self._orig_cache = serenity.CACHE_FILE
        serenity.CACHE_FILE = self.tmp.name

    def tearDown(self):
        self.serenity.CACHE_FILE = self._orig_cache
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_ok_has_disclaimer_and_caches(self):
        s = self.serenity
        calls = {"n": 0}

        def fake_deepseek(system, user):
            calls["n"] += 1
            return "## 核心判断\n看上游 HBM，而非英伟达本身。"

        with mock.patch.object(s, "_deepseek", side_effect=fake_deepseek), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]):
            r1 = s.analyze("英伟达", _ITEMS)
            r2 = s.analyze("英伟达", _ITEMS)  # 第二次应命中缓存

        self.assertEqual(r1["status"], "ok")
        self.assertIn(s.DISCLAIMER, r1["analysis_md"])
        self.assertEqual(r1["based_on"], ["a1", "b2"])
        self.assertFalse(r1["cached"])
        self.assertTrue(r2["cached"])
        self.assertEqual(calls["n"], 1)  # 命中缓存，DeepSeek 只调一次

    def test_no_llm_when_key_missing(self):
        s = self.serenity
        with mock.patch.object(s.llm, "enabled", return_value=False):
            r = s.analyze("英伟达", _ITEMS)
        self.assertEqual(r["status"], "no_llm")
        self.assertIn("message", r)

    def test_no_data_when_no_items(self):
        s = self.serenity
        with mock.patch.object(s.llm, "enabled", return_value=True):
            r = s.analyze("茅台", _ITEMS)
        self.assertEqual(r["status"], "no_data")

    def test_error_when_deepseek_raises(self):
        s = self.serenity
        with mock.patch.object(s, "_deepseek", side_effect=Exception("timeout")), \
             mock.patch.object(s.llm, "enabled", return_value=True), \
             mock.patch.object(s, "fetch_supplement", return_value=[]):
            r = s.analyze("英伟达", _ITEMS)
        self.assertEqual(r["status"], "error")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_serenity.TestAnalyze -v`
Expected: FAIL（`AttributeError: module 'serenity' has no attribute '_deepseek'` / `analyze`）。

- [ ] **Step 3: 追加实现** 到 `serenity.py`：

```python
_pack_text = None


def _pack():
    global _pack_text
    if _pack_text is None:
        try:
            with open(PACK_FILE, encoding="utf-8") as f:
                _pack_text = f.read()
        except Exception:
            _pack_text = ("你是产业链分析视角的买方研究者。按"
                          "核心判断/卡脖子/信号vs噪音/风险/底线 五段输出中文分析。"
                          "结尾保留一行：" + DISCLAIMER)
    return _pack_text


def _now_iso():
    return datetime.now(CST).isoformat(timespec="seconds")


def _today():
    return datetime.now(CST).date().isoformat()


def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print("[serenity] cache save error:", e)


def _deepseek(system, user):
    body = json.dumps({
        "model": MODEL, "temperature": 0.3, "max_tokens": MAX_TOKENS,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": "Bearer " + llm._key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def _err(entity, status, message, sources=None, based_on=None):
    return {"status": status, "entity": entity, "message": message,
            "sources": sources or [], "based_on": based_on or [],
            "generated_at": _now_iso(), "cached": False}


def analyze(entity, items):
    """对单个标的产出深度分析。任何失败都返回带 status 的结果，不抛异常。"""
    entity = (entity or "").strip()
    if not entity:
        return _err(entity, "no_data", "用法：/serenity 英伟达")
    if not llm.enabled():
        return _err(entity, "no_llm", "未配置分析能力（DeepSeek key）。")
    ctx = build_context(entity, items)
    if ctx["count"] == 0:
        return _err(entity, "no_data", "暂无足够信息分析「%s」。" % entity)

    cache = _load_cache()
    ckey = entity.lower() + "@" + _today()
    if ckey in cache:
        return {**cache[ckey], "cached": True}

    supp = fetch_supplement(entity)
    supp_text = "\n".join("- %s（%s）" % (s["title"], s["source"]) for s in supp) or "（无补充）"
    user = ("标的：%s\n\n【本地新闻条目】\n%s\n\n"
            "【最新外部头条（仅供补充背景，可能未在本地库）】\n%s\n\n"
            "请按系统提示的五段结构输出中文分析。" % (entity, ctx["lines"], supp_text))
    try:
        md = _deepseek(_pack(), user).strip()
    except Exception:
        return _err(entity, "error", "分析失败（DeepSeek 调用异常），稍后再试。",
                    ctx["sources"], ctx["based_on"])

    if DISCLAIMER not in md:
        md = md + "\n\n———\n" + DISCLAIMER
    result = {
        "status": "ok", "entity": entity, "analysis_md": md,
        "sources": ctx["sources"] + supp, "based_on": ctx["based_on"],
        "generated_at": _now_iso(), "cached": False,
    }
    cache[ckey] = {k: result[k] for k in
                   ("status", "entity", "analysis_md", "sources", "based_on", "generated_at")}
    _save_cache(cache)
    return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_serenity -v`
Expected: PASS（8 个测试）。

- [ ] **Step 5: Checkpoint** — 测试全绿。

---

## Task 5: server `/api/analysis` 端点

**Files:**
- Modify: `server.py`（`do_GET` 里、`/api/public/categories` 分支之后加路由）
- Test: `tests/test_api_analysis.py`

- [ ] **Step 1: 写失败契约测试** `tests/test_api_analysis.py`：

```python
# -*- coding: utf-8 -*-
import json
import threading
import unittest
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
        for p in (self.p_enabled, self.p_deep, self.p_supp, self.p_cache, self.p_save):
            p.stop()

    def _get(self, qs):
        url = "http://127.0.0.1:%d/api/analysis?%s" % (self.port, qs)
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_api_analysis -v`
Expected: FAIL（端点返回 404 → JSON 无 `status` 键 → KeyError/断言失败）。

- [ ] **Step 3: 加路由** 到 `server.py` 的 `do_GET`，放在 `if path == "/api/public/categories":` 整段**之后**、`if path == "/aihot-finance/SKILL.md":` **之前**：

```python
        if path == "/api/analysis":
            entity = qs.get("entity", [None])[0]
            try:
                import serenity
                result = serenity.analyze(entity or "", ITEMS)
            except Exception as e:
                result = {"status": "error", "entity": entity or "",
                          "message": "分析服务异常：%s" % e,
                          "sources": [], "based_on": [], "generated_at": "",
                          "cached": False}
            return self._send_json(result)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_api_analysis -v`
Expected: PASS（2 个测试）。

- [ ] **Step 5: 回归——其余测试仍绿**

Run: `python -m unittest discover -s tests -v`
Expected: PASS（全部，共 10 个）。

- [ ] **Step 6: Checkpoint** — 全绿。

---

## Task 6: Telegram `/serenity` 命令

**Files:**
- Modify: `telegram_bot.py`（加 `fetch_analysis` + `render_analysis` + 命令分支 + HELP）
- Test: `tests/test_serenity.py`（追加 `render_analysis` 单元测试）

> bot 主循环依赖网络（Telegram），不做集成测试；只单测纯函数 `render_analysis`，命令接线用 Task 7 手动冒烟覆盖。

- [ ] **Step 1: 追加失败测试** 到 `tests/test_serenity.py`：

```python
class TestRenderAnalysis(unittest.TestCase):
    def test_ok_renders_title_and_body(self):
        import telegram_bot
        r = {"status": "ok", "entity": "英伟达",
             "analysis_md": "## 核心判断\n看上游。", "sources": [], "based_on": []}
        out = telegram_bot.render_analysis(r)
        self.assertIn("英伟达", out)
        self.assertIn("看上游", out)

    def test_non_ok_renders_message(self):
        import telegram_bot
        r = {"status": "no_llm", "entity": "英伟达", "message": "未配置分析能力。"}
        out = telegram_bot.render_analysis(r)
        self.assertIn("未配置分析能力", out)
```

> 注：`telegram_bot` 模块顶层会调 `_load_conf()` 读 `bot_config.json`。测试机已有该文件，import 可成功。若在无 config 的 CI 上跑，需先设环境变量 `TELEGRAM_BOT_TOKEN=test`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_serenity.TestRenderAnalysis -v`
Expected: FAIL（`AttributeError: ... 'render_analysis'`）。

- [ ] **Step 3: 实现** —— 在 `telegram_bot.py` 加渲染与取数函数（放在 `render_quotes` 之后）：

```python
def fetch_analysis(entity):
    url = SERVER + "/api/analysis?" + urllib.parse.urlencode(
        {"channel": "finance", "entity": entity})
    return http_get_json(url, timeout=95)


def render_analysis(r):
    if r.get("status") != "ok":
        return esc(r.get("message") or "分析暂不可用。")
    head = "<b>🧠 Serenity 视角 · %s</b>" % esc(r.get("entity", ""))
    body = esc(r.get("analysis_md", ""))
    return head + "\n\n" + body
```

> `render_analysis` 用 `esc()` 转义后整体作为文本发送（不解析 markdown 标记，避免 DeepSeek 输出里的 `<>` 破坏 HTML）。`fetch_analysis` 超时设 95s，略大于服务端 90s。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_serenity.TestRenderAnalysis -v`
Expected: PASS。

- [ ] **Step 5: 接线命令** —— 在 `handle()` 的命令链里，`elif cmd in ("nvidia", "nvda"):` 分支**之前**加：

```python
        elif cmd in ("serenity", "deep"):
            if not arg:
                send(chat_id, "用法：/serenity 英伟达")
            else:
                send(chat_id, "🔍 正在做深度分析（约半分钟）…")
                send(chat_id, render_analysis(fetch_analysis(arg)))
```

- [ ] **Step 6: 加进 HELP** —— 在 `HELP` 字符串里 `/nvidia 英伟达（报价+新闻）\n` 之后加一行：

```python
    "/serenity 标的 深度分析（产业链/机构视角）\n"
```

- [ ] **Step 7: 回归全绿**

Run: `python -m unittest discover -s tests -v`
Expected: PASS（全部，共 12 个）。

- [ ] **Step 8: Checkpoint** — 全绿。

---

## Task 7: 端到端手动冒烟

**Files:** 无（仅验证）

- [ ] **Step 1: 重启服务**（先停旧守护，再起；单实例锁会拒绝重复启动）

PowerShell:
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'run\.py|server\.py|telegram_bot\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process python -ArgumentList 'run.py' -WorkingDirectory 'c:\Users\Administrator\Desktop\新闻' -WindowStyle Hidden
```

- [ ] **Step 2: 等服务起来后打端点**

```bash
curl -s "http://localhost:8910/api/analysis?channel=finance&entity=英伟达" | python -c "import sys,json;d=json.load(sys.stdin);print('status=',d['status']);print(d.get('analysis_md','')[:400])"
```
Expected: `status= ok`，打印出五段结构的中文分析、结尾含免责。

- [ ] **Step 3: Telegram 验证** —— 给 bot 发 `/serenity 英伟达`，应先收到"🔍 正在做深度分析…"，约半分钟后收到分析；发 `/serenity`（无参）应收到用法提示。

- [ ] **Step 4: 缓存验证** —— 再打一次 Step 2 的 curl，应**秒回**（命中当天缓存）。`data/analysis_cache.json` 应出现 `英伟达@<日期>` 键。

- [ ] **Step 5: Checkpoint** — 端到端通过。

---

## Self-Review（作者已过一遍）

- **Spec 覆盖**：数据源=本地条目(Task2)+RSS补充(Task3)；按需端点(Task5)；Telegram(Task6)；analysis_pack 重写(Task1)；缓存/预算/兜底(Task4)；输出五段+免责(Task1/4)；测试策略(Task2–6)；验收 1–7 各有对应 Task；明确不做（前端/真联网深研/自动管线）未排任务——符合 YAGNI。
- **回归保护**：Task5 Step5 / Task6 Step7 跑全量；端点为新增路由，不改既有分支。
- **类型一致**：`analyze()` 返回结构在 Task4 定义，Task5/6 端点与渲染按同一份键（status/analysis_md/message/sources/based_on）消费；`build_context` 返回 lines/based_on/sources/count 全程一致；`DISCLAIMER` 常量与 `analysis_pack.md` 结尾免责行逐字一致（Task1/4 已对齐）。
- **占位符扫描**：无 TBD/TODO；每个代码步骤都给出完整可粘贴的代码。
```
