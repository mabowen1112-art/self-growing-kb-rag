# eval_kb.py — RAG 评测集运行器（评测驱动调优：基线 → 改一个变量 → 对比）
#
# 用法：
#   uv run python eval_kb.py                        # 跑全部已配置的题，结果存 eval_results/
#   uv run python eval_kb.py --limit 3              # 冒烟：只跑前 3 题
#   uv run python eval_kb.py --ids q05,q06          # 只跑指定题
#   uv run python eval_kb.py --compare eval_results/eval_xxx.json          # 与最近一次对比
#   uv run python eval_kb.py --compare 旧.json,新.json                      # 指定两份对比
#
# 说明：
#   - 每题真实调 embedding + rerank + DeepSeek（不走 qa_cache，保证两次对比公平）
#   - 一个要点 = 一组 "关键词|同义词"，回答里出现任一关键词即算命中（不区分大小写）
#   - 单题失败不中断整轮（记录 error 继续跑）
#   - 判分口径：综合通过 = 检索命中 + 来源标注 + 回答非空

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
QFILE = os.path.join(HERE, "eval_questions.yaml")
OUTDIR = os.path.join(HERE, "eval_results")


def load_questions():
    with open(QFILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict):
        return data.get("questions", []) or []
    return data or []


def configured(item):
    return bool(str(item.get("q") or "").strip()) and bool(str(item.get("source") or "").strip())


def check_points(answer_text, points):
    """→ [{'point': 原文, 'ok': bool, 'missing': [缺的关键词]}]"""
    t = (answer_text or "").lower()
    out = []
    for p in points or []:
        kws = [k.strip() for k in str(p).split("|") if k.strip()]
        hit = any(k.lower() in t for k in kws)
        out.append({"point": str(p), "ok": hit, "missing": [] if hit else kws[:3]})
    return out


def run_questions(items):
    from ask_kb import retrieve, answer  # 懒加载：--compare 模式不需要 API 依赖

    results = []
    t_all = time.time()
    for i, item in enumerate(items, 1):
        qid, q, expect = item.get("id", f"q{i:02d}"), item["q"], item["source"]
        rec = {"id": qid, "q": q, "source_expect": expect}
        t0 = time.time()
        try:
            hits = retrieve(q)
            rec["hit_ok"] = any(expect in h[1]["source"] for h in hits) if hits else False
            if hits:
                text, sources = answer(q, hits)
            else:
                text, sources = "", []
            rec["src_ok"] = any(expect in s for s in sources)
            rec["passed"] = bool(text) and rec["hit_ok"] and rec["src_ok"]
            pts = check_points(text, item.get("points"))
            rec["points"] = pts
            rec["pts_hit"] = sum(1 for x in pts if x["ok"])
            rec["pts_total"] = len(pts)
            rec["sources"] = sources
            rec["answer_head"] = (text or "")[:160]
        except Exception as e:
            rec.update({"hit_ok": False, "src_ok": False, "passed": False,
                        "points": [], "pts_hit": 0, "pts_total": 0,
                        "sources": [], "answer_head": "", "error": str(e)})
        rec["elapsed_s"] = round(time.time() - t0, 1)
        results.append(rec)

        pts_s = f"要点 {rec['pts_hit']}/{rec['pts_total']}" if rec["pts_total"] else "要点 未配置"
        line = (f"[{i}/{len(items)}] {qid} {q} → "
                f"检索{'✅' if rec['hit_ok'] else '❌'} 来源{'✅' if rec['src_ok'] else '❌'} {pts_s}（{rec['elapsed_s']}s）")
        if rec.get("error"):
            line += f"  ⚠️ {rec['error'][:80]}"
        print(line)
        if rec["pts_total"] and rec["pts_hit"] < rec["pts_total"]:
            miss = "、".join(x["point"].split("|")[0] for x in rec["points"] if not x["ok"])
            print(f"      ↳ 未覆盖要点：{miss}")
    return results, round(time.time() - t_all, 1)


def find_latest(exclude):
    if not os.path.isdir(OUTDIR):
        return None
    files = sorted(f for f in os.listdir(OUTDIR) if f.startswith("eval_") and f.endswith(".json"))
    files = [os.path.join(OUTDIR, f) for f in files
             if os.path.abspath(os.path.join(OUTDIR, f)) != os.path.abspath(exclude)]
    return files[-1] if files else None


