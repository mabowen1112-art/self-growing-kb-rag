# test_review_sync.py — 拖拽感知同步器测试（V2-卡4）
# 运行：uv run pytest test_review_sync.py
# 3 场景：草稿被拖走=approved / 被删除=rejected / 未动=跳过；幂等

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

import config as cfg

TEST_TMP = None


def setup_env(tmp: Path):
    """迷你库：raw/_草稿/ 下 3 篇草稿 + 3 个 draft 任务"""
    concepts = tmp / "concepts"
    concepts.mkdir(parents=True)
    draft = tmp / "raw" / "_草稿"
    draft.mkdir(parents=True)

    # 草稿A：将"拖走"到 concepts（模拟用户转正）
    (draft / "笔记A.md").write_text(
        "---\ntitle: 笔记A\ntags: [待审核]\n---\n\n内容A\n", encoding="utf-8")
    # 草稿B：将被删除（模拟用户拒绝）
    (draft / "笔记B.md").write_text(
        "---\ntitle: 笔记B\ntags: [待审核]\n---\n\n内容B\n", encoding="utf-8")
    # 草稿C：保持不动
    (draft / "笔记C.md").write_text(
        "---\ntitle: 笔记C\ntags: [待审核]\n---\n\n内容C\n", encoding="utf-8")

    conn = sqlite3.connect(tmp / "kb_meta.db")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
                 "target TEXT, detail TEXT, confidence TEXT, source TEXT, status TEXT, created_at TEXT)")
    for name in ["笔记A.md", "笔记B.md", "笔记C.md"]:
        conn.execute("INSERT INTO tasks (type, target, detail, confidence, status) VALUES "
                     "('new_knowledge', ?, 'x', 'low', 'draft')", (name,))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    global TEST_TMP
    tmp = Path(tempfile.mkdtemp(prefix="rsync_test_"))
    TEST_TMP = tmp
    monkeypatch.setattr(cfg, "KB_ROOT", tmp)
    monkeypatch.setattr(cfg, "META_DB", tmp / "kb_meta.db")
    monkeypatch.setattr(cfg, "DRAFT_DIR", tmp / "raw" / "_草稿")
    setup_env(tmp)
    yield
    shutil.rmtree(tmp, ignore_errors=True)


def test_three_scenarios():
    """拖走→approved(清待审核tag) / 删除→rejected / 未动→跳过"""
    import review_sync

    # 模拟用户操作：A 拖到 concepts，B 删除
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记A.md", TEST_TMP / "concepts" / "笔记A.md")
    (TEST_TMP / "raw" / "_草稿" / "笔记B.md").unlink()

    stats = review_sync.sync_reviews()
    assert stats["approved"] == 1 and stats["rejected"] == 1, stats

    # 状态断言
    conn = sqlite3.connect(cfg.META_DB)
    statuses = dict(conn.execute("SELECT target, status FROM tasks").fetchall())
    conn.close()
    assert statuses["笔记A.md"] == "approved"
    assert statuses["笔记B.md"] == "rejected"
    assert statuses["笔记C.md"] == "draft", "未动的草稿任务应保持 draft"

    # A 的"待审核"tag 被清理
    content = (TEST_TMP / "concepts" / "笔记A.md").read_text(encoding="utf-8")
    assert "待审核" not in content, f"待审核 tag 未清理：{content}"

    # log 写了两条（approved + rejected）
    log_text = (TEST_TMP / "log.md").read_text(encoding="utf-8")
    assert "审核通过" in log_text and "审核拒绝" in log_text, log_text


def test_idempotent():
    """幂等：approved/rejected 的任务不再被处理"""
    import review_sync
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记A.md", TEST_TMP / "concepts" / "笔记A.md")
    (TEST_TMP / "raw" / "_草稿" / "笔记B.md").unlink()

    review_sync.sync_reviews()
    stats2 = review_sync.sync_reviews()  # 再跑一次
    assert stats2["approved"] == 0 and stats2["rejected"] == 0, \
        f"第二次应零处理：{stats2}"
    # log 不应重复写
    log_text = (TEST_TMP / "log.md").read_text(encoding="utf-8")
    assert log_text.count("审核通过") == 1, "log 重复写入！"
