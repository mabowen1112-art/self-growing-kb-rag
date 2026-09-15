# test_api.py — FastAPI 后端测试（TestClient 无头跑；真实调 embedding + DeepSeek 一次）
# 运行：uv run pytest test_api.py
# 检查：① health 正常 ② ask 带来源回答 ③ 空问题 400 ④ 检索 hits 结构

import os

from fastapi.testclient import TestClient

import api


def test_api():
    # 清缓存（保证这次真实调 API，确定性）
    if os.path.exists("qa_cache.json"):
        os.remove("qa_cache.json")

    client = TestClient(api.app)

    # ① 健康检查：库有数据
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    assert body["blocks"] > 0, "向量库是空的！先跑 uv run python ingest.py"

    # ② 统计：notes > 0
    r = client.get("/api/stats")
    assert r.status_code == 200, r.text
    assert r.json()["notes"] > 0, "SQLite 元数据为空"

    # ③ 正常问答：带来源、带 hits 结构
    r = client.post("/api/ask", json={"question": "复利是什么？"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["answer"], "回答为空"
    assert data["sources"], "没有来源"
    assert data["hits"] and all("source" in h and "score" in h for h in data["hits"]), \
        "hits 结构不完整"

    # ④ 边界：空问题 → 400
    r = client.post("/api/ask", json={"question": "   "})
    assert r.status_code == 400, f"空问题应 400：{r.status_code}"

    # ⑤ P5（2026-09-16）：两段式——/api/retrieve 只检索不生成 + /api/ask 复用第 1 步检索结果
    r = client.post("/api/retrieve", json={"question": "复利是什么？"})
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert hits and all("source" in h and "heading" in h and "score" in h for h in hits), hits

    r2 = client.post("/api/ask", json={"question": "复利是什么？", "reuse_last_retrieval": True})
    assert r2.status_code == 200 and r2.json()["answer"], r2.text

    # 复用时问题不匹配 → 回退为重新检索（不报错）
    r3 = client.post("/api/ask", json={"question": "定投是什么？", "reuse_last_retrieval": True})
    assert r3.status_code == 200 and r3.json()["answer"], r3.text

    # /api/retrieve 空问题 → 400
    r4 = client.post("/api/retrieve", json={"question": "  "})
    assert r4.status_code == 400, r4.status_code

    print("✅ test_api 通过：health/stats/ask/reuse_last_retrieval/retrieve/空问题400 全正常")
