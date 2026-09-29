#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIHOT 金融板块 · 本地版
零依赖（Python 标准库）。运行：python server.py
然后浏览器打开 http://localhost:8910
"""

import gzip
import hashlib
import json
import os
import re
import sys
import time
import threading
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import atomicio
import zhvariant

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "items.json")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = int(os.environ.get("PORT", "8910"))
CST = timezone(timedelta(hours=8))

CATEGORY_LABELS = [
    ("macro", "宏观·政策"),
    ("commodity", "大宗·能源"),
    ("equity", "股指·汇率"),
    ("sector", "行业·公司"),
    ("geo", "地缘·风险"),
    ("ashare", "A股·公告"),
]
LABEL_BY_SLUG = dict(CATEGORY_LABELS)

# 静态文件扩展名白名单:不在表里的一律 404(public/ 里的 *.bak-日期 备份、NTFS 流 x.html::$DATA 等)
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
# 这些类型且 >= GZIP_MIN 字节时,客户端声明支持就 gzip(JSON 实测省 ~69%,全走家宽上行 + 隧道)
_COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml")
GZIP_MIN = 1024
# 客户端中途断开(关页面/隧道抖动)或 socket 读写超时(Handler.timeout):不算服务端错误,不打 traceback
_CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)


REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "300"))  # 5分钟(2026-07-20 降 twitterapi 轮询成本)
ITEMS = []


def _attach_dt(items):
    for it in items:
        try:
            dt = datetime.fromisoformat(it["published_at"])
            if dt.tzinfo is None:           # 不带时区的按北京时间,免得和带时区的比较时抛 TypeError
                dt = dt.replace(tzinfo=CST)
            it["_dt"] = dt
        except Exception:
            it["_dt"] = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(key=lambda x: x["_dt"], reverse=True)
    return items


def load_cache():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return _attach_dt(json.load(f))


def save_cache(items):
    """原子写 items.json(不再 indent:48MB 每 5 分钟整写,缩进只增体积)。"""
    clean = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    if not atomicio.write_json(DATA_FILE, clean):
        raise OSError("items.json 保存失败")


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


def hot_start():
    """用磁盘缓存热启动 ITEMS(毫秒级、不联网),让端口能立即开始服务。返回条数。
    优先 items.json:它是上一轮 refresh() 的产物,已带 stock_only / propagation 等派生字段,
    比直接读库更接近稳态;读不到再退回库,最后退空列表(空列表也要能开服务,总好过 502)。"""
    global ITEMS
    try:
        ITEMS = load_cache()
        print("  缓存热启动: %d 条(后台抓取中,稍后自动刷新)" % len(ITEMS))
    except Exception:
        try:
            import store
            ITEMS = _attach_dt(store.recent_items())
            print("  库热启动: %d 条(无 items.json,后台抓取中)" % len(ITEMS))
        except Exception:
            ITEMS = []
            print("  无本地缓存,后台首轮抓取中…")
    return len(ITEMS)


def _bg_refresh(first_refresh=True):
    """后台刷新循环。first_refresh=True 时先立刻跑一轮 refresh():首轮真实抓取(金十 + X/KOL
    + LLM 增强)可达数分钟,放这里而不是 main() 里,保证端口在启动后**立即**可用。
    2026-09-10 实测:重启后 main() 卡在首轮 refresh() 上,09:46:59 起进程,09:50:08 才就绪,
    公网 /news/ 白吐了 3 分钟 502,run.py 的健康检查还误报了一次"数据服务无法访问"。"""
    if first_refresh:
        refresh(verbose=True)
        print("  数据: %d 条" % len(ITEMS))
    while True:
        after_refresh()
        time.sleep(REFRESH_SECONDS)
        refresh(verbose=True)


BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
_last_daily = None
_maintenance_enabled = False     # main() 里才打开:测试调 _bg_refresh 也碰不到生产 data/


def after_refresh():
    """每轮 refresh 之后的维护,只在正式启动的后台循环里跑(refresh() 本身不碰):
    ① 补救翻译/增强失败的 X 条目;② 付费服务连续失败(402 欠费等)-> 告警(送达才记账,
    发不出去下一轮重发);③ 每天一次:news.db 快照 + 清掉 LLM 缓存里已出窗口的条目
    (快照失败下一轮再试)。每步独立兜底,互不影响。"""
    global _last_daily
    if not _maintenance_enabled:
        return
    try:
        _repair_round()
    except Exception as e:
        print("[repair] failed:", e)
    try:
        import health
        import notify
        for service, state, msg in health.evaluate():
            if notify.send_alert(msg):
                health.mark(service, state)
    except Exception as e:
        print("[health] failed:", e)
    today = datetime.now(CST).date().isoformat()
    if _last_daily != today:
        try:
            import store
            p = store.backup(BACKUP_DIR)
            if p:
                print("[backup] %s" % p)
            _last_daily = today
        except Exception as e:
            print("[backup] failed:", e)
        try:
            import llm
            n = llm.prune(it["id"] for it in ITEMS)
            if n:
                print("[llm] 缓存清掉 %d 条已出窗口的" % n)
        except Exception as e:
            print("[llm] prune failed:", e)


def _repair_round():
    import store
    import newsfetch
    fixed = newsfetch.repair_items(store.repair_candidates(exclude=newsfetch.repair_exhausted()))
    if not fixed:
        return
    store.apply_repairs(fixed)
    by_id = {it["id"]: it for it in fixed}
    for it in ITEMS:                         # 工作集就地更新,不等下一轮 refresh
        f = by_id.get(it.get("id"))
        if f:
            for k in ("title_zh", "summary_zh", "sentiment", "llm_importance", "categories",
                      "verified", "score", "selected", "heat"):
                if k in f:
                    it[k] = f[k]
            newsfetch._tag_mainline(it)
    print("[repair] 补救 %d 条(翻译/情绪)" % len(fixed))


def item_date(it):
    """Beijing-local date string (published_at stored with +08:00)."""
    return it["_dt"].date().isoformat()


def public_item(it):
    out = {k: v for k, v in it.items() if not k.startswith("_")}
    return out


def parse_since(value):
    """解析 since 参数,总是返回带时区的 datetime(或 None)。
    - 不带时区 -> 按北京时间(否则和条目时间比较会抛 TypeError,连接直接断、nginx 回 502);
    - URL 里没编码的 "+08:00" 会被 parse_qs 解成空格 -> 把时间后面那个空格还原成 "+"。"""
    if not value:
        return None
    v = value.strip()
    v = re.sub(r"(\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?) (\d{2}:?\d{2})$", r"\1+\2", v)
    if v[-1:] in ("Z", "z"):
        v = v[:-1] + "+00:00"
    # Python 3.10 的 fromisoformat 只认 +HH:MM 和 3/6 位小数秒(云端是 3.10):先规整
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    v = re.sub(r"\.(\d{1,6})\d*(?=[+-]\d{2}:\d{2}$|$)", lambda m: "." + m.group(1).ljust(6, "0"), v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt


MAX_QUERY_TOKENS = 6
MAX_TOKEN_LEN = 40


def query_patterns(q):
    """搜索词 -> 正则列表(每个都要命中)。
    - 含中文:按空白切词(最多 6 个),每个字展开成繁简字符类,
      所以搜「台积电 涨价」也能命中 DIGITIMES 的繁体「台積電…漲價」;
    - 纯 ASCII:保持原来的整串短语匹配。英文切词后子串 AND 会大量误命中
      ("rate cut" 命中 moderate…consecutive),Telegram 里随手发的 "hi there" 也会被当搜索推一堆卡片。"""
    if not q or not q.strip():
        return []
    q = q.strip().lower()
    if q.isascii():
        return [re.compile(re.escape(q))]
    toks = [t for t in re.split(r"\s+", q) if t][:MAX_QUERY_TOKENS]
    return [re.compile(zhvariant.pattern(t[:MAX_TOKEN_LEN])) for t in toks]


def _inside(base, target):
    """target 是否在 base 目录内。不能用 startswith:'public_backup_x' 也以 'public' 开头。"""
    try:
        common = os.path.commonpath([base, target])
    except ValueError:                      # 跨盘符 / UNC 与本地路径混用
        return False
    return os.path.normcase(common) == os.path.normcase(base)


def filter_items(qs):
    channel = (qs.get("channel", ["ai"])[0]).lower()
    mode = (qs.get("mode", ["selected"])[0]).lower()
    category = qs.get("category", [None])[0]
    entity = qs.get("entity", [None])[0]
    market = qs.get("market", [None])[0]
    source = qs.get("source", [None])[0]
    stock = qs.get("stock", [None])[0]
    mainline = qs.get("mainline", [None])[0]
    q_pats = query_patterns(qs.get("q", [None])[0])
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
        if q_pats:
            hay = " ".join([
                it.get("title_zh") or "", it.get("summary_zh") or "",
                it.get("body_excerpt") or "", " ".join(it.get("tags") or []),
                " ".join(e.get("name") or "" for e in it.get("entities") or []),
            ]).lower()
            if not all(p.search(hay) for p in q_pats):
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


def _now_str():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


class Handler(BaseHTTPRequestHandler):
    # 只管 socket 读写(防慢连接一直占着线程);不限制 /api/analysis 这类慢计算本身
    timeout = 30
    _head_only = False

    def log_message(self, fmt, *args):
        pass  # quiet

    def _accepts_gzip(self):
        for part in (self.headers.get("Accept-Encoding") or "").split(","):
            name, _, params = part.partition(";")
            if name.strip().lower() == "gzip":
                return not re.match(r"(?i)^\s*q\s*=\s*0(?:\.0*)?\s*$", params)
        return False

    def _etag_matches(self, etag):
        """If-None-Match 用弱比较:去掉 W/ 前缀再比 opaque-tag。"""
        inm = self.headers.get("If-None-Match")
        if not inm:
            return False
        if inm.strip() == "*":
            return True
        opaque = etag[2:] if etag.startswith("W/") else etag
        for t in inm.split(","):
            t = t.strip()
            if (t[2:] if t.startswith("W/") else t) == opaque:
                return True
        return False

    def _send(self, body, ctype, status=200, headers=None):
        """统一出口:ETag/304、gzip、Cache-Control、HEAD 只发头。"""
        hdrs = [("X-Content-Type-Options", "nosniff")] + list(headers or [])
        compressible = ctype.startswith(_COMPRESSIBLE) and len(body) >= GZIP_MIN
        if compressible:
            hdrs.append(("Vary", "Accept-Encoding"))
        if status == 200:
            # 弱 ETag 按未压缩内容算:gzip/原文两种表示语义相同。no-cache = 可缓存但每次先问,
            # 没变就 304 不下发正文(前端每 45s 轮询,大多数轮次内容没变)
            etag = 'W/"%s"' % hashlib.md5(body).hexdigest()[:20]
            hdrs += [("ETag", etag), ("Cache-Control", "no-cache")]
            if self._etag_matches(etag):
                self.send_response(304)
                for k, v in hdrs:
                    self.send_header(k, v)
                self._headers_flushed = True
                self.end_headers()
                return
        if compressible and self._accepts_gzip():
            body = gzip.compress(body, compresslevel=6, mtime=0)
            hdrs.append(("Content-Encoding", "gzip"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in hdrs:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self._headers_flushed = True
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def _send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status,
                   [("Access-Control-Allow-Origin", "*")] + list(headers or []))

    def _send_static(self, path):
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self._send(body, STATIC_TYPES[ext])

    def do_GET(self):
        self._head_only = False
        self._dispatch()

    def do_HEAD(self):
        self._head_only = True
        self._dispatch()

    def _dispatch(self):
        """兜底:任何未预期异常都回 500 JSON,而不是直接断连接(经 nginx 就成了 502)。
        响应头已经发出去之后再出错,就不能再补一个 500(会被拼进正文),只断开连接。"""
        self._headers_flushed = False
        try:
            self._route()
        except _CLIENT_GONE:
            self.close_connection = True
        except Exception:
            print("[%s] [http] %s %s 处理异常:\n%s"
                  % (_now_str(), self.command, self.path, traceback.format_exc()))
            self.close_connection = True
            if not self._headers_flushed:
                try:
                    self._send_json({"error": "internal error"}, 500)
                except Exception:
                    pass

    def _route(self):
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
            if self._head_only:                 # HEAD 不能触发付费生成
                return self._send_json({"error": "method not allowed"}, 405,
                                       [("Allow", "GET")])
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
                return self._send(body, "text/plain; charset=utf-8")
            return self._send_json({"error": "not found"}, 404)

        # ---------- static ----------
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not _inside(PUBLIC_DIR, target):
            return self._send_json({"error": "forbidden"}, 403)
        if os.path.splitext(target)[1].lower() in STATIC_TYPES and os.path.isfile(target):
            return self._send_static(target)
        return self._send_json({"error": "not found", "path": path}, 404)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """客户端中途断开(连接被中止/重置)不刷 traceback:server.log 里原来 251/294 条都是这个。"""
        if isinstance(sys.exc_info()[1], _CLIENT_GONE):
            return
        print("[%s] [http] 连接 %s 处理出错:" % (_now_str(), client_address[0]))
        super().handle_error(request, client_address)


def main():
    print("AIHOT 金融板块 · 本地版（实时真实数据）")
    print("  正在抓取真实信源（金十 + X/Twitter KOL）…")
    global _maintenance_enabled
    import store
    import health
    health.enable()                      # 只有正式启动才记录付费服务健康(测试导入不产生副作用)
    _maintenance_enabled = True
    store.init_db()
    migrated = store.migrate_from_json(DATA_FILE)
    if migrated:
        print("  迁移 %d 条历史进库" % migrated)
    hot_start()
    print("  自动刷新: 每 %d 秒" % REFRESH_SECONDS)
    print("  打开: http://localhost:%d" % PORT)
    print("  停止: Ctrl+C")
    t = threading.Thread(target=_bg_refresh, daemon=True)
    t.start()
    # 默认只绑本机回环(nginx 反代到 127.0.0.1:PORT);要对外可设 HOST=0.0.0.0。
    host = os.environ.get("HOST", "127.0.0.1")
    Server((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
