# ingest.py — 增量导入器（v5，替代 build_kb.py；卡2 支持多模态）
# 运行：uv run python ingest.py          增量导入（只处理新增/变更/删除的笔记）
#       uv run python ingest.py --rebuild  全量重建（删库重来，逃生口）
# 原理：SQLite(kb_meta.db) 记录每篇笔记的 hash → 对比磁盘 → 只动变化的部分
# 切片/向量化参数与 v4 完全一致（已验证），只改了流程
# 卡2：md/pdf/docx/图片 统一走 extractors.extract()，增量逻辑完全复用

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ⚠️ 定时任务（schtasks）启动时 stdout 是 GBK 代码页，emoji 打印会崩
# 强制 stdout 用 UTF-8，保证任何环境启动都不报 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import requests
import chromadb

import config
from extractors import extract, chunk_md

# ⚠️ 真实世界经典坑：requests 默认读环境变量里的代理设置，
# Clash 时开时关会导致 API 偶发失败。国内 API 直连即可，显式禁用代理：
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

load_dotenv()
SF_KEY = os.getenv("SILICONFLOW_API_KEY")
if not SF_KEY:
    raise SystemExit("缺少 SILICONFLOW_API_KEY（在 .env 里配置）")


# ============ ② 向量化（与 v4 完全一致） ============

def embed_texts(texts):
    vectors = []
    for i in range(0, len(texts), config.EMBED_BATCH):
        r = requests.post(
            config.EMBED_URL,
            headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"},
            json={"model": config.EMBED_MODEL, "input": texts[i:i + config.EMBED_BATCH]},
            timeout=60,
        )
        r.raise_for_status()
        vectors.extend(d["embedding"] for d in r.json()["data"])
    return vectors


# ============ ③ SQLite 元数据库（新增） ============