def compare(a_path, b_path):
    a = json.load(open(a_path, encoding="utf-8"))
    b = json.load(open(b_path, encoding="utf-8"))
    print(f"对比：\n  旧 {a_path}（{a.get('ts')}）\n  新 {b_path}（{b.get('ts')}）\n")
    bmap = {r["id"]: r for r in b["results"]}
    for r in a["results"]:
        n = bmap.get(r["id"])
        if not n:
            continue
        pts_o = f"{r['pts_hit']}/{r['pts_total']}" if r["pts_total"] else "—"
        pts_n = f"{n['pts_hit']}/{n['pts_total']}" if n["pts_total"] else "—"
        flag = ""
        if n["pts_hit"] > r["pts_hit"] or (n["passed"] and not r["passed"]):
            flag = "  ⬆ 变好"
        elif n["pts_hit"] < r["pts_hit"] or (r["passed"] and not n["passed"]):
            flag = "  ⬇ 变差"
        print(f"  {r['id']:4} {r['q'][:26]:28} 综合 {'✅' if r['passed'] else '❌'}→{'✅' if n['passed'] else '❌'}  "
              f"要点 {pts_o}→{pts_n}{flag}")
    sa, sb = a["summary"], b["summary"]
    print(f"\n  综合通过：{sa['source_pass']}/{sa['ran']} → {sb['source_pass']}/{sb['ran']}")
    print(f"  要点覆盖：{sa['pts_hit']}/{sa['pts_total']} → {sb['pts_hit']}/{sb['pts_total']}")


def main():
    ap = argparse.ArgumentParser(description="rag_kb 评测集运行器")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟用）")
    ap.add_argument("--ids", type=str, default="", help="只跑指定题，如 q05,q06")
    ap.add_argument("--compare", type=str, default="", help="对比模式：单个路径或 旧,新")
    args = ap.parse_args()

    if args.compare:
        parts = [p.strip() for p in args.compare.split(",") if p.strip()]
        if len(parts) == 1:
            latest = find_latest(parts[0])
            if not latest:
                print("❌ 没找到另一份结果（eval_results/ 里至少要有两份）")
                return 1
            parts.append(latest)
        compare(parts[0], parts[1])
        return 0

    qs = load_questions()
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        qs = [x for x in qs if x.get("id") in want]
    if args.limit:
        qs = qs[: args.limit]
    todo = [x for x in qs if configured(x)]
    skipped = len(qs) - len(todo)

    print("=" * 52)
    print(f"📋 评测集 v1 · 本次题目 {len(qs)} 道（可跑 {len(todo)}，跳过未配置 {skipped}）")
    print("=" * 52)
    if not todo:
        print("⚠️ 没有可跑的题：先到 eval_questions.yaml 把 q / source 填上")
        return 1

    results, elapsed = run_questions(todo)

    n = len(results)
    src_pass = sum(1 for r in results if r["passed"])
    pts_hit = sum(r["pts_hit"] for r in results)
    pts_total = sum(r["pts_total"] for r in results)
    summary = {
        "ran": n,
        "source_pass": src_pass,
        "source_pass_pct": round(100 * src_pass / n, 1) if n else 0,
        "pts_hit": pts_hit,
        "pts_total": pts_total,
        "pts_pct": round(100 * pts_hit / pts_total, 1) if pts_total else None,
        "elapsed_s": elapsed,
    }

    os.makedirs(OUTDIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    git = ""
    try:
        git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        pass
    out = os.path.join(OUTDIR, f"eval_{ts}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"ts": ts, "git": git, "questions_file": "eval_questions.yaml",
                   "summary": summary, "results": results}, f, ensure_ascii=False, indent=2)

    print("-" * 52)
    print(f"📊 综合通过（检索+来源+有答案）：{src_pass}/{n}（{summary['source_pass_pct']}%）")
    if pts_total:
        print(f"📊 要点覆盖：{pts_hit}/{pts_total}（{summary['pts_pct']}%）")
    else:
        print("📊 要点覆盖：还没有配置要点（去 eval_questions.yaml 填 points）")
    errs = sum(1 for r in results if r.get("error"))
    if errs:
        print(f"⚠️ 有 {errs} 题出错（见结果文件 error 字段）")
    print(f"⏱ 用时 {elapsed}s | 结果已保存 → {os.path.relpath(out, HERE)}")
    print("➡ 下一步：改一个变量后重跑，然后 uv run python eval_kb.py --compare 这次的结果文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
