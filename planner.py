# planner.py — 规划器（V2-卡2，PRD §4.1）：生长循环的"脑子"
# 运行：uv run python planner.py
# 职责：收集 5 路信号 → LLM 判断"该做什么" → 任务清单入库（只列任务，不执行）
# 安全边界：规划器绝不修改知识库任何文件（执行是卡3/审核是卡4 的事）

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests

import config
from health_check import collect_pages, run_health, NAV_PAGES, WIKILINK_RE, link_target_exists, strip_code

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
load_dotenv()
DS_KEY = os.getenv("DEEPSEEK_API_KEY")

TASK_TYPES = {"fix_link", "add_dict", "link_orphan", "new_knowledge", "classify_note"}
VALID_STATUS = {"pending", "done", "skipped", "approved", "rejected"}
MAX_TASKS = 10  # 单次规划最多生成任务数（控制成本）
MAX_PENDING_TASKS = 30  # 任务池总量上限：pending 超过则暂停生成（2026-09-06）


# ============ 信号收集（规则层，LLM 不参与） ============

def collect_signals(kb_root=None):
    """5 路信号 → dict。kb_root 可注入（测试用）"""
    kb_root = Path(kb_root) if kb_root else config.KB_ROOT

    # 信号1：体检问题（复用 health_check）
    health = run_health(kb_root)

    # 信号2：红链缺口 = 内容页引用不存在的页（排除导航页/图片/占位符）
    pages = collect_pages(kb_root)
    # 磁盘文件全集（判断图片/附件）
    disk_files = set()
    for root, _, files in os.walk(kb_root):
        if ".obsidian" in root or ".git" in root:
            continue
        disk_files.update(files)
        disk_files.update(Path(f).stem for f in files)
    redlinks = []
    for name, page in pages.items():
        if name in NAV_PAGES:
            continue  # 导航页（log/IDEA）的红链是流水账，不算知识缺口
        for m in WIKILINK_RE.finditer(strip_code(page["text"])):
            target = m.group(1).strip()
            if not target or link_target_exists(target, pages, disk_files, kb_root):
                continue  # 存在（含路径式链接）不算缺口
            if len(target) < 2 or target in ("X", "x"):
                continue  # 占位符
            redlinks.append({"source": name, "target": target})
    seen = set()
    redlinks = [r for r in redlinks
                if not (r["source"], r["target"]) in seen and not seen.add((r["source"], r["target"]))][:20]

    # 信号3：低置信笔记 = frontmatter 缺 domain（卡4 只补 summary 没写 domain 的）
    unclassified = []
    for name, page in pages.items():
        if name in NAV_PAGES:
            continue
        fm = page["text"][:400]
        if "domain:" not in fm and fm.startswith("---"):
            unclassified.append(name)
        elif not fm.startswith("---"):
            unclassified.append(name)  # 完全没 frontmatter 的裸笔记
    unclassified = [n for n in unclassified if n not in NAV_PAGES][:20]

    # 信号4：待学习清单里的 ⏳ 条目（只取列表行，跳过说明行）
    todo_items = []
    todo_page = next((p for n, p in pages.items() if n == "待学习清单"), None)
    if todo_page:
        for line in todo_page["text"].splitlines():
            if "⏳" in line and line.strip().startswith(("-", "*")):
                todo_items.append(line.strip().lstrip("-* ")[:120])

    # 信号5：对话记录（文件名 + summary，不喂全文）
    chat_pages = []
    for name, page in pages.items():
        rel = str(page["path"].relative_to(kb_root))
        if "对话记录" in rel or "transcripts" in rel or "对话" in name:
            summary = ""
            m = re.search(r"^summary:\s*(.+)$", page["text"], re.M)
            if m:
                summary = m.group(1).strip()[:100]
            chat_pages.append(f"{name}: {summary}" if summary else name)
    chat_pages = chat_pages[:15]

    return {
        "health": health,
        "redlinks": redlinks,
        "unclassified": unclassified,
        "todo_items": todo_items,
        "chat_pages": chat_pages,
    }


def format_signals(signals) -> str:
    """信号整理成紧凑文本（喂给 LLM 的输入）"""
    h = signals["health"]
    parts = []
    parts.append(f"【体检发现】失效链接 {len(h['broken_links'])} 个："
                 + "；".join(f"{s}→{t}" for s, t in h["broken_links"][:8]))
    parts.append(f"孤立页 {len(h['orphans'])} 个（前8）：" + "、".join(h["orphans"][:8]))
    if h["dict_missing"]:
        parts.append(f"词典漏目录条目：{'、'.join(h['dict_missing'][:5])}")
    if signals["redlinks"]:
        parts.append("【红链缺口】(引用不存在页面，可能是该补的知识页或死链)"
                     + "；".join(f"{r['source']}→{r['target']}" for r in signals["redlinks"][:10]))
    if signals["unclassified"]:
        parts.append("【未分类笔记】" + "、".join(signals["unclassified"][:10]))
    if signals["todo_items"]:
        parts.append("【待学习清单 ⏳】" + " | ".join(signals["todo_items"][:10]))
    if signals["chat_pages"]:
        parts.append("【对话记录页】" + " | ".join(signals["chat_pages"][:10]))
    return "\n".join(parts)


