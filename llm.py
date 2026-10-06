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
import re
import json
import urllib.request

import atomicio
import health

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


def prompt_version():
    """当前 prompt 的版本号(内容哈希前 8 位)。写进每条缓存,评测时按版本筛;
    有 pv 的分用新门槛,没有 pv 的(旧 prompt 打的)沿用旧门槛,两套刻度不混。"""
    import hashlib
    return hashlib.sha1(_pack().encode("utf-8")).hexdigest()[:8]


EXCERPT_CHARS = 200


def build_line(it):
    """喂给打分模型的一行:中文标题 + 原文/摘要(最多 EXCERPT_CHARS 字)。
    原来只给标题前 80 字——DIGITIMES「產能吃緊」、推文里的关键数字常在摘要/原文里。
    刻意不给信源名(避免拿名气重复加分)。"""
    title = " ".join((it.get("title_zh") or "").split())[:100]
    ex = it.get("title_orig") or it.get("body_excerpt") or ""
    ex = " ".join(ex.split())[:EXCERPT_CHARS]
    if ex and ex[:40] != title[:40]:
        return "标题:%s | 原文/摘要:%s" % (title, ex)
    return "标题:%s" % (ex if len(ex) > len(title) else title)   # 中文推文:原文就是更长的标题


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
        _cache = atomicio.read_json(CACHE_FILE, {})
        if not isinstance(_cache, dict):
            _cache = {}
        patch = CACHE_FILE + ".patch"            # tools/backfill_selection.py 重打分的结果
        if os.path.exists(patch):
            extra = atomicio.read_json(patch, {})
            if isinstance(extra, dict) and extra:
                _cache.update(extra)
                if atomicio.write_json(CACHE_FILE, _cache):
                    os.replace(patch, patch + ".applied")
                    print("[llm] 合并回补缓存 %d 条" % len(extra))
    return _cache


def _save():
    if _cache is None:
        return
    atomicio.write_json(CACHE_FILE, _cache)     # 原子写:断电/被杀不会留下半截文件


def prune(keep_ids):
    """缓存只留还在工作集里的 id(X 推文只增强一次,出窗口后缓存永远用不到;实测 86% 是这种)。
    返回删掉的条数;有删才落盘。keep_ids 太少(库读失败等)时不动,防误清空。"""
    cache = _load()
    keep = set(keep_ids)
    if len(keep) < 1000:
        return 0
    dead = [k for k in cache if k not in keep]
    for k in dead:
        del cache[k]
    if dead:
        _save()
    return len(dead)


RECENT_MAX = 50              # 判重时给模型看的「近期已推送」最多几条
RECENT_TITLE_CHARS = 70


def _recent_block(recent):
    """【近期已推送】列表(R1 最新)。只用于判重:同一件事被不同账号/不同译法重复报道时,
    模型在 dup 里填被重复那条的编号,这条就不再推送。"""
    if not recent:
        return ""
    lines = ["R%d. %s" % (k + 1, " ".join((r.get("title") or "").split())[:RECENT_TITLE_CHARS])
             for k, r in enumerate(recent[:RECENT_MAX])]
    return "【近期已推送】(只用于判重,不要给它们打分):\n" + "\n".join(lines) + "\n\n"


def _resolve_dup(ref, recent, batch, j):
    """模型返回的 dup 编号(R3 / B2)-> 被重复条目的 id。无效/指向自己/指向后面的 -> None。"""
    m = re.match(r"^\s*([RrBb])\s*(\d+)\s*$", str(ref or ""))
    if not m:
        return None
    n = int(m.group(2)) - 1
    if m.group(1) in "Rr":
        return recent[n]["id"] if 0 <= n < min(len(recent), RECENT_MAX) else None
    return batch[n]["id"] if 0 <= n < j else None


def _call(news_lines, recent_block=""):
    user = (recent_block + "新闻列表（逐条处理，i 用下面的序号）:\n" + news_lines +
            '\n\n只输出 JSON 对象 {"items":[{"i","s","c","imp","fin","rumor","dir","tgt","str","dup"}]}。')
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 1500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _pack()},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    d = _post(body, timeout=60)
    content = d["choices"][0]["message"]["content"]
    return json.loads(content).get("items", [])


