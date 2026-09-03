# api.py — FastAPI 后端（卡3：三层架构的中间层）
# 运行：uv run uvicorn api:app --port 8100
# ⚠️ 端口用 8100：8000 被 ESP32 录音上传服务器（开机自启）占用，勿改回
# 职责：把 RAG 核心（ask_kb）包成 HTTP API，供 Streamlit 前端调用
# RAG 逻辑零改动：复用 ask_kb.py 的 retrieve / answer_cached / 缓存

import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import chromadb

import config
from ask_kb import retrieve, answer_cached, load_cache

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)  # 国内 API 直连
load_dotenv()

app = FastAPI(title="自生长知识库 RAG API", version="0.3.0")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    sources: list
    cached: bool
    hits: list = []  # 检索到的资料块（来源/章节/相关度）


# ---- POST /api/ask：问答 ----
@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    try:
        hits = retrieve(question)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"检索失败：{e}")

    if not hits:
        return AskResponse(answer="📭 没检索到相关资料（阈值过滤），换个问法试试", sources=[], cached=False)

    cache = load_cache()
    text, sources, cached = answer_cached(question, hits, cache)
    return AskResponse(
        answer=text,
        sources=sources,
        cached=cached,
        hits=[{"source": m["source"], "heading": m["heading"], "score": round(s, 3)}
              for _d, m, s in hits],
    )


# ---- GET /api/health：健康检查 ----
@app.get("/api/health")
def health():
    try:
        col = chromadb.PersistentClient(path=str(config.KB_DB_DIR)).get_collection(config.COLLECTION_NAME)
        blocks = col.count()
        return {"status": "ok", "blocks": blocks, "cache_entries": len(load_cache())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"向量库不可用：{e}")


# ---- GET /api/stats：统计（前端侧边栏用） ----
@app.get("/api/stats")
def stats():
    import sqlite3
    meta = {"notes": 0}
    try:
        conn = sqlite3.connect(config.META_DB)
        meta["notes"] = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        conn.close()
    except Exception:
        pass
    try:
        col = chromadb.PersistentClient(path=str(config.KB_DB_DIR)).get_collection(config.COLLECTION_NAME)
        blocks = col.count()
    except Exception:
        blocks = 0
    return {"blocks": blocks, "cache_entries": len(load_cache()), "notes": meta["notes"]}
