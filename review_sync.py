# review_sync.py — 拖拽感知同步器（V2-卡4）：检测草稿去向 → 自动标记任务 + 写 log
# 运行：uv run python review_sync.py
# 原理（差异对比，无需实时监听）：
#   status='draft' 的任务，草稿文件不在 raw/_草稿/ 了 →
#     在正式目录找到它（concepts/entities/raw 非草稿区）→ approved：清"待审核"tag + 写 log + 提示补 index
#     完全消失 → rejected（用户删了）
# 幂等：重复跑不重复处理（只处理仍为 draft 的任务）

import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import config

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _conn():
    conn = sqlite3.connect(config.META_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_undo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER, file_path TEXT,
            original_content TEXT, executed_at TEXT
        )
    """)
    conn.commit()
    return conn


def draft_tasks(conn):
    """所有 status='draft' 的 new_knowledge 任务"""
    return conn.execute("SELECT id, type, target, status FROM tasks "
                        "WHERE status='draft' ORDER BY id").fetchall()


def find_file_anywhere(filename: str):
    """在正式目录（排除 raw/_草稿）找文件 → Path 或 None"""
    kb = config.KB_ROOT
    for root, _, files in os.walk(kb):
        if ".obsidian" in root or ".git" in root or "_草稿" in root:
            continue
        if filename in files:
            return Path(root) / filename
    return None


def strip_pending_tag(abs_path: Path) -> bool:
    """把 frontmatter tags 里的"待审核"去掉（转正收尾）"""
    text = abs_path.read_text(encoding="utf-8")
    new_text = re.sub(r"tags: \[([^\]]*)\]",
                      lambda m: "tags: [" + ", ".join(
                          t.strip() for t in m.group(1).split(",")
                          if t.strip() != "待审核") + "]",
                      text, count=1)
    if new_text != text:
        abs_path.write_text(new_text, encoding="utf-8")
        return True
    return False


def in_index(page_name: str) -> bool:
    """检查 index.md 是否收录（[[页面名]] 或描述里含完整名）"""
    index_path = config.KB_ROOT / "index.md"
    if not index_path.exists():
        return True  # 没有 index 就不管
    text = index_path.read_text(encoding="utf-8")
    return f"[[{page_name}]]" in text


def append_log(page_name, action, target_dir):
    """写审核记录到知识库 log.md"""
    log_path = config.KB_ROOT / "log.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (f"\n## [{now}] review | 草稿审核同步\n\n"
             f"- **{page_name}**：{action}（目标位置 `{target_dir}`）——由用户拖拽操作触发，同步器自动记录\n")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def sync_reviews():
    """主流程：返回统计 dict"""
    conn = _conn()
    stats = {"approved": 0, "rejected": 0, "untouched": 0, "missing": 0}
    try:
        for task_id, ttype, target, status in draft_tasks(conn):
            page_name = Path(target).stem
            draft_path = config.DRAFT_DIR / f"{page_name}.md"

            if draft_path.exists():
                stats["untouched"] += 1  # 还在草稿区，没动
                continue

            # 草稿不在了 → 查去向
            found = find_file_anywhere(f"{page_name}.md")
            if found:
                # 审核通过：清待审核 tag
                stripped = strip_pending_tag(found)
                rel_dir = str(found.relative_to(config.KB_ROOT)).replace("\\", "/")
                conn.execute("UPDATE tasks SET status='approved' WHERE id=?", (task_id,))
                append_log(page_name, f"审核通过（拖拽转正至 {rel_dir}）", rel_dir)
                index_ok = in_index(page_name)
                after = found.read_text(encoding="utf-8")[:600]
                tags_empty = re.search(r"tags: \[\]", after) is not None
                print(f"✅ [{task_id}] {page_name}.md → approved（{rel_dir}"
                      f"{'，已清理待审核tag' if stripped else ''}）"
                      + ("" if index_ok else f"\n   ⚠️ 未收录 index.md，记得补条目")
                      + ("" if not tags_empty else "\n   ⚠️ tags 为空，记得补领域 tag（如 tool/course/finance）"))
                stats["approved"] += 1
            else:
                # 完全消失 = 用户删除 = 拒绝
                conn.execute("UPDATE tasks SET status='rejected' WHERE id=?", (task_id,))
                append_log(page_name, "审核拒绝（草稿被删除）", "-")
                print(f"❌ [{task_id}] {page_name}.md → rejected（草稿已删除）")
                stats["rejected"] += 1
        conn.commit()
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    stats = sync_reviews()
    print(f"\n📊 同步完成：approved {stats['approved']} / rejected {stats['rejected']} / "
          f"草稿未动 {stats['untouched']}")
