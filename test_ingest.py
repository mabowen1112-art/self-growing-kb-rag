# test_ingest.py — 增量导入器验证（真实调 embedding API，约 10-20 秒）
# 运行：uv run pytest test_ingest.py
# 检查：① 首次导入 ② 幂等（再跑零变化）③ 新增 ④ 修改（块数不变）⑤ 删除
# 使用临时目录构造迷你知识库，不污染真实 kb_db / kb_meta.db / 真实知识库

import shutil
import sqlite3
import tempfile
from pathlib import Path

import chromadb

import ingest
from extractors import chunk_md


def test_new_raw_markdown_is_never_rewritten(tmp_path, monkeypatch):
    """原始资料允许导入，但导入器不能替它补 frontmatter。"""
    raw = tmp_path / "raw" / "articles"
    raw.mkdir(parents=True)
    path = raw / "原始记录.md"
    original = "# 原始记录\n\n保留原文，不添加元数据。\n"
    path.write_text(original, encoding="utf-8")
    rel = "raw/articles/原始记录.md"

    class Collection:
        def get(self, where):
            return {"ids": []}

    import directions
    monkeypatch.setattr(directions, "classify", lambda _text: {})
    monkeypatch.setattr(directions, "ensure_frontmatter", lambda text, _result: (
        text + "\n不应写入\n", ["summary"]))
    monkeypatch.setattr(ingest, "extract", lambda *_args: [])
    conn = ingest.init_meta_db(tmp_path / "meta.db")
    try:
        ingest.sync(conn, Collection(), {rel: {"abs": path, "title": "原始记录", "hash": "new"}})
    finally:
        conn.close()
    assert path.read_text(encoding="utf-8") == original


def test_repeated_heading_has_unique_chunk_ids(monkeypatch):
    """同一笔记重复二级标题不能让 Chroma 收到重复 ID。"""
    rel = "concepts/重复标题.md"
    chunks = [(rel, "摘要", "摘要内容足够长，确保会作为独立切片进入向量库。"),
              (rel, "同名标题", "第一段正文足够长，确保会作为独立切片进入向量库。"),
              (rel, "同名标题", "第二段正文足够长，确保会作为独立切片进入向量库。")]

    class Collection:
        def __init__(self):
            self.ids = []

        def get(self, where):
            return {"ids": []}

        def add(self, ids, embeddings, documents, metadatas):
            assert len(ids) == len(set(ids)), "重复标题产生了相同的 Chroma ID"
            self.ids = ids

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE notes (rel_path TEXT PRIMARY KEY, domain TEXT, title TEXT, "
                 "chunk_count INTEGER, content_hash TEXT, imported_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO notes (rel_path, content_hash) VALUES (?, ?)", (rel, "old"))
    monkeypatch.setattr(ingest, "extract", lambda *_: chunks)
    monkeypatch.setattr(ingest, "embed_texts", lambda docs: [[0.0]] * len(docs))
    collection = Collection()
    try:
        ingest.sync(conn, collection, {rel: {"abs": Path("unused.md"),
                                             "title": "重复标题", "hash": "new"}})
    finally:
        conn.close()
    assert len(collection.ids) == 3


def test_frontmatter_not_chunked():
    """防回归（PRD §9.7）：YAML frontmatter 不能被切进向量块（曾导致检索污染）"""
    note = """---
title: 测试笔记
domain: ai
tags: [tool]
summary: 只有元数据没有正文的笔记
---

## 正文标题
这里才是真正的正文内容，足够长不会被过滤掉。这段内容讲的是如何验证 frontmatter 不会被切进向量块，字数足够超过三十字符的过滤阈值。
"""
    blocks = chunk_md(note, "测试.md")
    # frontmatter 内容绝不能出现在任何块里
    all_text = " ".join(c for _s, _h, c in blocks)
    assert "domain: ai" not in all_text, "frontmatter 泄漏进向量块！"
    assert "summary: 只有元数据" not in all_text, "frontmatter 泄漏进向量块！"
    # 块只来自正文
    assert any("正文标题" == h for _s, h, _c in blocks), "正文块丢失"


