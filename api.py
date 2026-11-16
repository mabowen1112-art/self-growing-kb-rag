# api.py — FastAPI 后端（卡3：三层架构的中间层）
# 运行：uv run uvicorn api:app --port 8100
# ⚠️ 端口用 8100：8000 被 ESP32 录音上传服务器（开机自启）占用，勿改回
# 职责：把 RAG 核心（ask_kb）包成 HTTP API，供 Streamlit 前端调用
# RAG 逻辑零改动：复用 ask_kb.py 的 retrieve / answer_cached / 缓存

import os
import time
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
    reuse_last_retrieval: bool = False   # P5：两段式前端在第 2 步复用它第 1 步的检索结果


class RetrieveResponse(BaseModel):
    hits: list


# P5（2026-09-16）：单用户本地服务——短命缓存最近一次检索结果，供"先亮资料块再生成"的前端复用，
# 避免重复 embedding+rerank（省约 1~2 秒与少量费用）
_LAST_RETRIEVAL = {"question": None, "hits": None, "ts": 0.0}
_RETRIEVAL_TTL = 120.0


def _hits_json(hits):
    return [{"source": m["source"], "heading": m["heading"], "score": round(s, 3)}
            for _d, m, s in hits
            if not config.is_private_path(m.get("doc_path") or m.get("source", ""))]


class AskResponse(BaseModel):
    answer: str
    sources: list
    cached: bool
    hits: list = []  # 检索到的资料块（来源/章节/相关度）


# ---- POST /api/retrieve：只检索不生成（P5：前端第 1 步，先亮资料块） ----
@app.post("/api/retrieve", response_model=RetrieveResponse)
def retrieve_only(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    try:
        hits = retrieve(question)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"检索失败：{e}")
    _LAST_RETRIEVAL.update(question=question, hits=hits, ts=time.time())
    return RetrieveResponse(hits=_hits_json(hits))


# ---- POST /api/ask：问答 ----
@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    hits = None
    if (req.reuse_last_retrieval and _LAST_RETRIEVAL["question"] == question
            and time.time() - _LAST_RETRIEVAL["ts"] < _RETRIEVAL_TTL):
        hits = _LAST_RETRIEVAL["hits"]      # 复用第 1 步的检索结果（省一次 embedding+rerank）
        if hits is not None:
            hits = [hit for hit in hits if not config.is_private_path(
                hit[1].get("doc_path") or hit[1].get("source", ""))]
    if hits is None:
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
        hits=_hits_json(hits),
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
