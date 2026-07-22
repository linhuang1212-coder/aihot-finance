#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 智能增强层（可选）。
对去重后的新闻批量调用 DeepSeek，产出：一句话中文摘要 / 精准分类 / 重要度 / 是否财经相关。
- 带持久缓存（同一条只调一次）+ 每轮预算上限，控成本。
- key 从 bot_config.json 的 deepseek_key 或环境变量 DEEPSEEK_API_KEY 读取。
- 没 key / 调用失败 时静默跳过，自动回退到关键词层，不影响主流程。
"""

import os
import json
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "data", "llm_cache.json")
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
BATCH = 8
DEFAULT_BUDGET = 120  # 每轮最多新处理多少条
PACK_FILE = os.path.join(BASE_DIR, "prompt_pack.md")

_FALLBACK_PACK = (
    "你是资深财经分析师。逐条处理新闻，输出 JSON 对象 "
    '{"items":[{"i":序号,"s":"一句话中文摘要(≤40字,点出对市场/标的的影响)",'
    '"c":[分类slug],"imp":重要度0-10,"fin":是否财经true/false,"rumor":是否传闻true/false,'
    '"dir":"利多/利空/中性","tgt":"受影响板块或标的","str":方向强度0-3}]}。'
    "dir 判断对受影响标的是利多还是利空(方向绑定对象，拿不准给中性，中性时 tgt 空、str 0)。"
    "分类slug: macro commodity equity sector geo ashare。只输出 JSON。"
)

_cache = None
_pack_text = None


def _pack():
    global _pack_text
    if _pack_text is None:
        try:
            with open(PACK_FILE, encoding="utf-8") as f:
                _pack_text = f.read()
        except Exception:
            _pack_text = _FALLBACK_PACK
    return _pack_text


def _key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    try:
        with open(os.path.join(BASE_DIR, "bot_config.json"), encoding="utf-8") as f:
            return json.load(f).get("deepseek_key")
    except Exception:
        return None


def enabled():
    return bool(_key())


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def _save():
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except Exception as e:
        print("[llm] cache save error:", e)


def _call(news_lines):
    user = ("新闻列表（逐条处理，i 用下面的序号）:\n" + news_lines +
            '\n\n只输出 JSON 对象 {"items":[{"i","s","c","imp","fin","rumor","dir","tgt","str"}]}。')
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 1500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _pack()},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    content = d["choices"][0]["message"]["content"]
    return json.loads(content).get("items", [])


def enrich(items, budget=DEFAULT_BUDGET):
    """就地增强 items（去重后的代表条目）。返回实际新处理条数。"""
    if not enabled():
        return 0
    cache = _load()
    todo = [it for it in items if it["id"] not in cache][:budget]
    done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        lines = "\n".join("%d. %s" % (j + 1, (it.get("title_zh") or "")[:80])
                          for j, it in enumerate(batch))
        try:
            results = _call(lines)
        except Exception as e:
            print("[llm] call error:", e)
            continue
        by_i = {}
        for r in results:
            try:
                by_i[int(r.get("i"))] = r
            except Exception:
                pass
        for j, it in enumerate(batch):
            r = by_i.get(j + 1)
            if not r:
                continue
            cache[it["id"]] = {
                "s": (r.get("s") or "").strip(),
                "c": r.get("c") or [],
                "imp": r.get("imp"),
                "fin": r.get("fin", True),
                "rumor": bool(r.get("rumor", False)),
                "dir": r.get("dir"),
                "tgt": (r.get("tgt") or "").strip(),
                "str": r.get("str"),
            }
            done += 1
    _save()
    # apply cache to items
    for it in items:
        r = cache.get(it["id"])
        if not r:
            continue
        if r.get("s"):
            it["summary_zh"] = r["s"]
        if r.get("c"):
            it["categories"] = [c for c in r["c"] if c in
                                ("macro", "commodity", "equity", "sector", "geo", "ashare")]
        if r.get("imp") is not None:
            it["llm_importance"] = r["imp"]
        it["llm_fin"] = bool(r.get("fin", True))
        if r.get("rumor"):                       # LLM 判定为传闻 -> 标未证实
            it["verified"] = "unverified"
        if r.get("dir"):                         # 利多/利空/中性（缺失则不塞，优雅降级）
            it["sentiment"] = {"dir": r["dir"], "tgt": r.get("tgt") or "",
                               "str": r.get("str") or 0}
    return done
