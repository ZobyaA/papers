# CLAUDE.md

## 当前项目上下文

本文件中的原则已通过以下实际架构实现——请先理解实际代码，再以此为理想方向演进。

### 实际架构（当前代码库）

```
papers/
├── common.py                      # 核心：arXiv获取、AI调用、评分解析、日志、推送
├── retrieve_fab.py                # Fab管线（超导量子芯片工艺，Q1~Q6 rubric评分）
├── retrieve_qubit.py              # Qubit管线（量子比特测控，多维rubric评分）
├── push_papers.py                 # 统一推送：读取各_latest.json → 钉钉
├── config.ini                     # API密钥配置
├── prompts/
│   └── fab_scoring_prompt.txt     # Fab评分prompt外部编辑版（与.py同步）
├── docs/
│   ├── SCORING_RULES.md           # Fab评分规则映射表 + 修改指南
│   └── ARCHITECTURE.md            # 系统架构文档
├── data/
│   ├── fab_paper_log.txt          # 累积日志（追加，不覆盖，跨运行累积）
│   ├── fab_paper_log_latest.json  # 🟢推送线(≥5分) → push_papers.py
│   ├── fab_paper_log_watch_latest.json  # 🟡关注线(2~4分) → 交互模式
│   ├── qubit_paper_log.txt        # Qubit累积日志
│   ├── qubit_paper_log_latest.json # Qubit最新结果摘要
│   ├── fab_author_keywords.json   # 通讯作者关键词积累
│   ├── author_keywords.json       # Qubit作者关键词
│   ├── sent_papers.json           # 已推送论文去重记录
│   ├── favorites.json             # 收藏论文
│   └── push.log                   # 推送操作日志
├── scripts/
│   └── generate_report.py         # Word报告生成
├── fetch.bat                      # 定时检索脚本
└── push.bat                       # 定时推送脚本
```

### 核心设计（当前已达成的原则）

- **AI 只提取事实，程序算分**：`retrieve_fab.py` 的 prompt 要求 AI 输出 Q1~Q6 判定+原文证据，禁止输出分数。`common.py` 中的 `FAB_SCORING_MAP` 字典完成自动算分。
- **双线推送**：≥5分 → 推送；2~4分 → 关注线供人工浏览。由 `run_retrieval_pipeline` 的 `push_score` 参数控制，qubit 管线不受影响。
- **抗幻觉**：默认 NO 原则（无原文证据→禁止给非 NO 判定）、禁止跨句拼接推理、Q2/Q3/Q4 必须附原文句子。
- **qubit 隔离**：`analyze_with_deepseek()` 通过检测 `直接工艺:` + `工艺可迁移:` 关键字自动选择 fab rubric 解析路径，qubit prompt 不含这些关键字，不受影响。

### 环境配置

```bash
# 每次新终端必须执行
source ~/miniconda3/etc/profile.d/conda.sh
conda activate quantum
unset SSL_CERT_FILE  # 修复 huggingface/sentence-transformers SSL 崩溃
```

### 常用命令

```bash
# Fab 管线（检索+评分+分线保存）
python papers/retrieve_fab.py --start-date 2026-06-04 --end-date 2026-06-07

# Fab 交互模式（浏览🟢推送+🟡关注论文，选中后可AI问答）
python papers/retrieve_fab.py -i --start-date 2026-06-04 --end-date 2026-06-07

# Qubit 管线
python papers/retrieve_qubit.py --start-date 2026-06-04 --end-date 2026-06-07

# 统一推送（需先跑检索生成 _latest.json）
python papers/push_papers.py
```

### 修改评分规则

1. **只改分值**：编辑 `common.py` 的 `FAB_SCORING_MAP` 字典
2. **改判定标准**：编辑 `retrieve_fab.py` 的 `FAB_SCORING_PROMPT_TEMPLATE`（或先改 `fab_scoring_prompt.txt` 再同步过去）
3. **改筛选阈值**：编辑 `retrieve_fab.py` 中 `FAB_PUSH_SCORE`（推送线）和 `min_score`（关注线）
4. **验证**：跑一次带 `--start-date` 的检索，查看日志中的 Q1~Q6 输出

### 理想架构 vs 实际差距

