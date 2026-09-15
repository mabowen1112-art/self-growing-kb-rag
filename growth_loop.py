# growth_loop.py — 生长循环闭环（V2-卡5）：一键跑完整一圈
# 运行：uv run python growth_loop.py            # 完整循环（执行器只预览）
#       uv run python growth_loop.py --apply    # 完整循环 + 高置信任务自动执行
# 流程：① 增量导入 → ② 体检 → ③ 规划器 → ④ 执行器 → ⑤ 审核同步 → ⑥ 矛盾复核 → 总结
#      + ⑦ 写《生长摘要》（知识库根目录，P1 2026-09-15：让"系统干了什么"看得见）
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


def step_executor(apply: bool, max_tasks: int, auto_safe: bool = False, detail_sink=None):
    """④ 执行器：按任务干活（默认预览；--apply 才执行；--auto-safe 只做高置信）"""
    print("\n" + "=" * 50)
    print(f"④ 执行器（{'自动执行·仅高置信' if auto_safe else '自动执行' if apply else '预览，--apply 才执行'}）")
    print("=" * 50)
    import executor
    return executor.run_executor(apply=apply, max_tasks=max_tasks, auto_safe=auto_safe,
                                 detail_sink=detail_sink)


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


DIGEST_NAME = "生长摘要.md"
DIGEST_KEEP = 30


def build_digest(result, details, pool_pending, draft_count, manual_count, elapsed, mode):
    """拼本轮摘要（markdown，## 开头）。mode: 'auto' / 'apply' / 'preview'"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    drafted = [d["target"].replace(".md", "") for d in details if d.get("outcome") == "drafted"]
    fixed = [d["target"] for d in details if d.get("outcome") == "fixed"]
    skipped_low = sum(1 for d in details if d.get("outcome") == "skipped_low")
    h = result["health"]
    if h["broken_links"] or h["orphans"] or h["dict_missing"]:
        health_line = f"失效 {len(h['broken_links'])} / 孤立 {len(h['orphans'])} / 词典 {len(h['dict_missing'])}"
    else:
        health_line = "全绿"
    mode_label = {"auto": "自动生长（auto-safe）", "apply": "生长（--apply）", "preview": "预览"}[mode]
    lines = [
        f"## {now} · {mode_label} · 耗时 {elapsed:.0f}s",
        f"- 🆕 起草新页：{('、'.join(drafted) + ' → raw/_草稿/') if drafted else '无'}",
        f"- 🔧 自动处理：{('、'.join(fixed)) if fixed else '无'}",
        f"- 📥 导入 {result['ingest']['added_notes']} 新 / {result['ingest']['changed_notes']} 变更"
        f" ｜ 🏥 体检 {health_line} ｜ 🗂 任务池 {pool_pending}",
        f"- 👀 待你处理：草稿 {draft_count} 篇（raw/_草稿/）｜ 人工任务 {manual_count} 个"
        + (f" ｜ 低置信挂起 {skipped_low} 个" if skipped_low else ""),
    ]
    return "\n".join(lines) + "\n"


def write_digest(block_md, keep=DIGEST_KEEP):
    """把本轮摘要写到《生长摘要》最上面（保留最近 keep 条）→ 返回写入路径"""
    path = config.KB_ROOT / DIGEST_NAME
    header = ("---\ntitle: 生长摘要\ntype: log\ndomain: general\n"
              "tags: [生长循环, 自动生成]\n"
              "summary: 每轮生长循环的自动摘要（最新在上）；草稿在 raw/_草稿/ 里待审\n---\n\n"
              "# 生长摘要（自动生成 · 最新在上）\n\n"
              f"> 每轮生长循环结束后自动更新，保留最近 {keep} 条；「待你处理」的草稿在 raw/_草稿/ 里。\n\n")
    blocks = []
    if path.exists():
        old = path.read_text(encoding="utf-8")
        blocks = [p for p in old.split("\n## ")[1:] if p.strip()]
    block = block_md.strip()
    if block.startswith("## "):
        block = block[3:]
    blocks = [block] + blocks
    blocks = blocks[:keep]
    path.write_text(header + "\n\n".join("## " + b for b in blocks), encoding="utf-8")
    return path


def run_loop(apply=False, max_tasks=5, auto_safe=False):
    """完整生长循环 → 总结"""
    t0 = datetime.now()
    print("🌱 ========== 生长循环开始 ==========")

    s1 = step_ingest(apply)
    r2 = step_health()
    added_tasks = step_planner()
    details = []
    done_exec = step_executor(apply, max_tasks, auto_safe, details)
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

    # ⑦ 生长摘要（P1，2026-09-15）：真跑时写入知识库根目录《生长摘要.md》；预览只打印
    summary_partial = {"ingest": s1, "health": r2}
    conn = sqlite3.connect(config.META_DB)
    try:
        pool_pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0]
    except sqlite3.OperationalError:
        pool_pending = 0
    conn.close()
    draft_dir = config.KB_ROOT / "raw" / "_草稿"
    draft_count = len(list(draft_dir.glob("*.md"))) if draft_dir.exists() else 0
    mode = "auto" if auto_safe else ("apply" if apply else "preview")
    digest = build_digest(summary_partial, details, pool_pending, draft_count,
                          len(manual), elapsed, mode)
    digest_written = None
    if apply or auto_safe:
        digest_written = write_digest(digest)
        print(f"\n📮 已写《{DIGEST_NAME}》（知识库根目录，手机同步后也能看）")
    else:
        print("\n📮 预览模式：摘要未落盘（--apply / --auto-safe 时自动写《生长摘要》）")
    print("🌱 ========== 生长循环结束 ==========")
    return {"ingest": s1, "health": r2, "tasks_added": added_tasks,
            "executed": done_exec, "review": s5, "contested": len(s6),
            "manual_tasks": len(manual), "digest_written": str(digest_written) if digest_written else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生长循环闭环（V2卡5）")
    parser.add_argument("--apply", action="store_true",
                        help="执行器自动执行（默认只预览）")
    parser.add_argument("--auto-safe", action="store_true",
                        help="安全自动模式：只自动执行高置信任务（低置信挂起等人工）")
    parser.add_argument("--max", type=int, default=5, help="执行器最多处理任务数")
    args = parser.parse_args()
    run_loop(apply=args.apply or args.auto_safe, max_tasks=args.max, auto_safe=args.auto_safe)