def init_meta_db(meta_db=None):
    """建表：notes（每篇笔记一行）。rel_path 主键 = 文件相对 KB_ROOT 的路径（/ 分隔）"""
    path = meta_db or config.META_DB
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            rel_path     TEXT PRIMARY KEY,
            domain       TEXT,              -- 所在目录（concepts/entities/…）
            title        TEXT,
            chunk_count  INTEGER,
            content_hash TEXT,              -- sha256 前 16 位，变更检测依据
            imported_at  TEXT,
            updated_at   TEXT
        )
    """)
    conn.commit()
    return conn


def meta_load(conn):
    """读现有记录 → {rel_path: row}"""
    rows = conn.execute("SELECT rel_path, content_hash FROM notes").fetchall()
    return {r[0]: r[1] for r in rows}


# ============ ④ 磁盘扫描 ============

def scan_files(kb_root: Path):
    """扫描配置的目录 → {rel_path: {abs, title, hash}}。rel_path 用 / 分隔（跨平台稳定）
    卡2：支持 md/pdf/docx/图片，按 config.IMPORT_EXTS 过滤"""
    files = {}
    for sub in config.IMPORT_DIRS:
        base = kb_root / sub
        if not base.exists():
            continue
        for root, dirs, fnames in os.walk(base):
            # 排除目录
            dirs[:] = [d for d in dirs
                       if str(Path(root).relative_to(kb_root)).replace("\\", "/") not in config.EXCLUDE_DIRS]
            for fn in fnames:
                if Path(fn).suffix.lower() not in config.IMPORT_EXTS:
                    continue
                abs_path = Path(root) / fn
                rel = str(abs_path.relative_to(kb_root)).replace("\\", "/")
                raw = abs_path.read_bytes()
                h = hashlib.sha256(raw).hexdigest()[:16]
                title = extract_title(abs_path)
                files[rel] = {"abs": abs_path, "title": title, "hash": h}
    return files


def extract_title(abs_path):
    """md 优先取 frontmatter 的 title；其他类型用文件名（去扩展名）"""
    if abs_path.suffix.lower() == ".md":
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return abs_path.stem
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                for line in text[3:end].splitlines():
                    if line.startswith("title:"):
                        return line.split(":", 1)[1].strip().strip('"\'')
    return abs_path.stem


# ============ ⑤ 稳定 chunk id（增量能成立的前提） ============

def chunk_id(rel_path, heading):
    """sha1(路径+标题) → 稳定 id：内容不变 id 不变，可增量更新/删除"""
    return hashlib.sha1(f"{rel_path}|{heading}".encode("utf-8")).hexdigest()


# ============ ⑥ 增量同步主逻辑 ============

def sync(conn, collection, files):
    """对比磁盘 vs SQLite → 只处理新增/变更/删除。返回统计 dict"""
    known = meta_load(conn)
    stats = {"added_notes": 0, "changed_notes": 0, "removed_notes": 0,
             "added_chunks": 0, "removed_chunks": 0, "skipped": 0}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 要处理的笔记：新增 + 变更 ----
    to_process = {rel: f for rel, f in files.items()
                  if rel not in known or known[rel] != f["hash"]}
    new_notes = [rel for rel in to_process if rel not in known]
    changed_notes = [rel for rel in to_process if rel in known]

    # ---- 要删除的笔记：磁盘上消失了 ----
    removed = [rel for rel in known if rel not in files]

    stats["skipped"] = len(files) - len(to_process)

    # 处理新增/变更：删旧块 → 切片 → 向量化 → 入库 → 更新元数据
    for rel, f in to_process.items():
        is_new = rel in new_notes

        # 卡4：新 md 笔记自动补全 frontmatter（缺 domain/tags/summary 才补，绝不覆盖已有）
        # 只处理新笔记，老笔记一根毛不碰；补完重算 hash（否则下次 ingest 误判"变更"）
        if is_new and f["abs"].suffix.lower() == ".md":
            try:
                from directions import classify, ensure_frontmatter
                text = f["abs"].read_text(encoding="utf-8")
                result = classify(text)
                new_text, added = ensure_frontmatter(text, result)
                if added:
                    f["abs"].write_text(new_text, encoding="utf-8")
                    f["hash"] = hashlib.sha256(f["abs"].read_bytes()).hexdigest()[:16]
                    print(f"  🏷️ {rel} 自动补全: {', '.join(added)}"
                          + (f"（方向: {result['matched']}）" if result["matched"] else "（低置信，仅补summary）"))
            except Exception as e:
                print(f"  ⚠️ {rel} frontmatter 补全跳过：{e}")

        # 变更的笔记：先按 doc_path 精准删掉旧块（标题变了也找得到）
        if not is_new:
            old = collection.get(where={"doc_path": rel})
            if old["ids"]:
                collection.delete(old["ids"])
                stats["removed_chunks"] += len(old["ids"])

        # 多模态提取：md/pdf/docx/图片 统一走 extract()（卡2）
        try:
            chunks = extract(rel, f["abs"])
        except ValueError as e:
            print(f"  ⚠️ 跳过 {rel}：{e}")
            chunks = []

        ids, docs, metas = [], [], []
        for src, heading, content in chunks:
            ids.append(chunk_id(rel, heading))
            docs.append(content)
            metas.append({"source": rel, "heading": heading, "doc_path": rel,
                          "domain": rel.split("/")[0]})
        if ids:
            vecs = embed_texts(docs)
            collection.add(ids=ids, embeddings=vecs, documents=docs, metadatas=metas)
            stats["added_chunks"] += len(ids)

        # 元数据：新增记 imported_at=now；变更保留原 imported_at，只更新 updated_at
        if is_new:
            imported_at = now
        else:
            row = conn.execute("SELECT imported_at FROM notes WHERE rel_path=?", (rel,)).fetchone()
            imported_at = row[0] if row and row[0] else now

        conn.execute(
            "INSERT OR REPLACE INTO notes (rel_path, domain, title, chunk_count, content_hash, imported_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, rel.split("/")[0], f["title"], len(ids), f["hash"], imported_at, now))
        conn.commit()

    stats["added_notes"] = len(new_notes)
    stats["changed_notes"] = len(changed_notes)

    # 处理删除：Chroma 删块 + SQLite 删记录
    for rel in removed:
        old = collection.get(where={"doc_path": rel})
        if old["ids"]:
            collection.delete(old["ids"])
            stats["removed_chunks"] += len(old["ids"])
        conn.execute("DELETE FROM notes WHERE rel_path=?", (rel,))
    conn.commit()
    if removed:
        stats["removed_notes"] = len(removed)

    return stats


def run_ingest(kb_root=config.KB_ROOT, rebuild=False, db_dir=None, meta_db=None):
    """主流程。返回 stats dict（测试可直接调用；db_dir/meta_db 用于测试隔离）"""
    t0 = time.time()
    db_dir = Path(db_dir) if db_dir else config.KB_DB_DIR
    conn = init_meta_db(meta_db)
    client = chromadb.PersistentClient(path=str(db_dir))

    if rebuild:
        try:
            client.delete_collection(config.COLLECTION_NAME)
        except Exception:
            pass
        conn.execute("DELETE FROM notes")
        conn.commit()
        print("♻️  --rebuild：旧库已清空，开始全量重建")

    collection = client.get_or_create_collection(
        name=config.COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    # ⚠️ v4 旧库迁移检测：库里已有块但 SQLite 元数据为空 = 旧版库（chunk-N id、无 doc_path），
    # 直接增量会和新 id 重复入库 → 自动全量重建一次，之后就是纯增量
    if not rebuild and collection.count() > 0 and not meta_load(conn):
        print("⚠️ 检测到 v4 旧版库（无 SQLite 元数据），自动执行全量重建迁移...")
        try:
            client.delete_collection(config.COLLECTION_NAME)
        except Exception:
            pass
        conn.execute("DELETE FROM notes")
        conn.commit()
        collection = client.get_or_create_collection(
            name=config.COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    print(f"🔍 扫描 {kb_root}（目录：{', '.join(config.IMPORT_DIRS)}）...")
    files = scan_files(kb_root)
    print(f"📄 共发现 {len(files)} 篇笔记")

    stats = sync(conn, collection, files)
    conn.close()

    print(f"\n✅ 同步完成（耗时 {time.time() - t0:.1f}s）")
    print(f"   新增 {stats['added_notes']} 篇 / 变更 {stats['changed_notes']} 篇 / "
          f"删除 {stats['removed_notes']} 篇 / 未变 {stats['skipped']} 篇")
    print(f"   向量块 +{stats['added_chunks']} / -{stats['removed_chunks']} "
          f"（当前库共 {collection.count()} 块）")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自生长知识库增量导入器")
    parser.add_argument("--rebuild", action="store_true", help="全量重建（删库重来）")
    args = parser.parse_args()
    run_ingest(rebuild=args.rebuild)
