# extractors.py — 多模态文本提取器：md / pdf / docx / 图片 → (source, heading, text) 列表
# 统一输出与 chunk_md 一致的 (source, heading, text) 三元组，让 ingest 管线无缝复用
# 全部只读原始文件，绝不修改

import config

# ============ md：按 ## 标题切片（与卡1 chunk_md 完全一致） ============

def chunk_md(text, source):
    """按 ## 标题把 md 切成块；<30 字符的块丢弃。
    ⚠️ frontmatter 处理（PRD §9.7 修复）：
    1. YAML frontmatter 整体跳过（不切块）——修复"frontmatter 高相关空壳块"污染检索
    2. summary 字段单独成块（heading=摘要，同样受 30 字符过滤），并前置 H1 标题——
       bge-m3 对"XX是谁"类疑问句匹配弱（实测纯 summary 距离 0.876 进不了粗筛，
       带实体名标题后 0.626 可命中），实体名必须留在摘要块里"""
    summary = None
    h1_title = None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_text = text[3:end]
            text = text[end + 4:].lstrip("\n")
            for line in fm_text.splitlines():
                if line.startswith("summary:"):
                    summary = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("title:"):
                    h1_title = line.split(":", 1)[1].strip().strip('"\'')

    # H1 标题（# xxx）也作为实体名来源（frontmatter 可能没 title 时兜底）
    if h1_title is None:
        for line in text.splitlines():
            if line.startswith("# ") and not line.startswith("## "):
                h1_title = line[2:].strip()
                break

    chunks = []
    current, current_heading = [], "（开头）"
    for line in text.splitlines():
        if line.startswith(config.HEADING_MARKER):
            if current:
                chunks.append((source, current_heading, "\n".join(current)))
            current_heading = line[len(config.HEADING_MARKER):].strip()
            current = []
        else:
            current.append(line)
    if current:
        chunks.append((source, current_heading, "\n".join(current)))

    blocks = [(s, h, c) for s, h, c in chunks if len(c.strip()) >= config.MIN_CHUNK_LEN]
    if summary and len(summary.strip()) >= config.MIN_CHUNK_LEN:
        # 摘要块前置实体名（H1 标题），保证"XX是谁"类查询能粗筛命中
        summary_text = f"{h1_title}：{summary.strip()}" if h1_title else summary.strip()
        blocks.insert(0, (source, "摘要", summary_text))
    return blocks


# ============ PDF：pymupdf 按页提取（文本型 PDF） ============

def extract_pdf(abs_path):
    """文本型 PDF → [(source, '第N页', text)]。
    判定扫描件 = 所有页都无文字层（不是过滤后为空）；
    相邻短页（<MIN_CHUNK_LEN）合并成一块，避免误丢"""
    import pymupdf

    doc = pymupdf.open(str(abs_path))
    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        if text:
            pages.append((i, text))
    doc.close()

    if not pages:
        raise ValueError(
            "PDF 所有页都无文字层（疑似扫描件）→ 需 OCR 通道（V2 实现），已跳过")

    # 合并相邻短页：短页文本暂存，直到与下一页拼够长度
    blocks = []
    pending = None  # (起始页号, 累积文本)
    for i, text in pages:
        if pending is None and len(text) < config.MIN_CHUNK_LEN:
            pending = [i, text]
        elif pending is not None:
            pending[1] += "\n" + text
            if len(pending[1]) >= config.MIN_CHUNK_LEN:
                blocks.append((str(abs_path), f"第{pending[0]}页起", pending[1]))
                pending = None
        else:
            blocks.append((str(abs_path), f"第{i}页", text))
    if pending is not None and len(pending[1]) >= config.MIN_CHUNK_LEN:
        blocks.append((str(abs_path), f"第{pending[0]}页起", pending[1]))

    return blocks


# ============ DOCX：python-docx 按标题分块 ============

def extract_docx(abs_path):
    """Word 文档 → 按标题样式分块；无标题的段落归入（开头）"""
    from docx import Document

    doc = Document(str(abs_path))
    blocks = []
    current_heading = "（开头）"
    current_lines = []

    def flush():
        text = "\n".join(current_lines).strip()
        if len(text) >= config.MIN_CHUNK_LEN:
            blocks.append((str(abs_path), current_heading, text))
        current_lines.clear()

    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("Heading"):
            flush()
            current_heading = text[:50]
        else:
            current_lines.append(text)
    flush()
    return blocks


# ============ 图片：RapidOCR 本地 OCR（零 API 费用） ============

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()  # 首次初始化加载模型，之后复用
    return _ocr


def extract_image(abs_path):
    """图片 → OCR 识别文本 → 单块（heading=图片OCR）。低置信度结果丢弃"""
    ocr = _get_ocr()
    result, _ = ocr(str(abs_path))
    if not result:
        return []

    lines = [r[1] for r in result if r[2] >= config.OCR_CONFIDENCE]
    text = "\n".join(lines).strip()
    if len(text) < config.MIN_CHUNK_LEN:
        return []
    return [(str(abs_path), "图片OCR", text)]


# ============ 统一入口：按扩展名分发 ============

def extract(rel_path, abs_path):
    """按扩展名分发，统一返回 [(source, heading, text)]，source 一律为相对路径"""
    ext = abs_path.suffix.lower()
    if ext == ".md":
        text = abs_path.read_text(encoding="utf-8")
        return chunk_md(text, rel_path)
    if ext == ".pdf":
        return _rewrite_source(extract_pdf(abs_path), rel_path)
    if ext == ".docx":
        return _rewrite_source(extract_docx(abs_path), rel_path)
    if ext in (".png", ".jpg", ".jpeg"):
        return _rewrite_source(extract_image(abs_path), rel_path)
    return []


def _rewrite_source(blocks, rel_path):
    """把提取器返回的绝对路径 source 统一改成相对路径（与 md 一致）"""
    return [(rel_path, h, c) for _s, h, c in blocks]
