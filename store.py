#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
条目持久化（SQLite，标准库零依赖）。
把"每次刷新覆盖 items.json"换成累积入库：跨刷新去重（id + dedup_group）、
近 N 天查询、超期清理。查询逻辑仍在 server 的内存 ITEMS 上跑，本模块只管存取。
"""

import os
import re
import json
import sqlite3
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "data", "news.db")
CST = timezone(timedelta(hours=8))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEDUP_SIM = 0.5          # 标题二元组 Jaccard >= 此值视为同事件（跨刷新去重，与 _cluster 一致）
PROP_WINDOW_MIN = int(os.environ.get("PROP_WINDOW_MIN", "90"))   # 传播统计滚动窗口(分钟)
PROP_LEVELS = [int(x) for x in os.environ.get("PROP_LEVELS", "3,5,7").split(",")]  # LV1/2/3 信源数阈值

_JSON_COLS = ("channels", "categories", "tags", "entities", "markets", "segments")
_COLS = ["id", "published_at", "title_zh", "summary_zh", "body_excerpt",
         "source", "source_url", "lang", "channels", "categories", "tags",
         "entities", "markets", "heat", "read_count", "verified",
         "dedup_group", "score", "selected", "dup_count", "sentiment", "fetched_at",
         "llm_importance", "title_orig", "segments"]
# 后加的列(旧库启动时 ALTER 补上;既有行为 NULL)。2026-09-29 前这三个字段不入库,
# 每轮从库重建工作集就丢了:推送卡「🔤原文」从未出现、X 连发合并的分段看不到、
# bot 的英伟达重要度门槛和发酵提醒门槛永远拿到 None。
_ADDED_COLS = [("sentiment", "TEXT"), ("llm_importance", "REAL"),
               ("title_orig", "TEXT"), ("segments", "TEXT")]


def _conn():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    return sqlite3.connect(DB_FILE)


def init_db():
    con = _conn()
    con.execute("""CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY, published_at TEXT,
        title_zh TEXT, summary_zh TEXT, body_excerpt TEXT,
        source TEXT, source_url TEXT, lang TEXT,
        channels TEXT, categories TEXT, tags TEXT, entities TEXT, markets TEXT,
        heat TEXT, read_count INTEGER, verified TEXT,
        dedup_group TEXT, score REAL, selected INTEGER, dup_count INTEGER,
        sentiment TEXT, fetched_at TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pub ON items(published_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_group ON items(dedup_group)")
    con.execute("""CREATE TABLE IF NOT EXISTS mentions (
        group_key TEXT, source TEXT, title_zh TEXT, ts TEXT,
        PRIMARY KEY (group_key, source))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_mention_ts ON mentions(ts)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    for name, typ in _ADDED_COLS:               # 旧库迁移：补列（既有行为 NULL）
        if name not in cols:
            con.execute("ALTER TABLE items ADD COLUMN %s %s" % (name, typ))
    con.commit()
    con.close()


def _to_row(it, fetched_at):
    row = []
    for c in _COLS:
        if c == "fetched_at":
            row.append(fetched_at)
        elif c == "sentiment":
            s = it.get("sentiment")
            row.append(json.dumps(s, ensure_ascii=False) if s else None)
        elif c == "segments":
            segs = it.get("segments")
            row.append(json.dumps(segs, ensure_ascii=False) if segs else None)
        elif c in _JSON_COLS:
            row.append(json.dumps(it.get(c) or [], ensure_ascii=False))
        elif c == "selected":
            row.append(1 if it.get("selected") else 0)
        else:
            row.append(it.get(c))
    return row


def _from_row(row):
    it = dict(zip(_COLS, row))
    for c in _JSON_COLS:
        try:
            it[c] = json.loads(it[c]) if it[c] else []
        except Exception:
            it[c] = []
    sraw = it.get("sentiment")                  # dict 或缺省（不塞空值，前端据有无判断）
    if sraw:
        try:
            it["sentiment"] = json.loads(sraw)
        except Exception:
            it.pop("sentiment", None)
    else:
        it.pop("sentiment", None)
    it["selected"] = bool(it["selected"])
    it.pop("fetched_at", None)
    for c in ("llm_importance", "title_orig", "segments"):   # 没有就不塞键(下游据有无判断)
        if not it.get(c) and it.get(c) != 0:
            it.pop(c, None)
    return it


def _record_mentions(con, group, it, now):
    """记录信源提及:(group_key, source) 首见才记一行——同源重发/重复抓取天然幂等。
    代表条目带 also_sources(单轮聚类合并的各家)时逐家记,否则记 source 本身。"""
    if not group:
        return
    sources = it.get("also_sources") or [it.get("source") or ""]
    for s in sources:
        if s:
            con.execute(
                "INSERT OR IGNORE INTO mentions (group_key, source, title_zh, ts) "
                "VALUES (?,?,?,?)", (group, s, (it.get("title_zh") or "")[:200], now))


_MISS = object()


def _similar_group(f, feats):
    """在 [(特征, 组)] 里找第一个标题相似的,返回它的组(可能是 None);没有返回 _MISS。"""
    for rf, rg in feats:
        if (len(f & rf) / (len(f | rf) or 1)) >= DEDUP_SIM:
            return rg
    return _MISS


def upsert_items(items):
    """累积入库。返回 (inserted, updated)。跨刷新去重：
    同 id -> 更新可变字段；新 id 但 dedup_group 已存在 -> 跳过；
    与近窗口已存条目标题高度相似（同事件不同源/重发）-> 跳过；全新 -> 插入。
    四条路径都把"信源提及"留痕进 mentions(传播热度信号,见 2026-06-10 spec)。

    隐身源(金十,只喂 A 股个股栏)和可见源各自去重,互不吞(2026-09-29):原来 X 条目撞上
    早先入库的金十就被跳过,整件事从主流视图消失,传播计数也挂在看不见的金十行上
    (近 30 天 252 个多源事件里 165 个如此)。现在跨两边相似只记提及、照常入库:
    - 可见条目撞上金十:入库,并把金十那一簇的提及并到自己的簇(曲线接得上);
    - 金十撞上可见条目:入库(个股栏要用),提及只记到可见条目的簇。"""
    import newsfetch  # 复用标题相似度（懒加载，避免模块级耦合）
    con = _conn()
    existing_ids = set(r[0] for r in con.execute("SELECT id FROM items"))
    # 同组(标题前 40 字相同)去重也分两边:中文 X 账号原样转发金十快讯时两边组号相同
    existing_groups = {False: set(), True: set()}
    for grp, src in con.execute(
            "SELECT dedup_group, source FROM items WHERE dedup_group IS NOT NULL AND dedup_group != ''"):
        existing_groups[src == newsfetch.HIDDEN_SOURCE].add(grp)
    sim_cutoff = (datetime.now(CST) - timedelta(days=2)).isoformat(timespec="seconds")
    # 两边的近窗口分开取:金十占量约 2/3,合在一起 LIMIT 会把可见条目挤出去重窗口
    feats = {False: [], True: []}           # 是否隐身 -> [(标题特征, dedup_group)]
    for hidden, op in ((False, "!="), (True, "=")):
        for title, grp in con.execute(
                "SELECT title_zh, dedup_group FROM items WHERE published_at >= ? AND source %s ? "
                "ORDER BY published_at DESC LIMIT 1500" % op, (sim_cutoff, newsfetch.HIDDEN_SOURCE)):
            f = newsfetch._title_features({"title_zh": title})
            if f:
                feats[hidden].append((f, grp))
    now = datetime.now(CST).isoformat(timespec="seconds")
    placeholders = ",".join("?" * len(_COLS))
    insert_sql = "INSERT INTO items (%s) VALUES (%s)" % (",".join(_COLS), placeholders)
    ins = upd = 0
    for it in items:
        iid = it.get("id")
        if not iid:
            continue
        g = it.get("dedup_group")
        if iid in existing_ids:
            sent = it.get("sentiment")
            # 本轮增强失败/没拿到的字段(空摘要、无情绪、无重要度)不覆盖库里已有的
            con.execute(
                "UPDATE items SET score=?, selected=?, heat=?, "
                "summary_zh=COALESCE(NULLIF(?, ''), summary_zh), dup_count=?, "
                "sentiment=COALESCE(?, sentiment), llm_importance=COALESCE(?, llm_importance) "
                "WHERE id=?",
                (it.get("score"), 1 if it.get("selected") else 0, it.get("heat"),
                 it.get("summary_zh"), it.get("dup_count"),
                 json.dumps(sent, ensure_ascii=False) if sent else None,
                 it.get("llm_importance"), iid))
            _record_mentions(con, g, it, now)   # 簇可能新增了信源(also_sources 增长)
            upd += 1
            continue
        hidden = newsfetch.is_hidden(it)
        if g and g in existing_groups[hidden]:
            _record_mentions(con, g, it, now)
            continue
        f = newsfetch._title_features(it)
        same = cross = _MISS
        if f:
            same = _similar_group(f, feats[hidden])
            if same is _MISS:
                cross = _similar_group(f, feats[not hidden])
        if same is not _MISS:
            _record_mentions(con, same or g, it, now)   # 同一边的重复 -> 归被命中条目的簇,跳过
            continue
        if cross is _MISS and g and g in existing_groups[not hidden]:
            cross = g                                   # 跨边同组:按跨边相似处理
        con.execute(insert_sql, _to_row(it, now))
        if hidden and cross is not _MISS:
            _record_mentions(con, cross, it, now)       # 金十只给可见事件计数
        else:
            if cross is not _MISS and cross and g and cross != g:
                # 可见条目接管金十那一簇的传播记录——先搬旧提及再记本轮的,
                # 否则本轮 also_sources 里的金十会以「现在」抢占首见时间,曲线断开
                con.execute("INSERT OR IGNORE INTO mentions (group_key, source, title_zh, ts) "
                            "SELECT ?, source, title_zh, ts FROM mentions WHERE group_key=?",
                            (g, cross))
            _record_mentions(con, g, it, now)
        existing_ids.add(iid)
        if g:
            existing_groups[hidden].add(g)
        if f:
            feats[hidden].append((f, g))
        ins += 1
    con.commit()
    con.close()
    return ins, upd


def recent_items(days=None):
    """取近 N 天条目（按 published_at 倒序），JSON 列还原成 dict。"""
    days = RETENTION_DAYS if days is None else days
    cutoff = (datetime.now(CST) - timedelta(days=days)).isoformat(timespec="seconds")
    con = _conn()
    rows = con.execute(
        "SELECT %s FROM items WHERE published_at >= ? ORDER BY published_at DESC" % ",".join(_COLS),
        (cutoff,)).fetchall()
    con.close()
    return [_from_row(r) for r in rows]


_CJK_RE = re.compile(r"[一-鿿]")


def repair_candidates(hours=48, limit=30, exclude=()):
    """近 hours 小时内需要补救的 X 条目:标题没翻成中文(不含汉字) 或 没有情绪。
    X 推文只抓一次(水位推进后不会再来),翻译/增强那一轮失败就永远是英文标题、没有情绪——
    这里捞出来给补救轮重试。按时间倒序,最多 limit 条。"""
    cutoff = (datetime.now(CST) - timedelta(hours=hours)).isoformat(timespec="seconds")
    con = _conn()
    rows = con.execute(
        "SELECT %s FROM items WHERE published_at >= ? AND source LIKE 'X·%%' "
        "ORDER BY published_at DESC" % ",".join(_COLS), (cutoff,)).fetchall()
    con.close()
    out = []
    for r in rows:
        it = _from_row(r)
        if it["id"] in exclude:              # 已用完补救次数的先排除,再截 limit(别让它们占名额)
            continue
        if not it.get("sentiment") or not _CJK_RE.search(it.get("title_zh") or ""):
            out.append(it)
            if len(out) >= limit:
                break
    return out


def apply_repairs(items):
    """把补救轮改好的字段写回。返回更新条数。"""
    con = _conn()
    n = 0
    for it in items:
        sent = it.get("sentiment")
        con.execute(
            "UPDATE items SET title_zh=?, summary_zh=?, sentiment=?, llm_importance=?, "
            "categories=?, verified=?, score=?, selected=?, heat=? WHERE id=?",
            (it.get("title_zh"), it.get("summary_zh"),
             json.dumps(sent, ensure_ascii=False) if sent else None, it.get("llm_importance"),
             json.dumps(it.get("categories") or [], ensure_ascii=False), it.get("verified"),
             it.get("score"), 1 if it.get("selected") else 0, it.get("heat"), it.get("id")))
        n += 1
    con.commit()
    con.close()
    return n


def backup(dest_dir, keep=7, day=None):
    """每日快照:用 SQLite 在线备份 API 拷一份 news-YYYY-MM-DD.db(读写并发安全,不会拷到半截),
    只留最近 keep 份。当天已有就跳过,返回 None;成功返回快照路径。
    mentions 表(传播历史)只存在于这一个库里,丢了无法重建。"""
    day = day or datetime.now(CST).date().isoformat()
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "news-%s.db" % day)
    if os.path.exists(path):
        return None
    tmp = path + ".tmp"
    for f in os.listdir(dest_dir):           # 上次中途失败留下的半成品
        if f.startswith("news-") and (f.endswith(".tmp") or f.endswith(".tmp-journal")):
            try:
                os.remove(os.path.join(dest_dir, f))
            except OSError:
                pass
    src = _conn()
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    except Exception:
        dst.close()
        src.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    dst.close()
    src.close()
    os.replace(tmp, path)
    snaps = sorted(f for f in os.listdir(dest_dir) if f.startswith("news-") and f.endswith(".db"))
    for f in snaps[:-keep]:
        try:
            os.remove(os.path.join(dest_dir, f))
        except OSError:
            pass
    return path


def prune(days=None):
    """删除超期条目，返回删除数。"""
    days = RETENTION_DAYS if days is None else days
    cutoff = (datetime.now(CST) - timedelta(days=days)).isoformat(timespec="seconds")
    con = _conn()
    cur = con.execute("DELETE FROM items WHERE published_at < ?", (cutoff,))
    n = cur.rowcount
    con.execute("DELETE FROM mentions WHERE ts < ?", (cutoff,))
    con.commit()
    con.close()
    return n


def _prop_level(n):
    lvl = 0
    for i, th in enumerate(PROP_LEVELS, 1):
        if n >= th:
            lvl = i
    return lvl


def propagation(window_min=None):
    """滚动窗口内各事件簇的首报信源数 -> 传播档位。
    返回 {group_key: {"level": 1-3, "sources": n, "window_min": w}};不足 LV1 的不返回。
    发酵=加速:统计的是窗口内**新加入**的信源,老事件无新跟进则档位自然回落。"""
    w = PROP_WINDOW_MIN if window_min is None else window_min
    cutoff = (datetime.now(CST) - timedelta(minutes=w)).isoformat(timespec="seconds")
    con = _conn()
    rows = con.execute(
        "SELECT group_key, COUNT(DISTINCT source) FROM mentions "
        "WHERE ts >= ? GROUP BY group_key", (cutoff,)).fetchall()
    con.close()
    out = {}
    for g, n in rows:
        lvl = _prop_level(n)
        if lvl:
            out[g] = {"level": lvl, "sources": n, "window_min": w}
    return out


def _sample_steps(steps, first_dt, max_points):
    """把累计阶梯 [(dt, cum)](dt 升序, cum 不减)在 [first, last] 按时间均匀采样到
    <=max_points 个 [rel_min, cum];含首尾,cum 取"该时刻为止"的值。零跨度 -> [[0, 1], [0, n]]。"""
    last_dt = steps[-1][0]
    n = steps[-1][1]
    total_sec = (last_dt - first_dt).total_seconds()
    if total_sec <= 0:
        return [[0, 1], [0, n]]
    k = min(max_points, len(steps))
    if k < 2:
        k = 2
    out, si = [], 0
    for i in range(k):
        t_sec = total_sec * (i / (k - 1))
        t_dt = first_dt + timedelta(seconds=t_sec)
        while si + 1 < len(steps) and steps[si + 1][0] <= t_dt:
            si += 1
        out.append([int(t_sec // 60), steps[si][1]])
    return out


def prop_series(groups=None, max_points=12, min_sources=2):
    """每个事件簇:从 mentions 算"累计不同信源数随时间"的紧凑序列(给网页卡片 sparkline)。
    返回 {group_key: {"pts": [[rel_min, cum], ...], "n": n, "span_min": m,
                      "first_ts": iso, "first_source": s}};仅 n>=min_sources 的返回。
    groups=要算的 group 集合(None=mentions 里全部;server 传 None 避免 IN 变量上限)。"""
    con = _conn()
    if groups is None:
        rows = con.execute(
            "SELECT group_key, source, ts FROM mentions ORDER BY group_key, ts").fetchall()
    else:
        gs = [g for g in groups if g]
        if not gs:
            con.close()
            return {}
        ph = ",".join("?" * len(gs))
        rows = con.execute(
            "SELECT group_key, source, ts FROM mentions WHERE group_key IN (%s) "
            "ORDER BY group_key, ts" % ph, gs).fetchall()
    con.close()
    by_group = {}
    for g, src, ts in rows:
        by_group.setdefault(g, []).append((ts, src))
    out = {}
    for g, seq in by_group.items():
        seen, steps = set(), []
        for ts, src in seq:
            seen.add(src)
            steps.append((datetime.fromisoformat(ts), len(seen)))
        n = len(seen)
        if n < min_sources:
            continue
        first_dt = steps[0][0]
        span_min = max(0, int((steps[-1][0] - first_dt).total_seconds() // 60))
        out[g] = {"pts": _sample_steps(steps, first_dt, max_points), "n": n,
                  "span_min": span_min,
                  "first_ts": first_dt.isoformat(timespec="seconds"),
                  "first_source": seq[0][1]}
    return out


def prop_detail(group):
    """单事件簇逐家信源加入明细(给网页大图)。
    返回 {"group", "n", "first_ts", "last_ts", "points":[{"ts","source","cum"}, ...]}。
    group 不存在/无 mention -> n=0, points=[]。"""
    if not group:
        return {"group": group, "n": 0, "first_ts": None, "last_ts": None, "points": []}
    con = _conn()
    rows = con.execute(
        "SELECT source, ts FROM mentions WHERE group_key=? ORDER BY ts", (group,)).fetchall()
    con.close()
    seen, points = set(), []
    for src, ts in rows:
        seen.add(src)
        points.append({"ts": ts, "source": src, "cum": len(seen)})
    return {"group": group, "n": len(seen),
            "first_ts": points[0]["ts"] if points else None,
            "last_ts": points[-1]["ts"] if points else None,
            "points": points}


def migrate_from_json(path):
    """库为空时把现有 items.json 迁进来（一次性，不丢现有数据）。返回迁入数。"""
    con = _conn()
    count = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    con.close()
    if count > 0:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return 0
    ins, _ = upsert_items(items)
    return ins