| 理想（本文档推荐） | 实际（当前代码） | 说明 |
|------|------|------|
| 多阶段 LLM 管线 | 单阶段 Q1~Q6 prompt | 当前方案经过验证有效，拆分不是紧急任务 |
| prompt 外部化加载 | 硬编码在 .py 中 | `fab_scoring_prompt.txt` 作为编辑副本存在，但代码读取硬编码版本 |
| Pydantic schema 验证 | `parse_fab_rubric()` 用正则解析 | 基本够用，但强类型验证是未来改进方向 |
| 黄金测试集 | 人工跑 6.4 单日验证 | 需要建立 `golden/` 目录存放已知正/负例论文 |
| 分块 500-1500 tokens | 全文送入 AI | 当前论文长度在 token 限制内，暂不需要 |

---


## 项目概览

本项目是一个高精度学术论文筛选与提取系统。

核心目标：

* 阅读科学论文（主要为PDF）
* 提取结构化事实
* 判断论文是否匹配高度特化的领域需求
* 不惜一切代价避免幻觉
* 支持可复现评估
* 支持长上下文推理
* 支持迭代式 prompt 工程

系统不是通用聊天机器人，而是确定性研究助手管线，聚焦于：

* 论文筛选
* 证据提取
* 领域分类
* 事实核查
* 结构化评分

---

# 核心工程原则

## 1. 绝不产生幻觉

系统严禁：

* 编造事实
* 推断缺失细节
* 假设实验条件
* 猜测制造工艺
* 推断未指明的材料
* 捏造数值

如果信息未在论文中明确陈述：

* 标记为 UNKNOWN
* 不做推断

错误示例：
"可能使用铝量子比特。"

正确示例：
"论文未明确指定量子比特材料。"

---

## 2. 证据优先架构

每条结论必须可追溯到：

* 原始文本
* 页码
* 章节标题
* 提取的证据片段

所有输出应支持：

* 证据引用
* 溯源追踪
* 可复现性

推荐结构：

```python
{
    "claim": "...",
    "evidence": "...",
    "page": 7,
    "confidence": "explicit"
}
```

绝不输出无证据支撑的结论。

---

## 3. 先提取再评分

管线顺序必须为：

1. 解析 PDF
2. 提取文本
3. 分块内容
4. 提取结构化事实
5. 验证事实
6. 分类相关性
7. 评分相关性

禁止直接对原始 PDF 文本打分。

不经过结构化提取的评分会导致幻觉。

---

# 推荐项目结构

```txt
project/
│
├── CLAUDE.md
├── README.md
├── requirements.txt
│
├── data/
│   ├── raw_pdfs/
│   ├── parsed/
│   ├── chunks/
│   └── outputs/
│
├── prompts/
│   ├── extraction/
│   ├── classification/
│   ├── scoring/
│   └── validation/
│
├── schemas/
│   ├── paper_schema.py
│   ├── extraction_schema.py
│   └── scoring_schema.py
│
├── src/
│   ├── pdf/
│   ├── chunking/
│   ├── extraction/
│   ├── classification/
│   ├── scoring/
│   ├── validation/
│   └── pipeline/
│
├── tests/
│
└── docs/
```

---

# 编码规范

## 强类型要求

使用：

* Pydantic
* TypedDict
* dataclass
* type hints

避免无类型的字典混乱。

推荐：

```python
class ExtractionResult(BaseModel):
    process_type: str | None
    material: str | None
    evidence: str
    page: int
```

---

## 模块化设计

每个阶段必须隔离。

错误做法：

* 巨型管线文件
* 单体式 prompt 逻辑

正确做法：

* 提取模块
* 验证模块
* 评分模块
* 排序模块

---

## Prompt 文件必须外部化

禁止在 Python 文件中硬编码大型 prompt。

将 prompt 存放在：

```txt
/prompts/
```

动态加载。

---

## 所有 LLM 输出必须验证

每个 LLM 响应必须通过：

* schema 验证
* 枚举验证
* 空值检查
* 幻觉检查

验证失败时：

* 重试
* 修复
* 或丢弃

绝不信任原始 LLM 输出。

---

# PDF 处理指南

## 推荐库

使用：

* pymupdf (fitz)
* pdfplumber
* unstructured
* marker-pdf（可选）
* nougat（可选）

避免依赖 OCR，除非必要。

---

## 保留文档结构

维护：

* 章节标题
* 图表标题
* 表格
* 参考文献
* 页码

科学含义往往依赖于结构。

---

## 分块策略

按以下方式分块：

* 章节
* 小节
* 语义边界

避免朴素的固定 token 分块。

推荐分块大小：

* 500–1500 tokens

每个分块应包含：

* 章节元数据
* 页码元数据
* 论文元数据

---

