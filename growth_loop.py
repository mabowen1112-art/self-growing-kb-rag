# growth_loop.py — 生长循环闭环（V2-卡5）：一键跑完整一圈
# 运行：uv run python growth_loop.py            # 完整循环（执行器只预览）
#       uv run python growth_loop.py --apply    # 完整循环 + 高置信任务自动执行
# 流程：① 增量导入 → ② 体检 → ③ 规划器 → ④ 执行器 → ⑤ 审核同步 → ⑥ 矛盾复核 → 总结
# 每步都复用已有模块（ingest/health_check/planner/executor/review_sync），本文件只做编排

import argparse
import sys
from datetime import datetime

import config

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def step_ingest(apply: bool):
    """① 增量导入：新笔记进向量库（幂等，无变化 0.2s）"""
    print("\n" + "=" * 50)
    print("① 增量导入（新笔记 → 向量库）")
    print("=" * 50)
    import ingest
    stats = ingest.run_ingest()
    return stats


def step_health():
    """② 体检：发现孤立页/失效链接/词典问题"""
    print("\n" + "=" * 50)
    print("② 体检（规划器的眼睛）")
    print("=" * 50)
    import health_check
    report = health_check.run_health()
    health_check.print_report(report)
    return report


def step_planner():
    """③ 规划器：5 路信号 → LLM 生成任务（去重入库）"""
    print("\n" + "=" * 50)
    print("③ 规划器（该做什么 → 任务清单）")
    print("=" * 50)
    import planner
    added = planner.run_planner()
    return added


def step_executor(apply: bool, max_tasks: int):
    """④ 执行器：按任务干活（默认预览；--apply 才真改/起草）"""
    print("\n" + "=" * 50)
    print(f"④ 执行器（{'自动执行' if apply else '预览，--apply 才执行'}）")
    print("=" * 50)
    import executor
    return executor.run_executor(apply=apply, max_tasks=max_tasks)


def step_review_sync():
    """⑤ 审核同步：检测草稿去向（你拖转正/删除的）自动标记"""
    print("\n" + "=" * 50)
    print("⑤ 审核同步（草稿去向检测）")
    print("=" * 50)
    import review_sync
    stats = review_sync.sync_reviews()
    print(f"  转正 {stats['approved']} / 拒绝 {stats['rejected']} / 草稿未动 {stats['untouched']}")
    return stats


def step_contested():
    """⑥ 矛盾复核（基础版）：列出 frontmatter 带 contested/contradictions 的页面
    LLM 语义级矛盾检测 → PRD 待优化（V2.5+），本步先立机制"""
    print("\n" + "=" * 50)
    print("⑥ 矛盾复核（contested 标记检查）")
    print("=" * 50)
    import os
    from pathlib import Path
    found = []
    for root, _, files in os.walk(config.KB_ROOT):
        if ".obsidian" in root or ".git" in root or "_草稿" in root:
            continue
        for fn in files:
            if not fn.endswith(".md"):
                continue
            text = (Path(root) / fn).read_text(encoding="utf-8")
            head = text[:600]
            if "contested: true" in head or "contradictions:" in head:
                found.append(fn)
    if found:
        print("  ⚠️ 发现 contested/矛盾标记页面：")
        for fn in found:
            print(f"    - {fn}（待人工复核）")
    else:
        print("  ✅ 无 contested/矛盾标记（机制已就位，出现矛盾时自动列出）")
    return found


def run_loop(apply=False, max_tasks=5):
    """完整生长循环 → 总结"""
    t0 = datetime.now()
    print("🌱 ========== 生长循环开始 ==========")

    s1 = step_ingest(apply)
    r2 = step_health()
    added_tasks = step_planner()
    done_exec = step_executor(apply, max_tasks)
    s5 = step_review_sync()
    s6 = step_contested()

    # 待人工处理任务（卡4界面砍掉后，这类需 Obsidian 手动，循环里提示）
    import sqlite3
    conn = sqlite3.connect(config.META_DB)
    manual = conn.execute(
        "SELECT id, type, target FROM tasks WHERE status='pending' "
        "AND type IN ('link_orphan','classify_note','add_dict') ORDER BY id").fetchall()
    conn.close()

    # 总结
    print("\n" + "=" * 50)
    print("📋 本轮生长总结")
    print("=" * 50)
    print(f"  ① 导入：新增 {s1['added_notes']} 篇 / 变更 {s1['changed_notes']}（向量块 +{s1['added_chunks']}/-{s1['removed_chunks']}）")
    issues = (len(r2["broken_links"]) + len(r2["orphans"]) + len(r2["dict_missing"]))
    print(f"  ② 体检：失效链接 {len(r2['broken_links'])} / 孤立页 {len(r2['orphans'])} / 词典漏 {len(r2['dict_missing'])}")
    print(f"  ③ 规划：新增任务 {added_tasks} 个")
    print(f"  ④ 执行：处理 {done_exec} 个（{'自动' if apply else '预览'}）")
    print(f"  ⑤ 审核同步：转正 {s5['approved']} / 拒绝 {s5['rejected']}")
    print(f"  ⑥ 矛盾标记：{len(s6)} 个")
    if manual:
        print(f"\n  👉 待人工处理 {len(manual)} 个任务（需 Obsidian 手动，处理后在任务表标记 done 或删除）：")
        for mid, mtype, mtarget in manual[:8]:
            print(f"     [{mid}] {mtype} → {mtarget}")
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n⏱️ 本轮耗时 {elapsed:.1f}s")
    print("🌱 ========== 生长循环结束 ==========")
    return {"ingest": s1, "health": r2, "tasks_added": added_tasks,
            "executed": done_exec, "review": s5, "contested": len(s6),
            "manual_tasks": len(manual)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生长循环闭环（V2卡5）")
    parser.add_argument("--apply", action="store_true",
                        help="执行器自动执行（默认只预览）")
    parser.add_argument("--max", type=int, default=5, help="执行器最多处理任务数")
    args = parser.parse_args()
    run_loop(apply=args.apply, max_tasks=args.max)
