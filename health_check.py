# health_check.py — 定时体检模块（V2-卡1，PRD §7）
# 运行：uv run python health_check.py
# 规则：① 失效链接 ② 孤立页面 ③ 词典一致性 ④ 过期内容
# 输出：控制台报告（后续卡接审核队列/循环骨架）
# 只读知识库，绝不修改任何文件

import os
import re
import sqlite3
from datetime import datetime, date
from pathlib import Path

import config

# 导航/管理页（不算内容，孤立页检测排除它们作为"入链来源"）
NAV_PAGES = {"index", "log", "SCHEMA", "IDEA", "wikilink"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]*)(#[^\]|]*)?(\|[^\]]*)?\]\]")
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")          # 行内代码 `...`
CODE_FENCE_RE = re.compile(r"```.*?```", re.S)    # 围栏代码块
STALE_DAYS = 90  # updated 超过 90 天且被体检提醒


def strip_code(text):
    """剔除代码（围栏块 + 行内）后再找 wikilink：Obsidian 不把代码里的 [[..]] 当链接，
    文档里的示例写法（`[[raw/x/y]]`）不该被当成真链接（2026-09-15）"""
    return CODE_SPAN_RE.sub("", CODE_FENCE_RE.sub("", text))


def collect_pages(kb_root=None):
    """扫描全部 md → {页面名(stem): {path, abs, text, updated}}
    ⚠️ 2026-09-15：排除 EXCLUDE_DIRS（raw/_草稿）——草稿未定稿，与 ingest 口径一致，
    不再被当成内容页参与孤立页/失效链接/未分类检查（否则一进草稿就刷假问题）"""
    kb_root = Path(kb_root) if kb_root else config.KB_ROOT
    excl = {d.replace("\\", "/").strip("/") for d in config.EXCLUDE_DIRS}
    pages = {}
    for root, dirs, files in os.walk(kb_root):
        if ".obsidian" in root or ".git" in root:
            dirs[:] = []
            continue
        rel_root = Path(root).relative_to(kb_root).as_posix()
        rel_root = "" if rel_root == "." else rel_root
        # 整棵子树跳过（含排除目录自身）——同 ingest 的 EXCLUDE_DIRS 修复（坑#12）
        if any(rel_root == e or rel_root.startswith(e + "/") for e in excl):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if f"{rel_root}/{d}".lstrip("/") not in excl]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            abs_path = Path(root) / fn
            text = abs_path.read_text(encoding="utf-8")
            updated = extract_updated(text)
            pages[Path(fn).stem] = {"path": abs_path, "text": text, "updated": updated}
    return pages


def link_target_exists(target, pages, all_files, kb_root):
    """链接目标是否存在：① 页面名(stem)/磁盘文件名 ② 路径式链接（[[raw/x/y]] / [[a/b.md]]）
    ⚠️ 2026-09-15：路径式链接是 Obsidian 合法写法，原实现只按文件名匹配 → 误报失效链接/红链"""
    if target in pages or target in all_files:
        return True
    rel = target.replace("\\", "/").strip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    if "/" not in rel:
        return False
    p = Path(kb_root) / rel
    if p.exists():
        return True
    return p.suffix == "" and p.with_suffix(".md").exists()


