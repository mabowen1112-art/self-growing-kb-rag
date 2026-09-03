# test_multimodal.py — 多模态导入验证（真实调 embedding API + 本地OCR，约 20-40 秒）
# 运行：uv run pytest test_multimodal.py
# 检查：pdf/docx/图片 三种素材 → 全管线入库 → 检索可命中 → 幂等
# 素材现场生成（临时目录），不污染真实库

import shutil
import tempfile
from pathlib import Path

import chromadb
import pymupdf
from docx import Document
from PIL import Image, ImageDraw, ImageFont

import ingest

FONT = "C:/Windows/Fonts/msyh.ttc"  # 微软雅黑，Windows 自带


def make_pdf(path: Path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "多模态测试PDF第一页：复利是长期投资的核心概念。",
                   fontsize=14, fontname="cn", fontfile=FONT)
    p2 = doc.new_page()
    p2.insert_text((72, 72), "第二页内容：定投策略适合普通投资者长期坚持。",
                   fontsize=14, fontname="cn", fontfile=FONT)
    doc.save(str(path))
    doc.close()


def make_docx(path: Path):
    doc = Document()
    doc.add_heading("Word测试文档标题", level=1)
    doc.add_paragraph("这是docx多模态导入测试正文，讲的是风险管理与资产配置的基本原理，内容足够长。")
    doc.add_paragraph("第二段：分散投资可以降低非系统性风险。")
    doc.save(str(path))


def make_image(path: Path):
    img = Image.new("RGB", (1300, 160), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 32)
    d.text((20, 55), "图片OCR测试：自生长知识库多模态导入验证，本地识别中文不花任何API费用且准确率高", fill="black", font=font)
    img.save(str(path))


def count_blocks(db_dir: Path) -> int:
    col = chromadb.PersistentClient(path=str(db_dir)).get_collection("my_kb")
    return col.count()


def test_multimodal_ingest():
    tmp = Path(tempfile.mkdtemp(prefix="multimodal_test_"))
    try:
        kb_root = tmp / "kb"
        (kb_root / "raw" / "papers").mkdir(parents=True)
        (kb_root / "raw" / "docs").mkdir(parents=True)
        (kb_root / "raw" / "assets").mkdir(parents=True)
        make_pdf(kb_root / "raw" / "papers" / "测试文档.pdf")
        make_docx(kb_root / "raw" / "docs" / "测试文档.docx")
        make_image(kb_root / "raw" / "assets" / "测试图片.png")

        db_dir = tmp / "chroma"
        meta_db = tmp / "meta.db"

        # ① 首次导入：3 个文件全部入库
        s1 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s1["added_notes"] == 3, f"应导入 3 个文件：{s1}"
        assert s1["added_chunks"] >= 3, f"至少 3 块：{s1}"
        blocks1 = count_blocks(db_dir)

        # 每个文件都有块（按 doc_path 查）
        col = chromadb.PersistentClient(path=str(db_dir)).get_collection("my_kb")
        for rel in ["raw/papers/测试文档.pdf", "raw/docs/测试文档.docx", "raw/assets/测试图片.png"]:
            r = col.get(where={"doc_path": rel})
            assert r["ids"], f"{rel} 没有入库"
            print(f"  {rel} → {len(r['ids'])} 块, 首块: {r['documents'][0][:30]!r}")

        # ② 幂等：再跑一次，零变化零向量化
        s2 = ingest.run_ingest(kb_root=kb_root, db_dir=db_dir, meta_db=meta_db)
        assert s2["added_notes"] == 0 and s2["added_chunks"] == 0, f"第二次应零变化：{s2}"
        assert s2["skipped"] == 3, f"3 个文件都应跳过：{s2}"
        assert count_blocks(db_dir) == blocks1

        # ③ 内容可检索：问 PDF 里的概念 → 应命中 pdf 块（注入临时库，不查真实库）
        from ask_kb import retrieve
        hits = retrieve("复利是长期投资的核心概念吗？", k=3, db_dir=db_dir)
        pdf_hits = [h for h in hits if "测试文档.pdf" in h[1]["source"]]
        assert pdf_hits, f"PDF 内容没检索到：{[h[1]['source'] for h in hits]}"
        print(f"  ✅ PDF 内容检索命中: {pdf_hits[0][1]['source']}（相关度 {pdf_hits[0][2]:.3f}）")

        print(f"✅ test_multimodal 通过：pdf/docx/图片 全管线入库 + 幂等 + 检索命中（共 {blocks1} 块）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_multimodal_ingest()
    print("✅ 多模态导入验证通过")
