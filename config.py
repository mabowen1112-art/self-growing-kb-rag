# config.py — 集中配置：所有路径/阈值/参数都在这，消灭硬编码
# 修改配置后：增量导入自动生效；全量重建用 python ingest.py --rebuild

from pathlib import Path

# ---- 路径 ----
KB_ROOT = Path(r"E:\自生长知识库")          # 知识库根目录（只读，绝不修改内容）
KB_DB_DIR = Path("./kb_db")                  # Chroma 向量库（本地持久化）
META_DB = Path("./kb_meta.db")               # SQLite 元数据库（笔记清单+hash+状态）
COLLECTION_NAME = "my_kb"                    # Chroma 集合名

# ---- 导入范围：要扫哪些目录（相对 KB_ROOT）----
# 新增目录直接往列表里加；用 exclude 排除子目录
IMPORT_DIRS = ["concepts", "entities", "raw"]
EXCLUDE_DIRS = ["raw/_草稿"]                  # AI起草的新笔记草稿区（审核转正前不进向量库）

# ---- AI 起草草稿目录（executor V2卡3）----
DRAFT_DIR = KB_ROOT / "raw" / "_草稿"
DRAFT_LIMIT = 10                             # 草稿区待审上限：达到后暂停自动起草（2026-09-15 用户要求，防积压看不过来）

# ---- 支持的文件类型（多模态：md + pdf + docx + 图片OCR）----
IMPORT_EXTS = [".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"]
OCR_CONFIDENCE = 0.5                         # OCR 置信度低于此值的结果丢弃（防垃圾块）

# ---- 切片 ----
MIN_CHUNK_LEN = 30                           # 小于 30 字符的块丢弃
HEADING_MARKER = "## "                       # 按二级标题切块

# ---- 向量化 ----
EMBED_MODEL = "BAAI/bge-m3"                  # 硅基流动 embedding 模型
EMBED_BATCH = 10                             # 一次批量 10 条
EMBED_URL = "https://api.siliconflow.cn/v1/embeddings"

# ---- 检索（知识库 200+ 块规模实测：top-3 会被"提及问题的噪声块"挤占，
#      调到 top-5 才稳，2026-08-17 知识库 139→206 块后实测调整）----
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RETRIEVE_TOP = 30                            # 粗筛 top-30
RETRIEVE_MAX_DISTANCE = 0.75                 # 距离阈值
RERANK_K = 7                                 # 精排 top-7（原 3→5；2026-09-11 实验#2：5→7，拥挤查询下定义源会被挤出 top-5）
RERANK_MIN_SCORE = 0.25                      # 相关度阈值（2026-09-11 实验#1：0.5→0.25，救回"两段式检索"被低估的命中块）

# ---- LLM ----
CHAT_URL = "https://api.deepseek.com/chat/completions"
CHAT_MODEL = "deepseek-chat"
QA_CACHE_FILE = Path("./qa_cache.json")      # 问答缓存（相同问题零费用）

# ---- API Key（从 .env 读，绝不写死在代码里）----
# SILICONFLOW_API_KEY / DEEPSEEK_API_KEY 在 .env 里配置
