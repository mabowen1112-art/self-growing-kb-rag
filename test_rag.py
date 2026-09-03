# test_rag.py — 一键体检：RAG 问答工具核心行为验证
# 运行：uv run python test_rag.py（真实调 embedding + DeepSeek API，约 10-20 秒）
# 检查三件事：① 库里有数据 ② 能检索到并带来源回答 ③ 防幻觉（没有的不编）

import os
import subprocess
import sys


def test_rag():
    # ① 清缓存（保证每次测试真实调 API，确定性）
    if os.path.exists("qa_cache.json"):
        os.remove("qa_cache.json")

    # ② 库里有数据
    import chromadb
    col = chromadb.PersistentClient(path="./kb_db").get_collection("my_kb")
    assert col.count() > 0, "向量库是空的！先跑 uv run python build_kb.py"

    # ② 库内问题：能命中知识并带来源
    r1 = subprocess.run(
        ["uv", "run", "python", "ask_kb.py", "复利是什么？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    out1 = r1.stdout
    assert r1.returncode == 0, f"ask_kb 崩溃：{r1.stderr}"
    assert "金融通识课.md" in out1, "没检索到金融通识课（检索失效？）"
    assert "来源：" in out1, "回答没带来源标注"

    # ③ 缓存：同一问题第二次应命中缓存（不调 API，省钱）
    r1b = subprocess.run(
        ["uv", "run", "python", "ask_kb.py", "复利是什么？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    out1b = r1b.stdout
    assert "命中缓存" in out1b, f"第二次提问没有命中缓存：{out1b[:200]}"

    # ④ 检索质量（rerank）：问"马博文是谁"必须命中 马博文.md（曾检不全）
    r3 = subprocess.run(
        ["uv", "run", "python", "ask_kb.py", "马博文是谁？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    out3 = r3.stdout
    assert r3.returncode == 0, f"ask_kb 崩溃：{r3.stderr}"
    assert "马博文.md" in out3, "rerank 后仍没命中马博文.md（检索优化失效？）"

    # ④ 防幻觉：库外问题必须拒绝，不能编造
    r2 = subprocess.run(
        ["uv", "run", "python", "ask_kb.py", "埃菲尔铁塔有多高？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    out2 = r2.stdout
    assert r2.returncode == 0, f"ask_kb 崩溃：{r2.stderr}"
    assert ("没有检索到" in out2) or ("资料里没有" in out2), "防幻觉失效：库外问题竟给出回答"

    # ⑤ 不泄漏 key
    for out in (out1, out3, out2):
        assert "sk-" not in out, "输出泄漏了 API Key！"

    print("✅ test_rag 体检通过：检索+来源标注+防幻觉+无泄漏")


if __name__ == "__main__":
    test_rag()
    print("✅ 全部通过，RAG 问答工具状态健康")
