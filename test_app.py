# test_app.py — Streamlit 界面无头测试（v3：mock 后端，不依赖真实 API/后端进程）
# 运行：uv run pytest（自动同时跑全部测试）
# AppTest 直接跑 app.py，app.py 会调 http://127.0.0.1:8100 → monkeypatch requests 防真实调用

import requests
from streamlit.testing.v1 import AppTest

MOCK_HEALTH = {"status": "ok", "blocks": 206, "cache_entries": 3}
MOCK_STATS = {"blocks": 206, "notes": 31, "cache_entries": 3}
MOCK_ASK = {
    "answer": "复利是指利息再生利息的计息方式，长期坚持复利效应巨大。",
    "sources": ["concepts/金融通识课.md"],
    "cached": False,
    "hits": [{"source": "concepts/金融通识课.md", "heading": "复利", "score": 0.9}],
}


def fake_get(url, timeout=5):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            if url.endswith("/api/health"):
                return MOCK_HEALTH
            return MOCK_STATS
    return R()


def fake_post(url, json=None, timeout=90):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return MOCK_ASK
    return R()


def test_app_answers(monkeypatch):
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    assert not at.exception, f"界面启动报错：{at.exception}"

    # 侧边栏统计渲染
    metrics = " ".join(m.value for m in at.metric)
    assert "206" in metrics, f"侧边栏统计没渲染：{metrics}"

    # 模拟用户：输入问题 → 点按钮
    at.text_input[0].set_value("复利是什么？")
    at.button[0].click()
    at.run()

    # 回答文本里应该有"复利"
    texts = " ".join(m.value for m in at.markdown)
    assert "复利" in texts, f"回答里没有复利相关内容：{texts[:200]}"

    # 来源标注（caption 里应有"来源"）
    captions = " ".join(c.value for c in at.caption)
    assert "来源" in captions, f"没有来源标注：{captions[:200]}"

    print("✅ app.py 界面测试通过（mock 后端）：能提问、能回答、有来源标注")
