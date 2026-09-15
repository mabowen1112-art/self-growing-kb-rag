# executor.py — 执行器（V2-卡3）：按任务清单"干活"
# 运行：uv run python executor.py            # dry-run：只预览要做什么，不写文件
#       uv run python executor.py --apply    # 真执行：改文件（改前自动备份可回滚）
# 安全设计：
#   - 默认 dry-run，--apply 才真改
#   - 改前备份原文件内容到 SQLite（task_undo 表），可回滚
#   - 每类任务执行后自动写执行日志（知识库 log.md，AI 起草：做了什么/放了哪）
#   - new_knowledge 一律只起草草稿到 raw/_草稿/（不碰正式库），审核通过才转正

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests

import config

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
load_dotenv()
DS_KEY = os.getenv("DEEPSEEK_API_KEY")

# 注意：DRAFT_DIR / KB_ROOT / META_DB 一律运行时读 config（测试会 patch config，
# 模块级缓存会导致测试/真实路径错乱——V2卡3 踩坑）


def _draft_dir():
    return config.DRAFT_DIR
# 确定性任务类型（高置信可直接改文件）；new_knowledge 永远只起草
AUTO_TYPES = {"fix_link", "add_dict", "link_orphan", "classify_note"}


# ============ 数据库：备份 + 任务状态 ============

def _conn():
    conn = sqlite3.connect(config.META_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_undo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            file_path TEXT,
            original_content TEXT,
            executed_at TEXT
        )
    """)
    conn.commit()
    return conn


def get_pending_tasks(conn, types=None):
    if types:
        ph = ",".join("?" * len(types))
        rows = conn.execute(f"SELECT id, type, target, detail, confidence FROM tasks "
                            f"WHERE status='pending' AND type IN ({ph}) ORDER BY id", types).fetchall()
    else:
        rows = conn.execute("SELECT id, type, target, detail, confidence FROM tasks "
                            "WHERE status='pending' ORDER BY id").fetchall()
    return [{"id": r[0], "type": r[1], "target": r[2], "detail": r[3], "confidence": r[4]}
            for r in rows]


def set_task_status(conn, task_id, status):
    conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    conn.commit()


def backup_file(conn, task_id, abs_path):
    """改文件前备份原文 → task_undo 表（可回滚）"""
    content = abs_path.read_text(encoding="utf-8")
    conn.execute("INSERT INTO task_undo (task_id, file_path, original_content, executed_at) "
                 "VALUES (?,?,?,?)",
                 (task_id, str(abs_path), content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def rollback(conn, task_id=None):
    """回滚：按 task_id（或最近一次）恢复文件原文"""
    if task_id:
        rows = conn.execute("SELECT file_path, original_content FROM task_undo "
                            "WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    else:
        rows = conn.execute("SELECT file_path, original_content FROM task_undo "
                            "ORDER BY id DESC LIMIT 1").fetchall()
    restored = 0
    for fp, content in rows:
        Path(fp).write_text(content, encoding="utf-8")
        restored += 1
    return restored


# ============ 执行日志（写知识库 log.md，AI 起草条目） ============

def append_execution_log(entries, dry_run=False):
    """entries: [{task_type, action, target, detail, result}] → 追加到 KB log.md
    条目由 AI 起草（本函数即"AI 写作"），记录做了什么/放到了哪里"""
    if dry_run:
        return
    log_path = config.KB_ROOT / "log.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["", f"## [{now}] exec | AI执行器批量执行", ""]
    for e in entries:
        lines.append(f"- **{e['task_type']}** → {e['target']}：{e['action']} | 结果：{e['result']}")
    lines.append("")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ============ 确定性任务执行（改文件） ============

def resolve_file(target: str):
    """按任务 target（页面名或文件名）定位真实文件"""
    kb = config.KB_ROOT
    # target 可能是 "页面名" / "页面名.md" / "路径/页面.md"
    name = Path(target).stem
    for root, _, files in os.walk(kb):
        if ".obsidian" in root or ".git" in root or "_草稿" in root:
            continue
        for fn in files:
            if Path(fn).stem == name and fn.endswith(".md"):
                return Path(root) / fn
    return None


def execute_deterministic(task, apply=False):
    """确定性任务：返回 (动作描述, 结果)。apply=True 才真写文件（安全闸）"""
    target_file = resolve_file(task["target"])
    if target_file is None:
        return None, f"找不到目标文件: {task['target']}"

    conn = _conn()
    try:
        if task["type"] == "fix_link":
            # 预览：算出要改什么但不写
            import re
            from health_check import collect_pages, link_target_exists, WIKILINK_RE
            text = target_file.read_text(encoding="utf-8")
            detail = task["detail"]
            m = re.search(r"\[\[([^\]|]+)\]\]", detail)
            if not m:
                m = re.search(r"([\u4e00-\u9fffA-Za-z0-9_\-（）()]+\.md)", detail)
            if not m:
                return None, "无法从 detail 提取修复目标"
            new_target = m.group(1).strip().removesuffix(".md")
            # 旧目标 = 文件里第一个"真坏链"（占位符/锚点/能解析的/图片嵌入都不动）
            # ⚠️ 2026-09-16 加固：原先盲取"文件第一个 wikilink"——实测把 PRD 表格里的示例 [[X]]
            #    改成了别的页（改错位置）；找不到真坏链时宁可拒绝，交人工
            kb = config.KB_ROOT
            all_files = set()
            for root, _, files in os.walk(kb):
                if ".obsidian" in root or ".git" in root:
                    continue
                all_files.update(files)
                all_files.update(Path(f).stem for f in files)
            pages_all = collect_pages(kb)
            old_target = None
            for mm in WIKILINK_RE.finditer(text):
                if mm.start() > 0 and text[mm.start() - 1] == "!":
                    continue  # 图片/附件嵌入不是坏链
                cand = mm.group(1).strip()
                if not cand or "#" in cand or "://" in cand:
                    continue
                if len(cand) < 2 or cand in ("X", "x"):
                    continue  # 占位符（含示例文字）
                if link_target_exists(cand, pages_all, all_files, kb):
                    continue  # 能解析的不是坏链
                old_target = cand
                break
            if old_target is None:
                return None, "文件里没有可修的坏链接（占位符/正常链接不算）——拒绝盲改，交人工"
            action = f"[[{old_target}]] → [[{new_target}]]"
            if not apply:
                return action, "preview（--apply 才真改）"
            backup_file(conn, task["id"], target_file)
            new_text = text.replace(f"[[{old_target}]]", f"[[{new_target}]]", 1)
            target_file.write_text(new_text, encoding="utf-8")
            return action, "ok"
        if task["type"] == "add_dict":
            return None, "add_dict 需人工确认插入位置（Obsidian 手动改词典目录，或修改任务 detail 后重跑）"
        if task["type"] == "link_orphan":
            return None, "link_orphan 需人工确认关联页面（Obsidian 手动在目标页加链接，改完删任务）"
        if task["type"] == "classify_note":
            return None, "classify_note 需人工确认 domain（Obsidian 补 frontmatter，改完删任务）"
    finally:
        conn.close()
    return None, f"未知任务类型 {task['type']}"


# ============ new_knowledge：起草草稿（不碰正式库） ============

def draft_new_page(task, apply=False):
    """new_knowledge：检索相关素材 → LLM 起草新页 → 存 raw/_草稿/
    返回 (草稿文件名, 结果描述)"""
    page_name = Path(task["target"]).stem
    draft_dir = _draft_dir()
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{page_name}.md"
    if draft_path.exists():
        return page_name, f"草稿已存在（{draft_path.name}），跳过（人工审核后删除可重新生成）"

    # 检索相关素材（复用 ask_kb 检索，防幻觉：起草要有知识库来源）
    from ask_kb import retrieve
    try:
        hits = retrieve(f"{page_name} 相关知识", k=3)
    except Exception as e:
        hits = []
        print(f"  ⚠️ 检索失败：{e}")

    context = "\n".join(
        f"【素材 {i+1}】来源:{m['source']}（{m['heading']}）\n{d[:300]}"
        for i, (d, m, _s) in enumerate(hits)) or "（知识库中没有直接相关素材）"

    prompt = f"""你是知识库的笔记起草员。请为概念「{page_name}」起草一篇知识库笔记。