def extract_updated(text):
    """从 frontmatter 取 updated 日期 → date 或 None"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("updated:"):
                    v = line.split(":", 1)[1].strip().strip('"\'')
                    try:
                        return datetime.strptime(v[:10], "%Y-%m-%d").date()
                    except ValueError:
                        return None
    return None


# ============ 规则1：失效链接 ============

def check_broken_links(pages, kb_root=None):
    """wikilink 指向不存在的文件 → [(来源文件, 失效目标)]
    目标可能指向非 md 资源（图片/附件），磁盘上存在同名文件就不算失效；
    路径式链接按 KB 相对路径解析（2026-09-15 修复误报）"""
    broken = []
    kb_root = Path(kb_root) if kb_root else config.KB_ROOT
    # 收集磁盘上所有文件名（含非 md 资源：图片/附件），供链接判断
    # 存全名 + stem 双份：链接可能写 [[图.png]]（带扩展名）或 [[图]]（不带）
    all_files = set()
    for root, _, files in os.walk(kb_root):
        if ".obsidian" in root or ".git" in root:
            continue
        all_files.update(files)
        all_files.update(Path(f).stem for f in files)
    for name, page in pages.items():
        if name in NAV_PAGES:
            continue  # 导航/管理页（log 操作日志等）的链接不检查——流水账不是内容
        for m in WIKILINK_RE.finditer(strip_code(page["text"])):
            target = m.group(1).strip()
            if not target:
                continue
            if len(target) < 2 or target in ("X", "x"):
                continue  # 占位符（PRD 表格里的示例链接等），同 planner 信号2 口径（2026-09-15）
            if not link_target_exists(target, pages, all_files, kb_root):
                broken.append((name, target))
    return broken


# ============ 规则2：孤立页面 ============

def check_orphans(pages, kb_root=None):
    """内容页没有任何其他内容页链接它 → [页面名]
    排除导航页（index/log/SCHEMA/IDEA）自身不算内容；导航页指向它的链接也不算入链；
    路径式入链（[[concepts/x/y]]）也算入链（2026-09-15 修复误报）"""
    kb_root = Path(kb_root) if kb_root else config.KB_ROOT
    linked = set()
    for name, page in pages.items():
        if name in NAV_PAGES:
            continue  # 导航页的链接不算"内容关联"
        for m in WIKILINK_RE.finditer(strip_code(page["text"])):
            target = m.group(1).strip()
            if not target:
                continue
            if target in pages:
                linked.add(target)
            else:
                rel = target.replace("\\", "/").strip("/")
                if "/" in rel and Path(rel).stem in pages:
                    linked.add(Path(rel).stem)
    orphans = [n for n in pages if n not in NAV_PAGES and n not in linked]
    return sorted(orphans)


# ============ 规则3：词典一致性（目录锚点 ↔ 正文条目） ============

def check_dict_consistency(pages):
    """学习概念词典：📑目录里的 [[#条目]] 必须对应正文条目
    条目层级 = ####（现行结构 ## 大类 > ### 子组 > #### 概念，2026-08-22 定）；
    兜底：正文没有任何 #### 时回退按 ### 检查（防词典再改结构时静默刷屏，2026-09-15）
    返回 (缺目录的条目, 目录里没有的死锚点)"""
    dict_page = None
    for name, page in pages.items():
        if name == "学习概念词典":
            dict_page = page
            break
    if not dict_page:
        return [], []

    text = dict_page["text"]
    # 目录区：## 📑 目录 到 第一个 --- 分隔线（目录区的分组标题也是 ###，必须剔除）
    toc_match = re.search(r"## 📑 目录(.*?)\n---", text, re.S)
    if toc_match:
        toc_text = toc_match.group(1)
        body_text = text.replace(toc_match.group(0), "", 1)  # 剔除目录区后才是正文
    else:
        toc_text = ""
        body_text = text

    toc_anchors = {m.group(1).strip() for m in
                   re.finditer(r"\[\[#([^\]|]+)", toc_text)}
    body_h4 = {m.group(1).strip() for m in
               re.finditer(r"^#### (.+)$", body_text, re.M)}
    if body_h4:
        body_items = body_h4
    else:
        body_items = {m.group(1).strip() for m in
                      re.finditer(r"^### (.+)$", body_text, re.M)}

    missing = sorted(body_items - toc_anchors)      # 正文有条目但目录没链接
    dead = sorted(toc_anchors - body_items)         # 目录有链接但正文没条目
    return missing, dead


# ============ 规则4：过期内容 ============

def check_stale(pages):
    """updated 超过 STALE_DAYS 天未更新的内容页 → [(页面名, 最后更新)]"""
    stale = []
    today = date.today()
    for name, page in pages.items():
        if name in NAV_PAGES or not page["updated"]:
            continue
        days = (today - page["updated"]).days
        if days > STALE_DAYS:
            stale.append((name, page["updated"].isoformat(), days))
    return sorted(stale, key=lambda x: -x[2])


# ============ 主入口 ============

def run_health(kb_root=None):
    """跑全部体检 → 报告 dict（测试可直接调用）"""
    kb_root = Path(kb_root) if kb_root else config.KB_ROOT
    pages = collect_pages(kb_root)

    broken = check_broken_links(pages, kb_root)
    orphans = check_orphans(pages, kb_root)
    dict_missing, dict_dead = check_dict_consistency(pages)
    stale = check_stale(pages)

    report = {
        "total_pages": len([n for n in pages if n not in NAV_PAGES]),
        "broken_links": broken,
        "orphans": orphans,
        "dict_missing": dict_missing,
        "dict_dead": dict_dead,
        "stale": stale,
    }
    return report


def print_report(report):
    print("=" * 50)
    print(f"🏥 知识库体检报告（{date.today().isoformat()}）")
    print("=" * 50)
    print(f"📄 内容页总数：{report['total_pages']}")

    print(f"\n🔗 失效链接：{len(report['broken_links'])} 个")
    for src, target in report["broken_links"][:20]:
        print(f"  ❌ {src}.md → [[{target}]]（页面不存在）")

    print(f"\n🕳️ 孤立页面（无内容页链接它）：{len(report['orphans'])} 个")
    for name in report["orphans"][:20]:
        print(f"  ⚠️ {name}.md")

    print(f"\n📖 词典一致性：缺目录条目 {len(report['dict_missing'])} 个 / 死锚点 {len(report['dict_dead'])} 个")
    for name in report["dict_missing"][:20]:
        print(f"  📝 正文有条目但目录没链接：{name}")
    for name in report["dict_dead"][:10]:
        print(f"  💀 目录有链接但正文没条目：{name}")

    print(f"\n⏰ 过期内容（> {STALE_DAYS} 天未更新）：{len(report['stale'])} 个")
    for name, updated, days in report["stale"][:10]:
        print(f"  🕐 {name}.md（最后更新 {updated}，{days} 天）")

    total = (len(report["broken_links"]) + len(report["orphans"]) +
             len(report["dict_missing"]) + len(report["dict_dead"]) +
             len(report["stale"]))
    print(f"\n{'=' * 50}")
    print(f"{'✅ 知识库健康' if total == 0 else f'⚠️ 共发现 {total} 个问题，建议逐项修复'}")
    return report


if __name__ == "__main__":
    report = run_health()
    print_report(report)
