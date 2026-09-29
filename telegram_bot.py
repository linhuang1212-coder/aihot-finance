#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIHOT 金融板块 · Telegram 机器人（长轮询 + 自动推送，零依赖）
- 命令式查询：/hot /breaking /daily /分类 /市场 /nvidia /search
- 自动推送：有新消息就主动发给订阅者（去重）；英伟达消息优先置顶、即使未入精选也推。
- 数据来自本机 server.py 的公开 API。token 从环境变量或 bot_config.json 读取。
运行：python telegram_bot.py   （需 server.py 已在跑，且 VPN 可达 api.telegram.org）
"""

import json
import os
import re
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime

import atomicio
import market

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "bot_state.json")
PUSH_INTERVAL = int(os.environ.get("PUSH_INTERVAL", "60"))   # 推送轮询秒数
PUSH_MAX = int(os.environ.get("PUSH_MAX", "8"))               # 每轮最多推几条
CARD_LIMIT = int(os.environ.get("CARD_LIMIT", "6"))          # 命令查询(/hot 等)逐条卡片上限
WATCH_ENTITY = "英伟达"                                        # 重点关注标的
PROP_IMP_MIN = int(os.environ.get("PROP_IMP_MIN", "7"))      # 发酵提醒的重要度门槛
PROP_ALERTED_CAP = 2000                                       # 已提醒记账上限(防状态膨胀)


def _watchlist_names():
    try:
        with open(os.path.join(BASE, "watchlist.json"), encoding="utf-8") as f:
            return {e.get("name") for e in json.load(f).get("entities", []) if e.get("name")}
    except Exception:
        return set()


WATCHLIST_NAMES = _watchlist_names()


def _load_conf():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    server = os.environ.get("AIHOT_SERVER")
    try:
        with open(os.path.join(BASE, "bot_config.json"), encoding="utf-8") as f:
            c = json.load(f)
        token = token or c.get("token")
        server = server or c.get("server")
    except Exception:
        pass
    if not token:
        raise SystemExit("缺少 token：设置 TELEGRAM_BOT_TOKEN 或填好 bot_config.json")
    return token, (server or "http://localhost:8910")


TOKEN, SERVER = _load_conf()
API = "https://api.telegram.org/bot%s/" % TOKEN

CATS = {"macro": "宏观·政策", "commodity": "大宗·能源", "equity": "股指·汇率",
        "sector": "行业·公司", "geo": "地缘·风险", "ashare": "A股·公告"}
MARKET_CMD = {"oil": "oil", "gold": "gold", "astock": "a-share", "us": "us",
              "hk": "hk", "fx": "fx"}

HELP = (
    "<b>AIHOT 金融机器人</b>\n"
    "实时财经新闻 + 自动推送。\n\n"
    "🔔 <b>自动推送已开启</b>：有新消息会直接发给你；英伟达消息优先。\n"
    "/stop 关闭推送 · /subscribe 重新开启\n\n"
    "/hot 精选  /breaking 突发  /all 全量\n"
    "/daily 金融日报\n"
    "/quote 自选股实时报价\n"
    "/nvidia 英伟达（报价+新闻）\n"
    "/serenity 标的 深度分析（产业链/机构视角）\n"
    "分类：/macro /commodity /equity /sector /geo /ashare\n"
    "市场：/oil /gold /astock /us /hk /fx\n"
    "/search 关键词（或直接发关键词搜索）\n\n"
    "⚠️ 来源真实 ≠ 内容已证实；不构成投资建议。"
)

# ---------------- state ----------------
_LOCK = threading.Lock()


def _load_state():
    # 读坏(半截文件)时改名留底并回退到上一份 .prev——原来直接当空表,订阅者清空、推送悄悄停
    s = atomicio.read_json(STATE_FILE, None, try_prev=True)
    if s is None and os.path.exists(STATE_FILE):
        # 文件在但读不出来(被占用等),.prev 也不行:宁可退出让 run.py 重启重读,
        # 也不能带着空订阅列表跑起来——下一次保存就会把真实订阅者覆盖掉
        raise SystemExit("bot_state.json 暂时读不了,退出等守护进程重启")
    if not isinstance(s, dict):
        s = {}
    s.setdefault("subscribers", [])
    s.setdefault("seen", [])
    s.setdefault("seen_groups", [])
    s.setdefault("baseline", False)
    s.setdefault("prop_alerted", {})    # dedup_group -> 已提醒到的传播档位
    return s


STATE = _load_state()


def _save_state():
    atomicio.write_json(STATE_FILE, STATE, keep_prev=True)


# ---------------- http ----------------
def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "aihot-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tg(method, params, timeout=40):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API + method, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_time(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def fetch_items(params):
    return http_get_json(SERVER + "/api/public/items?" + urllib.parse.urlencode(params)).get("items", [])


def fetch_daily():
    return http_get_json(SERVER + "/api/public/daily?channel=finance")


def is_nvidia(it):
    return any((e.get("name") == WATCH_ENTITY) for e in (it.get("entities") or []))


def _worth_push(it):
    """推送门槛：精选直接推；英伟达必推，但要过重要度门槛（砍掉 1–3 的琐碎/花边/传闻）。
    LLM 还没打分（llm_importance 为空）的英伟达条目维持原行为照推，稳妥不漏真消息。"""
    if it.get("selected"):
        return True
    if is_nvidia(it):
        imp = it.get("llm_importance")
        return imp is None or imp >= 4
    return False


SEEN_CAP = 5000


def _select_fresh(cur, seen_list, groups_list):
    """挑出该推送的新条目，并把已读集合**按插入顺序**裁剪到最近 SEEN_CAP 个。
    旧实现用 set->list 裁剪，顺序随机，会把刚推过的条目裁掉 -> 同一条反复推送，这里修掉。"""
    seen = set(seen_list)
    groups = set(groups_list)
    fresh, new_ids, new_groups = [], [], []
    for it in cur:
        iid = it.get("id")
        if not iid or iid in seen:
            continue
        seen.add(iid)
        new_ids.append(iid)
        g = it.get("dedup_group")
        dup = bool(g and g in groups)
        if g and g not in groups:
            groups.add(g)
            new_groups.append(g)
        if dup:
            continue
        if _worth_push(it):
            fresh.append(it)
    return fresh, (seen_list + new_ids)[-SEEN_CAP:], (groups_list + new_groups)[-SEEN_CAP:]


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


# ---------------- render ----------------
def render_daily(d):
    secs = d.get("sections", [])
    if not secs:
        return "<b>📰 金融日报</b>\n\n（今天暂无足够内容，试试 /hot）"
    out = ["<b>📰 金融日报 · %s</b>" % esc(d.get("date") or "")]
    for s in secs:
        out.append("<b>%s</b>" % esc(s["label"]))
        for it in s["items"][:5]:
            out.append('· <a href="%s">%s</a> — %s' % (
                esc(it.get("source_url") or "#"), esc(it["title_zh"]), esc(it.get("source", ""))))
    return "\n\n".join(out)   # 块间空一行（用户 2026-06-12 要求,与推送卡片排版一致）


def _is_chinese(text):
    t = re.sub(r"https?://\S+|\s+", "", text or "")
    return bool(t) and len(re.findall(r"[一-鿿]", t)) / len(t) >= 0.3


def render_push_item(it):
    """单条独立推送（贴图版结构，用户 2026-06-10 指定）：
        📊 快讯捕捉 | 来源: X        (有热度 -> 末尾 🔥)
        📝 翻译:/标题: <title_zh>    (英文源有译文=翻译, 中文源=标题)
        💡 AI 独家解读: <summary_zh> (与标题相同则整块省略)
        🎯 涉及板块: <tgt 或 实体>   (都无则省略)
        🟢 利空 / 🔴 利多 / ⚪ 关注  (独立一行, A股语境利多红/利空绿; 未证实挂 ⚠️)
        🕐 时间: <MM-DD HH:MM>
        🔤 原文: <title_orig>        (仅英文源)
        🔗 查看详情                  (source_url 链接)
    """
    s = it.get("sentiment") or {}
    title_zh = it.get("title_zh", "")
    orig = (it.get("title_orig") or "").strip()
    # 英文源译过 -> 有原文可展示;原文本身是中文(中文 X 账号,DeepSeek 只是改写/繁转简)不算翻译,
    # 否则卡片会多出一段和标题几乎一样的「🔤原文」
    translated = bool(orig) and orig != title_zh and not _is_chinese(orig)

    head = "📊 <b>快讯捕捉</b> | 来源: " + esc(it.get("source", ""))
    if it.get("heat"):
        head += " 🔥"

    title_line = "📝 <b>%s:</b> %s" % ("翻译" if translated else "标题", esc(title_zh))
    segs = "\n".join("▫️ " + esc(x) for x in (it.get("segments") or []) if x)

    summary = it.get("summary_zh") or ""
    jiedu = ("💡 <b>AI 独家解读:</b>\n" + esc(summary)) if summary and summary != title_zh else ""

    tgt = (s.get("tgt") or "").strip()
    if not tgt:
        tgt = " ".join(e.get("name") for e in (it.get("entities") or []) if e.get("name"))[:60]
    sector = ("🎯 <b>涉及板块:</b> " + esc(tgt)) if tgt else ""

    d = s.get("dir")
    if d == "利多":
        dir_line = "🔴 <b>利多</b>"
    elif d == "利空":
        dir_line = "🟢 <b>利空</b>"
    else:
        dir_line = "⚪ <b>关注</b>"
    if it.get("verified") == "unverified":
        dir_line += " ⚠️未证实"

    time_line = "🕐 <b>时间:</b> " + fmt_time(it.get("published_at", ""))
    yuanwen = ("🔤 <b>原文:</b> " + esc(orig)) if translated else ""
    link = '<a href="%s">🔗 查看详情</a>' % esc(it.get("source_url") or "#")

    parts = [head, title_line, segs, jiedu, sector, dir_line, time_line, yuanwen, link]
    # 块间空一行(用户 2026-06-10 反馈);末尾垫空行+U+2800 盲文空白符拉开相邻卡片
    # 的视觉距离(用户 2026-06-12 反馈)——普通空白/换行会被 Telegram 裁掉,U+2800 不会
    return "\n\n".join(x for x in parts if x) + "\n\n⠀"


def render_quotes():
    if not market.enabled():
        return "未配置行情数据（bot_config.json 的 finnhub_key）。"
    syms = market.watchlist_symbols()
    if not syms:
        return "watchlist.json 里没有美股标的（market=us 且有 code）。"
    lines = ["<b>📊 自选股实时报价</b>"]
    for name, sym in syms:
        q = market.get_quote(sym)
        lines.append(market.fmt_quote(name, q) if q else "%s %s 报价获取失败" % (name, sym))
    return "\n\n".join(lines)   # 块间空一行（用户 2026-06-12 要求）


def fetch_analysis(entity):
    url = SERVER + "/api/analysis?" + urllib.parse.urlencode(
        {"channel": "finance", "entity": entity})
    return http_get_json(url, timeout=120)


def render_analysis(r):
    if r.get("status") != "ok":
        return esc(r.get("message") or "分析暂不可用。")
    head = "<b>🧠 Serenity 视角 · %s</b>" % esc(r.get("entity", ""))
    body = esc(r.get("analysis_md", ""))
    return head + "\n\n" + body


def send(chat_id, text):
    tg("sendMessage", {"chat_id": chat_id, "text": text[:4000],
                       "parse_mode": "HTML", "disable_web_page_preview": "true"})


def _split_text(text, limit=3900):
    """按行边界把长文本切成 <=limit 的块（Telegram 单条上限 4096）。
    正文已转义、仅首块开头有 <b>…</b> 头，按 \\n 切不会切断标签。单段超长则硬切。"""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:                      # 单段就超长 -> 硬切
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + 1 + len(para) > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = (cur + "\n" + para) if cur else para
    if cur:
        chunks.append(cur)
    return chunks


def send_chunked(chat_id, text):
    for chunk in _split_text(text):
        send(chat_id, chunk)


def send_cards(chat_id, title, items, limit=CARD_LIMIT):
    """命令查询(/hot 等)也逐条发卡片：与自动推送同款 render_push_item，每条独立一条消息、不合并。
    先发一行小标题点明这是哪类查询，再逐条发；空结果只发一条占位。"""
    items = items[:limit]
    if not items:
        send(chat_id, "<b>%s</b>\n\n（暂无内容）" % esc(title))
        return
    send(chat_id, "<b>%s</b> · %d 条" % (esc(title), len(items)))
    for it in items:
        try:
            send(chat_id, render_push_item(it))
            time.sleep(0.4)        # 逐条发，避免触发 Telegram 单聊限流
        except Exception as e:
            print("[cards] send error:", e)


# ---------------- subscribe ----------------
def subscribe(chat_id):
    with _LOCK:
        if chat_id not in STATE["subscribers"]:
            STATE["subscribers"].append(chat_id)
            _save_state()
            return True
    return False


def unsubscribe(chat_id):
    with _LOCK:
        if chat_id in STATE["subscribers"]:
            STATE["subscribers"].remove(chat_id)
            _save_state()
            return True
    return False


# ---------------- push loop ----------------
def push_loop():
    # 首次启动：等服务端就绪后把当前存量标记为已读，避免一次性补推。
    # （run.py 同时拉起 server+bot，server 初次抓取需 30-90s，故需重试等待，否则竞态导致补推）
    if not STATE["baseline"]:
        for _ in range(30):
            try:
                cur = fetch_items({"channel": "finance", "mode": "all", "take": "100"})
            except Exception:
                time.sleep(5)
                continue
            with _LOCK:
                STATE["seen"] = [it["id"] for it in cur]
                STATE["seen_groups"] = [it.get("dedup_group") for it in cur if it.get("dedup_group")]
                STATE["baseline"] = True
                _save_state()
            print("[push] baseline set: %d items marked seen" % len(cur))
            break
    while True:
        time.sleep(PUSH_INTERVAL)
        try:
            subs = list(STATE["subscribers"])
            if not subs:
                continue
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
        except Exception as e:
            print("[push] loop error:", e)


# ---------------- commands ----------------
def handle(text, chat_id):
    text = (text or "").strip()
    if not text:
        return
    first = subscribe(chat_id)  # 任何互动即自动订阅
    parts = text.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower() if text.startswith("/") else ""
    arg = text[len(parts[0]):].strip()
    base = {"channel": "finance", "take": "10"}
    try:
        if cmd in ("start", "help"):
            send(chat_id, HELP)
        elif cmd in ("stop", "unsubscribe"):
            unsubscribe(chat_id)
            send(chat_id, "🔕 已关闭自动推送。发 /subscribe 可重新开启。")
        elif cmd == "subscribe":
            send(chat_id, "🔔 自动推送已开启。" if first or True else "")
        elif cmd in ("hot", "selected"):
            send_cards(chat_id, "📈 金融 · 精选", fetch_items({**base, "mode": "selected"}))
        elif cmd == "all":
            send_cards(chat_id, "📈 金融 · 全量", fetch_items({**base, "mode": "all"}))
        elif cmd == "breaking":
            send_cards(chat_id, "⚡ 突发快讯", fetch_items({**base, "mode": "breaking"}))
        elif cmd == "daily":
            send(chat_id, render_daily(fetch_daily()))
        elif cmd == "quote":
            send(chat_id, render_quotes())
        elif cmd in ("serenity", "deep"):
            if not arg:
                send(chat_id, "用法：/serenity 英伟达")
            else:
                send(chat_id, "🔍 正在做深度分析（约半分钟）…")
                send_chunked(chat_id, render_analysis(fetch_analysis(arg)))
        elif cmd in ("nvidia", "nvda"):
            q = market.get_quote("NVDA")
            if q:
                send(chat_id, "<b>" + esc(market.fmt_quote("🟢 英伟达", q)) + "</b>")
            send_cards(chat_id, "🟢 英伟达 · 新闻", fetch_items({**base, "mode": "all", "entity": WATCH_ENTITY}))
        elif cmd in CATS:
            send_cards(chat_id, "📂 " + CATS[cmd], fetch_items({**base, "mode": "all", "category": cmd}))
        elif cmd in MARKET_CMD:
            send_cards(chat_id, "🏷 市场 · " + cmd, fetch_items({**base, "mode": "all", "market": MARKET_CMD[cmd]}))
        elif cmd == "search":
            if not arg:
                send(chat_id, "用法：/search 关键词")
            else:
                send_cards(chat_id, "🔎 搜索：%s" % arg, fetch_items({**base, "mode": "all", "q": arg}))
        elif not text.startswith("/"):
            send_cards(chat_id, "🔎 搜索：%s" % text, fetch_items({**base, "mode": "all", "q": text}))
        else:
            send(chat_id, HELP)
        if first:
            send(chat_id, "🔔 已为你开启自动推送：有新消息会直接发来，英伟达优先。")
    except Exception as e:
        print("[handle] error:", e)
        try:
            send(chat_id, "出错了（可能是本机 server 没在跑或网络问题）。稍后再试。")
        except Exception:
            pass


def main():
    print("Telegram bot starting. server=%s push=%ds" % (SERVER, PUSH_INTERVAL))
    threading.Thread(target=push_loop, daemon=True).start()
    offset = None
    try:
        init = tg("getUpdates", {"timeout": 0})
        if init.get("result"):
            offset = init["result"][-1]["update_id"] + 1
        print("ready. send /start to the bot in Telegram.")
    except Exception as e:
        print("init getUpdates error:", e)
    while True:
        try:
            params = {"timeout": 25}
            if offset:
                params["offset"] = offset
            up = tg("getUpdates", params, timeout=40)
        except Exception as e:
            print("[getUpdates] error:", e)
            time.sleep(3)
            continue
        for u in up.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("channel_post")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            print("recv from %s: %r" % (chat_id, text))
            handle(text, chat_id)


if __name__ == "__main__":
    main()
