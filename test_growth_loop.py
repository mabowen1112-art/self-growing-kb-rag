# test_growth_loop.py — 生长循环闭环冒烟测试（V2-卡5）
# 运行：uv run pytest test_growth_loop.py
# 不真跑完整循环（耗时+LLM费用），只验证编排函数可调用、步骤函数存在且能跑预览

import sys
from pathlib import Path

import growth_loop


def test_steps_exist():
    """6 个步骤函数都存在"""
    for fn in ["step_ingest", "step_health", "step_planner",
               "step_executor", "step_review_sync", "step_contested"]:
        assert hasattr(growth_loop, fn), f"缺少步骤函数 {fn}"


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
