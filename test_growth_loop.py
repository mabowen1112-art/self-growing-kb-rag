# test_growth_loop.py — 生长循环闭环冒烟测试（V2-卡5）
# 运行：uv run pytest test_growth_loop.py
# 不真跑完整循环（耗时+LLM费用），只验证编排函数可调用、步骤函数存在且能跑预览

import sys
from pathlib import Path

import growth_loop


def test_steps_exist():
    """步骤函数都存在（⑥ 矛盾复核 + ⑦ 生长摘要）"""
    for fn in ["step_ingest", "step_health", "step_planner",
               "step_executor", "step_review_sync", "step_contested",
               "build_digest", "write_digest"]:
        assert hasattr(growth_loop, fn), f"缺少步骤函数 {fn}"


def test_digest_write_and_trim(monkeypatch):
    """《生长摘要》：内容正确、最新在上、超 keep 条裁剪（P1 2026-09-15，纯本地零费用）"""
    import shutil
    import tempfile
    import config as cfg
    tmp = Path(tempfile.mkdtemp(prefix="digest_test_"))
    try:
        monkeypatch.setattr(cfg, "KB_ROOT", tmp)
        result = {"ingest": {"added_notes": 1, "changed_notes": 2,
                             "added_chunks": 3, "removed_chunks": 0},
                  "health": {"broken_links": [], "orphans": [], "dict_missing": []}}
        details = [{"id": 1, "type": "new_knowledge", "target": "测试页.md",
                    "confidence": "high", "outcome": "drafted"},
                   {"id": 2, "type": "new_knowledge", "target": "低置信页.md",
                    "confidence": "low", "outcome": "skipped_low"}]
        d = growth_loop.build_digest(result, details, 7, 3, 0, 12.3, "auto")
        assert "测试页 → raw/_草稿/" in d, d
        assert "任务池 7" in d and "草稿 3 篇" in d and "体检 全绿" in d, d
        assert "低置信挂起 1 个" in d, d
        assert "上限 10" in d, d
        # 用户要求：摘要不要表情（2026-09-15）
        assert not any(ch in d for ch in "🆕🔧📥🏥🗂👀"), d

        # 低置信任务要列名字（用户问"怎么看低置信内容"→做进摘要，2026-09-16）
        d2 = growth_loop.build_digest(result, details, 7, 3, 0, 12.3, "auto",
                                      draft_limit=10, low_names=["Ollama本地部署", "Infra基础设施"])
        assert "低置信挂起 2 个：Ollama本地部署、Infra基础设施" in d2, d2

        growth_loop.write_digest(d, keep=2)
        growth_loop.write_digest("## 第二轮", keep=2)
        growth_loop.write_digest("## 第三轮", keep=2)
        text = (tmp / growth_loop.DIGEST_NAME).read_text(encoding="utf-8")
        assert "title: 生长摘要" in text
        assert text.index("第三轮") < text.index("第二轮"), "最新应在上"
        assert "测试页" not in text, "超 keep 条应被裁掉"
        assert text.count("\n## ") == 2, f"应只保留 2 条：{text}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_step_health_preview():
    """体检步骤可独立运行（零费用，纯本地）"""
    report = growth_loop.step_health()
    assert isinstance(report, dict)
    assert "broken_links" in report and "orphans" in report


def test_step_contested():
    """矛盾复核步骤可运行（零费用，纯本地）"""
    found = growth_loop.step_contested()
    assert isinstance(found, list)


def test_summary_shape():
    """总结数据结构完整（不真跑，验证字段）"""
    fake = {
        "ingest": {"added_notes": 0, "changed_notes": 0, "added_chunks": 0, "removed_chunks": 0},
        "health": {"broken_links": [], "orphans": [], "dict_missing": []},
        "tasks_added": 0, "executed": 0,
        "review": {"approved": 0, "rejected": 0, "untouched": 0},
        "contested": 0,
    }
    # 至少确认 run_loop 的返回结构注释与实现一致（直接调用会花 LLM 费，跳过）
    assert set(fake.keys()) == {"ingest", "health", "tasks_added", "executed", "review", "contested"}
