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
    # 草稿D：P2 转正收尾自动化测试用（带 domain/summary；正文含 AI 方向关键词→分类器可判）
    (draft / "笔记D.md").write_text(
        "---\ntitle: 笔记D\ndomain: ai\nsummary: 测试摘要：RAG 检索链路\ntags: [待审核]\n---\n\n"
        "# 笔记D\n\n本文讲 RAG 检索：embedding 向量、rerank 排序、大模型 LLM 调用。\n",
        encoding="utf-8")

    # index.md（P2 测试用）：声明 2 页 / 2 条
    (tmp / "index.md").write_text(
        "# 知识库索引\n\n> 内容目录。\n> 最后更新：2026-09-15 | 总页数：2\n\n---\n\n"
        "## 🧠 AI/AIGC\n\n### Concepts\n- [[原有页]] — 原有内容\n\n---\n\n"
        "## 💡 个人通用\n\n### Concepts\n- [[另一个原有页]] — 另一个\n", encoding="utf-8")

    conn = sqlite3.connect(tmp / "kb_meta.db")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
                 "target TEXT, detail TEXT, confidence TEXT, source TEXT, status TEXT, created_at TEXT)")
    for name in ["笔记A.md", "笔记B.md", "笔记C.md", "笔记D.md"]:
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


def test_promotion_autofinish():
    """P2（2026-09-16）：转正自动收尾——清待审核 + 补 tags（分类器）+ 自动补 index 条目与页数"""
    import review_sync
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记D.md", TEST_TMP / "concepts" / "笔记D.md")
    stats = review_sync.sync_reviews()
    assert stats["approved"] == 1, stats
    assert stats["tags_filled"] == 1, f"应自动补 tags：{stats}"
    assert stats["index_added"] == 1, f"应自动补 index 条目：{stats}"

    content = (TEST_TMP / "concepts" / "笔记D.md").read_text(encoding="utf-8")
    assert "待审核" not in content, content
    assert "tags: [" in content and "tags: []" not in content, f"tags 未补上：{content}"
    assert "tool" in content, f"分类器 tags 应写入：{content}"

    idx = (TEST_TMP / "index.md").read_text(encoding="utf-8")
    assert "[[笔记D]]" in idx, f"index 未自动收录：{idx}"
    assert "总页数：3" in idx, f"总页数应 +1 = 3：{idx}"
    assert idx.index("[[笔记D]]") > idx.index("## 🧠 AI/AIGC"), "应插到 AI 段"
    assert idx.index("[[笔记D]]") < idx.index("## 💡 个人通用"), "不应插到个人通用段"


def test_move_to_new_folder_still_approved():
    """用户新建文件夹再拖进去 → 照样认定 approved（2026-09-16 验证）"""
    import review_sync
    new_dir = TEST_TMP / "concepts" / "新文件夹"
    new_dir.mkdir(parents=True)
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记A.md", new_dir / "笔记A.md")
    stats = review_sync.sync_reviews()
    assert stats["approved"] == 1, f"拖进新文件夹应算通过：{stats}"
    conn = sqlite3.connect(cfg.META_DB)
    st = conn.execute("SELECT status FROM tasks WHERE target='笔记A.md'").fetchone()[0]
    conn.close()
    assert st == "approved", st


def test_move_into_raw_is_not_approval():
    """raw 是证据层，不能因草稿移入而自动转正。"""
    import review_sync
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记A.md", TEST_TMP / "raw" / "笔记A.md")
    stats = review_sync.sync_reviews()
    assert stats["approved"] == 0 and stats["missing"] == 1, stats
    conn = sqlite3.connect(cfg.META_DB)
    status = conn.execute("SELECT status FROM tasks WHERE target='笔记A.md'").fetchone()[0]
    conn.close()
    assert status == "draft"


def test_existing_same_name_is_not_approval():
    """草稿消失时，不能拿已有同名正式页冒充本次审核通过。"""
    import review_sync
    (TEST_TMP / "concepts" / "笔记A.md").write_text(
        "---\ntitle: 笔记A\ntags: [tool]\n---\n\n原有正式页\n", encoding="utf-8")
    (TEST_TMP / "raw" / "_草稿" / "笔记A.md").unlink()
    stats = review_sync.sync_reviews()
    assert stats["approved"] == 0 and stats["missing"] == 1, stats
    conn = sqlite3.connect(cfg.META_DB)
    status = conn.execute("SELECT status FROM tasks WHERE target='笔记A.md'").fetchone()[0]
    conn.close()
    assert status == "draft"


def test_trash_is_rejected_and_draft_subfolder_untouched():
    """① 删到库内回收站 .trash → 算拒绝（不算"正式目录找到"）
       ② 草稿挪进 _草稿 的子文件夹 → 算未动（不误判拒绝）"""
    import review_sync
    (TEST_TMP / ".trash").mkdir()
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记B.md", TEST_TMP / ".trash" / "笔记B.md")
    sub = TEST_TMP / "raw" / "_草稿" / "备份"
    sub.mkdir()
    shutil.move(TEST_TMP / "raw" / "_草稿" / "笔记C.md", sub / "笔记C.md")

    stats = review_sync.sync_reviews()
    assert stats["rejected"] == 1, f".trash 里的草稿应算拒绝：{stats}"
    # 未动 = A（原地）+ C（_草稿 子文件夹）+ D（原地）
    assert stats["untouched"] == 3, f"草稿区子文件夹应算未动：{stats}"
    conn = sqlite3.connect(cfg.META_DB)
    st = dict(conn.execute("SELECT target, status FROM tasks").fetchall())
    conn.close()
    assert st["笔记B.md"] == "rejected", st
    assert st["笔记C.md"] == "draft", st


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
