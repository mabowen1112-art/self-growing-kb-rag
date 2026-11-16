"""问答的来源与缓存必须跟随本次检索结果。"""

import json

import ask_kb


def test_answer_sources_are_limited_to_retrieved_files(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "回答", "sources": ["concepts/已检索.md"]
            })}}]}

    monkeypatch.setattr(ask_kb.requests, "post", lambda *_args, **_kwargs: Response())
    hits = [("正文", {"source": "concepts/已检索.md", "heading": "摘要"}, 0.9)]
    _answer, sources = ask_kb.answer("问题", hits)
    assert sources == ["concepts/已检索.md"]


def test_cached_answer_changes_when_retrieved_content_changes(monkeypatch):
    generated = []

    def fake_answer(_question, hits):
        generated.append(hits[0][0])
        return hits[0][0], ["concepts/笔记.md"]

    monkeypatch.setattr(ask_kb, "answer", fake_answer)
    monkeypatch.setattr(ask_kb, "save_cache", lambda _cache: None)
    meta = {"source": "concepts/笔记.md", "heading": "摘要"}
    cache = {}
    first = ask_kb.answer_cached("同一问题", [("旧内容", meta, 0.9)], cache)
    second = ask_kb.answer_cached("同一问题", [("纠正后内容", meta, 0.9)], cache)
    assert first[0] == "旧内容" and second[0] == "纠正后内容"
    assert generated == ["旧内容", "纠正后内容"]


def test_fabricated_source_in_answer_is_blocked(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "结论见 entities/虚构.md", "sources": ["entities/虚构.md"]
            })}}]}

    monkeypatch.setattr(ask_kb.requests, "post", lambda *_args, **_kwargs: Response())
    hits = [("正文", {"source": "concepts/真实.md", "heading": "摘要"}, 0.9)]
    answer, sources = ask_kb.answer("问题", hits)
    assert "entities/虚构.md" not in answer
    assert sources == []


def test_unlisted_fabricated_path_in_answer_is_blocked(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "结论见 entities/虚构.md", "sources": ["concepts/真实.md"]
            })}}]}

    monkeypatch.setattr(ask_kb.requests, "post", lambda *_args, **_kwargs: Response())
    hits = [("正文", {"source": "concepts/真实.md", "heading": "摘要"}, 0.9)]
    answer, sources = ask_kb.answer("问题", hits)
    assert "entities/虚构.md" not in answer
    assert sources == []


def test_source_validation_failure_is_not_cached(monkeypatch):
    writes = []
    monkeypatch.setattr(ask_kb, "answer", lambda *_args: ("⚠️ 来源校验失败", []))
    monkeypatch.setattr(ask_kb, "save_cache", lambda cache: writes.append(cache.copy()))
    hits = [("正文", {"source": "concepts/真实.md", "heading": "摘要"}, 0.9)]
    cache = {}
    answer, sources, cached = ask_kb.answer_cached("问题", hits, cache)
    assert answer.startswith("⚠️") and sources == [] and not cached
    assert cache == {} and writes == []
