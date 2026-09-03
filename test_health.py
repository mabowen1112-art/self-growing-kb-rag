# test_health.py — 体检模块测试（V2-卡1）
# 运行：uv run pytest test_health.py（纯本地，零 API 费）
# 用临时构造的迷你知识库验证四条规则，不依赖真实库状态

import shutil
import tempfile
from pathlib import Path

import health_check as hc


def make_fixture_kb(root: Path):
    """构造迷你知识库，埋入各类问题：
    - 失效链接：a.md → [[不存在的页]]
    - 孤立页：c.md 无人链接
    - 词典：目录漏了"信息差"条目
    - 正常关联：a → b 互链"""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)

    (concepts / "a.md").write_text(
        "---\ntitle: a\nupdated: 2026-09-01\n---\n\n# a\n\n链接到 [[b]]，还指向 [[不存在的页]]。\n", encoding="utf-8")
    (concepts / "b.md").write_text(
        "---\ntitle: b\nupdated: 2026-09-01\n---\n\n# b\n\n回链 [[a]]。\n", encoding="utf-8")
    (concepts / "c.md").write_text(
        "---\ntitle: c\nupdated: 2026-09-01\n---\n\n# c\n\n没有人链接我。\n", encoding="utf-8")
    # 词典：目录区列了 甲/乙，正文有 甲/乙/信息差（信息差漏目录）
    (concepts / "学习概念词典.md").write_text(
        "---\ntitle: 学习概念词典\nupdated: 2026-09-01\n---\n\n# 学习概念词典\n\n## 📑 目录\n\n### 一、测试区\n**子组**：[[#甲|甲]] · [[#乙|乙]]\n\n---\n\n## 一、测试区\n\n### 甲\n条目甲内容。\n\n### 乙\n条目乙内容。\n\n### 信息差（Information Asymmetry）\n目录漏掉了我。\n", encoding="utf-8")
    # 图片附件（链接可指向非 md 资源）
    (root / "图片附件.png").write_bytes(b"fake-png")
    (concepts / "d.md").write_text(
        "---\ntitle: d\nupdated: 2026-09-01\n---\n\n# d\n\n嵌入图片 [[图片附件.png]]。\n", encoding="utf-8")


def test_broken_links():
    tmp = Path(tempfile.mkdtemp(prefix="health_test_"))
    try:
        make_fixture_kb(tmp)
        pages = hc.collect_pages(tmp)
        broken = hc.check_broken_links(pages, tmp)
        assert ("a", "不存在的页") in broken, f"应发现失效链接：{broken}"
        # 图片链接不算失效
        assert not any(t == "图片附件.png" for _s, t in broken), f"图片误报失效：{broken}"
        # 正常互链不算失效
        assert len(broken) == 1, f"应只有 1 个失效链接：{broken}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_orphans():
    tmp = Path(tempfile.mkdtemp(prefix="health_test_"))
    try:
        make_fixture_kb(tmp)
        pages = hc.collect_pages(tmp)
        orphans = hc.check_orphans(pages)
        assert "c" in orphans, f"c 应判孤立：{orphans}"
        assert "a" not in orphans and "b" not in orphans, f"互链页不该孤立：{orphans}"
        assert "d" in orphans, f"d 只链图片不算入链：{orphans}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dict_consistency():
    tmp = Path(tempfile.mkdtemp(prefix="health_test_"))
    try:
        make_fixture_kb(tmp)
        pages = hc.collect_pages(tmp)
        missing, dead = hc.check_dict_consistency(pages)
        assert "信息差（Information Asymmetry）" in missing, f"应发现漏目录条目：{missing}"
        assert dead == [], f"不应有死锚点：{dead}"
        # 分区标题（一、测试区）不应被算作条目
        assert not any("测试区" in m for m in missing), f"分区标题误报为条目：{missing}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_kb_finds_issues():
    """真实知识库 smoke test：体检能跑且报告结构完整（不绑定具体问题——
    已知问题修复后可能为 0，断言的是机制本身工作）"""
    report = hc.run_health()
    assert report["total_pages"] > 20, f"真实库页数异常：{report['total_pages']}"
    # 报告结构完整
    assert isinstance(report["broken_links"], list)
    assert isinstance(report["orphans"], list)
    assert isinstance(report["dict_missing"], list)
    assert isinstance(report["stale"], list)
    print(f"  真实库体检：{report['total_pages']} 页，失效链接 {len(report['broken_links'])}，"
          f"词典遗漏 {len(report['dict_missing'])}，孤立 {len(report['orphans'])}")


if __name__ == "__main__":
    test_broken_links()
    test_orphans()
    test_dict_consistency()
    test_real_kb_finds_issues()
    print("✅ 体检模块测试全部通过")
