# arXiv 论文监控工具 — 架构文档

## 文件结构

```
papers/
├── common.py                      # 共享基础设施（所有可复用逻辑）
├── retrieve_qubit.py              # 超导量子器件领域检索脚本
├── retrieve_fab.py                # 芯片加工工艺领域检索脚本
├── push_papers.py                 # 统一推送脚本（多领域汇总→钉钉）
├── config.ini                     # API 密钥配置
├── CLAUDE.md                      # 项目指引
├── README.md                      # 项目说明
│
├── prompts/
│   └── fab_scoring_prompt.txt     # 工艺评分 Prompt 参考文本
│
├── docs/
│   ├── ARCHITECTURE.md            # 本文档
│   └── SCORING_RULES.md           # 评分规则映射表
│
├── data/
│   ├── pdfs/                      # 下载的 PDF 文件缓存
│   ├── qubit_paper_log.txt        # 超导量子器件检索日志（累计）
│   ├── qubit_paper_log_latest.json # 超导量子器件最新结果摘要
│   ├── author_keywords.json       # 超导量子器件作者关键词
│   ├── fab_paper_log.txt          # 芯片工艺检索日志（累计）
│   ├── fab_paper_log_latest.json  # 芯片工艺最新结果摘要
│   ├── fab_paper_log_watch_latest.json  # 芯片工艺关注线结果
│   ├── fab_author_keywords.json   # 芯片工艺作者关键词
│   ├── sent_papers.json           # 已推送论文记录（自动去重）
│   ├── favorites.json             # 收藏论文
│   └── push.log                   # 推送日志
│
├── scripts/
│   └── generate_report.py         # Word 报告生成
│
├── fetch.bat                      # 定时检索批处理
└── push.bat                       # 定时推送批处理
```

## 数据流

```
┌─────────────────────┐    ┌─────────────────────┐
│  retrieve_qubit.py   │    │   retrieve_fab.py    │
│                     │    │                     │
│  DEFAULT_KEYWORDS   │    │  FAB_KEYWORDS       │
│  SCORING_PROMPT     │    │  FAB_SCORING_PROMPT  │
│  min_score=5        │    │  min_score=3        │
│  days=3             │    │  days=3             │
└────────┬────────────┘    └────────┬────────────┘
         │                          │
         │  调用 common.py           │
         │  run_retrieval_pipeline() │
         │                          │
         ▼                          ▼
   ┌──────────────────────────────────────────────────┐
   │              common.py 过滤链                     │
   │                                                  │
   │  1. ANDNOT 服务端排除 (FAB_SERVER_NEGATIVE)       │
   │  2. fetch_arxiv_papers() ← arXiv API / RSS 回退  │
   │  3. load_sent_papers() + deduplicate_papers()   │
   │  4. 关键词黑名单硬过滤 (title+summary+categories) │
   │  5. Embedding 综合评分 (0.6*emb + 0.2*kw + ...)  │
   │  6. 排序 + Top-K (20%) 截断                      │
   │  7. PDF 下载 + DeepSeek AI 评分                  │
   │  8. save_titles_to_file() + save_latest_json()   │
   └──────────────────────────────────────────────────┘
         │                          │
         │  paper_log_latest.json   │  fab_paper_log_latest.json
         │                          │
         ▼                          ▼
   ┌─────────────────────────────────────────┐
   │            push_papers.py                │
   │                                         │
   │  1. load_latest_json() ← 读取各领域     │
   │  2. generate_markdown_content()         │
   │  3. DingTalkRobot.send_markdown()       │
   │  4. mark_papers_sent() → sent_papers.json│
   └─────────────────────────────────────────┘
```

## 过滤链详解（FAB 领域）

### 第0层：服务端 ANDNOT

在 arXiv 查询中直接追加 `ANDNOT all:"keyword"`，在服务器端排除噪音。
受限于 255 字符查询长度，实际能追加 1-4 个关键词。

```python
FAB_SERVER_NEGATIVE = ["llm", "robot", "protein", "biology", "vision", "auction", "causal", "cosmology"]
```

### 第1层：关键词黑名单硬过滤

基于标题 + 摘要 + arXiv 分类字段匹配，命中任一黑名单关键词即排除。
过滤后删除 summary 字段以减小数据体积。

```python
FAB_NEGATIVE_KEYWORDS = [
    "llm", "language model", "reinforcement learning", "graph neural",
    "auction", "causal", "mathematics", "topology", "social network",
    "vision", "robot", "protein", "biology", "astrophysics", "cosmology",
]
```

### 第2层：Embedding 综合评分

对通过黑名单的所有论文计算综合分数：

```
final_score = 0.6 * embedding_similarity
            + 0.2 * keyword_overlap
            + 0.1 * category_score
            + 0.1 * title_boost

category_score = (GOOD 命中 ? +0.1 : 0) + (BAD 命中 ? -0.15 : 0)
```

- **embedding_similarity**: 论文标题+摘要 vs FAB_TARGET_DESCRIPTION 的 cosine similarity
- **keyword_overlap**: FAB_KEYWORDS 在标题+摘要中的命中比例
- **category_score**: arXiv 分类匹配度
- **title_boost**: 标题中关键词数量（>=2 个=1.0, >=1=0.5）

### 第3层：Top-K 截断

按综合分数降序排序，取前 K 篇进入 LLM 评分：

```
K = max(10, int(论文数 × 20%))
```

不足 10 篇时全部进入 LLM。

### 第4层：LLM 评分 (10/6/2/0)

仅 Top-K 篇论文下载 PDF 并调用 DeepSeek。评分标准：