# 提取原则

## 提取事实，而非解读

正确做法：

* 衬底材料
* 沉积方法
* 刻蚀化学试剂
* 结制备工艺

错误做法：

* "论文具有创新性"
* "制造工艺看似先进"

---

## 使用受控词汇表

优先使用枚举。

示例：

```python
ETCH_METHODS = [
    "RIE",
    "ICP-RIE",
    "wet_etch",
    "ion_mill",
    "unknown"
]
```

尽可能避免自由形式标签。

---

# 评分系统设计

## 评分必须可解释

每个评分必须包含：

* 理由
* 证据
* 匹配的标准

示例：

```python
{
    "criterion": "提及Josephson结制备",
    "matched": True,
    "evidence": "...",
    "page": 4
}
```

---

## 避免隐藏的思维链依赖

不要依赖隐藏推理。

优先：

* 显式规则匹配
* 基于证据的评分
* 确定性逻辑

---

## 使用加法评分

推荐：

* 正面证据加分
* 不相关证据不加分
* 不确定性不施加过重惩罚

避免：

* 武断的负面罚分
* 情绪化评分逻辑

---

# 反幻觉规则

## 禁止行为

助手严禁：

* 推断未陈述的制造步骤
* 推断材料体系
* 假设超导架构
* 编造工艺流
* 编造晶圆尺寸
* 编造温度
* 编造化学试剂
* 推断器件性能

如果未明确陈述：

* 输出 UNKNOWN

---

## 置信度标签

每次提取应包含：

```python
confidence = [
    "explicit",
    "strong_evidence",
    "weak_evidence",
    "unknown"
]
```

绝不将推断内容标记为 explicit。

---

# 推荐 LLM 工作流

## 多阶段 prompting

推荐管线：

### 阶段 1

原始事实提取

### 阶段 2

事实规范化

### 阶段 3

验证

### 阶段 4

相关性分类

### 阶段 5

评分

绝不将所有任务合并到一个巨型 prompt 中。

---

## 优先使用小而聚焦的 prompt

错误做法：

* 巨型通用 prompt

正确做法：

* 一个 prompt 做提取
* 一个 prompt 做分类
* 一个 prompt 做评分

---

# 测试策略

## 必需的测试

必须包含：

* PDF 解析测试
* 分块测试
* schema 验证测试
* 抗幻觉测试
* prompt 回归测试

---

## 黄金数据集

维护一个人工验证的数据集。

结构：

```txt
/golden/
```

包含：

* 已知相关论文
* 已知不相关论文
* 边缘案例
* 模糊论文

所有 prompt 变更必须通过回归测试。

---

# 性能考虑

## 缓存中间输出

缓存：

* 已解析的 PDF 文本
* 分块
* 嵌入向量
* 提取输出

避免重复处理 PDF。

---

## 安全并行化

适合并行化：

* 分块提取
* 嵌入生成
* 分类

不适合：

* 未经聚合的全局论文推理

---

# 检索 / RAG 建议

如果使用 RAG：

推荐：

* 混合检索
* BM25 + 嵌入向量
* 元数据过滤

元数据示例：

* 年份
* 期刊
* 材料
* 制造类型

避免仅使用纯向量搜索。

---

# 推荐技术栈

## 后端

推荐：

* Python 3.11+
* FastAPI
* Pydantic
* SQLite / PostgreSQL

---

## NLP / LLM

推荐：

* Claude
* GPT
* 本地 reranker
* sentence-transformers

---

## PDF / 解析

推荐：

* pymupdf
* pdfplumber
* unstructured

---

# 输出要求

所有输出应是：

* 结构化的
* 可复现的
* 机器可读的
* 有证据支撑的

推荐格式：

* JSON
* JSONL
* Parquet

避免：

* 纯自由形式散文

---

# 重要理念

本项目优先追求：

1. 事实正确性
2. 可追溯性
3. 可复现性
4. 抗幻觉能力

而非：

* 创造性
* 流畅文笔
* 推测性推理

系统行为更像：

* 科学提取引擎

而非：

* 对话助手

---

# Claude Code 行为指引

生成代码时：

* 可维护性优先于聪明技巧
* 避免隐藏魔法
* 解释非显而易见的逻辑
* 保持函数短小
* 保持 prompt 外部化
* 处处添加类型标注
* 处处添加验证
* 假设科学用户需要可复现性

不确定时：

* 选择保守行为
* 返回 UNKNOWN 而非猜测

绝不静默推断缺失的科学事实。