def make_mini_kb(root: Path):
    """构造迷你知识库：concepts/ 下 2 篇笔记
    带完整 frontmatter（domain/tags/summary）→ 卡4 分类器不会改动它们，
    让测试专注验证增量导入逻辑"""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "笔记A.md").write_text(
        "---\ntitle: 笔记A\ndomain: life\ntags: [finance]\nsummary: 复利与风险测试笔记\n---\n\n## 复利是什么\n复利是指利息再生利息的计息方式，爱因斯坦称之为世界第八大奇迹，长期坚持复利效应巨大。\n\n## 风险与收益\n风险与收益成正比，高收益往往伴随高风险，投资前需要评估自己的风险承受能力。\n",
        encoding="utf-8")
    (concepts / "笔记B.md").write_text(
        "---\ntitle: 笔记B\ndomain: life\ntags: [finance]\nsummary: 定投策略测试笔记\n---\n\n## 定投策略\n定期定额投资基金，通过长期分批买入摊平成本，是适合普通人的理财方式。\n",
        encoding="utf-8")


def count_blocks(db_dir: Path) -> int:
    col = chromadb.PersistentClient(path=str(db_dir)).get_collection("my_kb")
    return col.count()


def test_incremental_ingest():
    tmp = Path(tempfile.mkdtemp(prefix="ingest_test_"))
    try:
        kb_root = tmp / "kb"
        make_mini_kb(kb_root)
        db_dir = tmp / "chroma"
        meta_db = tmp / "kb_meta.db"

        # ① 首次导入：2 篇新增
        s1 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s1["added_notes"] == 2, f"首次应导入 2 篇：{s1}"
        assert s1["added_chunks"] >= 2, f"至少 2 块：{s1}"
        blocks1 = count_blocks(db_dir)
        assert blocks1 == s1["added_chunks"], "库内块数应等于本次新增块数"

        # ② 幂等：再跑一次，零新增、零向量化
        s2 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s2["added_notes"] == 0 and s2["changed_notes"] == 0 and s2["removed_notes"] == 0, \
            f"第二次应零变化：{s2}"
        assert s2["skipped"] == 2, f"2 篇都应跳过：{s2}"
        assert s2["added_chunks"] == 0, f"不应再调 embedding：{s2}"
        assert count_blocks(db_dir) == blocks1, "块数不应变化"

        # ③ 新增 1 篇
        (kb_root / "concepts" / "笔记C.md").write_text(
            "---\ntitle: 笔记C\ndomain: life\ntags: [finance]\nsummary: 基金定投优点测试笔记\n---\n\n## 基金定投的优点\n强制储蓄、摊平成本、省心省力，适合没有时间研究市场的普通投资者长期坚持。\n",
            encoding="utf-8")
        s3 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s3["added_notes"] == 1, f"应新增 1 篇：{s3}"
        blocks3 = count_blocks(db_dir)
        assert blocks3 > blocks1, "新增后块数应增加"

        # ④ 修改 笔记A 内容（块数可能变，但该笔记被标记为 changed）
        (kb_root / "concepts" / "笔记A.md").write_text(
            "---\ntitle: 笔记A\n---\n\n## 复利是什么（修订）\n复利是指利息再生利息的计息方式，长期坚持复利效应巨大，巴菲特财富主要来自复利。\n\n## 风险与收益\n风险与收益成正比，高收益往往伴随高风险。\n\n## 新增小节\n用来验证修改后块数变化的第三个小节，内容足够长不会被过滤掉。\n",
            encoding="utf-8")
        s4 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s4["changed_notes"] == 1, f"应检测到 1 篇修改：{s4}"
        assert s4["added_notes"] == 0 and s4["removed_notes"] == 0, f"不应有其他变化：{s4}"
        assert s4["removed_chunks"] > 0, "修改应删除旧块"
        assert s4["added_chunks"] > 0, "修改应添加新块"
        # 修改后总块数 = 旧块数 - 删掉的 + 新增的（本次标题变了 → 2旧块删、3新块加）
        assert count_blocks(db_dir) == blocks3 - s4["removed_chunks"] + s4["added_chunks"]

        # ⑤ 删除 笔记B（frontmatter 被切片跳过 → 只有定投策略 1 块）
        (kb_root / "concepts" / "笔记B.md").unlink()
        s5 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s5["removed_notes"] == 1, f"应删除 1 篇：{s5}"
        assert s5["removed_chunks"] == 1, f"笔记B 有 1 块（frontmatter 跳过）：{s5}"
        assert count_blocks(db_dir) == blocks3 - s4["removed_chunks"] + s4["added_chunks"] - 1

        print(f"✅ test_ingest 通过：首次导入→幂等→新增→修改→删除 全链路正常（最终 {count_blocks(db_dir)} 块）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_incremental_ingest()
    print("✅ 增量导入器验证通过")
