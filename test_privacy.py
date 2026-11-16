"""普通问答和自动规划不得把私人记录送进模型上下文。"""

import time

from fastapi.testclient import TestClient

import api
import ask_kb
import config
import growth_loop
import health_check
import ingest
import planner


def test_private_path_policy():
    assert config.is_private_path("entities/马博文.md")
    assert config.is_private_path("entities/个人生活画像.md")
    assert config.is_private_path("entities/马博文-历史画像-2026-09-11.md")
    assert config.is_private_path("entities/东北大学.md")
    assert config.is_private_path("concepts/对话记录/自述分析-大学过半-2026-08-31.md")
    assert config.is_private_path("raw/transcripts/家庭对话.md")
    assert not config.is_private_path("concepts/学习概念词典.md")


def test_retrieve_filters_private_before_rerank(monkeypatch):
    rerank_input = []

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    def post(url, **kwargs):
        if url == ask_kb.EMBED_URL:
            return Response({"data": [{"embedding": [0.1]}]})
        rerank_input.extend(kwargs["json"]["documents"])
        return Response({"results": [{"index": 0, "relevance_score": 0.9}]})

    class Collection:
        def query(self, **_kwargs):
            return {"documents": [["PRIVATE", "PUBLIC"]],
                    "metadatas": [[{"doc_path": "entities/马博文.md", "source": "entities/马博文.md"},
                                   {"doc_path": "concepts/学习概念词典.md", "source": "concepts/学习概念词典.md"}]],
                    "distances": [[0.1, 0.2]]}

    class Client:
        def get_collection(self, _name):
            return Collection()

    monkeypatch.setattr(ask_kb.requests, "post", post)
    monkeypatch.setattr(ask_kb.chromadb, "PersistentClient", lambda **_kwargs: Client())
    hits = ask_kb.retrieve("测试问题")
    assert rerank_input == ["PUBLIC"]
    assert [h[1]["doc_path"] for h in hits] == ["concepts/学习概念词典.md"]


def test_import_and_planner_do_not_read_private_pages(tmp_path):
    kb = tmp_path / "kb"
    (kb / "concepts" / "对话记录").mkdir(parents=True)
    (kb / "entities").mkdir()
    (kb / "concepts" / "公开笔记.md").write_text(
        "---\ntitle: 公开笔记\ndomain: ai\n---\n\n## 正文\n这是普通学习笔记。", encoding="utf-8")
    (kb / "concepts" / "对话记录" / "家庭对话.md").write_text(
        "---\ntitle: 家庭对话\nsummary: 私密测试标记不可进入规划提示\n---\n\n## 正文\n私密测试标记",
        encoding="utf-8")
    (kb / "entities" / "马博文.md").write_text(
        "---\ntitle: 马博文\n---\n\n私密测试标记", encoding="utf-8")
    scanned = ingest.scan_files(kb)
    assert list(scanned) == ["concepts/公开笔记.md"]
    pages = health_check.collect_pages(kb)
    assert set(pages) == {"公开笔记"}
    signals = planner.collect_signals(kb)
    assert "私密测试标记" not in planner.format_signals(signals)


def test_growth_review_skips_private_files(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    private = kb / "concepts" / "对话记录"
    private.mkdir(parents=True)
    (private / "旧记录.md").write_bytes(b"\xff\xfe")  # 一旦读取会因 UTF-8 解码失败
    monkeypatch.setattr(config, "KB_ROOT", kb)
    assert growth_loop.step_contested() == []


def test_api_reuse_filters_stale_private_hit(monkeypatch):
    private = ("PRIVATE", {"source": "entities/马博文.md", "heading": "旧画像"}, 0.9)
    public = ("PUBLIC", {"source": "concepts/学习概念词典.md", "heading": "概念"}, 0.8)
    monkeypatch.setattr(api, "_LAST_RETRIEVAL", {
        "question": "测试", "hits": [private, public], "ts": time.time()})
    monkeypatch.setattr(api, "load_cache", lambda: {})
    seen = []

    def answer_cached(_question, hits, _cache):
        seen.extend(hits)
        return "公开回答", ["concepts/学习概念词典.md"], False

    monkeypatch.setattr(api, "answer_cached", answer_cached)
    result = TestClient(api.app).post("/api/ask", json={
        "question": "测试", "reuse_last_retrieval": True})
    assert result.status_code == 200
    assert [h[1]["source"] for h in seen] == ["concepts/学习概念词典.md"]
    assert [h["source"] for h in result.json()["hits"]] == ["concepts/学习概念词典.md"]
