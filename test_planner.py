# test_planner.py — 规划器测试（V2-卡2）
# 运行：uv run pytest test_planner.py
# 假 LLM 注入测 入库/去重/校验；真实 LLM 一次测端到端结构

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import planner


def make_fixture_kb(root: Path):
    """迷你知识库：1 个失效链接 + 1 个待学习条目 + 1 篇未分类笔记"""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "笔记A.md").write_text(
        "---\ntitle: 笔记A\ndomain: life\ntags: [finance]\n---\n\n## 定投\n定投内容。引用不存在的 [[年金]]。\n",
        encoding="utf-8")
    (concepts / "待学习清单.md").write_text(
        "---\ntitle: 待学习清单\n---\n\n## 清单\n- ⏳ **量化交易**：远期想学\n- ✅ 已完成项\n",
        encoding="utf-8")
    (concepts / "裸笔记.md").write_text(
        "没有 frontmatter 的裸笔记内容，讲的是今天的学习心得。\n", encoding="utf-8")


def test_validate_tasks():
    """LLM 输出校验：合法保留、非法丢弃、非 JSON 拦截"""
    good = ('[{"type": "new_knowledge", "target": "年金.md", '
            '"detail": "笔记A引用但无内容", "confidence": "low"}, '
            '{"type": "fix_link", "target": "笔记A.md", '
            '"detail": "应指向定投", "confidence": "high"}]')
    tasks, err = planner.validate_tasks(good)
    assert err is None and len(tasks) == 2, (tasks, err)

    # 坏类型/缺字段丢弃
    bad = ('[{"type": "hack_kb", "target": "x", "detail": "y", "confidence": "high"}, '
           '{"type": "new_knowledge", "target": "", "detail": "", "confidence": "low"}]')
    tasks, err = planner.validate_tasks(bad)
    assert tasks == [], f"非法任务应全丢弃：{tasks}"

    # 非 JSON 拦截
    tasks, err = planner.validate_tasks("我不是JSON")
    assert tasks == [] and err, "非 JSON 应拦截"


def test_save_dedup():
    """入库 + 去重：同 type+target 不重复"""
    tmp = Path(tempfile.mkdtemp(prefix="planner_test_"))
    try:
        meta_db = tmp / "test.db"
        tasks = [{"type": "new_knowledge", "target": "年金.md",
                  "detail": "d", "confidence": "low"}]
        added1 = planner.save_tasks(tasks, meta_db=meta_db)
        added2 = planner.save_tasks(tasks, meta_db=meta_db)  # 重复跑
        assert added1 == 1 and added2 == 0, f"去重失败：{added1}/{added2}"

        # done 的任务也去重；rejected 的可重新生成
        conn = sqlite3.connect(meta_db)
        conn.execute("UPDATE tasks SET status='rejected' WHERE target='年金.md'")
        conn.commit()
        conn.close()
        added3 = planner.save_tasks(tasks, meta_db=meta_db)
        assert added3 == 1, f"rejected 后应可重新生成：{added3}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_planner_with_fake_llm():
    """端到端（假 LLM）：信号收集 → 假 LLM → 入库"""
    tmp = Path(tempfile.mkdtemp(prefix="planner_test_"))
    try:
        kb = tmp / "kb"
        make_fixture_kb(kb)
        meta_db = tmp / "test.db"

        def fake_llm(signals_text):
            # 假 LLM：从信号里提取信息生成任务
            assert "待学习清单" in signals_text or "年金" in signals_text
            return json.dumps([{"type": "new_knowledge", "target": "量化交易.md",
                                "detail": "待学习清单提到想学", "confidence": "low"}])

        added = planner.run_planner(llm_fn=fake_llm, kb_root=kb, meta_db=meta_db)
        assert added == 1, f"应入库 1 个任务：{added}"
        conn = sqlite3.connect(meta_db)
        row = conn.execute("SELECT type, target, status FROM tasks").fetchone()
        conn.close()
        assert row and row[0] == "new_knowledge" and row[2] == "pending", f"任务状态异常：{row}"

        # 知识库零改动（规划器只读）
        original = (kb / "concepts" / "笔记A.md").read_text(encoding="utf-8")
        assert "年金" in original and "domain: life" in original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_llm_end_to_end():
    """真实 LLM 一次：端到端结构合法（真实库，真实 DeepSeek 调用）"""
    added = planner.run_planner()
    assert added >= 0  # 0 = 没新任务也合法；主要验证不崩 + 结构走通
    print(f"  真实规划完成，新增任务 {added} 个")


if __name__ == "__main__":
    test_validate_tasks()
    test_save_dedup()
    test_run_planner_with_fake_llm()
    test_real_llm_end_to_end()
    print("✅ 规划器测试全部通过")
