# app.py — RAG 问答网页界面（v3：三层架构，前端只调 FastAPI 后端）
# 运行：uv run uvicorn api:app --port 8100  +  uv run streamlit run app.py
# 前端职责：限流 + 界面展示；问答/统计全部走后端 API
# ⚠️ 端口用 8100：8000 被 ESP32 录音上传服务器（开机自启）占用，勿改回

import time
import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8100"  # FastAPI 后端地址


def api_get(path):
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=5)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


def api_post(path, payload):
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=90)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)


st.set_page_config(page_title="自生长知识库问答", page_icon="📚")
st.title("📚 自生长知识库问答")
st.caption("三层架构：Streamlit 前端 → FastAPI 后端 → RAG（检索 → Rerank → DeepSeek）")

# ---- 后端连接检查：自动重试 3 次（后端首次加载 Chroma 较慢，避免误报"未连接"）----
health = None
for _attempt in range(3):
    health, err = api_get("/api/health")
    if health:
        break
    time.sleep(2)
if err or not health:
    st.error("⚠️ 后端未连接（请先运行 `uv run uvicorn api:app --port 8100`，或检查 8100 端口）")
    st.stop()

# ---- 限流：60 秒内最多 10 次提问（防烧钱/被打爆）----
MAX_ASKS_PER_MIN = 10
if "ask_times" not in st.session_state:
    st.session_state.ask_times = []
if "history" not in st.session_state:
    st.session_state.history = []

# ---- 侧边栏：知识库统计（走后端 /api/stats）----
with st.sidebar:
    st.header("📊 知识库")
    stats, _err = api_get("/api/stats")
    if stats:
        st.metric("向量块数", stats["blocks"])
        st.metric("笔记数", stats["notes"])
        st.metric("缓存条目", stats["cache_entries"])
    st.caption("💡 相同问题直接返回缓存，不花 API 费用")
    st.caption("⏱️ 限流：60 秒最多 10 次提问")

question = st.text_input("❓ 输入你的问题：", placeholder="例如：复利是什么？")

if st.button("提问", type="primary"):
    now = time.time()
    st.session_state.ask_times = [t for t in st.session_state.ask_times if now - t < 60]
    if len(st.session_state.ask_times) >= MAX_ASKS_PER_MIN:
        st.error("⏱️ 提问太频繁啦，请 60 秒后再试")
    elif not question.strip():
        st.warning("先输入问题哦")
    else:
        st.session_state.ask_times.append(now)

        # P5（2026-09-16）两段式：第 1 步先检索（约 1~2 秒）——先把"查到了什么"亮出来，缓解等待感
        with st.spinner("🔍 第 1 步 / 2：在知识库里检索（向量粗筛 → 精排）..."):
            hits_data, err = api_post("/api/retrieve", {"question": question})
        if err:
            st.error(f"❌ 后端出错：{err}")
        else:
            hits = hits_data.get("hits") or []
            if hits:
                st.caption(f"🔍 第 1 步完成：检索到 {len(hits)} 个资料块 —— 正在生成回答（约 5~8 秒）")
                with st.expander(f"🔍 检索到的 {len(hits)} 个资料块（点击展开）"):
                    for h in hits:
                        st.markdown(f"**{h['source']}**（{h['heading']}）· 相关度 {h['score']}")
            else:
                st.caption("🔍 第 1 步完成：没有检索到相关资料（确信用了正确说法的话，换个问法试试）")

            # 第 2 步：生成回答（复用第 1 步的检索结果，不重复检索）
            with st.spinner("🤖 第 2 步 / 2：DeepSeek 正在组织回答..."):
                data, err = api_post("/api/ask",
                                     {"question": question, "reuse_last_retrieval": True})
            if err:
                st.error(f"❌ 后端出错：{err}")
            elif not data["sources"] and not data["hits"]:
                st.info(data["answer"])
            else:
                if data["cached"]:
                    st.success("⚡ 命中缓存，本次未调用 API（省钱）")
                st.markdown("---")
                st.markdown(data["answer"])
                if data["sources"]:
                    st.caption("📌 来源：" + "、".join(data["sources"]))
                # 会话历史（最近 10 条）
                st.session_state.history.append((question, data["answer"]))
                with st.expander("📜 本次会话历史"):
                    for q, a in st.session_state.history[-10:]:
                        st.markdown(f"**Q:** {q}")
                        st.markdown(f"{a[:120]}…" if len(a) > 120 else a)
                        st.markdown("---")
