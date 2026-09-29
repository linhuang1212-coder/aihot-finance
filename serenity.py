#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serenity 深度分析层（按需）。对单个标的产出产业链/机构/风险视角的中文分析。
数据：传入的本地条目 + Google News RSS 补充头条（零依赖）。
DeepSeek key 复用 llm.py。缓存按 (标的,当天) 存 data/analysis_cache.json。
没 key / 无数据 / 调用失败 时返回带 status 的友好结果，不抛异常。
"""

import os
import re
import json
import time
import threading
import concurrent.futures
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html import unescape

import llm      # 复用 _key / enabled
import market   # 复用 Finnhub 报价

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACK_FILE = os.path.join(BASE_DIR, "analysis_pack.md")
CACHE_FILE = os.path.join(BASE_DIR, "data", "analysis_cache.json")
CST = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_TOKENS = int(os.environ.get("ANALYSIS_MAX_TOKENS", "1500"))
CONTEXT_ITEMS = 12
DEEP_RESEARCH = os.environ.get("ANALYSIS_DEEP", "on").lower() != "off"
ARTICLE_MAX = 3          # 抓几篇正文
ARTICLE_CHARS = 1500     # 每篇正文截断字数
# 每天最多新生成几次:成功的按 analysis_cache.json 里当天的 key 计(重启不清零),
# 失败的(DeepSeek 欠费/故障时每次仍会去抓 Google News/DDG/正文)按进程内计数一起算。命中缓存不算。
DAILY_MAX = int(os.environ.get("ANALYSIS_DAILY_MAX", "20"))
DEADLINE = int(os.environ.get("ANALYSIS_DEADLINE", "240"))   # 单次 DeepSeek 调用总时长上限(秒)
ENTITY_MAX_LEN = 30
_GEN_LOCK = threading.Lock()
_FAILS = {}                  # {日期: 当天生成失败次数},只在持锁时读写
# 须与 analysis_pack.md 结尾的免责语逐字一致（analyze 用子串判断避免重复追加）
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
            "sources": sources, "count": len(rel), "items": rel}


def _read_capped(r, deadline, max_bytes=None):
    """分块读响应,超过总时限就抛 TimeoutError。urlopen 的 timeout 只管单次 socket 读:
    对端每隔几秒吐一点(DeepSeek 排队时会持续发空行,官方说最长 10 分钟)就永远不超时,
    而深度分析是持锁生成的,卡住期间网页和 /serenity 全部 busy。"""
    chunks, n = [], 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("response exceeded deadline")
        b = r.read1(65536)
        if not b:
            break
        chunks.append(b)
        n += len(b)
        if max_bytes and n >= max_bytes:
            break
    return b"".join(chunks)


def _get_bytes(url, timeout=10, data=None, headers=None, max_bytes=2_000_000):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _read_capped(r, time.monotonic() + timeout * 3, max_bytes)


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


# ---------- 方案C：联网深研（抓正文 + DuckDuckGo 搜索 + 报价，全并行、可降级）----------
def _fetch_article(url):
    """抓单篇文章正文（剥 HTML）。Google News 跳转链或失败 -> ""。"""
    if not url or "news.google.com" in url:
        return ""
    try:
        raw = _get_bytes(url, timeout=6).decode("utf-8", "replace")
    except Exception:
        return ""
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(re.sub(r"\s+", " ", txt)).strip()
    return txt[:ARTICLE_CHARS]


def _ddg_search(query, n=6):
    """DuckDuckGo HTML 搜索（POST，免 key）。返回 [{title,snippet,url}]，失败 []。"""
    try:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        raw = _get_bytes("https://html.duckduckgo.com/html/", timeout=8,
                         data=data).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    pat = r'result__a"[^>]*href="(.*?)"[^>]*>(.*?)</a>.*?result__snippet"[^>]*>(.*?)</a>'
    for m in re.finditer(pat, raw, re.S):
        title = unescape(re.sub("<[^>]+>", "", m.group(2))).strip()
        snip = unescape(re.sub("<[^>]+>", "", m.group(3))).strip()
        if title and snip:
            out.append({"title": title, "snippet": snip, "url": m.group(1)})
        if len(out) >= n:
            break
    return out


def _quote_block(entity, items):
    """标的是美股则给实时报价 + 财报日；否则 ""。"""
    ent = (entity or "").strip().lower()
    code, name = None, entity
    for it in items:
        for e in it.get("entities", []):
            nm = (e.get("name") or "").lower()
            cd = (e.get("code") or "").lower()
            if (ent in nm or ent == cd) and e.get("market") == "us" and e.get("code"):
                code, name = e["code"], e.get("name") or entity
                break
        if code:
            break
    if not code:
        return ""
    try:
        q = market.get_quote(code)
        if not q:
            return ""
        line = market.fmt_quote(name, q) or ""
        earn = market.next_earnings(code)
        if earn:
            line += "（下次财报 %s）" % earn
        return line
    except Exception:
        return ""


def _gather_research(entity, items):
    """并行做研究：抓正文 + 联网搜索 + 报价。返回 {text, sources}。任何一块挂了不影响其余。"""
    if not DEEP_RESEARCH:
        return {"text": "", "sources": []}
    urls = []
    for it in items:
        u = it.get("source_url")
        if u and "news.google.com" not in u:
            urls.append(u)
        if len(urls) >= ARTICLE_MAX:
            break
    query = "%s 产业链 财报 市场份额" % entity
    articles, search, quote = [], [], ""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            fa = [ex.submit(_fetch_article, u) for u in urls]
            fs = ex.submit(_ddg_search, query)
            fq = ex.submit(_quote_block, entity, items)
            articles = [f.result() for f in fa]
            search = fs.result()
            quote = fq.result()
    except Exception as e:
        print("[serenity] research error:", e)
    parts, sources = [], []
    arts = [a for a in articles if a]
    if arts:
        parts.append("【相关文章正文摘录】\n" + "\n---\n".join(arts))
    if search:
        lines = ["[S%d] %s：%s" % (i, s["title"], s["snippet"]) for i, s in enumerate(search, 1)]
        parts.append("【联网搜索结果（网络检索，未核实；引用其中数据请标注来源号 [S#]）】\n"
                     + "\n".join(lines))
        sources += [{"title": s["title"], "url": s["url"], "source": "搜索"} for s in search]
    if quote:
        parts.append("【实时行情】\n" + quote)
    return {"text": "\n\n".join(parts), "sources": sources, "search": search}


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
    except ValueError:
        # 文件坏了(半截写入):改名留底再从空开始,别让下一次保存把历史分析整份覆盖掉
        try:
            os.replace(CACHE_FILE, CACHE_FILE + ".corrupt-%s" % datetime.now(CST).strftime("%Y%m%d%H%M%S"))
        except OSError:
            pass
        return {}
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)          # 原子替换:断电/被杀也不会留下半截文件
    except Exception as e:
        print("[serenity] cache save error:", e)


def _deepseek(system, user):
    key = llm._key()
    if not key:                       # 正常路径已被 analyze 的 enabled() 拦住；这里兜底防直接调用
        raise RuntimeError("no DeepSeek key")
    body = json.dumps({
        "model": MODEL, "temperature": 0.3, "max_tokens": MAX_TOKENS,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    # 生成一般 30–60s。timeout=90 是单次 socket 读超时;整次调用另有 DEADLINE 硬上限
    # (DeepSeek 拥堵时持续发空行,单次读永远不超时)。bot 侧 fetch_analysis 的 HTTP 超时是 120s。
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(_read_capped(r, time.monotonic() + DEADLINE))
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
    if len(entity) > ENTITY_MAX_LEN:
        return _err(entity[:ENTITY_MAX_LEN], "bad_request",
                    "标的名太长（最多 %d 个字）。" % ENTITY_MAX_LEN)
    if not llm.enabled():
        return _err(entity, "no_llm", "未配置分析能力（DeepSeek key）。")
    ctx = build_context(entity, items)
    if ctx["count"] == 0:
        return _err(entity, "no_data", "暂无足够信息分析「%s」。" % entity)

    cache = _load_cache()
    ckey = entity.lower() + "@" + _today()
    if ckey in cache:
        return {**cache[ckey], "cached": True}

    # 往下要花钱(DeepSeek + 外部抓取)。/api/analysis 经 fblerp.com/news 公网匿名可达,
    # 且子串匹配让几乎任意短串都能命中条目 -> 同时只生成一个 + 每天限额,防被刷费用。
    # 拿不到锁直接回 busy,不排队(排队会让请求线程和隧道连接一起堆积)。
    if not _GEN_LOCK.acquire(blocking=False):
        return _err(entity, "busy", "另一个深度分析正在生成，请稍后再试。",
                    ctx["sources"], ctx["based_on"])
    try:
        cache = _load_cache()           # 拿到锁后重读:可能刚被上一个请求生成好
        if ckey in cache:
            return {**cache[ckey], "cached": True}
        today = _today()
        used = sum(1 for k in cache if k.endswith("@" + today)) + _FAILS.get(today, 0)
        if used >= DAILY_MAX:
            return _err(entity, "quota",
                        "今日深度分析次数已用完（每天 %d 次），明天再试。" % DAILY_MAX,
                        ctx["sources"], ctx["based_on"])
        result = _generate(entity, ctx, cache, ckey)
        if result.get("status") != "ok":
            for d in [d for d in _FAILS if d != today]:
                del _FAILS[d]
            _FAILS[today] = _FAILS.get(today, 0) + 1
        return result
    finally:
        _GEN_LOCK.release()


def _generate(entity, ctx, cache, ckey):
    """真正生成一次分析(付费),成功则写入 cache[ckey]。调用方须持有 _GEN_LOCK。"""
    supp = fetch_supplement(entity)
    supp_text = "\n".join("- %s（%s）" % (s["title"], s["source"]) for s in supp) or "（无补充）"
    research = _gather_research(entity, ctx["items"])
    research_text = ("\n\n" + research["text"]) if research["text"] else ""
    user = ("标的：%s\n\n【本地新闻条目】\n%s\n\n"
            "【最新外部头条（仅供补充背景，可能未在本地库）】\n%s%s\n\n"
            "请综合以上全部资料，按系统提示的五段结构输出中文分析。"
            % (entity, ctx["lines"], supp_text, research_text))
    try:
        md = _deepseek(_pack(), user).strip()
    except Exception:
        return _err(entity, "error", "分析失败（DeepSeek 调用异常），稍后再试。",
                    ctx["sources"], ctx["based_on"])

    # 统一把免责放到最后；中间插入"联网检索来源"出处清单（未核实）
    # 用开头特征去重：LLM 可能输出措辞略不同的免责（如"不预测价格点位"），精确匹配抓不住
    body = re.sub(r'\s*(?:[—\-]{2,}\s*)?本分析为框架推理[^\n]*', '', md).rstrip().rstrip("—-").rstrip()
    footer = ""
    if research.get("search"):
        cites = "\n".join("[S%d] %s — %s" % (i, x["title"], x["url"])
                          for i, x in enumerate(research["search"], 1))
        footer = "\n\n**联网检索来源（网络检索，未核实）**\n" + cites
    md = body + footer + "\n\n———\n" + DISCLAIMER
    result = {
        "status": "ok", "entity": entity, "analysis_md": md,
        "sources": ctx["sources"] + supp + research["sources"], "based_on": ctx["based_on"],
        "generated_at": _now_iso(), "cached": False,
    }
    cache[ckey] = {k: v for k, v in result.items() if k != "cached"}  # 排除瞬时字段，其余全持久化
    _save_cache(cache)
    return result