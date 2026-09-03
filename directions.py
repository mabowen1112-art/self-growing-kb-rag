# directions.py — 生长方向分类器（卡4）
# 读 directions.yaml → 关键词打分 → 返回 (domain, tags, hits, matched_name)
# 规则版判断：确定性、零 API 费。低置信（命中 < min_hits）→ 只补 summary 不写 domain

from pathlib import Path
import re

import yaml

DIRECTIONS_FILE = Path(__file__).parent / "directions.yaml"


def load_directions(path=None):
    """读 directions.yaml → {name: {domain, tags, min_hits, keywords, exclude}}
    path 可注入（测试用临时 yaml 验证"加方向不改代码"）"""
    p = Path(path) if path else DIRECTIONS_FILE
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data["directions"], data.get("default", {})


def classify(text: str):
    """对笔记文本打分 → 返回 dict：{domain, tags, hits, matched}
    hits=0 表示低置信（未达 min_hits / 混合文档 / 平局），调用方只补 summary 不写 domain
    计分：keywords 命中 +1，strong 命中 +2，exclude 命中 -3（惩罚不判负，防误杀高分方向）"""
    directions, default = load_directions()
    best = None
    qualified = 0  # 达标方向数（混合文档检测）
    for d in directions:
        hits = sum(1 for k in d["keywords"] if k.lower() in text.lower())
        hits += 2 * sum(1 for k in d.get("strong", []) if k.lower() in text.lower())
        hits -= 3 * sum(1 for k in d.get("exclude", []) if k.lower() in text.lower())
        if hits >= d["min_hits"]:
            qualified += 1
            if best is None or hits > best["hits"]:
                best = {"domain": d["domain"], "tags": d["tags"][:3],
                        "hits": hits, "matched": d["name"]}
            elif hits == best["hits"]:
                best["tie"] = True  # 平局标记：最高分有多个方向
    # 低置信：无达标 / 混合文档（≥3 方向）/ 平局 → 不写 domain
    if best is None or qualified >= 3 or best.get("tie"):
        return {"domain": default.get("domain", "general"),
                "tags": default.get("tags", [])[:3],
                "hits": 0, "matched": None}
    return best


# ============ frontmatter 补全（方案A：写进笔记，只在缺失时补，绝不覆盖） ============

def parse_frontmatter(text: str):
    """返回 (fm_dict, rest_text)；无 frontmatter 返回 ({}, text)"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end]
    rest = text[end + 4:]
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, rest


def ensure_frontmatter(text: str, classify_result: dict):
    """缺 domain/tags/summary 就补（分类结果）；已有字段绝不覆盖。
    返回 (新文本, 补了哪些字段)"""
    fm, rest = parse_frontmatter(text)
    added = []

    if "domain" not in fm and classify_result["hits"] > 0:
        fm["domain"] = classify_result["domain"]
        added.append("domain")
    if "tags" not in fm and classify_result["tags"]:
        fm["tags"] = "[" + ", ".join(classify_result["tags"]) + "]"
        added.append("tags")
    if "summary" not in fm:
        summary = extract_summary(rest)
        if summary:
            fm["summary"] = summary
            added.append("summary")

    if not added:
        return text, []

    # 重建 frontmatter（保留原字段顺序，追加新字段）
    lines = ["---"]
    for k, v in fm.items():
        if v.startswith("[") or ":" in v:
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + rest.lstrip("\n"), added


def extract_summary(text: str, limit: int = 100):
    """摘要规则：正文第一段截断 ≤100 字；正文空 → 不写"""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉 wikilink 语法残留
        line = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", line)
        return line[:limit]
    return None


if __name__ == "__main__":
    # 自测：几个典型输入
    tests = [
        "RAG 检索增强生成，用 Chroma 向量库做 embedding",
        "ESP32 用 GPIO 控制 LED 点灯，按键输入",
        "复利是长期投资的核心，定投基金分散风险",
        "今天随便记点东西",
    ]
    for t in tests:
        r = classify(t)
        print(f"{t[:20]:<22} → domain={r['domain']} hits={r['hits']} matched={r['matched']}")
