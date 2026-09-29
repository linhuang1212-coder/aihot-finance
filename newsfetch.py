#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实信源抓取（Python 标准库，零依赖）。
- 新浪财经 7x24 全球财经快讯（中文，大陆可达）
- Google News 商业/财经（英文，国际，需 VPN）
- CNBC Markets（英文，国际，需 VPN）

输出统一为 AIHOT 金融板块的条目结构。分类/市场为【关键词启发式】，非 LLM，
匹配不到就留空——不编造。来源真实，但「来源真实 ≠ 内容已证实」。
"""

import os
import json
import re
import time
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANS_FILE = os.path.join(BASE_DIR, "data", "translations.json")
CST = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

CAT_KEYWORDS = {
    "macro": ["美联储", "利率", "通胀", "非农", "失业", "就业", "央行", "GDP", "关税",
              "财政", "降息", "加息", "PMI", "CPI", "Fed", "inflation", "jobs", "rate cut",
              "rate hike", "tariff", "Treasury yield", "ECB", "unemployment", "payroll",
              "central bank", "GDP"],
    "commodity": ["原油", "石油", "欧佩克", "OPEC", "黄金", "金价", "天然气", "铜", "铝",
                  "镍", "锂", "煤", "油价", "oil", "gold", "crude", "natural gas", "copper",
                  "lithium", "commodit", "Brent", "WTI"],
    "equity": ["股指", "纳指", "道指", "标普", "创业板", "沪指", "深成指", "收涨", "收跌",
               "IPO", "股市", "日经", "KOSPI", "恒指", "index", "Nasdaq", "Dow", "S&P",
               "stocks", "shares", "equit", "earnings", "Nikkei", "rally", "sell-off"],
    "sector": ["芯片", "半导体", "英伟达", "台积电", "苹果", "特斯拉", "光伏", "锂电",
               "新能源", "机器人", "银行", "车企", "chip", "semiconductor", "Nvidia",
               "TSMC", "Apple", "Tesla", "data center", "bank", "AI"],
    "geo": ["伊朗", "以色列", "俄罗斯", "乌克兰", "中东", "制裁", "战争", "导弹", "霍尔木兹",
            "巴勒斯坦", "朝鲜", "黎巴嫩", "Iran", "Israel", "Russia", "Ukraine", "war",
            "sanction", "missile", "strike", "military", "Gaza", "Hormuz"],
    "ashare": ["A股", "北交所", "科创板", "涨停", "跌停", "龙虎榜", "主力", "证监会"],
}
MARKET_KEYWORDS = {
    "oil": ["原油", "石油", "欧佩克", "OPEC", "油价", "crude", "oil", "WTI", "Brent"],
    "gold": ["黄金", "金价", "gold", "bullion"],
    "a-share": ["A股", "沪指", "深成指", "创业板", "科创板", "北交所", "涨停"],
    "hk": ["港股", "恒指", "恒生", "Hang Seng", "Hong Kong"],
    "us": ["美股", "纳指", "道指", "标普", "华尔街", "Nasdaq", "Dow", "S&P", "Wall Street"],
    "fx": ["美元", "汇率", "日元", "欧元", "人民币", "离岸", "dollar", "yen", "euro",
           "forex", "currency"],
    "bond": ["国债", "债券", "收益率", "Treasury", "bond", "yield"],
}
AI_KEYWORDS = ["AI", "人工智能", "英伟达", "Nvidia", "OpenAI", "大模型", "算力", "芯片",
               "semiconductor", "GPU", "DeepSeek", "Anthropic", "数据中心"]

# ---- B/C 质量参数 ----
SELECT_THRESHOLD = 5.0  # 综合分 >= 此值进「精选」

IMPORTANT_KW = {  # 重要度关键词 -> 权重
    "美联储": 3, "fed": 3, "利率": 2, "降息": 3, "加息": 3, "rate cut": 3, "rate hike": 3,
    "欧佩克": 3, "opec": 3, "央行": 2, "通胀": 2, "inflation": 2, "cpi": 2, "gdp": 2,
    "非农": 3, "payroll": 2, "就业": 1, "失业": 1, "关税": 2, "tariff": 2,
    "英伟达": 3, "nvidia": 3, "黄仁勋": 3,
    "制裁": 2, "sanction": 2, "战争": 2, "war": 2, "导弹": 2, "missile": 2, "霍尔木兹": 2,
    "突发": 2, "breaking": 2, "涨停": 2, "ipo": 2, "收购": 2, "并购": 2, "merger": 2,
    "acquisition": 2, "财报": 2, "earnings": 2,
}

# 用户优先主线（补强弱项：原材料涨价 / 半导体加工·材料·技术迭代 / 新能源 / 数据中心 /
# 境外对华政策）。命中即加权，让这些主线从消防水管里浮上来。文本已 lower()，英文键用小写。
THEME_KW = {
    # 原材料稀缺 / 涨价（#1 关注）
    "涨价": 2, "提价": 2, "缺货": 2, "紧缺": 2, "短缺": 2, "供不应求": 2, "涨价潮": 2,
    "缺口": 1, "停产": 1, "减产": 1, "shortage": 2, "price hike": 2,
    # 半导体加工 / 材料 / 技术迭代
    "hbm": 2, "存储芯片": 2, "光模块": 2, "先进封装": 2, "cpo": 2, "石英砂": 2, "光棒": 2,
    "光刻": 2, "晶圆": 1, "制程": 1, "2nm": 1, "3nm": 1, "良率": 1, "封测": 1, "光纤": 1,
    # 新能源
    "光伏": 1, "储能": 1, "锂电": 1, "固态电池": 2, "风电": 1,
    # 数据中心 / 算力
    "数据中心": 1, "算力": 1, "液冷": 2, "idc": 1,
    # 境外对华政策 / 出口管制
    "出口管制": 2, "实体清单": 2, "对华关税": 2, "芯片法案": 2, "export control": 2,
    # 卖铲子传导补强(2026-06-26):产能 / 先进封装 / 上游电力能源 / 国产替代 / 政策
    "产能": 1, "扩产": 2, "cowos": 2, "soic": 1, "先进制程": 1,
    "特高压": 2, "西电东送": 2, "东数西算": 2, "算力枢纽": 2,
    "可控核聚变": 2, "核聚变": 1, "超导": 1, "氢能": 1,
    "国产替代": 2, "自主可控": 2, "长江存储": 1, "长鑫": 1, "中芯国际": 1,
    # 不收裸"核电"(命中扎波罗热核电站战争新闻=风险罩噪音)、不收裸"十五五"(跨全行业)
    # 繁体变体(DIGITIMES 涨价大追踪是繁体源,与上面简体键互补)
    "漲價": 2, "缺貨": 2, "記憶體": 2, "封測": 2, "製程": 1, "晶圓": 1,
    "產能": 1, "半導體": 1, "光纖": 1, "光模組": 2, "儲存": 2, "先進封裝": 2,
    "調漲": 2, "漲幅": 1, "pcb": 1, "載板": 2,
}
THEME_BOOST_CAP = 4.0  # 主线加权封顶，避免堆砌关键词刷分

# 宽口径「中国/中国企业相关」关键词(配合 _all_text 的 lower；英文键小写)。可调常量。
CN_KEYWORDS = [
    # 国家/宏观/政策/货币
    "中国", "中企", "中资", "国务院", "发改委", "工信部", "央行", "人民银行", "证监会",
    "银保监", "金融监管总局", "财政部", "商务部", "人民币", "离岸", "在岸", "国常会",
    "政治局", "一带一路", "内需", "内地", "大陆", "中美", "对华", "北向", "南向", "沪深港通",
    # 市场
    "a股", "港股", "中概", "沪指", "深成指", "上证", "深证", "创业板", "科创板", "北交所", "沪深",
    # 大市值中国企业(财经语境)
    "阿里", "腾讯", "字节", "拼多多", "美团", "京东", "百度", "网易", "快手", "小米",
    "比亚迪", "宁德时代", "华为", "中芯", "小鹏", "蔚来", "理想", "茅台", "五粮液",
    "隆基", "阳光电源", "长江存储", "海康", "立讯", "中石油", "中石化", "中海油",
    "国家电网", "中国移动", "中国联通", "中国电信", "中远海控", "顺丰",
    "工商银行", "建设银行", "农业银行", "中国银行", "招商银行",
    # 英文(小写)
    "china", "chinese", "pboc", "yuan", "renminbi", "csrc", "hong kong",
    "alibaba", "tencent", "byd", "huawei", "baidu", "pinduoduo",
]

# 一线优质信源（编辑部，权重高）
SOURCE_TIER1 = {
    "新浪财经", "华尔街见闻", "金十", "东方财富", "同花顺",
    "Reuters", "Bloomberg", "Bloomberg.com", "CNBC", "WSJ",
    "Financial Times", "MarketWatch", "Yahoo Finance", "The New York Times", "AP News",
    "The Guardian", "联合早报", "CNN", "NPR", "The Washington Post", "Politico", "Axios",
    "Fortune", "财新", "Caixin", "Seeking Alpha", "Investor's Business Daily", "Barron's",
    "DIGITIMES·涨价追踪",
}
# 低质/无关源（剔除）
JUNK_SOURCES = {
    "CaptainAltcoin", "Crypto Briefing", "CryptoPotato", "Cryptonews.net", "MEXC",
    "RS Web Solutions", "sekbernews.id", "HarianBasis.co", "MyJoyOnline", "Dailyhunt",
    "AOL.com", "Newser", "Kavout", "IndexBox", "Discovery Alert", "Analytics Insight",
    "Tech Times", "StartupHub.ai", "Mashable", "Gizmodo", "PCMag", "TechSpot",
    "CEOWORLD magazine", "F4W/WON", "Sportscar365", "The Hollywood Reporter",
    "ABC7 Los Angeles", "ABC13 Houston", "KOMO", "Space", "Phys.org", "Gothamist",
    "The Urbanist",
}
# 噪音话题（命中且无重点实体则剔除）
NOISE_KW = [
    "prime day", "hollywood", "box office", "wwe", "taylor swift", "recipe", "celebrity",
    "trailer", "video game", "gaming", "red carpet", "娱乐", "电影", "食谱", "菜谱",
    "球星", "综艺", "明星",
]
# 按用户要求屏蔽的信源（含 Google News 转手的同名条目）
BLOCKED_SOURCES = {"新浪财经", "新浪网", "同花顺", "同花顺财经"}


def _classify(text):
    low = text.lower()
    cats = [c for c, ks in CAT_KEYWORDS.items() if any(k.lower() in low for k in ks)]
    markets = [m for m, ks in MARKET_KEYWORDS.items() if any(k.lower() in low for k in ks)]
    channels = ["finance"]
    if any(k.lower() in low for k in AI_KEYWORDS):
        channels.append("ai")
    return cats, markets, channels


def _hash_id(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _norm_title(t):
    return re.sub(r"\s+", "", t.lower())[:40]


def _http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- 免费翻译（Google gtx，无需 key，需 VPN）+ 本地缓存 ----------
_TRANS_CACHE = None
_trans_budget = 0


def _load_trans():
    global _TRANS_CACHE
    if _TRANS_CACHE is None:
        try:
            with open(TRANS_FILE, encoding="utf-8") as f:
                _TRANS_CACHE = json.load(f)
        except Exception:
            _TRANS_CACHE = {}
    return _TRANS_CACHE


def _save_trans():
    if _TRANS_CACHE is None:
        return
    try:
        os.makedirs(os.path.dirname(TRANS_FILE), exist_ok=True)
        with open(TRANS_FILE, "w", encoding="utf-8") as f:
            json.dump(_TRANS_CACHE, f, ensure_ascii=False)
    except Exception as e:
        print("[trans] save error:", e)


def translate(text, budget_max=220):
    """英文->中文。命中缓存直接返回；超出本轮预算或失败则原样返回（下轮再补）。
    缓存持久化到 data/translations.json，所以同一标题永远只翻一次。"""
    global _trans_budget
    text = (text or "").strip()
    if not text:
        return text
    cache = _load_trans()
    if text in cache:
        return cache[text]
    if _trans_budget >= budget_max:
        return text
    # TRANSLATE_BACKEND=deepseek：直接走 DeepSeek(跳过 Google,免掉限流/被墙/超时);
    # 不设则默认用 Google 免费翻译。
    if os.environ.get("TRANSLATE_BACKEND") == "deepseek":
        try:
            import llm
            zh = (llm.translate(text) or "").strip()
            if zh:
                cache[text] = zh
                _trans_budget += 1
                return zh
        except Exception as e:
            print("[trans] deepseek error:", e)
        return text   # DeepSeek 失败:原样返回,下轮再试
    try:
        u = ("https://translate.googleapis.com/translate_a/single?client=gtx"
             "&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text))
        d = json.loads(_http_get(u, timeout=10))
        zh = "".join(seg[0] for seg in d[0] if seg and seg[0]).strip()
        time.sleep(0.05)  # 轻微节流，避免触发限频
        if zh:
            cache[text] = zh
            _trans_budget += 1
            return zh
    except Exception as e:
        print("[trans] error:", e)
    return text


def fetch_sina(pages=2):
    items = []
    for page in range(1, pages + 1):
        url = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=%d&page_size=50"
               "&zhibo_id=152&tag_id=0&dire=f&dpc=1" % page)
        try:
            raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn/7x24/"})
            d = json.loads(raw)
            lst = d["result"]["data"]["feed"]["list"]
        except Exception as e:
            print("[sina] error:", e)
            continue
        for it in lst:
            text = unescape(re.sub("<[^>]+>", "", it.get("rich_text") or "")).strip()
            if not text:
                continue
            m = re.match(r"^【(.+?)】(.*)$", text, re.S)
            if m:
                title = m.group(1).strip()
                summary = m.group(2).strip() or title
            else:
                title = text[:34] + ("…" if len(text) > 34 else "")
                summary = text
            try:
                dt = datetime.strptime(it["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            except Exception:
                continue
            cats, markets, channels = _classify(text)
            tags = [t.get("name") for t in (it.get("tag") or []) if t.get("name")]
            items.append({
                "id": "sina" + str(it.get("id")),
                "published_at": dt.isoformat(),
                "title_zh": title, "summary_zh": summary[:200], "body_excerpt": summary[:400],
                "source": "新浪财经",
                "source_url": it.get("docurl") or "https://finance.sina.com.cn/7x24/",
                "lang": "zh", "channels": channels, "categories": cats, "tags": tags,
                "entities": [], "markets": markets, "heat": "", "read_count": None,
                "verified": "confirmed", "selected": bool(cats),
                "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
            })
    return items


RSS_FEEDS = [
    # 国际源（多数需 VPN）。抓不到会自动跳过。
    ("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", "Google News"),
    ("https://news.google.com/rss/search?q=stock%20market%20OR%20oil%20OR%20Federal%20Reserve%20OR%20gold%20when:1d&hl=en-US&gl=US&ceid=US:en", "Google News"),
    ("https://www.cnbc.com/id/20910258/device/rss/rss.html", "CNBC"),          # Markets
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),         # Top News
    ("https://finance.yahoo.com/news/rssindex", "Yahoo Finance"),
    ("http://feeds.marketwatch.com/marketwatch/topstories/", "MarketWatch"),
    ("http://feeds.marketwatch.com/marketwatch/realtimeheadlines/", "MarketWatch"),
    # 英伟达专门信源（保证覆盖；英文会被翻译，中文直接用）
    ("https://news.google.com/rss/search?q=Nvidia%20OR%20NVDA%20when%3A2d&hl=en-US&gl=US&ceid=US:en", "Google News"),
    ("https://news.google.com/rss/search?q=%E8%8B%B1%E4%BC%9F%E8%BE%BE%20when%3A2d&hl=zh-CN&gl=CN&ceid=CN:zh", "Google News"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US", "Yahoo Finance"),
]


def _gnews_cn(query):
    """Google News 中文搜索 RSS（自动 URL 编码，带 when:2d 时间窗）。需 VPN，抓不到自动跳过。"""
    q = urllib.parse.quote(query + " when:2d")
    return "https://news.google.com/rss/search?q=%s&hl=zh-CN&gl=CN&ceid=CN:zh" % q


# 用户优先主线的定向抓取（补强弱项）。国际定向兜底；中文源命中主要靠 _score 的 THEME_KW 加权。
PRIORITY_QUERIES = [
    "涨价 OR 缺货 OR 紧缺 OR 提价",            # 原材料稀缺 / 涨价
    "HBM OR 存储芯片 OR 光模块 OR 先进封装 OR 石英砂",  # 半导体加工 / 材料
    "光伏 OR 储能 OR 固态电池",                # 新能源
    "数据中心 OR 算力 OR 液冷",                # 数据中心
    "出口管制 OR 对华关税 OR 实体清单",        # 境外对华政策
]
RSS_FEEDS += [(_gnews_cn(q), "Google News") for q in PRIORITY_QUERIES]

# 实体识别（关键词 -> 标的），从 watchlist.json 加载你的自选股；命中即打实体。
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")


def _load_entity_map():
    default = [(["英伟达", "nvidia", "nvda", "黄仁勋", "jensen huang"],
                {"name": "英伟达", "code": "NVDA", "market": "us"})]
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            wl = json.load(f)
        em = []
        for e in wl.get("entities", []):
            if not e.get("name"):
                continue
            kws = [k.lower() for k in (e.get("keywords") or [e["name"]])]
            em.append((kws, {"name": e["name"], "code": e.get("code"), "market": e.get("market")}))
        return em or default
    except Exception:
        return default


ENTITY_MAP = _load_entity_map()
WATCHLIST_NAMES = {ent["name"] for _, ent in ENTITY_MAP}  # 自选标的名集合（情绪加分用）


def _tag_entities(it):
    text = " ".join([
        it.get("title_zh", ""), it.get("summary_zh", ""),
        it.get("body_excerpt", ""), it.get("title_orig", ""),
    ]).lower()
    ents = it.get("entities") or []
    names = {e.get("name") for e in ents}
    for kws, ent in ENTITY_MAP:
        if ent["name"] not in names and any(k in text for k in kws):
            ents.append(ent)
            names.add(ent["name"])
    it["entities"] = ents
    return it

# 华尔街见闻 lives（中文，无需 VPN）。频道 -> 该频道固定市场标签。
WSCN_CHANNELS = [
    ("global-channel", []),
    ("a-stock-channel", ["a-share"]),
    ("us-stock-channel", ["us"]),
    ("hk-stock-channel", ["hk"]),
    ("forex-channel", ["fx"]),
    ("commodity-channel", []),
]


def fetch_wscn(limit=30):
    items = []
    for channel, ch_markets in WSCN_CHANNELS:
        url = ("https://api-prod.wallstreetcn.com/apiv1/content/lives?channel=%s&limit=%d"
               % (channel, limit))
        try:
            raw = _http_get(url)
            d = json.loads(raw)
            lst = d["data"]["items"]
        except Exception as e:
            print("[wscn] error %s: %s" % (channel, e))
            continue
        for it in lst:
            text = (it.get("content_text") or "").strip()
            if not text:
                continue
            title = (it.get("title") or "").strip()
            if not title:
                m = re.match(r"^【(.+?)】(.*)$", text, re.S)
                if m:
                    title = m.group(1).strip()
                else:
                    title = text[:34] + ("…" if len(text) > 34 else "")
            try:
                dt = datetime.fromtimestamp(int(it.get("display_time")), CST)
            except Exception:
                continue
            cats, markets, channels = _classify(title + " " + text)
            for mk in ch_markets:
                if mk not in markets:
                    markets.append(mk)
            if channel == "a-stock-channel" and "ashare" not in cats:
                cats.append("ashare")
            items.append({
                "id": "wscn" + str(it.get("id")),
                "published_at": dt.isoformat(),
                "title_zh": title, "summary_zh": text[:200], "body_excerpt": text[:400],
                "source": "华尔街见闻",
                "source_url": it.get("uri") or "https://wallstreetcn.com/live/global",
                "lang": "zh", "channels": channels, "categories": cats, "tags": [],
                "entities": [], "markets": markets, "heat": "", "read_count": None,
                "verified": "confirmed", "selected": bool(cats),
                "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
            })
    return items


# ---- 金十数据 7×24（中文，免 VPN，需固定 app 头）----
JIN10_HEADERS = {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"}


def _jin10_entities(it):
    """从金十 remark 的 quotes 抽个股：symbol 如 '000062.SZ' -> {name, code, market}。"""
    ents = []
    for r in (it.get("remark") or []):
        if r.get("type") == "quotes" and r.get("symbol") and r.get("title"):
            sym = str(r["symbol"])
            code = sym.split(".")[0]
            suf = sym.split(".")[1].upper() if "." in sym else ""
            market = {"SZ": "a-share", "SH": "a-share", "BJ": "a-share",
                      "HK": "hk"}.get(suf, "us" if suf else None)
            ents.append({"name": r["title"], "code": code, "market": market})
    return ents


def _ths_entities(it):
    """从同花顺 stock 字段抽个股：6 位纯数字码记为 a-share。"""
    ents = []
    for s in (it.get("stock") or []):
        if s.get("name") and s.get("stockCode"):
            code = str(s["stockCode"])
            market = "a-share" if code.isdigit() and len(code) == 6 else None
            ents.append({"name": s["name"], "code": code, "market": market})
    return ents


def fetch_jin10():
    items = []
    url = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"
    try:
        d = json.loads(_http_get(url, headers=JIN10_HEADERS, timeout=12))
        lst = d.get("data") or []
    except Exception as e:
        print("[jin10] error:", e)
        return items
    for it in lst:
        if it.get("type") != 0:
            continue
        data = it.get("data") or {}
        if data.get("lock"):                       # VIP 锁定项，无正文，跳过
            continue
        content = unescape(re.sub("<[^>]+>", "", data.get("content") or "")).strip()
        if not content:
            continue
        m = re.match(r"^【(.+?)】(.*)$", content, re.S)
        if m:
            title = m.group(1).strip()
            summary = m.group(2).strip() or title
        else:
            title = content[:34] + ("…" if len(content) > 34 else "")
            summary = content
        try:
            dt = datetime.strptime(it["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
        except Exception:
            continue
        cats, markets, channels = _classify(content)
        items.append({
            "id": "jin10" + str(it.get("id")),
            "published_at": dt.isoformat(),
            "title_zh": title, "summary_zh": summary[:200], "body_excerpt": summary[:400],
            "source": "金十", "source_url": "https://www.jin10.com/",
            "lang": "zh", "channels": channels, "categories": cats, "tags": [],
            "entities": _jin10_entities(it), "markets": markets,
            "heat": "热" if it.get("important") else "",
            "read_count": None, "verified": "confirmed", "selected": bool(cats),
            "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
        })
    return items


DT_URL = "https://www.digitimes.com.tw/tech/dt/most.asp?pack=77820&cnlid=1"  # 涨价大追踪专题
DT_TITLE_RE = re.compile(
    r'font-weight:\s*600;?"><a href="(/tech/dt/n/shwnws\.asp\?[^"]*?id=(\d+)[^"]*)">([^<]+)</a>')
DT_DATE_RE = re.compile(r'color:#808080[^>]*>\s*(\d{4}/\d{1,2}/\d{1,2})')
DT_SUMM_RE = re.compile(r'<h7[^>]*title="([^"]*)"')


def _parse_digitimes(html):
    """解析「涨价大追踪」列表页(纯函数,便于测试)。font-weight:600 唯一锁定标题锚,
    避开同 id 缩图锚重复;锚后窗口取日期/摘要。日期无时分,统一 08:00 CST。"""
    items = []
    idx = 0
    for m in DT_TITLE_RE.finditer(html):
        href, aid, title = m.group(1), m.group(2), unescape(m.group(3)).strip()
        if not title:
            continue
        tail = html[m.end():m.end() + 700]
        dm = DT_DATE_RE.search(tail)
        if not dm:
            continue
        try:
            # 列表无具体时分:锚定当天 12:00,再按列表位置(越靠前越新)递减分钟,保序
            dt = datetime.strptime(dm.group(1), "%Y/%m/%d").replace(
                hour=12, tzinfo=CST) - timedelta(minutes=idx)
            idx += 1
        except Exception:
            continue
        sm = DT_SUMM_RE.search(tail)
        summary = (unescape(sm.group(1)).strip() if sm else "") or title
        cats, markets, channels = _classify(title + " " + summary)
        url = "https://www.digitimes.com.tw" + href.replace("&amp;", "&")
        items.append({
            "id": "dt" + aid,
            "published_at": dt.isoformat(),
            "title_zh": title, "summary_zh": summary[:200], "body_excerpt": summary[:400],
            "source": "DIGITIMES·涨价追踪", "source_url": url,
            "lang": "zh", "channels": channels, "categories": cats, "tags": [],
            "entities": [], "markets": markets, "heat": "", "read_count": None,
            "verified": "confirmed", "selected": bool(cats),
            "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
        })
    return items


def fetch_digitimes():
    """DIGITIMES「涨价大追踪」(pack=77820):繁体半导体涨价/缺货/产能列表。
    列表页公开(正文付费,不取);失败返回 []。"""
    try:
        html = _http_get(DT_URL).decode("utf-8", "replace")
    except Exception as e:
        print("[digitimes] error:", e)
        return []
    return _parse_digitimes(html)


def fetch_em_kuaixun():
    items = []
    url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html"
    try:
        raw = _http_get(url).decode("utf-8", "replace")
        m = re.search(r"\{.*\}", raw, re.S)          # 剥 JSONP 外壳 var ajaxResult={...};
        lst = json.loads(m.group(0)).get("LivesList") or []
    except Exception as e:
        print("[em] error:", e)
        return items
    for it in lst:
        title = (it.get("title") or "").strip()
        digest = (it.get("digest") or "").strip()
        if not title and not digest:
            continue
        if not title:
            title = digest[:34] + ("…" if len(digest) > 34 else "")
        try:
            dt = datetime.strptime(it["showtime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
        except Exception:
            continue
        cats, markets, channels = _classify(title + " " + digest)
        items.append({
            "id": "emkx" + str(it.get("id")),
            "published_at": dt.isoformat(),
            "title_zh": title, "summary_zh": (digest or title)[:200],
            "body_excerpt": (digest or title)[:400],
            "source": "东方财富", "source_url": it.get("url_w") or "https://finance.eastmoney.com/",
            "lang": "zh", "channels": channels, "categories": cats, "tags": [],
            "entities": [], "markets": markets, "heat": "", "read_count": None,
            "verified": "confirmed", "selected": bool(cats),
            "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
        })
    return items


def fetch_ths():
    items = []
    url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
    try:
        d = json.loads(_http_get(url))
        lst = (d.get("data") or {}).get("list") or []
    except Exception as e:
        print("[ths] error:", e)
        return items
    for it in lst:
        title = (it.get("title") or "").strip()
        digest = (it.get("digest") or "").strip()
        if not title:
            continue
        try:
            dt = datetime.fromtimestamp(int(it.get("ctime")), CST)
        except Exception:
            continue
        try:
            heat = "热" if int(it.get("import") or 0) >= 3 else ""
        except Exception:
            heat = ""
        cats, markets, channels = _classify(title + " " + digest)
        items.append({
            "id": "ths" + str(it.get("id")),
            "published_at": dt.isoformat(),
            "title_zh": title, "summary_zh": (digest or title)[:200],
            "body_excerpt": (digest or title)[:400],
            "source": "同花顺", "source_url": it.get("url") or "https://news.10jqka.com.cn/",
            "lang": "zh", "channels": channels, "categories": cats, "tags": [],
            "entities": _ths_entities(it), "markets": markets, "heat": heat, "read_count": None,
            "verified": "confirmed", "selected": bool(cats),
            "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
        })
    return items


def fetch_rss(url, default_source):
    items = []
    try:
        raw = _http_get(url)
        root = ET.fromstring(raw)
    except Exception as e:
        print("[rss] error %s: %s" % (default_source, e))
        return items
    for n in root.iterfind(".//item"):
        title = (n.findtext("title") or "").strip()
        link = (n.findtext("link") or "").strip()
        pub = n.findtext("pubDate")
        if not title:
            continue
        src = default_source
        src_el = n.find("source")
        if src_el is not None and (src_el.text or "").strip():
            src = src_el.text.strip()
            if title.endswith(" - " + src):
                title = title[: -(len(src) + 3)].strip()
        elif " - " in title and default_source == "Google News":
            title, src = title.rsplit(" - ", 1)
            title, src = title.strip(), src.strip()
        try:
            dt = parsedate_to_datetime(pub).astimezone(CST)
        except Exception:
            continue
        cats, markets, channels = _classify(title)
        tzh = translate(title)
        items.append({
            "id": _hash_id(link or title),
            "published_at": dt.isoformat(),
            "title_zh": tzh, "summary_zh": tzh, "body_excerpt": title,
            "title_orig": title,
            "source": src, "source_url": link or url,
            "lang": "en", "channels": channels, "categories": cats, "tags": [],
            "entities": [], "markets": markets, "heat": "", "read_count": None,
            "verified": "confirmed", "selected": bool(cats),
            "dedup_group": _hash_id(_norm_title(title)), "score": 0.0,
        })
    return items


# ---------- B. 信源分级 + 降噪 ----------
def _source_tier(src):
    if src in SOURCE_TIER1:
        return 1
    if src in JUNK_SOURCES:
        return 3
    return 2


def _all_text(it):
    return " ".join([
        it.get("title_zh", ""), it.get("summary_zh", ""),
        it.get("body_excerpt", ""), it.get("title_orig", ""),
    ]).lower()


def _tag_mainline(it):
    """命中任一主线词(THEME_KW = 用户 AI 算力链词表)-> it["mainline"]=True。
    供网页「主线」专栏筛选;一套词既调 _score 加权又当筛子。"""
    text = _all_text(it)                       # 已 lower
    it["mainline"] = any(k in text for k in THEME_KW)


def _is_china_related(it):
    """宽口径:跟中国/中国企业相关。CN 关键词 ∪ 实体市场(a-share/hk) ∪ ashare 分类/市场。"""
    text = _all_text(it)                       # 已 lower
    if any(k in text for k in CN_KEYWORDS):
        return True
    if any(e.get("market") in ("a-share", "hk") for e in it.get("entities", [])):
        return True
    if "ashare" in it.get("categories", []):
        return True
    ms = it.get("markets", [])
    return "a-share" in ms or "hk" in ms


_X_PREFIX_RE = re.compile(r"^\s*(breaking|just in|update|快讯|突发|独家|重磅|最新)\s*[:：]\s*", re.I)


def _x_subject(text):
    """从 X 推文原文取「主语行」用于同账号同事件聚合;仅「{主语}:」格式取主语,否则 None。"""
    line = (text or "").split("\n", 1)[0]
    line = _X_PREFIX_RE.sub("", line).strip()
    if line.endswith(":") or line.endswith("："):
        subj = line[:-1].strip()
        return subj.lower() if subj else None
    return None


X_MERGE_WINDOW_MIN = int(os.environ.get("X_MERGE_WINDOW_MIN", "120"))  # 同组时间跨度兜底(分钟)


def _span_minutes(a, b):
    try:
        return abs((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()) / 60.0
    except Exception:
        return 0.0


def _merge_group(src, subj, grp):
    """grp 已按 published_at 升序。首条作 title,其余作 segments。"""
    first = grp[0]
    origs = [g.get("title_orig") or "" for g in grp]
    joined = "\n".join(o for o in origs if o)[:500]
    return {
        "id": _hash_id("xmerge:" + src + subj + (first.get("published_at") or "")),
        "published_at": grp[-1].get("published_at"),          # 最晚,保持新鲜
        "title_zh": first.get("title_zh", ""), "summary_zh": "",
        "segments": [g.get("title_zh", "") for g in grp[1:] if g.get("title_zh")],
        "title_orig": joined, "body_excerpt": joined,
        "source": src, "source_url": first.get("source_url"),
        "lang": first.get("lang", "en"), "verified": first.get("verified", "confirmed"),
        "channels": first.get("channels") or ["finance"],
        "categories": [], "markets": [], "entities": [], "tags": [],
        "heat": "", "read_count": None,
        "dedup_group": _hash_id(_norm_title(first.get("title_zh", ""))),
        "score": 0.0,
    }


def _merge_x_bursts(items):
    """同 X 账号+同主语+同批次(跨度<=窗口)的 >=2 条 -> 合一条;其余原样。"""
    groups, passthrough = {}, []
    for it in items:
        src = it.get("source") or ""
        subj = _x_subject(it.get("title_orig") or "") if src.startswith("X·") else None
        if subj:
            groups.setdefault((src, subj), []).append(it)
        else:
            passthrough.append(it)
    out = list(passthrough)
    for (src, subj), grp in groups.items():
        if len(grp) < 2:
            out.extend(grp)
            continue
        grp.sort(key=lambda x: x.get("published_at", ""))
        if _span_minutes(grp[0].get("published_at"), grp[-1].get("published_at")) > X_MERGE_WINDOW_MIN:
            out.extend(grp)
            continue
        out.append(_merge_group(src, subj, grp))
    return out


def _is_finance_relevant(it):
    if it.get("categories") or it.get("markets") or it.get("entities"):
        return True
    text = _all_text(it)
    for ks in list(CAT_KEYWORDS.values()) + list(MARKET_KEYWORDS.values()):
        if any(k.lower() in text for k in ks):
            return True
    return False


def _keep(it):
    src = it.get("source", "")
    if src in BLOCKED_SOURCES:        # 用户屏蔽源：任何渠道一律丢
        return False
    if src.startswith("X·"):          # X 白名单 = 人工精选,直通(spec 2026-06-11)
        return True
    if _source_tier(src) == 3:
        return False
    title = (it.get("title_zh", "") + " " + it.get("title_orig", "")).lower()
    if any(n in title for n in NOISE_KW) and not it.get("entities"):
        return False
    if src in ("新浪财经", "华尔街见闻") or _source_tier(src) == 1:
        return True
    if it.get("lang") == "en":       # 英文长尾：非一线源须命中重点标的，否则视为碎片噪音丢弃
        return bool(it.get("entities"))
    return _is_finance_relevant(it)  # 其余中文 tier2 必须财经相关


# ---------- A. 聚类去重（按标题相似度，跨源）----------
_PREFIX_RE = re.compile(r"^(市场资讯|快讯|突发|独家|重磅|最新|消息|盘前|盘后|早报|午评)[:：]?")


def _title_features(it):
    t = (it.get("title_zh") or "").lower()
    t = _PREFIX_RE.sub("", t)
    t = re.sub(r"[\s，。、！？：；（）()\[\]【】\"'’“”·\-—…,.!?:;]", "", t)
    feats = set()
    cjk = re.findall(r"[一-鿿]", t)
    for i in range(len(cjk) - 1):
        feats.add(cjk[i] + cjk[i + 1])          # 中文字符二元组
    for tok in re.findall(r"[a-z0-9]{2,}", t):
        feats.add(tok)                           # 英文/数字词
    return feats


def _merge_entities(a, b):
    """按名称并集合并两组实体（去重时把个股实体并到代表条目，避免代码丢失）。"""
    out = list(a or [])
    names = {e.get("name") for e in out}
    for e in (b or []):
        if e.get("name") and e["name"] not in names:
            out.append(e)
            names.add(e["name"])
    return out


def _cluster(items, threshold=0.5):
    """items 按时间倒序。标题字符二元组 Jaccard >= threshold 且 24h 内 -> 同一事件。"""
    reps = []
    for it in items:
        f = _title_features(it)
        try:
            dt = datetime.fromisoformat(it["published_at"])
        except Exception:
            dt = None
        match = None
        if f:
            for r in reps:
                if dt and r["dt"] and abs((dt - r["dt"]).total_seconds()) > 86400:
                    continue
                inter = len(f & r["f"])
                if not inter:
                    continue
                if inter / len(f | r["f"]) >= threshold:
                    match = r
                    break
        if match is None:
            it["dup_count"] = 1
            it["also_sources"] = [it.get("source", "")]
            reps.append({"f": f, "dt": dt, "rep": it})
        else:
            rep = match["rep"]
            rep["dup_count"] = rep.get("dup_count", 1) + 1
            if it.get("source") and it["source"] not in rep["also_sources"]:
                rep["also_sources"].append(it["source"])
            merged_ents = _merge_entities(rep.get("entities"), it.get("entities"))
            better = (_source_tier(it.get("source", "")) < _source_tier(rep.get("source", "")) or
                      len(it.get("summary_zh", "")) > len(rep.get("summary_zh", "")))
            if better:
                it["dup_count"] = rep["dup_count"]
                it["also_sources"] = rep["also_sources"]
                it["entities"] = merged_ents
                match["rep"] = it
            else:
                rep["entities"] = merged_ents
    return [r["rep"] for r in reps]


# ---------- C. 重要度打分 ----------
def _on_watchlist(it):
    return any(e.get("name") in WATCHLIST_NAMES for e in (it.get("entities") or []))


def _sentiment_boost(it):
    """强方向(利多/利空 且 str=3)且命中自选标的 -> +1.0（保守，接「涨价=景气」框架）。"""
    s = it.get("sentiment") or {}
    if s.get("dir") in ("利多", "利空") and s.get("str") == 3 and _on_watchlist(it):
        return 1.0
    return 0.0


def _score(it):
    text = _all_text(it)
    tier = _source_tier(it.get("source", ""))
    s = {1: 4.0, 2: 2.0, 3: 0.0}[tier]
    for k, w in IMPORTANT_KW.items():
        if k in text:
            s += w
    theme = sum(w for k, w in THEME_KW.items() if k in text)
    s += min(theme, THEME_BOOST_CAP)                    # 用户优先主线加权，封顶
    s += min((it.get("dup_count", 1) - 1) * 1.0, 4.0)  # 多家印证加分，封顶 +4
    if it.get("entities"):
        s += 2.0                                        # 自选股(英伟达等)加权
    if it.get("heat"):
        s += 1.5
    s += _sentiment_boost(it)                           # 强方向+自选标的加权
    return round(s, 2), s >= SELECT_THRESHOLD


# ---------- 热度：跨源印证数 -> 沸/爆/火/热（源自带标记作兜底）----------
HEAT_BY_DUP = [(6, "沸"), (4, "爆"), (3, "火"), (2, "热")]


def _heat_tier(dup_count, base=""):
    """越多家独立报道同一事件 = 越热。单源时退回源自带的热度标记（如金十 important）。"""
    for n, label in HEAT_BY_DUP:
        if dup_count >= n:
            return label
    return base


def fetch_all():
    global _trans_budget
    _trans_budget = 0
    # 信源(2026-06-26 pivot)：主流 = DIGITIMES 涨价大追踪 + X(KOL)。华尔街见闻/东方财富/RSS
    # 的 fetch 函数全部保留但不再调用（「留函数不调用」先例，方便日后再开）。
    # 金十仍抓,但仅作「个股(A股)栏」的喂料：标 stock_only,不进主流/不推送(见下方打分循环)。
    items = fetch_digitimes()
    items.extend(fetch_jin10())
    try:                              # X 信源(KOL,可选,无 key 自动跳过)
        import xfetch
        items.extend(xfetch.fetch_x())
    except Exception as e:
        print("[xfetch] skipped:", e)
    _save_trans()
    items = _merge_x_bursts(items)        # 同 X 账号同事件连发 -> 合并成一条
    for it in items:
        _tag_entities(it)
    items = [it for it in items if _keep(it)]            # B 降噪
    items.sort(key=lambda x: x["published_at"], reverse=True)
    reps = _cluster(items)                               # A 去重聚类
    try:                                                 # LLM 增强（可选，自动跳过）
        import llm
        # 金十只喂「个股(A股)栏」,用不到增强(情绪/重要度)-> 跳过,省 DeepSeek 约 2/3
        n = llm.enrich([it for it in reps if it.get("source") != "金十"])
        if n:
            print("[llm] enriched %d new items" % n)
    except Exception as e:
        print("[llm] skipped:", e)
    out = []
    for it in reps:                                      # C 打分 + 精选判定
        if it.get("llm_fin") is False:                   # LLM 判定非财经 -> 丢弃
            continue
        if it.get("llm_importance") is not None:
            imp = float(it["llm_importance"])
            s = imp + min((it.get("dup_count", 1) - 1) * 0.5, 3.0)
            if it.get("entities"):
                s += 1.0
            s += _sentiment_boost(it)                    # 强方向+自选标的加权
            it["score"], it["selected"] = round(s, 2), imp >= 6
        else:
            it["score"], it["selected"] = _score(it)
        if it.get("source") == "金十":
            # 金十只喂「个股(A股)栏」:强制不选中 -> 不进精选/主线、不推 Telegram;
            # server.refresh 据 source 派生 stock_only,filter_items 只在个股视图放行。
            it["selected"] = False
        it["heat"] = _heat_tier(it.get("dup_count", 1), it.get("heat") or "")
        out.append(it)
    out.sort(key=lambda x: x["published_at"], reverse=True)
    return out


if __name__ == "__main__":
    data = fetch_all()
    print("fetched %d items" % len(data))
    from collections import Counter
    print("by source:", dict(Counter(x["source"] for x in data)))
    for it in data[:5]:
        print("---", it["published_at"], "|", it["source"], "|", it["title_zh"][:50])
