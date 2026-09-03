# ask_kb.py — RAG 第④~⑤步：检索 → 生成（v4：粗筛+Rerank+结构化输出+缓存）
# 运行：uv run python ask_kb.py "你的问题"

import os
import sys
import json
from dotenv import load_dotenv
import requests
import chromadb
import config

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)  # 国内 API 直连

load_dotenv()
SF_KEY = os.getenv("SILICONFLOW_API_KEY")
DS_KEY = os.getenv("DEEPSEEK_API_KEY")
EMBED_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL = "BAAI/bge-m3"
RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
CHAT_URL = "https://api.deepseek.com/chat/completions"

# ---- 缓存（成本管控）：相同问题不重复调 API ----
CACHE_FILE = "qa_cache.json"


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache):
    json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ④ 检索（v3 两段式）：向量粗筛 top-30 → Rerank 精排 top-k
# 参数集中到 config.py（知识库规模变化时只改一处）；db_dir 供测试注入临时库
def retrieve(question, k=config.RERANK_K, max_distance=config.RETRIEVE_MAX_DISTANCE,
             min_score=config.RERANK_MIN_SCORE, db_dir=None):
    r = requests.post(
        EMBED_URL,
        headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": [question]},
        timeout=60,
    )
    r.raise_for_status()
    q_vec = r.json()["data"][0]["embedding"]

    db_dir = db_dir or config.KB_DB_DIR
    collection = chromadb.PersistentClient(path=str(db_dir)).get_collection(config.COLLECTION_NAME)
    res = collection.query(query_embeddings=[q_vec], n_results=30)

    cands = [
        (doc, meta, dist)
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
        if dist <= max_distance
    ]
    if not cands:
        return []

    rr = requests.post(
        RERANK_URL,
        headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"},
        json={"model": RERANK_MODEL, "query": question, "documents": [c[0] for c in cands]},
        timeout=60,
    )
    rr.raise_for_status()
    ranked = sorted(rr.json()["results"], key=lambda x: -x["relevance_score"])

    hits = []
    for it in ranked:
        doc, meta, _dist = cands[it["index"]]
        score = it["relevance_score"]
        if score < min_score:
            continue
        hits.append((doc, meta, score))
        if len(hits) >= k:
            break
    return hits


# ⑤ 生成：结构化 JSON + 二次校验
def answer(question, hits):
    parts = [
        f"【资料块】来源:{meta['source']}（章节:{meta['heading']}）\n{doc}"
        for doc, meta, _score in hits
    ]
    prompt = f"""你是一个知识库助手。回答用户问题时，把内容分两层组织：

【📌 资料层】根据下面提供的知识库资料回答——这是权威依据，必须忠实于资料，并在 sources 里列出用到的来源文件。
【💡 补充层】资料不足或太单薄时，可以用你自己的通用知识补充说明（原理、背景、例子），让回答丰满、像人话。
但补充内容必须明确标注"💡 补充："开头，让用户一眼分清哪些是知识库里写的、哪些是你的通用知识。

回答要求：
1. 回答要丰满详细（多角度、有解释、必要时给例子），不要一句话打发
2. 资料里有的内容详细讲，标注来源
3. 资料里完全没有相关内容时，明确说"知识库里没有相关内容"，然后仍可用 💡 补充通用知识（若有把握）
4. 补充的通用知识不得编造具体数字/人名/日期等硬事实；没把握就说不确定

{chr(10).join(parts)}

【问题】{question}

请输出 JSON 格式：{{"answer": "回答内容（含 📌资料层 / 💡补充层 标注）", "sources": ["用到的来源文件名1", "文件名2"]}}"""

    r = requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    try:
        data = json.loads(raw)
        if not data.get("answer") or not isinstance(data.get("sources"), list):
            return "⚠️ AI 返回格式异常（已拦截）", []
        return data["answer"], data["sources"]
    except json.JSONDecodeError:
        return "⚠️ AI 返回不是合法 JSON（已拦截）", []


# 带缓存的回答：命中缓存不调 API（省钱），未命中调 API 并存入缓存
def answer_cached(question, hits, cache):
    key = question.strip()
    if key in cache:
        item = cache[key]
        return item["answer"], item["sources"], True
    text, sources = answer(question, hits)
    cache[key] = {"answer": text, "sources": sources}
    save_cache(cache)
    return text, sources, False


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "复利是什么？"
    cache = load_cache()
    print(f"❓ 问题：{q}\n")
    print("④ 检索到的资料块（Rerank 精排后）：")
    hits = retrieve(q)
    if not hits:
        print("📭 没有检索到相关资料（阈值过滤），换个问法试试")
    else:
        for _doc, meta, score in hits:
            print(f"   📄 {meta['source']}（{meta['heading']}）相关度 {score:.3f}")
        print("-" * 52)
        print("⑤ DeepSeek 回答：\n")
        text, sources, cached = answer_cached(q, hits, cache)
        if cached:
            print("⚡ 命中缓存（本次没花 API 费用）")
        print(text)
        if sources:
            print("\n📌 来源：", "、".join(sources))
