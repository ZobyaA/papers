# arXiv 论文智能筛选与推送系统

面向量子计算研究团队的高精度学术论文自动筛选与推送系统。系统每日自动从 arXiv 获取最新论文，通过多层预筛选、语义排序、LLM 结构化分析等环节，自动识别与超导量子计算高度相关的论文，并通过钉钉机器人推送到研究团队。

---

## 快速体验（10分钟上手）

```bash
# 1. 克隆项目
git clone https://github.com/your-repo/arxiv-paper-monitor.git
cd arxiv-paper-monitor/papers

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置（必须修改！见下文详细说明）
#    - config.ini: 填入你的 DeepSeek/OpenAI API Key
#    - push_papers.py: 填入你的钉钉机器人 Webhook 和 Secret
#    - 确保 conda 在系统 PATH 中（定时任务 .bat 脚本需要）

# 4. 首次运行（测试检索）
python retrieve_qubit.py --days 1

# 5. 预览推送内容
python push_papers.py --dry-run

# 6. 设置定时任务（每天自动推送）
#    详见「定时任务」章节
```

---

## 目录

- [快速体验（10分钟上手）](#快速体验10分钟上手)
- [第1步：克隆项目](#第1步克隆项目)
- [第2步：安装依赖](#第2步安装依赖)
- [第3步：配置系统（必须修改）](#第3步配置系统必须修改)
  - [3.1 配置 API 密钥](#31-配置-api-密钥)
  - [3.2 配置钉钉机器人](#32-配置钉钉机器人)
  - [3.3 批处理脚本（无需手动修改路径）](#33-批处理脚本无需手动修改路径)
- [第4步：首次运行测试](#第4步首次运行测试)
- [第5步：设置定时任务](#第5步设置定时任务)
- [使用说明](#使用说明)
  - [交互模式](#交互模式)
  - [命令行参数](#命令行参数)
- [自定义配置](#自定义配置)
  - [修改关键词](#修改关键词)
  - [调整评分规则](#调整评分规则)
  - [新增监控领域](#新增监控领域)
- [文件结构](#文件结构)
- [常见问题与故障排除](#常见问题与故障排除)
- [技术栈](#技术栈)

---

## 第1步：克隆项目

```bash
git clone https://github.com/your-repo/arxiv-paper-monitor.git
cd arxiv-paper-monitor/papers
```

> **注意**：项目代码位于 `papers/` 目录下，后续所有操作都在此目录进行。

---

## 第2步：安装依赖

确保已安装 Python 3.11+，然后安装依赖：

```bash
# 使用 pip 安装
pip install -r requirements.txt

# 或手动安装（如果遇到版本问题）
pip install arxiv requests PyMuPDF sentence-transformers python-docx torch
```

> **注意**：`sentence-transformers` 首次运行时会自动下载约 80MB 的 Embedding 模型（all-MiniLM-L6-v2），请确保网络通畅。

---

## 第3步：配置系统（必须修改）

系统运行前必须完成以下三项配置，否则无法正常工作：

### 3.1 配置 API 密钥

**第一步**：从模板复制配置文件

```bash
# 复制模板文件为实际配置（Windows）
copy config.ini.example config.ini

# 复制模板文件为实际配置（Linux/macOS）
cp config.ini.example config.ini
```

**文件位置**：`config.ini`（手动创建，`.gitignore` 已屏蔽，不会上传到 GitHub）

**修改内容**：编辑 `config.ini`，填入你的 API Key

```ini
[API_KEYS]
deepseek = sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # 推荐使用，性价比高
openai = sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx    # 可选
claude = sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx    # 可选
gemini = AQ.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx     # 可选
```

**获取方式**：
- **DeepSeek**：https://platform.deepseek.com/
- **OpenAI**：https://platform.openai.com/
- **Claude**：https://console.anthropic.com/
- **Gemini**：https://ai.google.dev/

**选择建议**：推荐使用 DeepSeek，性价比最高，中文理解能力强。至少配置一个 API Key。

### 3.2 配置钉钉机器人

**文件位置**：`push_papers.py` 第 17-18 行

**修改内容**：替换为你的钉钉机器人 Webhook 和 Secret

```python
# 当前代码（需要修改！）
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=****"
DINGTALK_SECRET = "****"

# 修改为你的机器人配置
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=你的机器人token"
DINGTALK_SECRET = "SEC你的机器人secret"
```

**获取方式**：
1. 在钉钉群聊中点击「设置」→「智能群助手」→「添加机器人」
2. 选择「自定义」机器人
3. 填写机器人名称，开启「加签」（必须开启）
4. 复制 Webhook 和 Secret

### 3.3 批处理脚本（无需手动修改路径）

**文件位置**：`fetch.bat` 和 `push.bat`

脚本使用 `%~dp0` 自动推导项目根目录，使用 `conda run -n quantum python` 调用 Python 解释器，**换机即用，无需修改任何路径**。

**前置条件（换机使用唯一要求）**：

1. **conda 已安装并在 PATH 中** — 安装 Miniconda 时勾选"Add to PATH"，或手动将 conda 目录加入系统环境变量
2. **conda 环境名为 `quantum`** — 运行 `conda env list` 确认 `quantum` 环境存在
3. **项目目录可任意放置** — 脚本使用 `%~dp0` 自动定位，不依赖绝对路径

验证 conda 是否可用（在新终端中执行）：

```bash
conda run -n quantum python --version
# 预期输出: Python 3.11.x
```

#### fetch.bat（检索脚本）

```batch
@echo off
chcp 65001 >nul
set ARXIV_SCHEDULED=1

REM === 自动推导项目根目录（%~dp0 = 此脚本所在目录，.. 即项目根） ===
cd /d "%~dp0.."

echo [%date% %time%] === fetch started (delay 1h) ===
timeout /t 3600 /nobreak > nul

echo [%date% %time%] [NEW] retrieve_qubit...
conda run -n quantum python papers\retrieve_qubit.py --days 3

echo [%date% %time%] [NEW] retrieve_fab...
conda run -n quantum python papers\retrieve_fab.py --days 3 --max-results 999

echo [%date% %time%] === fetch done ===
```

#### push.bat（推送脚本）

```batch
@echo off
chcp 65001 >nul

REM === 自动推导项目根目录 ===
cd /d "%~dp0.."

echo [%date% %time%] push start >> papers\data\push.log 2>nul
conda run -n quantum python papers\push_papers.py >> papers\data\push.log 2>&1
echo [%date% %time%] push done (exit %errorlevel%) >> papers\data\push.log 2>nul
```

> **备选方案**：如果不希望依赖 `conda run`，可改用系统 Python：将 `conda run -n quantum python` 替换为 `python`（需确保 `quantum` 环境已激活或已安装所需依赖）。

---

## 第4步：首次运行测试

完成配置后，按以下顺序测试：

### 4.1 测试检索功能

```bash
# 测试量子器件领域（搜索过去1天的论文）
python retrieve_qubit.py --days 1

# 测试芯片工艺领域（搜索过去1天的论文）
python retrieve_fab.py --days 1 --max-results 50
```

**预期输出**：
```
从arXiv获取到 X 篇论文
[预筛选] X 篇通过, X 篇黑名单排除, X 篇白名单未命中
[分类过滤] X 篇通过, X 篇坏分类排除, X 篇未命中好分类
正在下载PDF...
成功下载并提取 PDF 内容，长度：XXXX 字符
AI分析完成：评分 X/10
已保存论文标题到: data/fab_paper_log.txt
```

### 4.2 预览推送内容

```bash
python push_papers.py --dry-run
```

**预期输出**：显示即将推送的论文列表，但不会发送到钉钉。

### 4.3 正式推送

```bash
python push_papers.py
```

**预期输出**：成功推送后，钉钉群会收到包含论文列表的 Markdown 消息。

---

## 第5步：设置定时任务

系统需要每天定时执行检索和推送，推荐使用 Windows 任务计划程序：

### 5.1 创建检索任务

1. 打开「任务计划程序」
2. 点击「创建基本任务」
3. 名称：`arxiv-fetch`，描述：`arXiv论文检索`
4. 触发器：每天，时间选择 8:00（建议早于推送时间）
5. 操作：启动程序
6. 程序或脚本：`fetch.bat` 的完整路径（如 `C:\Users\<你的用户名>\Desktop\ClaudeCode\papers\fetch.bat`）
7. 完成

### 5.2 创建推送任务

1. 点击「创建基本任务」
2. 名称：`arxiv-push`，描述：`arXiv论文推送`
3. 触发器：每天，时间选择 9:30（检索完成后推送）
4. 操作：启动程序
5. 程序或脚本：`push.bat` 的完整路径（如 `C:\Users\<你的用户名>\Desktop\ClaudeCode\papers\push.bat`）
6. 完成

> **注意**：`fetch.bat` 内置了 1 小时延迟，以错开 arXiv 服务器高峰期。如果不需要延迟，删除 `timeout /t 3600 /nobreak > nul` 行。

---

## 使用说明

### 交互模式

```bash
# 浏览已筛选论文并可与AI交互提问
python retrieve_fab.py -i --days 3
```

交互模式支持：
- 浏览推送线（≥5分）+ 关注线（2~4分）全部论文
- 对单篇论文向 AI 提问（技术细节、创新点等）
- 收藏论文到 `data/favorites.json` 供后续回顾
- 先 AI 分析再提问（对未评分的论文）

### 命令行参数

#### retrieve_qubit.py / retrieve_fab.py

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ai` | AI服务选择 (deepseek/openai/claude) | `deepseek` |
| `--start-date` | 开始日期 (YYYY-MM-DD) | None |
| `--end-date` | 结束日期 (YYYY-MM-DD) | None |
| `--days` | 搜索过去N天的论文 | 3 |
| `--max-results` | 最大论文数 | qubit:9999 / fab:200 |
| `-i / --interactive` | 启动交互式模式 | False |

#### push_papers.py

| 参数 | 说明 |
|------|------|
| `--dry-run` | 仅预览推送内容，不实际发送 |
| `--list-domains` | 列出所有注册的领域 |

---

## 自定义配置

### 修改关键词

**文件位置**：`retrieve_fab.py` 和 `retrieve_qubit.py`

#### 芯片工艺领域（retrieve_fab.py）

```python
# 搜索关键词（命中即保留）
FAB_KEYWORDS = [
    "fab", "fabrication", "process", "wafer",
    "Junction", "CMOS", "yield", "QPU"
]

# 黑名单关键词（命中即排除）
FAB_NEGATIVE_KEYWORDS = [
    "llm", "language model", "reinforcement learning",
    "graph neural", "auction", "causal", "mathematics",
    # ... 更多关键词
]
```

#### 量子器件领域（retrieve_qubit.py）

```python
DEFAULT_KEYWORDS = [
    "quantum error correction",
    "superconducting qubits",
    "transmon qubits",
    "superconducting quantum device"
]
```

### 调整评分规则

**文件位置**：`common.py` 的 `FAB_SCORING_MAP` 字典（第 1329-1340 行）

```python
FAB_SCORING_MAP = {
    'Q2': {'YES': +5, 'NO': 0},                          # 直接工艺
    'Q3': {'强可迁移': +4, '中可迁移': +2, '弱可迁移': +1, 'NO': 0},  # 工艺可迁移
    'Q4': {'直接相关': +2, '临近相关': +1, 'NO': 0},      # 量子硬件相关性
    'Q5': {'YES': +2, '部分': +1, 'NO': 0},               # 实验验证
    'Q6': {'完全无关': -5, '远缘': -2, 'NO': 0},           # 领域偏离(扣分)
}
```

**修改指南**：
- 想保留更多边缘论文：降低 Q3 弱可迁移的判断门槛，或提高弱可迁移分值(+1→+2)
- 想更严格排除噪音：扩大 Q6 完全无关的覆盖范围，或提高扣分(-5→-7)
- 想强调实验数据：提高 Q5 分值(+2→+3)
- 调整推送阈值：修改 `retrieve_fab.py` 中 `FAB_PUSH_SCORE`（推送线，默认≥5）和 `min_score`（关注线，默认≥2）

### 新增监控领域

#### 第1步：创建检索脚本

在 `papers/` 目录下创建 `retrieve_xxx.py`：

```python
import os
from common import run_retrieval_pipeline

DOMAIN_LABEL = "新领域名称"
KEYWORDS = ["keyword1", "keyword2", "keyword3"]
SCORING_PROMPT = """评分规则Prompt..."""
LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "xxx_paper_log.txt")
AUTHOR_FILE = os.path.join(os.path.dirname(__file__), "data", "xxx_author_keywords.json")

def main(days=3, max_results=200):
    return run_retrieval_pipeline(
        keywords=KEYWORDS,
        scoring_prompt=SCORING_PROMPT,
        requirement="领域筛选要求",
        log_file=LOG_FILE,
        author_keywords_file=AUTHOR_FILE,
        domain_label=DOMAIN_LABEL,
        days=days, max_results=max_results, min_score=3,
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=3)
    parser.add_argument('--max-results', type=int, default=200)
    args = parser.parse_args()
    main(days=args.days, max_results=args.max_results)
```

#### 第2步：注册到推送系统

在 `push_papers.py` 的 `DOMAINS` 列表中添加：

```python
DOMAINS = [
    {"label": "超导量子器件", "log_file": "...", "retrieve_script": "retrieve_qubit.py"},
    {"label": "芯片加工工艺",   "log_file": "...", "retrieve_script": "retrieve_fab.py"},
    {"label": "新领域",        "log_file": "...", "retrieve_script": "retrieve_xxx.py"},
]
```

#### 第3步：更新定时任务

在 `fetch.bat` 中添加新领域的检索命令：

```batch
echo [%date% %time%] [NEW] retrieve_xxx...
%PYTHON% papers\retrieve_xxx.py --days 3 --max-results 200
```

---

## 文件结构

```
papers/
├── common.py                      # 核心引擎：arXiv客户端、PDF处理、AI调用、评分、钉钉推送
├── retrieve_fab.py                # 芯片加工工艺管线（Q1~Q6 rubric评分）
├── retrieve_qubit.py              # 超导量子器件管线（多维rubric评分）
├── push_papers.py                 # 统一推送脚本（多领域汇总→钉钉）【需修改】
├── fetch.bat                      # Windows定时任务：检索脚本【需修改】
├── push.bat                       # Windows定时任务：推送脚本【需修改】
├── config.ini.example             # API密钥配置模板（复制到 config.ini 使用）
├── .gitignore                     # Git忽略规则（屏蔽敏感文件和运行时数据）
├── requirements.txt               # Python依赖列表
├── README.md                      # 本文档
├── CLAUDE.md                      # 核心工程原则与理想架构方向
│
├── data/
│   ├── author_keywords.json       # 量子器件作者关键词积累（预置）
│   ├── fab_author_keywords.json   # 芯片工艺作者关键词积累（预置）
│   ├── favorites.json             # 收藏论文列表（预置）
│   ├── sent_papers.json           # 已推送论文去重记录（预置）
│   ├── pdfs/                      # PDF文件缓存（运行时自动创建，.gitignore屏蔽）
│   ├── fab_paper_log.txt          # 芯片工艺检索日志（运行时生成，.gitignore屏蔽）
│   ├── fab_paper_log_latest.json  # 芯片工艺推送线结果（运行时生成，.gitignore屏蔽）
│   ├── fab_paper_log_watch_latest.json  # 芯片工艺关注线结果（运行时生成，.gitignore屏蔽）
│   ├── qubit_paper_log.txt        # 量子器件检索日志（运行时生成，.gitignore屏蔽）
│   ├── qubit_paper_log_latest.json  # 量子器件最新结果摘要（运行时生成，.gitignore屏蔽）
│   └── push.log                   # 推送操作日志（运行时生成，.gitignore屏蔽）
│
├── docs/
│   ├── ARCHITECTURE.md            # 系统架构文档
│   └── SCORING_RULES.md           # Fab评分规则映射表
│
├── prompts/
│   └── fab_scoring_prompt.txt     # Fab评分prompt外部编辑版
│
└── scripts/
    └── generate_report.py         # Word报告生成脚本
```

> **说明**：
> - `config.ini`：用户手动创建（从 `config.ini.example` 复制），**不会上传到 GitHub**（`.gitignore` 已屏蔽）
> - `data/` 目录下标记为「运行时生成」的文件在首次运行检索脚本后自动创建，也被 `.gitignore` 屏蔽

---

## 常见问题与故障排除

### Q1：运行时报错 "DeepSeek API密钥未设置"

**原因**：`config.ini` 中的 API Key 仍然是默认值

**解决**：编辑 `config.ini`，填入你的 API Key：

```ini
[API_KEYS]
deepseek = sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Q2：推送失败，钉钉机器人没有收到消息

**可能原因**：
1. 钉钉 Webhook 或 Secret 配置错误
2. 钉钉机器人未在群聊中启用「加签」
3. 网络问题

**解决**：
1. 检查 `push_papers.py` 第 17-18 行的 `DINGTALK_WEBHOOK` 和 `DINGTALK_SECRET` 是否正确
2. 在钉钉群聊的机器人设置中确认「加签」已开启
3. 查看 `data/push.log` 中的错误信息

### Q3：出现 429 错误（arXiv API 限流）

**原因**：arXiv API 对请求频率有限制

**解决**：系统已内置自动降级机制：
1. 指数退避冷却（最多8次，最大1800秒）
2. 持续限流时自动切换到 RSS Feed（无频率限制）

无需手动干预，等待系统自动恢复即可。

### Q4：SSL 证书错误

**原因**：国内网络环境下 HuggingFace 的 SSL 证书验证失败

**解决**：系统已自动禁用 SSL 证书验证（`common.py` 第 37 行）。如果仍然报错，在运行前执行：

```bash
set SSL_CERT_FILE=
```

### Q5：Windows 下中文乱码

**原因**：Windows 默认编码为 GBK，与程序的 UTF-8 输出冲突

**解决**：系统已自动处理（`common.py` 第 22-34 行）。如果仍然乱码，确保运行脚本前执行：

```batch
chcp 65001
```

### Q6：PDF 下载失败

**原因**：网络问题或 arXiv 服务器暂时不可用

**解决**：系统会自动跳过下载失败的论文，不阻塞其余论文的处理。下次运行时会重新尝试下载。

### Q7：想更换 AI 服务

**解决**：通过 `--ai` 参数切换：

```bash
python retrieve_qubit.py --ai openai --days 3   # 使用 OpenAI
python retrieve_fab.py --ai claude --days 3      # 使用 Claude
python retrieve_qubit.py --ai gemini --days 3    # 使用 Gemini
```

确保在 `config.ini` 中配置了对应服务的 API Key。

### Q8：其他 AI 服务输出格式问题

**注意**：系统的评分 Prompt 是基于 **DeepSeek** 模型优化的，其他 AI 服务（如 OpenAI、Claude、Gemini）可能存在输出格式差异，导致分数解析失败。

**表现**：
- Gemini：可能只输出判定项（如"直接优化: YES"），但不输出"最终分数"和"一句话总结"
- Claude：输出格式可能包含额外的 Markdown 标记

**建议**：
- 如果使用其他 AI 服务遇到分数解析错误，需要根据该模型的输出风格微调 `retrieve_qubit.py` 和 `retrieve_fab.py` 中的 `SCORING_PROMPT_TEMPLATE`
- 重点调整输出格式部分的指令，确保模型严格按照要求输出"最终分数"和"一句话总结"

---

## 技术栈

| 层次 | 技术选型 | 选择理由 |
|------|---------|---------|
| 语言 | Python 3.11+ | NLP/LLM 生态最完善，类型注解支持好 |
| LLM | DeepSeek / OpenAI / Claude 三选一 | 通过 `--ai` 切换，默认 DeepSeek（性价比最高） |
| 论文获取 | arXiv API + RSS Feed | 官方接口 + 无限流回退方案 |
| PDF 解析 | PyMuPDF (fitz) | 纯 Python，速度快，无需系统级依赖 |
| 语义筛选 | sentence-transformers | 轻量（~80MB），CPU 毫秒级推理，免费 |
| 通知推送 | 钉钉机器人 Webhook | 团队日常使用平台，Markdown 支持好 |

---

## 相关文档

- **`CLAUDE.md`**: 核心工程原则与理想架构方向
- **`docs/ARCHITECTURE.md`**: 系统架构与数据流详解
- **`docs/SCORING_RULES.md`**: Fab 管线评分规则映射表与修改指南
- **`prompts/fab_scoring_prompt.txt`**: Fab 评分 prompt 外部编辑版
