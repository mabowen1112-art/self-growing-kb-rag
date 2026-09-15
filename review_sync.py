# review_sync.py — 拖拽感知同步器（V2-卡4）：检测草稿去向 → 自动标记任务 + 写 log
# 运行：uv run python review_sync.py
# 原理（差异对比，无需实时监听）：
#   status='draft' 的任务，草稿文件不在 raw/_草稿/ 了 →
#     在正式目录找到它（concepts/entities/raw 非草稿区）→ approved：
#       清"待审核"tag + 【P2 2026-09-16】自动补 tags（分类器）+ 自动补 index.md 条目 + 写 log
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
    """在正式目录找文件（排除草稿区 + 所有以 . 开头的目录：.trash/.obsidian/.git…）
    ⚠️ 2026-09-16：原先只排除 .obsidian/.git——若 Obsidian 用库内回收站（.trash），
    删掉的草稿会被"找到"→ 误判 approved；现在隐藏目录一律不算"正式目录"。"""
    kb = config.KB_ROOT
    for root, dirs, files in os.walk(kb):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "_草稿" in root:
            dirs[:] = []
            continue
        if filename in files:
            return Path(root) / filename
    return None


def draft_still_present(page_name: str) -> bool:
    """草稿是否还在草稿区（含子目录——用户把草稿挪进 _草稿 的子文件夹也算"未动"）"""
    d = config.DRAFT_DIR
    if not d.exists():
        return False
    target = f"{page_name}.md"
    if (d / target).exists():
        return True
    return any(p.name == target for p in d.rglob("*.md"))


def parse_frontmatter_text(abs_path: Path):
    """轻读 frontmatter 字段（summary/domain 等）→ (dict, 全文)"""
    import directions
    text = abs_path.read_text(encoding="utf-8")
    fm, _rest = directions.parse_frontmatter(text)
    return fm, text


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


# ---- P2（2026-09-16）：转正收尾自动化 ----

INDEX_SECTIONS = {          # domain → index.md 第一层 section
    "ai": "## 🧠 AI/AIGC",
    "im": "## 📊 信息管理专业",
    "life": "## 🏠 生活",
    "english": "## 📚 英语精读",
    "general": "## 💡 个人通用",
}


def index_section_for(rel_path: str, domain: str) -> str:
    """按路径+domain 决定 index 里插到哪个 section（english/ 路径优先）"""
    rel = (rel_path or "").replace("\\", "/")
    if rel.startswith("english/"):
        return "## 📚 英语精读"
    return INDEX_SECTIONS.get((domain or "").strip(), "## 💡 个人通用")


def fill_tags_if_empty(abs_path: Path):
    """tags 为空 → 用方向分类器补（零 API 费；低置信保持空）→ (是否补了, 说明)"""
    import directions
    text = abs_path.read_text(encoding="utf-8")
    fm, _rest = directions.parse_frontmatter(text)
    if "tags" not in fm:
        return False, "无 tags 字段"
    if fm["tags"].strip() not in ("[]", ""):
        return False, "已有 tags"
    res = directions.classify(text)
    if res.get("hits", 0) <= 0 or not res.get("tags"):
        return False, "分类低置信（tags 留空待人工）"
    new_tags = "[" + ", ".join(res["tags"][:3]) + "]"
    abs_path.write_text(re.sub(r"tags: \[[^\]]*\]", f"tags: {new_tags}", text, count=1), encoding="utf-8")
    return True, f"已补 tags {new_tags}"


def add_index_entry(page_name: str, rel_path: str, domain: str, summary: str):
    """index.md 未收录 → 自动插条目 + 总页数+1 → (是否加了, 说明)"""
    index_path = config.KB_ROOT / "index.md"
    if not index_path.exists():
        return False, "无 index.md（跳过）"
    text = index_path.read_text(encoding="utf-8")
    if f"[[{page_name}]]" in text:
        return False, "index 已收录"
    section = index_section_for(rel_path, domain)
    lines = text.split("\n")
    sec_i = next((i for i, l in enumerate(lines) if l.strip() == section), None)
    if sec_i is None:
        return False, f"未找到分类 section（{section}），条目未自动插（人工补）"
    end_i = len(lines)
    for j in range(sec_i + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].strip() == "---":
            end_i = j
            break
    # 优先落在 ### Concepts 子节；否则整个 section
    sub_i = next((j for j in range(sec_i + 1, end_i)
                  if lines[j].strip().lower().startswith("### concepts")), sec_i)
    entries = [j for j in range(sub_i + 1, end_i) if lines[j].startswith("- [[")]
    insert_at = entries[-1] + 1 if entries else sub_i + 1
    item = f"- [[{page_name}]] — {(summary or '（待补摘要）')[:120]}"
    lines.insert(insert_at, item)
    # 头部：最后更新 + 总页数
    for i, l in enumerate(lines[:8]):
        if l.startswith("> 最后更新"):
            m = re.search(r"总页数：(\d+)", l)
            l = re.sub(r"最后更新：\d{4}-\d{2}-\d{2}",
                       f"最后更新：{datetime.now().strftime('%Y-%m-%d')}", l)
            if m:
                l = re.sub(r"总页数：\d+", f"总页数：{int(m.group(1)) + 1}", l)
            lines[i] = l
            break
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return True, f"已补 index 条目（{section.replace('## ', '')}）"


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
    stats = {"approved": 0, "rejected": 0, "untouched": 0, "missing": 0,
             "tags_filled": 0, "index_added": 0}
    try:
        for task_id, ttype, target, status in draft_tasks(conn):
            page_name = Path(target).stem

            if draft_still_present(page_name):
                stats["untouched"] += 1  # 还在草稿区（含子文件夹），没动
                continue

            # 草稿不在了 → 查去向
            found = find_file_anywhere(f"{page_name}.md")
            if found:
                # 审核通过：清待审核 tag → 自动收尾（补 tags / 补 index 条目）
                stripped = strip_pending_tag(found)
                rel_dir = str(found.relative_to(config.KB_ROOT)).replace("\\", "/")
                fm, _ = parse_frontmatter_text(found)
                tags_filled, tags_note = fill_tags_if_empty(found)
                index_added, index_note = add_index_entry(
                    page_name, rel_dir, fm.get("domain", ""), fm.get("summary", "").strip('"'))
                notes = "；".join(x for x in [
                    "已清理待审核tag" if stripped else "",
                    tags_note if tags_filled else (tags_note if "低置信" in tags_note else ""),
                    index_note,
                ] if x)
                conn.execute("UPDATE tasks SET status='approved' WHERE id=?", (task_id,))
                append_log(page_name, f"审核通过（拖拽转正至 {rel_dir}）"
                                      + (f"｜自动收尾：{notes}" if notes else ""), rel_dir)
                print(f"✅ [{task_id}] {page_name}.md → approved（{rel_dir}）"
                      + (f"\n   ↳ 自动收尾：{notes}" if notes else ""))
                stats["approved"] += 1
                stats["tags_filled"] += 1 if tags_filled else 0
                stats["index_added"] += 1 if index_added else 0
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