# ============ LLM 生成任务 ============

def llm_plan(signals_text: str):
    """一次 DeepSeek 调用 → 任务 JSON 字符串（真实 LLM，可注入替身测试）"""
    prompt = f"""你是知识库的规划器。根据下面的知识库现状信号，判断"接下来该做什么"。

任务类型（type）只能是：
- fix_link: 修失效链接（引用是打字错误/旧名，应改指现有页）
- add_dict: 补词典目录（正文有条目目录漏了）
- link_orphan: 给孤立页加关联（建议关联到哪个现有页）
- new_knowledge: 补新知识页（知识库里没有但有理由建，如待学习清单/对话提到想学）
- classify_note: 给未分类笔记确认 domain

规则：
1. new_knowledge 是重点——判断哪些"该学/被提到但没有"的概念值得建页，detail 写清理由和从哪个现有页切入
2. fix_link 的 detail 必须给出"应改为指向哪个现有页面"
3. 拿不准的 confidence=low，有把握的 high
4. 最多生成 {MAX_TASKS} 个任务，宁缺毋滥，没有值得做的就返回空数组
5. 新建页 target 用 "概念名.md" 格式

【知识库现状】
{signals_text}

只输出 JSON 数组，不要其他文字：
[{{"type": "...", "target": "...", "detail": "...", "confidence": "high|low"}}]"""

    r = requests.post(
        config.CHAT_URL,
        headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
        json={
            "model": config.CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2048,  # 2026-09-11 1200→2048（防新模型回答变长截断）
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def validate_tasks(raw_text: str):
    """解析 LLM 输出 → 合法任务列表（二次校验，坏任务丢弃不崩）"""
    try:
        data = json.loads(raw_text)
        tasks = data if isinstance(data, list) else data.get("tasks", [])
    except json.JSONDecodeError:
        return [], "LLM 输出不是合法 JSON，已拦截"
    if not isinstance(tasks, list):
        return [], "LLM 输出结构异常，已拦截"

    valid = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type", "")
        target = str(t.get("target", "")).strip()
        detail = str(t.get("detail", "")).strip()
        conf = t.get("confidence", "low")
        if ttype in TASK_TYPES and target and detail and conf in ("high", "low"):
            valid.append({"type": ttype, "target": target,
                          "detail": detail, "confidence": conf})
    return valid, None


# ============ 入库去重 ============

def init_tasks_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            target TEXT NOT NULL,
            detail TEXT,
            confidence TEXT,
            source TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    conn.commit()


def save_tasks(tasks, source="planner", conn=None, meta_db=None):
    """任务入库（去重：同 type+target 已有 pending/draft/done 则跳过——draft=已起草待审，
    2026-09-15 补：原先漏了 draft，导致同一页草稿已生成还会重复生成任务）。返回实际新增数"""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(meta_db or config.META_DB)
    try:
        init_tasks_table(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        added = 0
        for t in tasks:
            exists = conn.execute(
                "SELECT 1 FROM tasks WHERE type=? AND target=? AND status IN ('pending','draft','done')",
                (t["type"], t["target"])).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO tasks (type, target, detail, confidence, source, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (t["type"], t["target"], t["detail"], t["confidence"],
                 source, "pending", now))
            added += 1
        conn.commit()
        return added
    finally:
        if own_conn:
            conn.close()


# ============ 主流程 ============

def run_planner(llm_fn=None, kb_root=None, meta_db=None):
    """跑完整规划 → 返回新增任务数（llm_fn 可注入替身，测试用）"""
    # 任务池总量上限（2026-09-06）：pending > 30 暂停生成，提示先处理积压
    import sqlite3 as _sq
    conn = _sq.connect(meta_db or config.META_DB)
    try:
        conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'")  # 表不存在则异常
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0]
    except _sq.OperationalError:
        pending_count = 0  # tasks 表还没建（首次运行），不拦截
    finally:
        conn.close()
    if pending_count >= MAX_PENDING_TASKS:
        print(f"⚠️ 任务池已满（{pending_count} ≥ {MAX_PENDING_TASKS} pending），"
              f"先处理积压任务再生成新的（跑 growth_loop.py 或手动审草稿）")
        return 0

    print("🔍 收集 5 路信号...")
    signals = collect_signals(kb_root)
    signals_text = format_signals(signals)
    print(signals_text[:500])
    print("...\n")

    print("🤖 LLM 判断中...")
    llm_fn = llm_fn or llm_plan
    raw = llm_fn(signals_text)
    tasks, error = validate_tasks(raw)
    if error:
        print(f"⚠️ {error}")
        return 0
    if not tasks:
        print("📭 本次没有值得做的任务")
        return 0

    added = save_tasks(tasks, meta_db=meta_db)
    print(f"✅ 规划完成：生成 {len(tasks)} 个任务，新增入库 {added} 个（其余为已存在）")
    for t in tasks:
        print(f"  [{t['confidence']}] {t['type']} → {t['target']}: {t['detail'][:80]}")
    return added


if __name__ == "__main__":
    run_planner()
