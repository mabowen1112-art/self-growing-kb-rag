# test_executor.py — 执行器测试（V2-卡3）
# 运行：uv run pytest test_executor.py
# 用临时迷你库 + 注入假 LLM：测 改链接 / 备份回滚 / 草稿生成 / 日志 / 排除目录

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# 测试前把 config 指向临时库（executor/planner 都读 config）
import config as cfg

TEST_TMP = None
_cleanup = []


def setup_kb(tmp: Path):
    """构造迷你知识库 + 一条 fix_link pending 任务 + 一条 new_knowledge 任务"""
    concepts = tmp / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "笔记A.md").write_text(
        "---\ntitle: 笔记A\ndomain: life\n---\n\n# 笔记A\n\n引用 [[错误页面]] 和 [[正确页面]]。\n",
        encoding="utf-8")
    (concepts / "正确页面.md").write_text(
        "---\ntitle: 正确页面\n---\n\n# 正确页面\n", encoding="utf-8")
    # 草稿区（应被 ingest 排除）
    draft = tmp / "raw" / "_草稿"
    draft.mkdir(parents=True)
    (draft / "草稿测试.md").write_text("草稿内容", encoding="utf-8")

    # 建 meta db + tasks + 备份表
    conn = sqlite3.connect(tmp / "kb_meta.db")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
                 "target TEXT, detail TEXT, confidence TEXT, source TEXT, status TEXT, created_at TEXT)")
    conn.execute("""CREATE TABLE task_undo (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
        file_path TEXT, original_content TEXT, executed_at TEXT)""")
    conn.execute("INSERT INTO tasks (type, target, detail, confidence, status) VALUES "
                 "('fix_link', '笔记A', '应改为指向 [[正确页面]]', 'high', 'pending')")
    conn.execute("INSERT INTO tasks (type, target, detail, confidence, status) VALUES "
                 "('new_knowledge', '年金.md', '待学习清单提到', 'low', 'pending')")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    """每个测试：独立临时目录 + config 指向它，用完清理"""
    global TEST_TMP
    tmp = Path(tempfile.mkdtemp(prefix="exec_test_"))
    _cleanup.append(tmp)
    TEST_TMP = tmp
    monkeypatch.setattr(cfg, "KB_ROOT", tmp)
    monkeypatch.setattr(cfg, "META_DB", tmp / "kb_meta.db")
    monkeypatch.setattr(cfg, "DRAFT_DIR", tmp / "raw" / "_草稿")
    setup_kb(tmp)
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def test_auto_safe_low_not_counted(monkeypatch):
    """auto_safe：低置信跳过不占 max_tasks 名额（2026-09-15 修复：队头低置信曾白占名额）"""
    import executor
    conn = sqlite3.connect(cfg.META_DB)
    # 现有：1 高 fix_link + 1 低 new_knowledge；再加 2 个高 new_knowledge
    conn.execute("INSERT INTO tasks (type, target, detail, confidence, status) VALUES "
                 "('new_knowledge', '高页1.md', 'd', 'high', 'pending')")
    conn.execute("INSERT INTO tasks (type, target, detail, confidence, status) VALUES "
                 "('new_knowledge', '高页2.md', 'd', 'high', 'pending')")
    conn.commit()
    conn.close()

    calls = []

    def fake_draft(task, apply=False):
        calls.append(task["target"])
        return task["target"].replace(".md", ""), "草稿已生成 → raw/_草稿/（测试）"

    monkeypatch.setattr(executor, "draft_new_page", fake_draft)

    sink = []
    done = executor.run_executor(apply=True, max_tasks=2, auto_safe=True, detail_sink=sink)
    assert done == 2, f"max=2 应处理 2 个高置信（低置信不占名额）：{done}"
    assert calls == ["高页1.md"], f"第 1 个高置信 fix_link 之后应轮到高页1：{calls}"
    outcomes = [x["outcome"] for x in sink]
    assert outcomes == ["fixed", "skipped_low", "drafted"], outcomes


