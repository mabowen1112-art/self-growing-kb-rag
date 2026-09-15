# test_eval.py — 评测判分纯函数测试（评测 v2 对抗题，2026-09-16）
# 运行：uv run pytest test_eval.py（纯本地，零 API 费）

import eval_kb


def test_check_points():
    pts = eval_kb.check_points("两段式检索：先粗筛再精排", ["粗筛|海选", "精排|Rerank", "决赛"])
    assert [p["ok"] for p in pts] == [True, True, False]


def test_judge_refusal():
    # 明说"没有" → 通过
    ok, why = eval_kb.judge_refusal(eval_kb.NO_HITS_ANSWER)
    assert ok and "没有" in why
    # 明确标注 💡补充 → 通过（设计允许：AI 通用知识必须标出来）
    ok, why = eval_kb.judge_refusal("【📌 资料层】库里没有直接内容\n【💡 补充层】这是通用知识……")
    assert ok and "补充" in why
    # 拧紧（2026-09-16）：裸 💡 符号不算，必须明确标注"补充"
    ok, _why = eval_kb.judge_refusal("费马大定理由怀尔斯证明。💡")
    assert not ok, "裸 💡 不应通过"
    # 直接编造、不标来源 → 失败
    ok, why = eval_kb.judge_refusal("费马大定理由安德鲁·怀尔斯于 1994 年证明。")
    assert not ok
    ok, why = eval_kb.judge_refusal("")
    assert not ok


def test_count_multi():
    hits = [("d", {"source": "concepts/课程/ESP32硬件课/ESP32硬件课-第05课-录音存SD卡WAV.md"}, 0.1),
            ("d", {"source": "concepts/课程/ESP32硬件课/ESP32硬件课-第07课-WiFi上传到电脑.md"}, 0.2),
            ("d", {"source": "concepts/学习概念词典.md"}, 0.3)]
    n, which = eval_kb.count_multi(["ESP32硬件课-第05课", "ESP32硬件课-第07课", "ESP32硬件课-第10课"], hits, [])
    assert n == 2 and which == ["ESP32硬件课-第05课", "ESP32硬件课-第07课"]
    # 回答 sources 也计入
    n2, _ = eval_kb.count_multi(["ESP32硬件课-第10课"], [], ["concepts/…/ESP32硬件课-第10课-语音转文字.md"])
    assert n2 == 1


def test_src_match_list_and_str():
    assert eval_kb.src_match("词典", ["concepts/学习概念词典.md"])
    assert eval_kb.src_match(["A", "词典"], ["concepts/学习概念词典.md"])
    assert not eval_kb.src_match("没有的页", ["concepts/学习概念词典.md"])
    assert eval_kb.src_match("", ["任意"])  # 空 expect = 不判来源


def test_configured_modes():
    assert eval_kb.configured({"q": "问题", "source": "x"})
    assert not eval_kb.configured({"q": "问题"})                    # 普通题缺 source 不可跑
    assert eval_kb.configured({"q": "问题", "check": "refusal"})    # 拒答题只需题面
    assert not eval_kb.configured({"q": "", "check": "refusal"})
    assert eval_kb.configured({"q": "问题", "check": "multi", "sources": ["A", "B"]})
    assert not eval_kb.configured({"q": "问题", "check": "multi"})
