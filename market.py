#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情数据（Finnhub 免费版）。给自选股提供实时报价、财报日期。
- key 从 bot_config.json 的 finnhub_key 或环境变量 FINNHUB_API_KEY 读取。
- 仅支持美股报价（A 股 Finnhub 免费版不覆盖）。
- 30 秒内同标的走缓存，避免触发免费版限频（60 次/分钟）。
"""

import os
import json
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_quote_cache = {}   # symbol -> (ts, data)


def _key():
    k = os.environ.get("FINNHUB_API_KEY")
    if k:
        return k
    try:
        with open(os.path.join(BASE_DIR, "bot_config.json"), encoding="utf-8") as f:
            return json.load(f).get("finnhub_key")
    except Exception:
        return None


def enabled():
    return bool(_key())


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "aihot-market"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_quote(symbol):
    """返回 {symbol, price, change, pct, open, prev, high, low} 或 None。"""
    key = _key()
    if not key:
        return None
    now = time.time()
    hit = _quote_cache.get(symbol)
    if hit and now - hit[0] < 30:
        return hit[1]
    try:
        j = _get("https://finnhub.io/api/v1/quote?symbol=%s&token=%s" % (symbol, key))
    except Exception as e:
        print("[market] quote error %s: %s" % (symbol, e))
        return None
    if not j or j.get("c") in (None, 0):
        return None
    data = {
        "symbol": symbol, "price": j.get("c"), "change": j.get("d"),
        "pct": j.get("dp"), "open": j.get("o"), "prev": j.get("pc"),
        "high": j.get("h"), "low": j.get("l"),
    }
    _quote_cache[symbol] = (now, data)
    return data


def next_earnings(symbol, days=90):
    """返回最近一次未来财报日期字符串，或 None。"""
    key = _key()
    if not key:
        return None
    try:
        import datetime as _dt
        today = time.strftime("%Y-%m-%d")
        future = time.strftime("%Y-%m-%d", time.localtime(time.time() + days * 86400))
        j = _get("https://finnhub.io/api/v1/calendar/earnings?from=%s&to=%s&symbol=%s&token=%s"
                 % (today, future, symbol, key))
        rows = (j or {}).get("earningsCalendar") or []
        if rows:
            return rows[0].get("date")
    except Exception as e:
        print("[market] earnings error %s: %s" % (symbol, e))
    return None


def fmt_quote(name, q):
    """格式化成一行中文报价。"""
    if not q:
        return None
    arrow = "▲" if (q.get("change") or 0) >= 0 else "▼"
    return "%s %s  $%.2f  %s%.2f%%（昨收 %.2f）" % (
        name, q["symbol"], q["price"], arrow, abs(q.get("pct") or 0), q.get("prev") or 0)


def watchlist_symbols():
    """从 watchlist.json 取美股 (name, symbol) 列表。"""
    out = []
    try:
        with open(os.path.join(BASE_DIR, "watchlist.json"), encoding="utf-8") as f:
            wl = json.load(f)
        for e in wl.get("entities", []):
            if e.get("market") == "us" and e.get("code"):
                out.append((e.get("name") or e["code"], e["code"]))
    except Exception:
        pass
    return out


if __name__ == "__main__":
    for name, sym in (watchlist_symbols() or [("英伟达", "NVDA")]):
        print(fmt_quote(name, get_quote(sym)), "| 下次财报:", next_earnings(sym))
