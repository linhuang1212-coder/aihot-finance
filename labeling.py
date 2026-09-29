# -*- coding: utf-8 -*-
"""精选校准用的「要/不要」标注(零依赖)。标注页 public/label.html 调这里的逻辑。

- 访问要带密钥:页面链接用 label.html#key=...(# 后面不会发给服务器,不进 nginx 日志/Referer),
  页面再用请求头 X-Label-Key 调接口。密钥首次用到时生成到 data/label_key.txt(data/ 不入 git)。
  这是服务器上唯一的写接口,经隧道公网可达,所以:密钥用 hmac.compare_digest 比较、请求体限长、
  只允许往 data/labels.jsonl 追加一行、id 必须是工作集里真实存在的条目、每分钟限次。
- 出题按「是否精选 × 是否主线」四格轮流抽(不只抽已推送的),这样才量得出召回率(漏选);
  已标过的不再出现。不给标注人看 AI 分数,免得被带着走。
- 标注文件每行 {"id","label"(1=要 0=不要),"ts", 以及当时的 imp/pv/selected/mainline/source/title_zh 快照}。
  同一条标了多次,评测时以最后一次为准。
"""
import hmac
import json
import os
import random
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE, "data", "label_key.txt")
LABELS_FILE = os.path.join(BASE, "data", "labels.jsonl")
CST = timezone(timedelta(hours=8))
WINDOW_DAYS = 7
RATE_PER_MIN = 120
MAX_BODY = 4096

_lock = threading.Lock()
_key = None
_writes = []                 # 最近一分钟的写入时间戳
_labeled = None              # 已标注 id 集合(懒加载)
_rng = random.Random()


def get_key():
    """读取(没有就生成)标注密钥。"""
    global _key
    if _key:
        return _key
    with _lock:
        if not _key:
            try:
                with open(KEY_FILE, encoding="utf-8-sig") as f:   # 记事本另存会带 BOM
                    _key = f.read().strip()
            except FileNotFoundError:
                _key = ""
            if not _key:
                _key = secrets.token_urlsafe(18)
                os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
                with open(KEY_FILE, "w", encoding="utf-8") as f:
                    f.write(_key)
    return _key


def check_key(given):
    return bool(given) and hmac.compare_digest(str(given).encode("utf-8"), get_key().encode("utf-8"))


def _load_labeled():
    global _labeled
    if _labeled is None:
        s = {}
        try:
            with open(LABELS_FILE, encoding="utf-8", errors="replace") as f:   # 坏字节不能让接口一直 500
                for line in f:
                    try:
                        r = json.loads(line)
                        s[r["id"]] = int(r["label"])
                    except (ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        _labeled = s
    return _labeled


def stats():
    with _lock:
        lab = _load_labeled()
        return {"labeled": len(lab), "keep": sum(1 for v in lab.values() if v)}


def _eligible(items, now=None):
    now = now or datetime.now(CST)
    cutoff = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")
    return [it for it in items
            if it.get("source") != "金十" and (it.get("published_at") or "") >= cutoff
            and "finance" in (it.get("channels") or ["finance"])]


def next_item(items):
    """从近 WINDOW_DAYS 天的可见条目里抽一条还没标过的:四格(精选/非精选 × 主线/非主线)随机选一格再抽。"""
    with _lock:
        lab = _load_labeled()
        pool = [it for it in _eligible(items) if it.get("id") not in lab]
    cells = {}
    for it in pool:
        cells.setdefault((bool(it.get("selected")), bool(it.get("mainline"))), []).append(it)
    if not cells:
        return None
    cell = cells[_rng.choice(sorted(cells))]
    return _rng.choice(cell)


def public_view(it):
    """给标注页看的字段:不含 AI 打分/是否精选(免得带偏判断)。"""
    return {
        "id": it.get("id"), "source": it.get("source"), "published_at": it.get("published_at"),
        "title_zh": it.get("title_zh"), "summary_zh": it.get("summary_zh"),
        "title_orig": it.get("title_orig") or "", "body_excerpt": (it.get("body_excerpt") or "")[:600],
        "segments": it.get("segments") or [], "source_url": it.get("source_url"),
    }


def rate_limited(now=None):
    now = now or time.time()
    with _lock:
        while _writes and now - _writes[0] > 60:
            _writes.pop(0)
        if len(_writes) >= RATE_PER_MIN:
            return True
        _writes.append(now)
        return False


def record(it, label, pv=None):
    """追加一条标注。label: 1=要 0=不要。"""
    row = {
        "id": it["id"], "label": int(label), "ts": datetime.now(CST).isoformat(timespec="seconds"),
        "imp": it.get("llm_importance"), "pv": pv, "selected": bool(it.get("selected")),
        "mainline": bool(it.get("mainline")), "source": it.get("source"),
        "title_zh": (it.get("title_zh") or "")[:120],
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        labeled = _load_labeled()               # 先加载再追加:加载出错就别写,免得「已写入却回 500」
        os.makedirs(os.path.dirname(LABELS_FILE), exist_ok=True)
        with open(LABELS_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        labeled[it["id"]] = int(label)
    return row