| 分数 | 含义 |
|:----:|------|
| 10 | 核心贡献是超导量子芯片制造、加工或表征 |
| 6 | 工艺/材料/器件技术与量子硬件有明确工程关联 |
| 2 | 与超导量子硬件无直接工艺关系但有参考价值 |
| 0 | 与超导量子芯片制造工艺无关 |

min_score=3 保留 10 分和 6 分论文。

### arXiv API 限流回退

当 `/api/query` 持续返回 429 时（最多 8 次指数冷却重试），自动降级到 RSS Feed：

- 无频率限制，天然是当日论文
- 支持 5 个分类: `quant-ph`, `cond-mat.*`, `physics.app-ph`
- 本地按 RSS 专用关键词过滤 (`FAB_RSS_KEYWORDS`)

## 核心机制

### 去重机制

- **存储**：`sent_papers.json` 记录所有已推送论文的 `entry_id`
- **时机**：检索流水线在过滤之前进行去重
- **标记**：推送成功后由 `push_papers.py` 调用 `mark_papers_sent()` 写入

### JSON 配置文件

| 文件 | 用途 | 生产者 | 消费者 |
|------|------|--------|--------|
| `sent_papers.json` | 已推送论文 ID | `push_papers.py` | `common.py`（去重）|
| `*_latest.json` | 最新检索结果摘要 | `common.py` | `push_papers.py` |
| `author_keywords.json` | 高评分论文作者 | `common.py` | 检索脚本（扩展关键词）|

### 分类过滤

| 类型 | 匹配方式 | 作用 |
|------|---------|------|
| `FAB_GOOD_CATEGORIES` | 精确匹配 | 评分 +0.1 |
| `FAB_BAD_CATEGORIES` | 通配符（如 `math.*`） | 评分 -0.15 |

## 使用方式

### 日常运行

```bash
# 步骤1：检索
python retrieve_qubit.py --days 3
python retrieve_fab.py --days 3 --max-results 200

# 步骤2：预览推送内容（不实际发送）
python push_papers.py --dry-run

# 步骤3：确认无误后真发
python push_papers.py
```

### 定时任务

```bash
# 早上 9:00 检索量子器件，9:05 检索芯片工艺（错开避免限流）
0 9 * * * cd /path/to/project && python retrieve_qubit.py
5 9 * * * cd /path/to/project && python retrieve_fab.py
30 9 * * * cd /path/to/project && python push_papers.py
```

## 扩展新领域

### 第1步：创建检索脚本

```python
import os
from common import run_retrieval_pipeline

DOMAIN_LABEL = "新领域"
KEYWORDS = ["keyword1", "keyword2"]
SCORING_PROMPT = """...10/6/2/0 评分规则..."""
LOG_FILE = os.path.join(os.path.dirname(__file__), "new_paper_log.txt")
AUTHOR_FILE = os.path.join(os.path.dirname(__file__), "new_author_keywords.json")

def main(days=3, max_results=200):
    return run_retrieval_pipeline(
        keywords=KEYWORDS,
        scoring_prompt=SCORING_PROMPT,
        requirement="...",
        log_file=LOG_FILE,
        author_keywords_file=AUTHOR_FILE,
        domain_label=DOMAIN_LABEL,
        days=days, max_results=max_results, min_score=3,
        # 可选：黑名单、分类、embedding 目标描述
        prefilter_blacklist=NEGATIVE_KEYWORDS,
        target_description=TARGET_DESC,
        good_categories=GOOD_CATS,
        bad_categories=BAD_CATS,
        fetch_negative_keywords=SERVER_NEGATIVE,
        rss_keywords=RSS_KW,
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=3)
    parser.add_argument('--max-results', type=int, default=200)
    args = parser.parse_args()
    main(days=args.days, max_results=args.max_results)
```

### 第2步：注册到推送系统

在 `push_papers.py` 的 `DOMAINS` 列表中添加：

```python
DOMAINS = [
    {"label": "超导量子器件", "log_file": "...", "retrieve_script": "retrieve_qubit.py"},
    {"label": "芯片加工工艺",   "log_file": "...", "retrieve_script": "retrieve_fab.py"},
    {"label": "新领域",        "log_file": "...", "retrieve_script": "retrieve_new.py"},
]
```

## common.py 关键 API

| 函数 | 用途 |
|------|------|
| `fetch_arxiv_papers(keywords, ...)` | 从 arXiv API 获取论文（含 ANDNOT + 429 回退） |
| `fetch_arxiv_papers_rss(keywords, ...)` | RSS Feed 回退方案（无频率限制） |
| `compute_paper_score(paper, keywords, ...)` | 综合评分（embedding + keyword + category + title） |
| `rank_and_select_top_papers(papers, ...)` | 排序 + Top-K 截断 |
| `prefilter_papers_by_keywords(papers, ...)` | 关键词黑名单/白名单过滤（title+summary+categories） |
| `prefilter_papers_by_category(papers, ...)` | arXiv 分类过滤（支持通配符） |
| `analyze_papers_with_ai(papers, prompt, ...)` | AI 批量分析评分 |
| `run_retrieval_pipeline(...)` | 完整检索流水线 |
| `load_sent_papers()` / `mark_papers_sent()` | 去重管理 |
| `DingTalkRobot(webhook, secret)` | 钉钉机器人 |
| `generate_markdown_content(...)` | 单领域推送 Markdown |
| `save_latest_json()` / `load_latest_json()` | JSON 摘要读写 |

## 环境依赖

```
arxiv               # arXiv API 客户端
requests            # HTTP 调用
PyMuPDF (fitz)      # PDF 文本提取
sentence-transformers  # Embedding 语义相似度（本地模型，通过 HF 镜像下载）
torch               # sentence-transformers 依赖
```

安装：`pip install arxiv requests PyMuPDF sentence-transformers`