要求：
1. 内容准确、结构清晰，用中文；涉及硬数据/事实必须用【素材】里的内容，没有把握的用"一般/通常"措辞，绝不编造具体数字
2. 若知识库素材相关，结合素材组织；若素材无关，基于通用知识写概念科普（标题注明"待审核"）
3. 输出完整 Markdown（含 YAML frontmatter）：
---
title: {page_name}
created: {datetime.now().strftime("%Y-%m-%d")}
updated: {datetime.now().strftime("%Y-%m-%d")}
type: concept
domain: ai
tags: [待审核]
sources: [AI起草]
confidence: low
summary: 一句话摘要
---

【素材】
{context[:2000]}"""

    r = requests.post(
        config.CHAT_URL,
        headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
        json={"model": config.CHAT_MODEL,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 3000},  # 2026-09-11 1500→3000（草稿较长，防新模型截断）
        timeout=120,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if not content.startswith("---"):
        content = "---\ntitle: " + page_name + "\n---\n\n" + content

    if apply:
        draft_path.write_text(content, encoding="utf-8")
        return page_name, f"草稿已生成 → raw/_草稿/{page_name}.md（待人工审核转正）"
    return page_name, f"[dry-run] 将生成 raw/_草稿/{page_name}.md"


# ============ 主入口 ============

def run_executor(apply=False, only_type=None, max_tasks=5, auto_safe=False, detail_sink=None):
    """执行器。apply=True 真执行；auto_safe=True 时只自动执行高置信任务
    （安全模式：自动跑生长循环用——🔴high 的 new_knowledge/fix_link 自动做，🟡low 挂起留人工）
    2026-09-15 修复：auto_safe 下低置信跳过**不占 max_tasks 名额**（原先 5 个名额被队头低置信白占）
    detail_sink：可选 list，逐条记录本轮明细 [{id,type,target,confidence,outcome,...}]（供生长摘要）"""
    conn = _conn()
    try:
        types = None if only_type is None else [only_type]
        tasks = get_pending_tasks(conn, types)
        if not tasks:
            print("📭 没有 pending 任务（先跑 planner.py 生成）")
            return 0

        print(f"{'⚡ 执行模式（会改文件）' if apply else '👀 dry-run 预览（--apply 才真改）'}"
              + (" · 仅高置信" if auto_safe else ""))

        # 草稿上限（2026-09-15 用户要求）：待审草稿达到 DRAFT_LIMIT 后暂停起草，防积压
        draft_dir = _draft_dir()
        draft_count = len(list(draft_dir.glob("*.md"))) if draft_dir.exists() else 0
        draft_budget = max(0, getattr(config, "DRAFT_LIMIT", 10) - draft_count)
        if draft_budget <= 0:
            print(f"⏸ 草稿区已满（{draft_count}/{getattr(config, 'DRAFT_LIMIT', 10)} 篇待审）——本次暂缓起草，先审草稿")

        done = 0
        skipped_low = 0
        blocked_draft = 0
        processed = 0
        log_entries = []
        for task in tasks:
            if processed >= max_tasks:
                break
            if auto_safe and task["confidence"] != "high":
                print(f"\n[{task['id']}] 🟡 {task['type']} → {task['target']}（低置信，跳过等人工·不占名额）")
                skipped_low += 1
                if detail_sink is not None:
                    detail_sink.append({"id": task["id"], "type": task["type"], "target": task["target"],
                                        "confidence": task["confidence"], "outcome": "skipped_low"})
                continue
            if task["type"] == "new_knowledge" and draft_budget <= 0:
                print(f"\n[{task['id']}] ⏸ new_knowledge → {task['target']}：草稿已满，暂缓起草（不占名额）")
                blocked_draft += 1
                if detail_sink is not None:
                    detail_sink.append({"id": task["id"], "type": task["type"], "target": task["target"],
                                        "confidence": task["confidence"], "outcome": "draft_blocked"})
                continue
            processed += 1
            print(f"\n[{task['id']}] {task['type']} → {task['target']}（{task['confidence']}）")
            print(f"   detail: {task['detail'][:100]}")
            outcome, note = "noop", ""
            if task["type"] in AUTO_TYPES:
                action, result = execute_deterministic(task, apply=apply)
                note = result
                print(f"   {result}")
                if result == "ok" and action:
                    log_entries.append({"task_type": task["type"], "target": task["target"],
                                        "action": action, "result": "已修改文件"})
                    if apply:
                        set_task_status(conn, task["id"], "done")
                    done += 1
                    outcome = "fixed"
                elif apply and result != "ok":
                    print(f"   ⚠️ 跳过（不自动改，留给人工）：{result}")
                    outcome = "refused"
                else:
                    outcome = "preview"
            elif task["type"] == "new_knowledge":
                name, result = draft_new_page(task, apply=apply)
                note = result
                print(f"   {result}")
                if apply and "草稿已生成" in result:
                    set_task_status(conn, task["id"], "draft")
                    log_entries.append({"task_type": "new_knowledge", "target": name,
                                        "action": f"起草新页 → raw/_草稿/{name}.md",
                                        "result": "草稿待审核（未进正式库）"})
                    done += 1
                    outcome = "drafted"
                    draft_budget -= 1   # 每起草一篇扣一个名额（硬上限，绝不超过 DRAFT_LIMIT）
                else:
                    outcome = "preview" if not apply else "noop"
            else:
                print(f"   ⚠️ 类型 {task['type']} 无执行器，跳过")
            if detail_sink is not None:
                detail_sink.append({"id": task["id"], "type": task["type"], "target": task["target"],
                                    "confidence": task["confidence"], "outcome": outcome, "note": note[:120]})

        if log_entries:
            append_execution_log(log_entries, dry_run=not apply)
            print(f"\n📝 已写执行日志到知识库 log.md（{len(log_entries)} 条）")
        print(f"\n✅ 处理 {done}/{processed} 个任务"
              + (f"，低置信跳过 {skipped_low} 个（不占名额）" if auto_safe else "")
              + (f"，草稿已满暂缓 {blocked_draft} 个（不占名额）" if blocked_draft else "")
              + ("（dry-run 未改任何文件）" if not apply else ""))
        return done
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V2执行器：按任务干活")
    parser.add_argument("--apply", action="store_true", help="真执行（默认 dry-run 预览）")
    parser.add_argument("--type", help="只执行某类任务，如 fix_link / new_knowledge")
    parser.add_argument("--max", type=int, default=5, help="最多处理任务数")
    args = parser.parse_args()
    run_executor(apply=args.apply, only_type=args.type, max_tasks=args.max)
