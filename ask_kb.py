# ask_kb.py — RAG 第④~⑤步：检索 → 生成（v4：粗筛+Rerank+结构化输出+缓存）
# 运行：uv run python ask_kb.py "你的问题"

import os
import sys
import json
import hashlib
import re
from dotenv import load_dotenv
import requests
import chromadb
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
CACHE_FILE = str(config.QA_CACHE_FILE)  # 旧隐私策略和旧提示词的缓存不再加载
CACHE_VERSION = "v4"
SOURCE_PATH_RE = re.compile(
    r"(?<![\w/])(?:concepts|entities|raw|english)/[^\s，。；：、！？!?,;:()（）\[\]`\"']+?\.md")


def cache_key(question, hits):
    """答案依赖问题、提示词版本和本次实际资料，不能只依赖问题文字。"""
    evidence = [(doc, meta.get("doc_path") or meta.get("source"), meta.get("heading"))
                for doc, meta, _score in hits]
    payload = json.dumps([CACHE_VERSION, question.strip(), evidence], ensure_ascii=False)
    return f"{CACHE_VERSION}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


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
        proxies={"http": None, "https": None},
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
        and not config.is_private_path(meta.get("doc_path") or meta.get("source", ""))
    ]
    if not cands:
        return []

    rr = requests.post(
        RERANK_URL,
        headers={"Authorization": f"Bearer {SF_KEY}", "Content-Type": "application/json"},
        json={"model": RERANK_MODEL, "query": question, "documents": [c[0] for c in cands]},
        timeout=60,
        proxies={"http": None, "https": None},
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
def answer(question, hits, _retry=True):
    parts = [
        f"【资料块】来源:{meta['source']}（章节:{meta['heading']}）\n{doc}"
        for doc, meta, _score in hits
    ]
    prompt = f"""你是一个知识库助手。回答用户问题时，把内容分两层组织：

【📌 资料层】根据下面提供的知识库资料回答，忠实标注来源。资料可能是历史记录、AI 整理或待核对判断，不要自动当作当前事实。
【💡 补充层】资料不足或太单薄时，可以用你自己的通用知识补充说明（原理、背景、例子），让回答丰满、像人话。
但补充内容必须明确标注"💡 补充："开头，让用户一眼分清哪些是知识库里写的、哪些是你的通用知识。

回答要求：
1. 回答要丰满详细（多角度、有解释、必要时给例子），不要一句话打发
2. 资料里有的内容详细讲，标注来源
3. 资料里完全没有相关内容时，明确说"知识库里没有相关内容"，然后仍可用 💡 补充通用知识（若有把握）
4. 补充的通用知识不得编造具体数字/人名/日期等硬事实；没把握就说不确定
5. 涉及用户本人时，区分本人明确表述与 AI 推断；旧规划或旧画像不能替代当前确认。资料不足时直接说明不确定
6. sources 只能列出本次提供的资料块的来源，不能自行编造文件名

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
            "max_tokens": 2048,  # 2026-09-11 上调：DeepSeek 换新模型后回答更长，800 会被截断（finish_reason=length）
            "response_format": {"type": "json_object"},
        },
        timeout=60,
        proxies={"http": None, "https": None},
    )
    r.raise_for_status()
    choice = r.json()["choices"][0]
    raw = choice["message"]["content"]
    if choice.get("finish_reason") == "length":
        return "⚠️ 回答被截断（finish_reason=length，max_tokens 不足）", []
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("answer") or not isinstance(data.get("sources"), list):
            if _retry:  # 2026-09-11：模型偶发格式异常 → 自动重试一次
                return answer(question, hits, _retry=False)
            return "⚠️ AI 返回格式异常（已拦截）", []
        allowed = {meta.get("source") for _doc, meta, _score in hits}
        invalid_sources = [s for s in data["sources"] if not isinstance(s, str) or s not in allowed]
        invalid_paths = [p for p in SOURCE_PATH_RE.findall(data["answer"]) if p not in allowed]
        if invalid_sources or invalid_paths:
            if _retry:
                return answer(question, hits, _retry=False)
            return "⚠️ 来源校验失败：模型引用了未检索资料，回答已拦截", []
        sources = list(dict.fromkeys(data["sources"]))
        return data["answer"], sources
    except json.JSONDecodeError:
        if _retry:  # 2026-09-11：偶发非法 JSON → 自动重试一次
            return answer(question, hits, _retry=False)
        return "⚠️ AI 返回不是合法 JSON（已拦截）", []


# 带缓存的回答：命中缓存不调 API（省钱），未命中调 API 并存入缓存
def answer_cached(question, hits, cache):
    hits = [hit for hit in hits
            if not config.is_private_path(hit[1].get("doc_path") or hit[1].get("source", ""))]
    if not hits:
        return "📭 普通知识库中没有可用资料", [], False
    key = cache_key(question, hits)
    if key in cache:
        item = cache[key]
        return item["answer"], item["sources"], True
    text, sources = answer(question, hits)
    if text.startswith("⚠️"):
        return text, sources, False
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
