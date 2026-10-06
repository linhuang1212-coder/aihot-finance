# -*- coding: utf-8 -*-
"""回补:用当前 prompt 给一段时间内的可见条目重新打分(带判重),把精选/重要度/摘要/情绪写回库。

什么时候用:改了推送口径(prompt/门槛)之后,想让改之前那几天的条目也按新口径重新筛一遍。
    python tools/backfill_selection.py --since 2026-09-30T00:33:00+08:00

做法:
- 按天拆成子进程并行(每天内部按时间正序,滚动维护「已推送」列表交给模型判重);
- 每个子进程把新分数写到 data/llm_cache.json.patch.<日期>,不碰运行中 server 的缓存文件;
- 全部跑完合并成 data/llm_cache.json.patch —— **server 下次启动时自动并入**(llm._load),
  所以跑完要重启一次 server;
- 库是边跑边写回的(短事务,和运行中的 server 并存);server 每轮 refresh 从库重建工作集,
  网页「精选」几分钟内就能看到回补结果。
- 不会触发 Telegram 推送:这些条目 bot 早已见过(seen),只影响网页和日报。

付费:DeepSeek,每 8 条一次调用。零依赖。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
CST = timezone(timedelta(hours=8))
CHUNK = 80


def _rows(since, day=None, until=None):
    import store
    import newsfetch
    con = sqlite3.connect("file:%s?mode=ro" % store.DB_FILE, uri=True)
    q = "SELECT %s FROM items WHERE source != ? AND published_at >= ?" % ",".join(store._COLS)
    args = [newsfetch.HIDDEN_SOURCE, since]
    if day:
        q += " AND substr(published_at, 1, 10) = ?"
        args.append(day)
    if until:
        q += " AND published_at < ?"
        args.append(until)
    rows = con.execute(q + " ORDER BY published_at ASC", args).fetchall()
    con.close()
    return [store._from_row(r) for r in rows]


def _apply(items):
    import store
    for attempt in range(8):
        try:
            return store.apply_repairs(items)
        except sqlite3.OperationalError as e:        # 运行中的 server 正在写:等一下再试
            if attempt == 7:
                raise
            time.sleep(1.5)


def run_day(since, day, until=None):
    """子进程:重打分某一天。"""
    import llm
    import newsfetch
    import store
    llm.CACHE_FILE = os.path.join(BASE, "data", "llm_cache.json.patch.%s" % day)
    llm._cache = None
    pv = llm.prompt_version()
    items = _rows(since, day, until)
    recent, n_sel_before, n_sel_after, n_dup, n_done = [], 0, 0, 0, 0
    for i in range(0, len(items), CHUNK):
        chunk = items[i:i + CHUNK]
        before = {it["id"]: bool(it.get("selected")) for it in chunk}
        for it in chunk:
            it.pop("llm_dup", None)
        llm.enrich(chunk, budget=10 ** 6, recent=recent, push_imp=newsfetch.SELECT_IMP)
        done = [it for it in chunk if it.get("llm_pv") == pv]       # 这次确实打到分的
        for it in done:
            newsfetch._finalize(it)
            if it.get("llm_fin") is False:
                it["selected"] = False
            n_sel_before += before[it["id"]]
            n_sel_after += bool(it["selected"])
            n_dup += bool(it.get("llm_dup"))
            if it["selected"]:
                recent.insert(0, {"id": it["id"], "title": it.get("title_zh") or ""})
        del recent[llm.RECENT_MAX:]
        if done:
            _apply(done)
        n_done += len(done)
        print("[%s] %d/%d 已重打分 | 精选 %d -> %d | 判重 %d" % (
            day, n_done, len(items), n_sel_before, n_sel_after, n_dup), flush=True)
    print("[%s] DONE total=%d scored=%d selected %d -> %d dup=%d" % (
        day, len(items), n_done, n_sel_before, n_sel_after, n_dup), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO 时间,如 2026-09-30T00:33:00+08:00")
    ap.add_argument("--until", help="ISO 时间(不含),默认到现在")
    ap.add_argument("--day", help="内部用:只跑这一天")
    args = ap.parse_args()
    if args.day:
        return run_day(args.since, args.day, args.until)
    days = sorted({it["published_at"][:10] for it in _rows(args.since, until=args.until)})
    print("要回补的日期:", days, flush=True)
    procs = []
    for d in days:
        cmd = [sys.executable, "-u", os.path.abspath(__file__), "--since", args.since, "--day", d]
        if args.until:
            cmd += ["--until", args.until]
        procs.append((d, subprocess.Popen(cmd, cwd=BASE, env=dict(os.environ, PYTHONIOENCODING="utf-8"))))
    failed = [d for d, p in procs if p.wait() != 0]
    merged = {}
    for d in days:
        p = os.path.join(BASE, "data", "llm_cache.json.patch.%s" % d)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                merged.update(json.load(f))
            os.remove(p)
    out = os.path.join(BASE, "data", "llm_cache.json.patch")
    if os.path.exists(out):                          # 上次的补丁还没被 server 并入:叠加
        with open(out, encoding="utf-8") as f:
            old = json.load(f)
        old.update(merged)
        merged = old
    import atomicio
    atomicio.write_json(out, merged)
    print("合并补丁缓存 %d 条 -> %s(重启 server 后并入)" % (len(merged), out))
    if failed:
        print("这些日期的子进程失败了:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
