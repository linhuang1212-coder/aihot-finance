#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIHOT 金融板块 · 本地版
零依赖（Python 标准库）。运行：python server.py
然后浏览器打开 http://localhost:8910
"""

import json
import os
import re
import time
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "items.json")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = int(os.environ.get("PORT", "8910"))

CATEGORY_LABELS = [
    ("macro", "宏观·政策"),
    ("commodity", "大宗·能源"),
    ("equity", "股指·汇率"),
    ("sector", "行业·公司"),
    ("geo", "地缘·风险"),
    ("ashare", "A股·公告"),
]
LABEL_BY_SLUG = dict(CATEGORY_LABELS)

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))  # 5分钟(2026-07-20 降 twitterapi 轮询成本)
ITEMS = []


def _attach_dt(items):
    for it in items:
        try:
            it["_dt"] = datetime.fromisoformat(it["published_at"])
        except Exception:
            it["_dt"] = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(key=lambda x: x["_dt"], reverse=True)
    return items


def load_cache():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return _attach_dt(json.load(f))


def save_cache(items):
    clean = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def refresh(verbose=True):
    """抓取 -> 累积入库(SQLite) -> 取近 N 天为工作集；失败回退到库/磁盘缓存。"""
    global ITEMS
    import store
    live = []
    try:
        import newsfetch
        live = newsfetch.fetch_all()
    except Exception as e:
        if verbose:
            print("[refresh] live fetch failed:", e)
    if live:
        try:
            ins, _ = store.upsert_items(live)
            store.prune()
            if verbose:
                print("[refresh] %d live (+%d new) 入库" % (len(live), ins))
        except Exception as e:
            if verbose:
                print("[store] upsert failed:", e)
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
            try:                            # 传播累计曲线:挂 prop_curve 给网页 sparkline
                series = store.prop_series()        # 全量算,Python 侧按 group 取(避开 IN 变量上限)
                for it in items:
                    s = series.get(it.get("dedup_group"))
                    if s:
                        it["prop_curve"] = s
            except Exception as e:
                if verbose:
                    print("[prop_curve] failed:", e)
            try:                            # 主线标记:命中算力链词表 -> mainline,供「主线」专栏
                import newsfetch
                for it in items:
                    newsfetch._tag_mainline(it)
            except Exception as e:
                if verbose:
                    print("[mainline] failed:", e)
            for it in items:                # 金十只喂「个股(A股)栏」:派生 stock_only,主流视图隐藏
                it["stock_only"] = it.get("source") == "金十"
            ITEMS = _attach_dt(items)
            try:
                save_cache(ITEMS)           # 仍写 items.json：run.py 健康检查 + 兜底
            except Exception as e:
                if verbose:
                    print("[cache] write failed:", e)
    except Exception as e:
        if verbose:
            print("[store] read failed:", e)
    if not ITEMS:
        try:
            ITEMS = load_cache()
        except Exception:
            ITEMS = []
    if verbose:
        print("[refresh] 窗口内 %d 条（近 %d 天）" % (len(ITEMS), store.RETENTION_DAYS))


def _bg_refresh():
    while True:
        time.sleep(REFRESH_SECONDS)
        refresh(verbose=True)


def item_date(it):
    """Beijing-local date string (published_at stored with +08:00)."""
    return it["_dt"].date().isoformat()


def public_item(it):
    out = {k: v for k, v in it.items() if not k.startswith("_")}
    return out


def parse_since(value):
    if not value:
        return None
    v = value.strip()
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def filter_items(qs):
    channel = (qs.get("channel", ["ai"])[0]).lower()
    mode = (qs.get("mode", ["selected"])[0]).lower()
    category = qs.get("category", [None])[0]
    entity = qs.get("entity", [None])[0]
    market = qs.get("market", [None])[0]
    source = qs.get("source", [None])[0]
    stock = qs.get("stock", [None])[0]
    mainline = qs.get("mainline", [None])[0]
    q = qs.get("q", [None])[0]
    since = parse_since(qs.get("since", [None])[0])
    include_unverified = (qs.get("include_unverified", ["true"])[0]).lower() != "false"
    try:
        take = min(int(qs.get("take", ["50"])[0]), 100)
    except ValueError:
        take = 50
    try:
        offset = max(int(qs.get("offset", ["0"])[0]), 0)
    except ValueError:
        offset = 0

    results = []
    for it in ITEMS:
        if channel not in it.get("channels", []):
            continue
        if not include_unverified and it.get("verified") == "unverified":
            continue
        if mode == "selected" and not it.get("selected"):
            continue
        if category and category not in it.get("categories", []):
            continue
        if market and market not in it.get("markets", []):
            continue
        if source:
            s = it.get("source") or ""
            if not (s == source or s.startswith(source + "·")):
                continue
        if stock and not any(e.get("market") == "a-share" for e in it.get("entities", [])):
            continue
        if it.get("stock_only") and not stock:      # 金十:仅个股(A股)视图放行,主流视图隐藏
            continue
        if mainline and not it.get("mainline"):
            continue
        if entity:
            ent = entity.strip().lower()
            hit = False
            for e in it.get("entities", []):
                name = (e.get("name") or "").lower()
                code = (e.get("code") or "").lower()
                if ent in name or ent == code:
                    hit = True
                    break
            if not hit:
                continue
        if since and it["_dt"] < since:
            continue
        if q:
            qq = q.strip().lower()
            hay = " ".join([
                it.get("title_zh", ""), it.get("summary_zh", ""),
                it.get("body_excerpt", ""), " ".join(it.get("tags", [])),
                " ".join(e.get("name", "") for e in it.get("entities", [])),
            ]).lower()
            if qq not in hay:
                continue
        results.append(it)

    # breaking = pure time-desc stream (already sorted); others also time-desc feed
    return [public_item(x) for x in results[offset:offset + take]]


def available_dates(channel):
    dates = []
    for it in ITEMS:
        if channel in it.get("channels", []):
            d = item_date(it)
            if d not in dates:
                dates.append(d)
    dates.sort(reverse=True)
    return dates


def build_daily(channel, date):
    sections = []
    for slug, label in CATEGORY_LABELS:
        bucket = []
        seen = set()
        for it in ITEMS:
            if channel not in it.get("channels", []):
                continue
            if item_date(it) != date:
                continue
            if it.get("verified") == "unverified":
                continue  # 未证实不进日报正文
            if not it.get("selected"):
                continue
            cats = it.get("categories", [])
            if not cats or cats[0] != slug:  # 只进主分类，避免重复
                continue
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            bucket.append(public_item(it))
            if len(bucket) >= 8:
                break
        if bucket:
            sections.append({"slug": slug, "label": label, "items": bucket})
    return sections


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path):
        ext = os.path.splitext(path)[1]
        ctype = STATIC_TYPES.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # ---------- API ----------
        if path == "/api/public/items":
            return self._send_json({
                "channel": qs.get("channel", ["ai"])[0],
                "mode": qs.get("mode", ["selected"])[0],
                "items": filter_items(qs),
            })

        if path == "/api/public/dailies":
            channel = qs.get("channel", ["ai"])[0]
            try:
                take = min(int(qs.get("take", ["30"])[0]), 180)
            except ValueError:
                take = 30
            return self._send_json({
                "channel": channel,
                "dates": available_dates(channel)[:take],
            })

        m = re.match(r"^/api/public/daily(?:/(\d{4}-\d{2}-\d{2}))?$", path)
        if m:
            channel = qs.get("channel", ["ai"])[0]
            date = m.group(1)
            dates = available_dates(channel)
            if not dates:
                return self._send_json({"channel": channel, "date": None, "sections": []})
            if not date:
                date = dates[0]
            return self._send_json({
                "channel": channel,
                "date": date,
                "sections": build_daily(channel, date),
            })

        if path == "/api/public/categories":
            return self._send_json({"categories": [
                {"slug": s, "label": l} for s, l in CATEGORY_LABELS
            ]})

        if path == "/api/public/propagation":
            import store
            group = qs.get("group", [None])[0]
            return self._send_json(store.prop_detail(group))

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

        if path == "/aihot-finance/SKILL.md":
            skill_path = os.path.join(BASE_DIR, "aihot-finance", "SKILL.md")
            if os.path.isfile(skill_path):
                with open(skill_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._send_json({"error": "not found"}, 404)

        # ---------- static ----------
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not target.startswith(PUBLIC_DIR):
            return self._send_json({"error": "forbidden"}, 403)
        if os.path.isfile(target):
            return self._send_static(target)
        return self._send_json({"error": "not found", "path": path}, 404)


def main():
    print("AIHOT 金融板块 · 本地版（实时真实数据）")
    print("  正在抓取真实信源（金十 + X/Twitter KOL）…")
    import store
    store.init_db()
    migrated = store.migrate_from_json(DATA_FILE)
    if migrated:
        print("  迁移 %d 条历史进库" % migrated)
    refresh(verbose=True)
    print("  数据: %d 条" % len(ITEMS))
    print("  自动刷新: 每 %d 秒" % REFRESH_SECONDS)
    print("  打开: http://localhost:%d" % PORT)
    print("  停止: Ctrl+C")
    t = threading.Thread(target=_bg_refresh, daemon=True)
    t.start()
    # 默认只绑本机回环(nginx 反代到 127.0.0.1:PORT);要对外可设 HOST=0.0.0.0。
    host = os.environ.get("HOST", "127.0.0.1")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