def test_fix_link_dry_run_no_write():
    """dry-run 不改文件"""
    import executor
    conn = executor._conn()
    tasks = executor.get_pending_tasks(conn)
    conn.close()
    fix = [t for t in tasks if t["type"] == "fix_link"][0]
    action, result = executor.execute_deterministic(fix, apply=False)
    # dry-run 模式下不应写（当前实现 execute_deterministic 无论 apply 与否都调 fix_link_in_file，
    # 由 CLI 层控制 —— 这里直接验证 apply 路径）
    content = (TEST_TMP / "concepts" / "笔记A.md").read_text(encoding="utf-8")
    assert "[[错误页面]]" in content, "dry-run 后文件不应被改动"


def test_fix_link_apply_and_backup():
    """apply：链接被修 + 原文进备份表 + 可回滚"""
    import executor
    conn = executor._conn()
    fix = [t for t in executor.get_pending_tasks(conn) if t["type"] == "fix_link"][0]
    action, result = executor.execute_deterministic(fix, apply=True)
    conn.close()

    assert result == "ok", result
    content = (TEST_TMP / "concepts" / "笔记A.md").read_text(encoding="utf-8")
    assert "[[错误页面]]" not in content and "[[正确页面]]" in content, f"链接没修对：{content}"

    # 回滚
    conn = executor._conn()
    restored = executor.rollback(conn, task_id=fix["id"])
    conn.close()
    assert restored >= 1
    content = (TEST_TMP / "concepts" / "笔记A.md").read_text(encoding="utf-8")
    assert "[[错误页面]]" in content, "回滚失败"


def test_draft_new_page(monkeypatch):
    """new_knowledge：起草草稿（注入假 LLM 内容），不碰正式库"""
    import executor

    # 检索走"无素材"分支（避免真实 embedding 调用）
    import ask_kb

    def fake_retrieve(*a, **kw):
        raise RuntimeError("测试：跳过真实检索")

    monkeypatch.setattr(ask_kb, "retrieve", fake_retrieve)

    task = {"id": 99, "type": "new_knowledge", "target": "年金.md",
            "detail": "测试", "confidence": "low"}

    # monkeypatch LLM 调用：直接给合法 md
    import requests
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content":
                "---\ntitle: 年金\ncreated: 2026-09-03\n---\n\n# 年金\n\n年金是定期收付款项。\n"}}]}

    original_post = requests.post

    def fake_post(url, **kwargs):
        return FakeResp()

    requests.post = fake_post
    try:
        name, result = executor.draft_new_page(task, apply=True)
    finally:
        requests.post = original_post

    assert "草稿已生成" in result, result
    draft_file = cfg.DRAFT_DIR / "年金.md"
    assert draft_file.exists(), "草稿文件不存在"
    content = draft_file.read_text(encoding="utf-8")
    assert "title: 年金" in content, f"草稿内容异常：{content[:100]}"


def test_ingest_excludes_draft_dir():
    """ingest 扫描必须排除 raw/_草稿（修复的 bug）"""
    import ingest
    stats = ingest.run_ingest(kb_root=TEST_TMP, db_dir=TEST_TMP / "chroma",
                              meta_db=TEST_TMP / "kb_meta2.db")
    # 草稿不该进库
    import chromadb
    col = chromadb.PersistentClient(path=str(TEST_TMP / "chroma")).get_collection("my_kb")
    res = col.get()
    docs = " ".join(res["documents"])
    assert "草稿内容" not in docs, "raw/_草稿 被 ingest 扫进去了！"
    assert stats["added_notes"] >= 2, f"正常笔记应入库：{stats}"


def test_execution_log_written():
    """执行日志写入知识库 log.md"""
    import executor
    entries = [{"task_type": "fix_link", "target": "笔记A.md",
                "action": "[[错误页面]] → [[正确页面]]", "result": "已修改文件"}]
    log_path = TEST_TMP / "log.md"
    executor.append_execution_log(entries, dry_run=False)
    text = log_path.read_text(encoding="utf-8")
    assert "AI执行器" in text and "fix_link" in text and "正确页面" in text, f"日志异常：{text[:200]}"
