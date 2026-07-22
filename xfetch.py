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
MAX_PAGES = 3                      # 每片成本护栏(每页约 20 条)
FIRST_RUN_BACK_MIN = 30            # 首跑只回看 30 分钟,避免历史灌爆
OVERLAP_SEC = 60                   # 水位重叠 1 分钟防边界丢推(id 去重兜底)
QUERY_CHAR_BUDGET = 450            # X 高级搜索 query 上限 512 字符,超长会被静默
                                   # 返回空结果(2026-06-12 事故);留余量给后缀

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
        "conversation_id": tw.get("conversationId"),     # 留作未来真线程合并
        "dedup_group": newsfetch._hash_id(newsfetch._norm_title(text)),
        "score": 0.0,
    }


def _account_chunks(accts):
    """按字符预算把账号表切片,保证每片拼出的查询不超 X 的 512 字符上限。"""
    chunks, cur, cur_len = [], [], 0
    for a in accts:
        u = a.get("user")
        if not u:
            continue
        seg = len("from:") + len(u) + len(" OR ")
        if cur and cur_len + seg > QUERY_CHAR_BUDGET:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(a)
        cur_len += seg
    if cur:
        chunks.append(cur)
    return chunks


def fetch_x():
    """抓白名单账号新推文(增量,账号多时分片查询)。无 key/无账号 -> [];
    任一片失败 -> [] 且水位不推进,下轮整轮重试(id 去重兜底,不会重复入库)。"""
    if not enabled():
        return []
    accts = _accounts()
    if not accts:
        return []
    by_user = {a["user"].lower(): a for a in accts if a.get("user")}
    now_ts = int(datetime.now(CST).timestamp())
    state = _load_state()
    since = int(state.get("last_since_time") or (now_ts - FIRST_RUN_BACK_MIN * 60))
    items = []
    try:
        for chunk in _account_chunks(accts):
            query = "(%s) -filter:retweets since_time:%d" % (
                " OR ".join("from:" + a["user"] for a in chunk), since)
            cursor = ""
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