def _post(body, timeout):
    """调 DeepSeek 并记健康(402 欠费等连续失败 -> health 告警)。"""
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except Exception as e:
        health.note_error("deepseek", e)
        raise
    health.note_ok("deepseek")
    return d


def translate(text):
    """英文/繁体 -> 简体中文(DeepSeek)。供 newsfetch.translate 在 Google 翻译被墙
    (云端阿里云)时回退。无 key/空文本 -> 返回空串(调用方保持原文)。"""
    if not enabled() or not (text or "").strip():
        return ""
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 400,
        "messages": [
            {"role": "system", "content": "你是专业财经翻译。把用户给的文本准确翻成简体中文,"
                                          "只输出译文本身,不要解释、不加引号。"},
            {"role": "user", "content": text[:500]},
        ],
    }).encode("utf-8")
    d = _post(body, timeout=15)       # 单条翻译 15s 足够;原来 30s,故障时一轮被逐条拖十几分钟
    return (d["choices"][0]["message"]["content"] or "").strip()


def enrich(items, budget=DEFAULT_BUDGET, recent=None, push_imp=None):
    """就地增强 items（去重后的代表条目）。返回实际新处理条数。
    recent: 近期已推送的条目 [{"id","title"}](新的在前),交给模型判重;
    push_imp: 推送线。给了就把本轮里过线且不重复的条目滚动加进 recent,同一轮后面的重复也能认出来。"""
    if not enabled():
        return 0
    recent = list(recent or [])
    cache = _load()
    todo = [it for it in items if it["id"] not in cache][:budget]
    done = fails = bad400 = 0
    any_ok, rejected = False, []
    pv = prompt_version()
    queue = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    while queue:
        batch = queue.pop(0)
        lines = "\n".join("%d. %s" % (j + 1, build_line(it)) for j, it in enumerate(batch))
        seen_recent = list(recent)               # 这一批看到的列表快照(编号要对得上)
        try:
            results = _call(lines, _recent_block(seen_recent))
            fails = bad400 = 0
            any_ok = True
        except Exception as e:
            print("[llm] call error:", e)
            if getattr(e, "code", None) == 400:
                # 400 多半是内容风控拒了批里某一条:拆成单条重送,只丢真正被拒的那条,
                # 别让同批 7 条陪着失败(原来补救轮也总把它们打成同一批,永远补不上)
                if len(batch) > 1:
                    queue[:0] = [[it] for it in batch]
                    continue
                rejected.append(batch[0]["id"])
                bad400 += 1
                if bad400 >= 4:                  # 单条也接连被拒:多半是整体故障,本轮停
                    break
                continue
            fails += 1
            if fails >= 2:                       # 连续失败:本轮放弃,别逐批等超时(补救轮会重试)
                print("[llm] 连续失败,本轮停止增强")
                break
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
                "pv": pv,
            }
            dup = _resolve_dup(r.get("dup"), seen_recent, batch, j)
            if dup:
                cache[it["id"]]["dup"] = dup
            elif push_imp is not None and r.get("fin", True) and (r.get("imp") or 0) >= push_imp:
                recent.insert(0, {"id": it["id"], "title": it.get("title_zh") or ""})
                del recent[RECENT_MAX:]
            done += 1
    if any_ok:
        # 服务本身正常时,单条被拒的记个墓碑,以后主轮/补救轮都不再提交它(整体故障时不记,免得全被标死)
        for k in rejected:
            cache[k] = {"fin": True, "rejected": True}
    if done or (any_ok and rejected):
        _save()                                  # 没新结果就不重写整份缓存(原来每轮都写 21MB)
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
            it["llm_pv"] = r.get("pv")
        it["llm_fin"] = bool(r.get("fin", True))
        if r.get("dup"):                         # 与近期已推送的某条是同一件事 -> 不再推
            it["llm_dup"] = r["dup"]
        if r.get("rumor"):                       # LLM 判定为传闻 -> 标未证实
            it["verified"] = "unverified"
        if r.get("dir"):                         # 利多/利空/中性（缺失则不塞，优雅降级）
            it["sentiment"] = {"dir": r["dir"], "tgt": r.get("tgt") or "",
                               "str": r.get("str") or 0}
    return done
