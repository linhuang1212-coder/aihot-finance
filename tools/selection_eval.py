# -*- coding: utf-8 -*-
"""精选校准工具(零依赖):给一批条目用指定 prompt 打分,再拿「要/不要」标注算各门槛的
准确率(P)/召回率(R)/F1,并按随机样本推算每天会推送多少条。

    # 用某个 prompt 给一批条目打分(付费,DeepSeek;已打过的 id 跳过,可断点续跑)
    python tools/selection_eval.py score --prompt prompt_pack.md --items sample.json --out scores.json

    # 标注 vs 分数:各门槛 P/R/F1 + 预计每天推送量 + 错判例子
    python tools/selection_eval.py eval --labels data/labels.jsonl --scores scores.json \\
        [--random-scores random_scores.json --intake-per-day 530]

    # 直接用线上缓存里的分数(只取某版 prompt 打的)
    python tools/selection_eval.py eval --labels data/labels.jsonl --scores cache --pv 1a2b3c4d

items 文件:JSON 数组,每项至少有 id/title_zh,可带 title_orig/body_excerpt。
labels 文件:JSONL,每行 {"id":..., "label": 1 或 0}(标注页写的就是这个格式),或 {id: 0/1} 的 JSON。
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

THRESHOLDS = range(3, 10)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_labels(path):
    if path.endswith(".jsonl"):
        out = {}
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["id"]] = int(r["label"])  # 同一 id 标了多次:以最后一次为准
                except (ValueError, KeyError, TypeError):
                    continue                        # 空行/写断的半行:跳过
        return out
    return {k: int(v) for k, v in _load_json(path).items()}


def load_scores(path, pv=None):
    """{id: imp}。path='cache' 时读线上 llm_cache(可按 pv 筛)。"""
    if path == "cache":
        import llm
        cache = llm._load()
        return {k: v.get("imp") for k, v in cache.items()
                if v.get("imp") is not None and (pv is None or v.get("pv") == pv)}
    d = _load_json(path)
    return {k: (v.get("imp") if isinstance(v, dict) else v) for k, v in d.items()
            if (v.get("imp") if isinstance(v, dict) else v) is not None}


def score(prompt_path, items, out_path, batch=8):
    """用 prompt_path 当系统提示给 items 打分,结果按 id 存到 out_path(已有的跳过)。"""
    import llm
    with open(prompt_path, encoding="utf-8") as f:
        system = f.read()
    done = _load_json(out_path) if os.path.exists(out_path) else {}
    todo = [it for it in items if it["id"] not in done]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        lines = "\n".join("%d. %s" % (j + 1, llm.build_line(it)) for j, it in enumerate(chunk))
        user = ("新闻列表（逐条处理，i 用下面的序号）:\n" + lines +
                '\n\n只输出 JSON 对象 {"items":[{"i","s","c","imp","fin","rumor","dir","tgt","str"}]}。')
        body = json.dumps({
            "model": llm.MODEL, "temperature": 0, "max_tokens": 1500,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        try:
            d = llm._post(body, timeout=90)
            res = json.loads(d["choices"][0]["message"]["content"]).get("items", [])
        except Exception as e:
            print("batch %d failed: %s" % (i // batch, e))
            continue
        by_i = {}
        for r in res:
            try:
                by_i[int(r.get("i"))] = r
            except Exception:
                pass
        for j, it in enumerate(chunk):
            r = by_i.get(j + 1)
            if r:
                done[it["id"]] = {"imp": r.get("imp"), "s": r.get("s"), "dir": r.get("dir"),
                                  "tgt": r.get("tgt"), "fin": r.get("fin")}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)
        print("scored %d/%d" % (len(done), len(items)))
    return done


def metrics(labels, scores, thresholds=THRESHOLDS):
    """每个门槛:选中数/P/R/F1(只算既有标注又有分数的条目)。"""
    ids = [k for k in labels if k in scores]
    pos = sum(labels[k] for k in ids)
    rows = []
    for t in thresholds:
        sel = [k for k in ids if (scores[k] or 0) >= t]
        tp = sum(labels[k] for k in sel)
        p = tp / len(sel) if sel else 0.0
        r = tp / pos if pos else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        rows.append({"t": t, "sel": len(sel), "tp": tp, "p": p, "r": r, "f1": f1})
    return {"n": len(ids), "pos": pos, "rows": rows}


def volume(random_scores, intake_per_day, thresholds=THRESHOLDS):
    """随机样本上各门槛的选中比例 × 每天进来的条数 = 预计每天推送条数。"""
    vals = [v or 0 for v in random_scores.values()]
    return {t: (sum(1 for v in vals if v >= t) / len(vals) * intake_per_day if vals else 0.0)
            for t in thresholds}


def report(labels, scores, items=None, random_scores=None, intake_per_day=None, show=8):
    m = metrics(labels, scores)
    vol = volume(random_scores, intake_per_day) if random_scores and intake_per_day else {}
    out = ["标注 %d 条(要 %d 条),有分数的 %d 条" % (len(labels), m["pos"], m["n"]),
           "门槛  选中  准确率P  召回率R   F1   预计每天推送"]
    for r in m["rows"]:
        v = ("%6.0f" % vol[r["t"]]) if r["t"] in vol else "     -"
        out.append("imp>=%d %4d   %5.0f%%   %5.0f%%   %.2f   %s" % (
            r["t"], r["sel"], r["p"] * 100, r["r"] * 100, r["f1"], v))
    if items:
        best = max(m["rows"], key=lambda r: r["f1"])
        t = best["t"]
        byid = {it["id"]: it for it in items}
        fn = [k for k in labels if labels[k] and k in scores and (scores[k] or 0) < t][:show]
        fp = [k for k in labels if not labels[k] and k in scores and (scores[k] or 0) >= t][:show]
        out.append("\n按 F1 最高的门槛 imp>=%d:" % t)
        out.append("-- 该要却没选上(%d 例):" % len(fn))
        out += ["   imp=%s | %s" % (scores[k], (byid.get(k, {}).get("title_zh") or k)[:60]) for k in fn]
        out.append("-- 不该要却选上了(%d 例):" % len(fp))
        out += ["   imp=%s | %s" % (scores[k], (byid.get(k, {}).get("title_zh") or k)[:60]) for k in fp]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("score")
    a.add_argument("--prompt", required=True)
    a.add_argument("--items", required=True)
    a.add_argument("--out", required=True)
    b = sp.add_parser("eval")
    b.add_argument("--labels", required=True)
    b.add_argument("--scores", required=True)
    b.add_argument("--pv")
    b.add_argument("--items")
    b.add_argument("--random-scores")
    b.add_argument("--intake-per-day", type=float)
    args = ap.parse_args()
    if args.cmd == "score":
        score(args.prompt, _load_json(args.items), args.out)
    else:
        items = _load_json(args.items) if args.items else None
        rnd = load_scores(args.random_scores) if args.random_scores else None
        print(report(load_labels(args.labels), load_scores(args.scores, args.pv), items,
                     rnd, args.intake_per_day))


if __name__ == "__main__":
    main()
