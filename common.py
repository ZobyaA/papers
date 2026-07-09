# %%
import arxiv
import random
import time
from datetime import datetime, timedelta, timezone
import requests
import json
import hmac
import hashlib
import base64
from urllib.parse import quote, quote_plus
from typing import List, Dict, TypedDict
import os
import configparser
import io
import fitz  # PyMuPDF for PDF processing
import ssl
import sys
import re

# 强制 stdout/stderr 使用 UTF-8，解决 Windows GBK 环境下特殊字符（如 ∗ U+2217）的编码崩溃问题
def _wrap_stdio_utf8():
    """将标准输出/错误流重新包装为 UTF-8 编码，替换无法编码的字符"""
    for attr in ('stdout', 'stderr'):
        stream = getattr(sys, attr)
        try:
            if hasattr(stream, 'buffer'):
                setattr(sys, attr, io.TextIOWrapper(
                    stream.buffer, encoding='utf-8', errors='replace'
                ))
        except (ValueError, AttributeError):
            pass

_wrap_stdio_utf8()

if hasattr(ssl, '_create_unverified_context'):
    ssl._create_default_https_context = ssl._create_unverified_context

# =============================================================================
#  模块常量
# =============================================================================
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL = "gemini-2.5-flash"
ARXIV_BASE_URL = "http://export.arxiv.org/api/query?"

# =============================================================================
#  arXiv Client 单例（统一管理请求参数，避免各脚本各自创建客户端）
# =============================================================================
_ARXIV_CLIENT = None


def _get_arxiv_client():
    """获取全局单例 arXiv Client，使用保守的参数避免触发限流。

    参数设计依据 arXiv API 使用条款：
    - page_size=50（减少单页负载，默认100偏大）
    - delay_seconds=10（远低于限制，原默认3秒太激进）
    - num_retries=2（库内部重试2次而非默认3次，减少总请求量）
    """
    global _ARXIV_CLIENT
    if _ARXIV_CLIENT is None:
        _ARXIV_CLIENT = arxiv.Client(
            page_size=50,
            delay_seconds=10,
            num_retries=2,
        )
    return _ARXIV_CLIENT


# =============================================================================
#  网络与重试工具
# =============================================================================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
]

def get_random_user_agent():
    """获取随机User-Agent"""
    return random.choice(USER_AGENTS)

def get_jittered_delay(base_delay, attempt, jitter_factor=0.6, max_delay=180):
    """计算带随机抖动的指数退避延迟"""
    exponential_delay = base_delay * (2 ** attempt)
    exponential_delay = min(exponential_delay, max_delay)
    jitter = random.uniform(-jitter_factor, jitter_factor) * exponential_delay
    return max(1.0, exponential_delay + jitter)

def get_random_request_delay(min_delay=10.0, max_delay=30.0):
    """获取随机请求延迟（用于请求之间）"""
    return random.uniform(min_delay, max_delay)


# =============================================================================
#  配置加载
# =============================================================================

def load_config():
    """加载配置文件"""
    config = configparser.ConfigParser()
    config_file = os.path.join(PAPERS_DIR, 'config.ini')

    # 如果配置文件不存在，创建默认配置
    if not os.path.exists(config_file):
        config['API_KEYS'] = {
            'deepseek': 'your_deepseek_api_key',
            'openai': 'your_openai_api_key',
            'claude': 'your_claude_api_key'
        }
        with open(config_file, 'w') as f:
            config.write(f)
    else:
        config.read(config_file)

    return config


# papers/ 目录，用于定位 config.ini、data/、prompts/ 等资源
PAPERS_DIR = os.path.dirname(os.path.abspath(__file__))

config = load_config()
PDF_DIR = os.path.join(PAPERS_DIR, 'data', 'pdfs')
if not os.path.exists(PDF_DIR):
    os.makedirs(PDF_DIR)

SENT_PAPERS_FILE = os.path.join(PAPERS_DIR, 'data', 'sent_papers.json')

# =============================================================================
#  通讯作者提取正则模式 & Paper 类型
# =============================================================================
STAR_PATTERNS = [
    r'\*\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'∗\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'＊\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+\s*\*',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+\s*∗',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+\s*＊',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+[,\d\s]*\*',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+[,\d\s]*∗',
    r'[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+[,\d\s]*＊',
    r'\*([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
    r'([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)\*',
    r'∗([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
    r'([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)∗',
    r'＊([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
    r'([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)＊',
]

CA_PATTERNS = [
    r'corresponding author\s*[:：]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'通讯作者\s*[:：]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'Corresponding Author\s*[:：]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'correspondence\s*[:：]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'Correspondence\s*[:：]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z]\.)*\s+[A-Z][a-zA-Z]+',
    r'corresponding author\s*[:：]\s*([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
    r'通讯作者\s*[:：]\s*([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
    r'Corresponding Author\s*[:：]\s*([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',
]


class Paper(TypedDict, total=False):
    """论文信息类型定义"""
    title: str
    authors: List[str]
    published: str
    link: str
    pdf_link: str
    entry_id: str
    relevance_score: int
    summary: str
    ai_service: str
    corresponding_authors: List[str]


# =============================================================================
#  PDF 下载与文本提取
# =============================================================================

def get_paper_pdf_text(paper, pdf_dir=None):
    """
    统一的PDF下载与文本提取逻辑，供多处复用。
    :param paper: 论文信息字典（需含 title 和 pdf_link）
    :param pdf_dir: PDF 存储目录，默认使用全局 PDF_DIR
    :return: (pdf_text: str, paper: dict) — 返回提取的文本和可能更新了 corresponding_authors 的 paper
    """
    if pdf_dir is None:
        pdf_dir = PDF_DIR

    pdf_text = ""
    if 'pdf_link' in paper:
        safe_title = re.sub(r'[^a-zA-Z0-9_-]', '', paper['title'])
        pdf_filename = f"{safe_title[:50]}.pdf"
        pdf_path = os.path.join(pdf_dir, pdf_filename)

        if os.path.exists(pdf_path):
            pdf_text = extract_pdf_text(pdf_path)
            print(f"使用已下载的 PDF 内容，长度：{len(pdf_text)} 字符")
        elif download_pdf(paper['pdf_link'], pdf_path):
            pdf_text = extract_pdf_text(pdf_path)
            print(f"成功下载并提取 PDF 内容，长度：{len(pdf_text)} 字符")
        else:
            print(f"PDF下载失败，跳过分析: {paper['title']}")

        # 从 PDF 文本中提取通讯作者
        if pdf_text:
            corresponding_authors = extract_corresponding_authors_from_text(pdf_text)
            if corresponding_authors:
                paper['corresponding_authors'] = corresponding_authors
                print(f"从PDF中提取到通讯作者: {corresponding_authors}")
            else:
                paper['corresponding_authors'] = []

    return pdf_text, paper


def _is_valid_author_name(name: str) -> bool:
    """
    验证是否为合理的英文人名。
    - 至少 5 个字符
    - 以字母开头和结尾
    - 只包含字母、空格、点、连字符
    - 字母数量 > 数字/符号数量
    """
    s = name.strip()
    if len(s) < 5:
        return False
    # 以字母开头结尾，中间只允许字母、空格、点、连字符
    if not re.match(r'^[A-Za-z][A-Za-z\s.\-]*[A-Za-z]$', s):
        return False
    # 字母数量至少占一半
    alpha_count = sum(1 for c in s if c.isalpha())
    if alpha_count < len(s) * 0.5:
        return False
    return True


def extract_corresponding_authors_from_text(text):
    """
    从文本中提取通讯作者（星号标记 + 文字标注）。
    仅保留合理的英文人名，过滤数字/符号等垃圾数据。
    :param text: PDF 文本内容
    :return: 去重后的通讯作者列表
    """
    corresponding_authors = []

    for pattern in STAR_PATTERNS:
        matches = re.findall(pattern, text)
        corresponding_authors.extend(matches)

    for pattern in CA_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        corresponding_authors.extend(matches)

    # 去重 + 清理：只保留合理人名
    return [a for a in set(corresponding_authors) if _is_valid_author_name(a)]


def call_deepseek_api(messages, max_tokens=1000, timeout=10):
    """
    统一的 DeepSeek API 调用，返回解析后的文本内容。
    :param messages: 消息列表
    :param max_tokens: 最大 token 数
    :param timeout: 超时时间（秒）
    :return: AI 响应文本
    :raises Exception: API 密钥未设置或响应错误
    """
    deepseek_api_key = config.get('API_KEYS', 'deepseek')
    if deepseek_api_key == "your_deepseek_api_key":
        raise Exception("DeepSeek API密钥未设置")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {deepseek_api_key}"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "top_p": 1,
        "max_tokens": max_tokens
    }

    response = requests.post(
        DEEPSEEK_API_URL,
        headers=headers,
        json=data,
        timeout=timeout
    )
    response_data = response.json()

    if "choices" in response_data and len(response_data["choices"]) > 0:
        return response_data["choices"][0]["message"]["content"]
    else:
        raise Exception(f"API响应错误：{response_data}")


def call_openai_api(messages, max_tokens=1000, timeout=10):
    """
    统一的 OpenAI API 调用，返回解析后的文本内容。
    :param messages: 消息列表
    :param max_tokens: 最大 token 数
    :param timeout: 超时时间（秒）
    :return: AI 响应文本
    :raises Exception: API 密钥未设置或响应错误
    """
    openai_api_key = config.get('API_KEYS', 'openai')
    if openai_api_key == "your_openai_api_key":
        raise Exception("OpenAI API密钥未设置")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {openai_api_key}"
    }
    data = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "top_p": 1,
        "max_tokens": max_tokens
    }

    response = requests.post(
        OPENAI_API_URL,
        headers=headers,
        json=data,
        timeout=timeout
    )
    response_data = response.json()

    if "choices" in response_data and len(response_data["choices"]) > 0:
        return response_data["choices"][0]["message"]["content"]
    else:
        raise Exception(f"API响应错误：{response_data}")


def call_claude_api(messages, max_tokens=1000, timeout=10):
    """
    调用 Anthropic Claude API。
    将 OpenAI 格式的 messages 转为 Anthropic Messages API 格式（system 消息独立传递）。
    """
    api_key = config.get('API_KEYS', 'claude')
    if not api_key or api_key == 'your_claude_api_key':
        raise ValueError("Claude API key 未配置，请在 config.ini 中设置 [API_KEYS] claude = your_key")

    system_msg = ""
    anthropic_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            anthropic_messages.append({"role": m["role"], "content": m["content"]})

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": anthropic_messages
    }
    if system_msg:
        payload["system"] = system_msg

    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "content" in data and len(data["content"]) > 0:
        return data["content"][0]["text"]
    else:
        raise Exception(f"Claude API响应错误：{data}")


def call_gemini_api(messages, max_tokens=1000, timeout=60):
    """
    调用 Gemini API（通过 OpenAI 兼容接口）。
    使用 SOCKS5 代理访问 Google 服务。
    """
    api_key = config.get('API_KEYS', 'gemini')
    if not api_key or api_key == 'your_gemini_api_key':
        raise ValueError("Gemini API key 未配置，请在 config.ini 中设置 [API_KEYS] gemini = your_key")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": GEMINI_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "top_p": 1,
        "max_tokens": max_tokens
    }

    proxies = {
        "http": "socks5h://127.0.0.1:7897",
        "https": "socks5h://127.0.0.1:7897"
    }

    response = requests.post(
        GEMINI_API_URL,
        headers=headers,
        json=data,
        timeout=timeout,
        proxies=proxies
    )
    response_data = response.json()

    if "choices" in response_data and len(response_data["choices"]) > 0:
        return response_data["choices"][0]["message"]["content"]
    else:
        raise Exception(f"Gemini API响应错误：{response_data}")


def parse_deepseek_score(content):
    """
    从 DeepSeek 响应文本中解析评分和总结。
    :param content: AI 响应文本
    :return: (score: int, summary: str)
    :raises Exception: 无法提取分数或总结
    """
    score_patterns = [
        r'最终分数\s*[:：]\s*(-?\d+)',
        r'Final\s*Score\s*[:：]\s*(-?\d+)',
        r'分数\s*[:：]\s*(-?\d+)',
        r'Score\s*[:：]\s*(-?\d+)'
    ]

    score = None
    for pattern in score_patterns:
        score_match = re.search(pattern, content)
        if score_match:
            try:
                score = int(score_match.group(1))
                break
            except ValueError:
                continue

    if score is None:
        score_match = re.search(r'\b(-?\d+)\b', content)
        if score_match:
            score = int(score_match.group(1))
        else:
            raise Exception(f"未能提取最终分数：{content}")

    summary_match = re.search(r'一句话总结\s*[:：]\s*(.*?)(?:\n|$)', content)
    if summary_match:
        summary = summary_match.group(1).strip()
    else:
        lines = content.split('\n')
        summary = ""
        for line in reversed(lines):
            line = line.strip()
            if line and not any(skip in line for skip in [
                '最终分数', 'Final Score', '论文相关性', '直接优化', '分数分解', '评分理由', '---'
            ]):
                summary = line
                break
        if not summary:
            raise Exception(f"未能提取总结：{content}")

    score = min(score, SCORE_CAP)  # 截断至上限（如 quit 管线 AI 可能输出 12/10）
    return score, summary


def download_pdf(pdf_url, save_path, max_retries=3, timeout=30, progress_callback=None):
    """
    下载PDF文件
    :param pdf_url: PDF文件的URL
    :param save_path: 保存路径
    :param max_retries: 最大重试次数
    :param timeout: 超时时间（秒）
    :param progress_callback: 可选进度回调函数，签名为 callback(message: str, progress: float | None)
    :return: 下载是否成功
    """
    def _report(msg, progress=None):
        if progress_callback:
            progress_callback(msg, progress)
        else:
            # 输出到 stderr 避免通过 Tee 写入日志文件
            print(msg, file=sys.stderr)

    if os.path.exists(save_path):
        _report(f"PDF文件已存在: {save_path}")
        return True

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    for retries in range(max_retries):
        try:
            _report(f"正在下载PDF (尝试 {retries+1}/{max_retries}): {pdf_url}")
            response = requests.get(pdf_url, timeout=timeout, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100
                            _report(f"下载进度: {progress:.2f}%", progress)

            _report("PDF下载成功")
            return True
        except requests.exceptions.RequestException as e:
            _report(f"下载PDF时出错 (尝试 {retries+1}/{max_retries}): {e}")
            if retries < max_retries - 1:
                time.sleep(2)
            else:
                _report("达到最大重试次数，下载失败")
                return False
        except OSError as e:
            _report(f"下载PDF时文件系统错误: {e}")
            return False

class Tee:
    """同时写入多个文件对象的输出分流器（编码容错）"""
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            try:
                f.write(obj)
            except UnicodeEncodeError:
                # 兼容非 UTF-8 流：将不可编码字符替换为 ?
                safe = obj.encode(f.encoding or 'utf-8', errors='replace').decode(f.encoding or 'utf-8', errors='replace')
                f.write(safe)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


def extract_pdf_text(pdf_path):
    """
    提取PDF文本内容
    :param pdf_path: PDF文件路径
    :return: 提取的文本内容
    """
    try:
        doc = fitz.open(pdf_path)
        text = ' '.join([doc[i].get_text() for i in range(min(50, doc.page_count))])
        doc.close()
        return ' '.join(text.split())[:500000] + ('...' if len(text) > 500000 else '')
    except (RuntimeError, OSError, ValueError) as e:
        print(f"提取PDF文本时出错: {e}")
        return ""


# =============================================================================
#  arXiv API 交互
# =============================================================================

def fetch_arxiv_papers(keywords: List[str], max_results: int = 9999, days: int = 1, start_date: str = None, end_date: str = None, max_retries: int = 3, initial_delay: float = 10.0, cooldown_429: int = 300, negative_keywords: List[str] = None):
    """
    从arXiv获取论文（使用官方 arxiv 库）

    :param keywords: 关键词列表
    :param max_results: 最大返回结果数
    :param days: 最近多少天的文章
    :param start_date: 开始日期 (格式: YYYY-MM-DD)
    :param end_date: 结束日期 (格式: YYYY-MM-DD)
    :param max_retries: 最大重试次数（默认3，429限流不计入此配额）
    :param initial_delay: 初始延迟时间（秒），用于处理限流
    :param cooldown_429: 429 限流初始冷却时间（秒），每次429翻倍，最大1800秒
    :param negative_keywords: 可选，在arXiv服务端通过 ANDNOT 排除的关键词列表
                              （受限于255字符查询长度，仅追加能容纳的词）
    :return: 元组 (success: bool, papers: list, error_message: str)
    :return: 元组 (success: bool, papers: list, error_message: str)
             success=True 表示成功获取（可能返回0篇或多篇）
             success=False 表示获取失败
    """
    # 构建查询字符串
    keyword_query = " OR ".join([f'all:"{kw}"' for kw in keywords])

    # 设置日期范围
    if start_date and end_date:
        start_date_formatted = start_date.replace('-', '')
        end_date_formatted = end_date.replace('-', '')
    else:
        end_datetime = datetime.now()
        start_datetime = end_datetime - timedelta(days=days)
        start_date_formatted = start_datetime.strftime('%Y%m%d')
        end_date_formatted = end_datetime.strftime('%Y%m%d')

    # 在查询中添加日期过滤
    date_filter = f"submittedDate:[{start_date_formatted} TO {end_date_formatted}]"
    query = f"({keyword_query}) AND {date_filter}"

    # 服务端黑名单过滤：在查询中加入 ANDNOT 子句（受限于 arXiv API 约255字符的查询长度限制）
    if negative_keywords:
        appended = 0
        for nk in negative_keywords:
            clause = f' ANDNOT all:"{nk}"'
            if len(query) + len(clause) <= 250:  # 留5字符余量
                query += clause
                appended += 1
            else:
                print(f"查询已接近长度上限({len(query)}字符)，跳过后端 ANDNOT: {nk}")
        if appended > 0:
            print(f"服务端 ANDNOT: 已追加 {appended} 个排除关键词")

    print(f"搜索查询 ({len(query)}字符): {query}")
    print(f"日期范围: {start_date_formatted[:4]}-{start_date_formatted[4:6]}-{start_date_formatted[6:]} 至 {end_date_formatted[:4]}-{end_date_formatted[4:6]}-{end_date_formatted[6:]}")

    # 首次请求前添加随机启动延迟（3-10秒），避免与其他检索脚本同时请求
    startup_delay = random.uniform(3, 10)
    print(f"随机启动延迟 {startup_delay:.1f} 秒（避免多脚本同时请求）...")
    time.sleep(startup_delay)

    # 使用单例 Client，参数已调优（page_size=50, delay_seconds=10, num_retries=2）
    client = _get_arxiv_client()

    max_429_attempts = 8  # 429 限流最大容忍次数，超过后回退 OAI-PMH
    attempt_429 = 0
    attempt_other = 0

    while attempt_429 < max_429_attempts:
        try:
            # 使用 arxiv 官方库构建搜索
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending
            )

            papers = []
            for result in client.results(search):
                paper = {
                    "title": result.title.replace('\n', ' ').replace('  ', ' '),
                    "authors": [author.name for author in result.authors][:3],
                    "published": result.published.strftime('%Y-%m-%d'),
                    "link": result.entry_id,
                    "pdf_link": result.pdf_url,
                    "entry_id": normalize_entry_id(result.entry_id),
                    "summary": (result.summary or '').replace('\n', ' ').replace('  ', ' '),
                    "categories": list(result.categories) if result.categories else [],
                }
                papers.append(paper)

            print(f"从arXiv获取到 {len(papers)} 篇论文")
            return True, papers, ""

        except Exception as e:
            last_error = e

            # ---- 429 限流处理：指数冷却，不计入其他错误配额 ----
            is_429 = False
            # arxiv 库的 HTTPError 没有 status_code 属性，状态码嵌入在消息字符串中
            err_str = str(e)
            if type(e).__name__ == 'HTTPError' and '429' in err_str:
                is_429 = True

            if is_429:
                attempt_429 += 1
                if attempt_429 >= max_429_attempts:
                    print(f"⚠️  429 限流已达 {max_429_attempts} 次，退回 OAI-PMH 接口获取论文")
                    return False, [], "RATELIMITED"
                print(f"⚠️  429 限流（第 {attempt_429}/{max_429_attempts} 次），冷却 {cooldown_429} 秒（{cooldown_429/60:.1f} 分钟）...")
                time.sleep(cooldown_429)
                cooldown_429 = min(cooldown_429 * 2, 1800)  # 指数退避，最大30分钟
                continue
            # --------------------------------------------------------

            # 非429错误：正常指数退避重试
            attempt_other += 1
            if attempt_other < max_retries:
                delay = get_jittered_delay(initial_delay, attempt_other - 1, max_delay=120)
                print(f"请求 arXiv API 失败 (尝试 {attempt_other}/{max_retries}): {type(e).__name__}: {e}")
                print(f"等待 {delay:.1f} 秒后重试...")
                time.sleep(delay)
                continue
            else:
                error_msg = f"获取arXiv数据时出错: {type(last_error).__name__}: {last_error}"
                print(error_msg)
                return False, [], error_msg

    # safety net（不应到达）
    return False, [], "获取arXiv数据时发生未知错误"


# =============================================================================
#  RSS Feed 回退接口（arXiv API 限流时的替代方案，无频率限制）
# =============================================================================

def fetch_arxiv_papers_rss(
    keywords: List[str],
    max_results: int = 9999,
    days: int = 3,
    start_date: str = None,
    end_date: str = None,
    rss_categories: List[str] = None,
) -> tuple:
    """
    通过 arXiv RSS Feed 拉取论文，作为 /api/query 限流时的回退方案。

    优势：
    - RSS Feed 无频率限制（为 RSS 阅读器设计）
    - 天然是当日最新论文，不会混入旧论文
    - 简单 XML 格式，解析快速

    :param keywords: 关键词列表（用于本地过滤标题+摘要）
    :param max_results: 最大返回结果数
    :param days: 日期范围天数（默认3天），用于本地 pubDate 过滤
    :param start_date: 开始日期 (YYYY-MM-DD)
    :param end_date: 结束日期 (YYYY-MM-DD)
    :param rss_categories: arXiv 分类列表
    :return: (success, papers, error_message)
    """
    import urllib.request
    import xml.etree.ElementTree as ET
    import ssl as _ssl
    import re as _re
    from email.utils import parsedate_to_datetime

    # === 日期范围计算（使用 UTC 以匹配 RSS pubDate 的时区）===
    if start_date and end_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    else:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)

    # === arXiv 分类 ===
    if rss_categories is None:
        rss_categories = [
            "quant-ph",
            "cond-mat.mes-hall",
            "cond-mat.mtrl-sci",
            "cond-mat.supr-con",
            "physics.app-ph",
        ]

    print(f"[RSS] 日期范围: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}, 分类: {rss_categories}")

    _ssl._create_default_https_context = _ssl._create_unverified_context
    ns_dc = 'http://purl.org/dc/elements/1.1/'

    all_papers = []
    seen_ids = set()

    for cat in rss_categories:
        url = f'https://rss.arxiv.org/rss/{cat}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'arxiv-monitor/1.0'})
            resp = urllib.request.urlopen(req, timeout=15)
            data = resp.read()
            root = ET.fromstring(data)
        except Exception as e:
            print(f"[RSS] 分类 {cat} 请求失败: {e}")
            continue

        items = root.findall('.//item')
        cat_count = 0

        for item in items:
            try:
                # --- 解析 pubDate ---
                pub_date_str = (item.findtext('pubDate') or '').strip()
                paper_dt = None
                if pub_date_str:
                    try:
                        paper_dt = parsedate_to_datetime(pub_date_str)
                    except Exception:
                        pass

                # --- 日期过滤 ---
                if paper_dt is not None:
                    if paper_dt < start_dt or paper_dt > end_dt:
                        continue  # 超出请求日期范围

                # --- 解析字段 ---
                title = (item.findtext('title') or '').replace('\n', ' ').replace('  ', ' ').strip()
                link = (item.findtext('link') or '').strip()
                description = (item.findtext('description') or '').strip()
                guid = (item.findtext('guid') or '').strip()

                # 从 guid 提取 arXiv ID: oai:arXiv.org:2606.XXXXXv1
                arxiv_id = ''
                gm = _re.search(r'oai:arXiv\.org:(\d{4}\.\d{4,5})', guid)
                if gm:
                    arxiv_id = gm.group(1)
                else:
                    lm = _re.search(r'/abs/(\d{4}\.\d{4,5})', link)
                    if lm:
                        arxiv_id = lm.group(1)
                if not arxiv_id:
                    continue

                # 从 description 提取摘要: "arXiv:... Announce Type: new \nAbstract: ..."
                abstract = ''
                am = _re.search(r'Abstract:\s*(.*)', description, _re.DOTALL)
                if am:
                    abstract = am.group(1).replace('\n', ' ').replace('  ', ' ').strip()

                # 分类
                rss_cat = (item.findtext('category') or '').strip()

                # 提取作者
                authors = []
                for creator in item.findall(f'{{{ns_dc}}}creator'):
                    name = (creator.text or '').strip()
                    if name:
                        authors.append(name)

                if not arxiv_id or not title:
                    continue

                # 本地关键词过滤（标题 + 摘要）
                text = (title + ' ' + abstract).lower()
                if not any(kw.lower() in text for kw in keywords):
                    continue

                # 去重
                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)

                paper = {
                    "title": title,
                    "authors": authors[:3],
                    "published": paper_dt.strftime('%Y-%m-%d') if paper_dt else start_dt.strftime('%Y-%m-%d'),
                    "link": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_link": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "entry_id": normalize_entry_id(arxiv_id),
                    "summary": abstract,
                    "categories": [rss_cat] if rss_cat else [],
                }
                all_papers.append(paper)
                cat_count += 1

                if len(all_papers) >= max_results:
                    break

            except Exception:
                continue

        print(f"[RSS] {cat}: {cat_count} 篇相关")

        if len(all_papers) >= max_results:
            break

    print(f"[RSS] 总计获取 {len(all_papers)} 篇论文")
    return True, all_papers, ""


# =============================================================================
#  论文预筛选（关键词白名单 + 黑名单）
# =============================================================================

def prefilter_papers_by_keywords(
    papers: List[Dict],
    whitelist: List[str] = None,
    blacklist: List[str] = None,
    strip_summary: bool = True,
) -> List[Dict]:
    """
    基于论文标题和摘要的关键词预筛选，在下载 PDF / 调用 AI 之前过滤噪音。

    规则：
    - 黑名单优先：标题 OR 摘要命中任一黑名单关键词 → 直接排除
    - 白名单：标题 OR 摘要命中任一白名单关键词 → 通过
    - 未命中白名单 → 排除
    - 通过后默认删除 summary 字段以减小数据体积（下游 AI 分析使用 PDF 全文）

    :param papers: 论文列表（需含 title 和 summary 字段）
    :param whitelist: 白名单关键词列表（命中任一即通过）
    :param blacklist: 黑名单关键词列表（命中任一即排除）
    :param strip_summary: 通过后是否删除 summary 字段
    :return: 通过筛选的论文列表
    """
    if not whitelist and not blacklist:
        return papers

    passed = []
    skipped_bl = 0
    skipped_wl = 0

    for paper in papers:
        # 合并标题 + 摘要 + 分类做全文匹配
        text = (
            (paper.get('title', '') or '') + ' ' +
            (paper.get('summary', '') or '') + ' ' +
            ' '.join(paper.get('categories', []))
        ).lower()

        # 黑名单优先
        if blacklist:
            if any(kw.lower() in text for kw in blacklist):
                skipped_bl += 1
                continue

        # 白名单
        if whitelist:
            if not any(kw.lower() in text for kw in whitelist):
                skipped_wl += 1
                continue

        # 通过：删除 summary 避免膨胀
        if strip_summary and 'summary' in paper:
            del paper['summary']

        passed.append(paper)

    print(f"[预筛选] {len(passed)} 篇通过, {skipped_bl} 篇黑名单排除, {skipped_wl} 篇白名单未命中")
    return passed



# =============================================================================
#  论文分类过滤（arXiv 学科分类）
# =============================================================================

def prefilter_papers_by_category(
    papers: List[Dict],
    good_categories: List[str] = None,
    bad_categories: List[str] = None,
) -> List[Dict]:
    """
    基于 arXiv 学科分类过滤论文。

    - good_categories：论文的 categories 中至少命中一个才保留（精确匹配）
    - bad_categories：论文的 categories 中命中任一即排除（支持通配符，如 math.*）
    - 过滤优先级：bad > good（先排除坏分类，再检查好分类）

    :param papers: 论文列表（需含 categories 字段）
    :param good_categories: 好分类列表
    :param bad_categories: 坏分类列表（支持 fnmatch 通配符）
    :return: 通过过滤的论文列表
    """
    import fnmatch as _fnmatch

    if not good_categories and not bad_categories:
        return papers

    passed = []
    skipped_bad = 0
    skipped_no_good = 0

    for paper in papers:
        cats = paper.get('categories', [])
        if not cats:
            # 无分类信息，保守保留
            passed.append(paper)
            continue

        # 坏分类优先排除
        if bad_categories:
            is_bad = False
            for cat in cats:
                for pattern in bad_categories:
                    if _fnmatch.fnmatch(cat, pattern):
                        is_bad = True
                        break
                if is_bad:
                    break
            if is_bad:
                skipped_bad += 1
                continue

        # 好分类检查
        if good_categories:
            if not any(cat in good_categories for cat in cats):
                skipped_no_good += 1
                continue

        passed.append(paper)

    if skipped_bad or skipped_no_good:
        print(f"[分类过滤] {len(passed)} 篇通过, {skipped_bad} 篇坏分类排除, {skipped_no_good} 篇未命中好分类")
    return passed


# =============================================================================
#  钉钉通知
# =============================================================================

class DingTalkRobot:
    def __init__(self, webhook, secret):
        self.webhook = webhook
        self.secret = secret

    def _generate_sign(self, timestamp):
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f"{timestamp}\n{self.secret}"
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(hmac_code))
        return sign

    def send_markdown(self, title, text):
        timestamp = str(round(time.time() * 1000))
        sign = self._generate_sign(timestamp)

        url = f"{self.webhook}&timestamp={timestamp}&sign={sign}"

        headers = {"Content-Type": "application/json"}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            }
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
            return response.json()
        except Exception as e:
            print(f"发送消息时出错: {e}")
            return {"errcode": -1, "errmsg": str(e)}


def generate_markdown_content(papers, domain_label, keywords, requirement="", ai_service="deepseek"):
    """
    生成钉钉推送的 Markdown 内容。
    :param papers: 论文列表
    :param domain_label: 领域标签，如 "超导量子器件" / "芯片工艺"
    :param keywords: 关键词列表
    :param requirement: 筛选要求
    :param ai_service: AI 服务名称
    """
    if not papers:
        content = f"### 📚 arXiv论文监控 — {domain_label}\n\n今天没有找到相关的新论文。\n\n"
        content += f"**监控关键词:** {', '.join(keywords)}\n"
        if requirement:
            content += f"**筛选要求:** {requirement}\n"
        return content

    content = f"### 📚 最新arXiv论文推荐 — {domain_label}\n\n"
    content += f"**监控关键词:** {', '.join(keywords)}\n\n"
    if requirement:
        content += f"**筛选要求:** {requirement}\n\n"
    content += f"**找到 {len(papers)} 篇新论文:**\n\n"

    for i, paper in enumerate(papers, 1):
        # 清理标题中的特殊字符
        clean_title = paper['title'].replace('*', '').replace('#', '').replace('`', '')
        content += f"#### {i}. {clean_title}\n"
        content += f"**作者:** {', '.join(paper['authors'][:3])}{'等' if len(paper['authors']) > 3 else ''}\n"
        content += f"**发布时间:** {paper['published']}\n\n"
        if 'summary' in paper:
            content += f"**AI总结:** {paper['summary']}\n"
        content += f"**相关性评分:** {paper['relevance_score']}/10\n\n" if 'relevance_score' in paper else "\n"
        content += f"[📄 论文页面]({paper['link']}) | [📎 PDF下载]({paper['pdf_link']})\n\n---\n\n"

    content += f"---\n*自动推送服务 powered by arXiv API*\n*AI分析 powered by {ai_service.capitalize()}*"
    return content


def generate_combined_markdown(domain_results, ai_service="deepseek"):
    """
    将多个领域的论文结果合并为一条钉钉消息。
    :param domain_results: [(domain_label, papers, keywords, requirement), ...]
    :param ai_service: AI 服务名称
    :return: 合并后的 Markdown 字符串
    """
    total_count = sum(len(papers) for _, papers, _, _ in domain_results)
    if total_count == 0:
        content = "### 📚 arXiv论文监控\n\n今天所有领域均未找到相关的新论文。\n"
        content += f"---\n*自动推送服务 powered by arXiv API*\n*AI分析 powered by {ai_service.capitalize()}*"
        return content

    content = f"### 📚 最新arXiv论文推荐（{datetime.now().strftime('%Y-%m-%d')}）\n\n"
    content += f"**共找到 {total_count} 篇新论文**\n\n"

    for domain_label, papers, keywords, requirement in domain_results:
        if not papers:
            content += f"#### 🏷️ {domain_label}\n> 今天未找到相关新论文\n\n---\n\n"
            continue
        content += f"#### 🏷️ {domain_label}（{len(papers)} 篇）\n"
        content += f"关键词: {', '.join(keywords)}\n\n"
        if requirement:
            content += f"筛选要求: {requirement}\n\n"
        for i, paper in enumerate(papers, 1):
            clean_title = paper['title'].replace('*', '').replace('#', '').replace('`', '')
            content += f"**{i}. {clean_title}**\n"
            content += f"作者: {', '.join(paper['authors'][:3])}{'等' if len(paper['authors']) > 3 else ''}\n"
            content += f"评分: {paper.get('relevance_score', 'N/A')}/10\n"
            if 'summary' in paper:
                content += f"总结: {paper['summary']}\n"
            content += f"[📄论文]({paper['link']}) | [📎PDF]({paper['pdf_link']})\n\n"
        content += "---\n\n"

    content += f"*自动推送服务 powered by arXiv API | AI分析 powered by {ai_service.capitalize()}*"
    return content


def save_latest_json(papers: List[Dict], log_file: str, domain_label: str = "", keywords=None, requirement: str = ""):
    """
    保存最新一批论文的 JSON 摘要，供统一推送脚本读取。
    文件名由 log_file 派生而来（paper_log.txt → paper_log_latest.json）。
    采用合并模式：加载已有队列 → 合并新论文(entry_id去重) → 保存，避免覆盖丢失。
    """
    json_file = log_file.replace('.txt', '_latest.json')
    try:
        # 1. 加载已有队列
        existing = load_latest_json(log_file)
        existing_papers = existing.get('papers', []) if existing else []

        # 2. 合并：entry_id 去重，新论文覆盖旧论文（同篇论文可能分数/总结更新）
        merged_map = {}
        for p in existing_papers:
            eid = p.get('entry_id', '')
            if eid:
                merged_map[eid] = p
        for p in papers:
            eid = p.get('entry_id', '')
            if eid:
                merged_map[eid] = p  # 新覆盖旧

        merged = list(merged_map.values())

        # 3. 保存（不去重 sent_papers，由 push_papers.py 负责）
        data = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "domain_label": domain_label,
            "keywords": keywords or [],
            "requirement": requirement,
            "count": len(merged),
            "papers": merged
        }
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        new_n = len(papers)
        dup_n = len(existing_papers) + new_n - len(merged)
        extra = f", 更新{dup_n}篇" if dup_n else ""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已保存 JSON 摘要到: {json_file} "
              f"(累计{len(merged)}篇, 新增{new_n}篇{extra})")
    except (OSError, IOError, TypeError) as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 保存 JSON 摘要时出错: {e}")


def load_latest_json(log_file: str):
    """
    从日志文件对应的 JSON 摘要中加载最新论文。
    :param log_file: 日志文件路径
    :return: dict with keys: timestamp, domain_label, keywords, requirement, count, papers
    """
    json_file = log_file.replace('.txt', '_latest.json')
    try:
        if os.path.exists(json_file):
            with open(json_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"加载 JSON 摘要时出错 ({json_file}): {e}")
    return None


def load_papers_from_log(log_file: str, date_start: str = None, date_end: str = None):
    """
    从累积的文本日志中解析全部历史已筛选论文，按日期范围过滤后返回。

    日志格式示例（每篇论文一个 block）：
      1. Title
         作者: A, B
         发布日期: 2026-06-05
         链接: http://...
         PDF: https://...
         AI总结: ...
         相关性评分: 8/10
      --------------------------------------------------

    :param log_file: 日志文件路径
    :param date_start: 起始日期 YYYY-MM-DD（含），None 表示不限制
    :param date_end: 结束日期 YYYY-MM-DD（含），None 表示不限制
    :return: (papers: list, source_desc: str) — papers 是按 published 降序排列的论文列表
    """
    if not os.path.exists(log_file):
        return [], "日志文件不存在"

    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 按分隔线切分 block，每个 block 可能包含一篇论文
    blocks = re.split(r'\n-{30,50}\n', content)
    papers = []
    seen_links = set()

    for block in blocks:
        # 提取标题（以 "数字. " 开头的一行）
        title_m = re.search(r'^\d+\.\s*(.+?)$', block, re.MULTILINE)
        if not title_m:
            continue
        title = title_m.group(1).strip()

        # 作者
        authors_m = re.search(r'作者:\s*(.+?)$', block, re.MULTILINE)
        authors = [a.strip() for a in authors_m.group(1).split(',')] if authors_m else []

        # 发布日期
        pub_m = re.search(r'发布日期:\s*(\d{4}-\d{2}-\d{2})', block)
        published = pub_m.group(1) if pub_m else ''

        # 链接
        link_m = re.search(r'链接:\s*(https?://\S+)', block)
        link = link_m.group(1).strip() if link_m else ''

        # PDF
        pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)
        pdf_link = pdf_m.group(1).strip() if pdf_m else ''

        # AI总结
        summary_m = re.search(r'AI总结:\s*(.+?)$', block, re.MULTILINE)
        summary = summary_m.group(1).strip() if summary_m else ''

        # 相关性评分
        score_m = re.search(r'相关性评分:\s*(\d+)/10', block)
        score = int(score_m.group(1)) if score_m else 0

        # 通讯作者（可选）
        ca_m = re.search(r'通讯作者:\s*(.+?)$', block, re.MULTILINE)
        corresponding = [a.strip() for a in ca_m.group(1).split(',')] if ca_m else []

        # 去重（按链接）
        if link and link in seen_links:
            continue
        if link:
            seen_links.add(link)

        # 日期过滤
        if date_start and published < date_start:
            continue
        if date_end and published > date_end:
            continue

        entry_id = normalize_entry_id(link) if link else ''

        papers.append({
            'title': title,
            'authors': authors,
            'published': published,
            'link': link,
            'pdf_link': pdf_link,
            'entry_id': entry_id,
            'summary': summary,
            'relevance_score': score,
            'corresponding_authors': corresponding,
        })

    # 按发布日期降序
    papers.sort(key=lambda p: p.get('published', ''), reverse=True)

    src = os.path.basename(log_file)
    return papers, src



def normalize_entry_id(raw: str) -> str:
    """
    将任意格式的 arXiv entry_id 规范化为短格式。
    'http://arxiv.org/abs/2606.08406v1' → '2606.08406v1'
    '2606.08406v1' → '2606.08406v1'
    """
    if not raw:
        return ''
    # 从完整 URL 中提取 arXiv ID
    m = re.search(r'arxiv\.org/abs/([^/\s?#]+)', raw)
    if m:
        return m.group(1)
    # 如果已经是短格式，去掉可能的版本号后缀以外的杂质
    # 匹配纯 arXiv ID: 四位以上数字.五位数字(可选vN)
    m = re.search(r'(\d{4,}\.\d{4,}(?:v\d+)?)', raw)
    if m:
        return m.group(1)
    # 无法识别，返回去空格原文
    return raw.strip()


def load_sent_papers(sent_file: str = None):
    """
    加载已推送论文的 ID 集合（自动规范化为短格式）。
    :param sent_file: sent_papers.json 路径，默认使用全局 SENT_PAPERS_FILE
    :return: set of entry_id strings (短格式)
    """
    if sent_file is None:
        sent_file = SENT_PAPERS_FILE
    try:
        if os.path.exists(sent_file):
            with open(sent_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 规范化所有历史 ID（兼容旧数据的混合格式）
            raw_ids = data.get('entry_ids', [])
            ids = {normalize_entry_id(rid) for rid in raw_ids}
            # 有 ID 格式不一致或重复时，回写修复
            needs_rewrite = (len(ids) != len(raw_ids)) or any(
                normalize_entry_id(rid) != rid for rid in raw_ids
            )
            if needs_rewrite:
                print(f"sent_papers 格式修复: {len(raw_ids)} → {len(ids)} (去重+规范化)")
                data['entry_ids'] = sorted(ids)
                data['count'] = len(ids)
                with open(sent_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"已加载 {len(ids)} 条已推送记录 (来源: {os.path.basename(sent_file)})")
            return ids
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"加载 sent_papers 出错: {e}")
    return set()


def save_sent_papers(entry_ids: set, sent_file: str = None):
    """
    保存已推送论文 ID 到文件。
    :param entry_ids: entry_id 集合
    :param sent_file: sent_papers.json 路径
    """
    if sent_file is None:
        sent_file = SENT_PAPERS_FILE
    try:
        data = {
            "updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "count": len(entry_ids),
            "entry_ids": sorted(list(entry_ids))
        }
        with open(sent_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"已更新 sent_papers: {len(entry_ids)} 条记录")
    except (OSError, IOError, TypeError) as e:
        print(f"保存 sent_papers 出错: {e}")


def mark_papers_sent(papers: List[Dict], sent_file: str = None):
    """
    将一批论文标记为已推送（追加到 sent_papers.json）。
    自动规范化 entry_id 为短格式，兼容新旧数据。
    :param papers: 论文列表（需含 entry_id 字段）
    :param sent_file: sent_papers.json 路径
    """
    if not papers:
        return
    entry_ids = load_sent_papers(sent_file)
    new_ids = {normalize_entry_id(p.get('entry_id', p.get('link', '')))
               for p in papers if p.get('entry_id') or p.get('link')}
    entry_ids.update(new_ids)
    save_sent_papers(entry_ids, sent_file)


def deduplicate_papers(papers: List[Dict], sent_ids: set) -> List[Dict]:
    """
    根据已推送 ID 去重，返回未推送的论文。
    自动规范化 entry_id 后再比较，兼容新旧数据格式。
    :param papers: 论文列表（需含 entry_id 字段）
    :param sent_ids: 已推送的 entry_id 集合（应为短格式）
    :return: 去重后的论文列表
    """
    if not sent_ids:
        return papers
    fresh = []
    removed = 0
    for p in papers:
        pid = normalize_entry_id(p.get('entry_id', p.get('link', '')))
        if pid in sent_ids:
            removed += 1
        else:
            fresh.append(p)
    if removed > 0:
        print(f"去重: 跳过 {removed} 篇已推送论文，剩余 {len(fresh)} 篇待处理")
    return fresh


def save_titles_to_file(papers: List[Dict], log_file: str = "arxiv_titles.txt", requirement: str = "", total_papers: int = 0, selected_papers: int = 0):
    """
    将文章标题保存到本地txt文件

    :param papers: 文章列表
    :param log_file: 日志文件名
    :param requirement: 筛选要求
    :param total_papers: 从arXiv获取的论文数量
    :param selected_papers: AI筛选出的论文数量
    """
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\narXiv论文监控 - 记录时间: {current_time}\n{'='*60}\n\n")

            if requirement:
                f.write(f"筛选要求: {requirement}\n\n")

            if total_papers > 0:
                f.write(f"从arXiv获取: {total_papers} 篇论文\n")
            if selected_papers > 0:
                f.write(f"AI筛选出: {selected_papers} 篇论文\n\n")

            if not papers:
                f.write("本次未找到相关新论文\n")
            else:
                f.write(f"找到 {len(papers)} 篇新论文:\n\n")
                for i, paper in enumerate(papers, 1):
                    f.write(f"{i}. {paper['title']}\n   作者: {', '.join(paper['authors'])}{'等' if len(paper['authors']) > 3 else ''}\n")
                    if 'corresponding_authors' in paper and paper['corresponding_authors']:
                        f.write(f"   通讯作者: {', '.join(paper['corresponding_authors'])}\n")
                    f.write(f"   发布日期: {paper['published']}\n   链接: {paper['link']}\n   PDF: {paper['pdf_link']}\n")
                    if 'summary' in paper:
                        f.write(f"   AI总结: {paper['summary']}\n")
                    if 'relevance_score' in paper:
                        f.write(f"   相关性评分: {paper['relevance_score']}/10\n")
                    f.write("-" * 50 + "\n")

            f.write(f"\n记录完成于: {current_time}\n")

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已保存论文标题到: {log_file}")
        return True

    except (OSError, IOError) as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 保存到文件时出错: {e}")
        return False

# =============================================================================
#  Fab Rubric 评分规则（仅 fab 管线使用，qubit 管线不受影响）
#
#  工作方式：
#  1. AI 输出 Q1~Q6 判定文本（prompt 中禁止 AI 打分）
#  2. 程序检测到 "直接工艺:" 和 "工艺可迁移:" 关键字 → 自动启用 rubric 解析
#  3. qubit prompt 不含这些关键字 → 走原有 parse_deepseek_score 路径
#
#  修改评分映射：编辑 FAB_SCORING_MAP 字典即可，无需改 prompt
# =============================================================================

FAB_SCORING_MAP = {
    # Q2 直接工艺
    'Q2': {'YES': +5, 'NO': 0},
    # Q3 工艺可迁移性
    'Q3': {'强可迁移': +4, '中可迁移': +2, '弱可迁移': +1, 'NO': 0},
    # Q4 量子硬件相关性
    'Q4': {'直接相关': +2, '临近相关': +1, 'NO': 0},
    # Q5 实验验证
    'Q5': {'YES': +2, '部分': +1, 'NO': 0},
    # Q6 领域偏离（扣分项）
    'Q6': {'完全无关': -5, '远缘': -2, 'NO': 0},
}

FAB_SCORE_CAP = 10  # Fab rubric 分数上限
SCORE_CAP = 10       # 通用分数上限（qubit/quit 管线推送时截断至10）


def parse_fab_rubric(content: str) -> dict:
    """解析 fab rubric 格式的 AI 输出（Q1~Q6 + 证据 + 总结），不包含分数。"""
    result = {
        'Q1': '', 'Q2': 'NO', 'Q3': 'NO', 'Q4': 'NO', 'Q5': 'NO', 'Q6': 'NO',
        'summary': '', 'evidence': {},
    }

    m = re.search(r'实际研究对象\s*[:：]\s*(.+?)(?:\n|$)', content)
    if m: result['Q1'] = m.group(1).strip()

    for dim, label, pattern in [
        ('Q2', '直接工艺', r'(YES|NO)'),
        ('Q3', '工艺可迁移', r'(强可迁移|中可迁移|弱可迁移|NO)'),
        ('Q4', '量子硬件相关性', r'(直接相关|临近相关|NO)'),
        ('Q5', '实验验证', r'(YES|部分|NO)'),
        ('Q6', '领域偏离', r'(完全无关|远缘|NO)'),
    ]:
        m = re.search(rf'{label}\s*[:：]\s*{pattern}', content)
        if m: result[dim] = m.group(1).strip()
        ev = re.search(rf'{label}.*?\n\s*证据\s*[:：]\s*"(.+?)"', content, re.DOTALL)
        if ev: result['evidence'][dim] = ev.group(1).strip()

    m = re.search(r'一句话总结\s*[:：]\s*(.+?)(?:\n|$)', content)
    if m:
        result['summary'] = m.group(1).strip()
    return result


def compute_fab_score(parsed: dict) -> int:
    """根据 parse_fab_rubric 的解析结果计算最终分数，上限 FAB_SCORE_CAP。"""
    score = 0
    for dim in ['Q2', 'Q3', 'Q4', 'Q5', 'Q6']:
        val = parsed.get(dim, 'NO')
        score += FAB_SCORING_MAP[dim].get(val, 0)
    return min(score, FAB_SCORE_CAP)


# =============================================================================
#  AI 分析与评分
# =============================================================================

def analyze_paper_with_ai(paper, scoring_prompt, ai_service="deepseek", system_prompt=None):
    """
    使用指定的AI服务分析论文。
    - fab rubric 格式（含"直接工艺:"+"工艺可迁移:"关键字）→ 自动走 rubric 解析
    - 其他格式（qubit等）→ 走 parse_deepseek_score 解析
    :param paper: 论文信息字典
    :param scoring_prompt: 评分规则 Prompt 模板
    :param ai_service: AI服务名称，支持 'deepseek', 'openai', 'claude', 'gemini'
    :param system_prompt: 系统角色描述，默认使用通用描述
    :return: (score: int, summary: str)
    """
    if system_prompt is None:
        system_prompt = "你是一位专业的学术论文分析助手。请严格按照既定规则对每篇论文进行评审，禁止使用主观判断，不受论文顺序或其他因素的影响，保持输出格式统一。"

    # 下载PDF并提取文本（含通讯作者提取）
    pdf_text, paper = get_paper_pdf_text(paper)
    if not pdf_text and 'pdf_link' in paper:
        print(f"PDF下载失败，跳过分析: {paper['title']}")
        return 0, "PDF下载失败，无法分析内容"

    # 构建提示词
    prompt = f"请分析以下论文，并按照指定规则进行判定和总结：\n\n"
    prompt += f"PDF内容：\n{pdf_text}\n\n"
    prompt += "判定规则：\n"
    prompt += scoring_prompt

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # 根据AI服务调用不同的API
    if ai_service == "deepseek":
        content = call_deepseek_api(messages, max_tokens=1000, timeout=10)
    elif ai_service == "openai":
        content = call_openai_api(messages, max_tokens=1000, timeout=10)
    elif ai_service == "claude":
        content = call_claude_api(messages, max_tokens=1000, timeout=10)
    elif ai_service == "gemini":
        content = call_gemini_api(messages, max_tokens=1000, timeout=60)
    else:
        raise Exception(f"不支持的AI服务: {ai_service}")

    print(content)

    # ---- fab rubric 格式检测（仅 fab prompt 会触发，qubit 不受影响） ----
    if '直接工艺:' in content and '工艺可迁移:' in content:
        parsed = parse_fab_rubric(content)
        score = compute_fab_score(parsed)
        summary = parsed.get('summary', '')
        print(f"{score},{summary}")
        return score, summary

    # ---- 原有路径：qubit 等格式 ----
    score, summary = parse_deepseek_score(content)
    print(f"{score},{summary}")
    return score, summary


def analyze_with_deepseek(paper, scoring_prompt, system_prompt=None):
    """
    使用DeepSeek AI分析论文。（兼容旧调用）
    """
    return analyze_paper_with_ai(paper, scoring_prompt, ai_service="deepseek", system_prompt=system_prompt)


def analyze_with_openai(paper, scoring_prompt, system_prompt=None):
    """
    使用OpenAI分析论文。
    """
    return analyze_paper_with_ai(paper, scoring_prompt, ai_service="openai", system_prompt=system_prompt)


def analyze_with_claude(paper, scoring_prompt, system_prompt=None):
    """
    使用Claude分析论文。
    """
    return analyze_paper_with_ai(paper, scoring_prompt, ai_service="claude", system_prompt=system_prompt)


def analyze_with_gemini(paper, scoring_prompt, system_prompt=None):
    """
    使用Gemini分析论文。
    """
    return analyze_paper_with_ai(paper, scoring_prompt, ai_service="gemini", system_prompt=system_prompt)


def analyze_papers_with_ai(papers: List[Dict], scoring_prompt, min_score: int = 5, max_selected: int = 10, ai_service: str = "deepseek"):
    """
    使用AI分析论文，筛选最符合要求的文章并生成总结。

    :param papers: 论文列表
    :param scoring_prompt: 评分规则 Prompt 模板
    :param min_score: 最低评分阈值
    :param max_selected: 最大选择数量
    :param ai_service: AI服务名称，支持 'deepseek', 'openai'
    :return: 筛选后的论文列表，每篇包含summary字段
    """
    if not papers:
        return []

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在使用{ai_service} AI分析论文...")

    analyzed_papers = []

    for paper in papers:
        try:
            # 根据选择的AI服务调用分析函数
            score, summary = analyze_paper_with_ai(paper, scoring_prompt, ai_service=ai_service)

            paper['relevance_score'] = score
            paper['summary'] = summary
            paper['ai_service'] = ai_service  # 记录使用的AI服务
            analyzed_papers.append(paper)

        except (requests.exceptions.RequestException, KeyError, ValueError, OSError) as e:
            print(f"分析论文时出错：{e}")
            continue

    # 按Final Score排序并筛选
    analyzed_papers.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    high_score_papers = [paper for paper in analyzed_papers if paper.get('relevance_score', 0) >= min_score]
    selected_papers = high_score_papers[:max_selected]

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]{ai_service} AI分析完成，筛选出 {len(selected_papers)} 篇最相关的论文")
    return selected_papers


def extract_authors_from_log(log_file: str, min_score: int = 5) -> list:
    """
    从日志文件中提取高评分论文的通讯作者（通过星号、三角符号或文字标注识别）

    :param log_file: 日志文件路径
    :param min_score: 最低评分阈值
    :return: 作者列表
    """
    authors = []

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()

        paper_blocks = content.split('=' * 60)

        for block in paper_blocks:
            if '相关性评分:' in block:
                score_match = re.search(r'相关性评分:\s*(\d+)/10', block)
                if score_match:
                    score = int(score_match.group(1))
                    if score >= min_score:
                        # 优先从"通讯作者:"字段提取
                        ca_match = re.search(r'通讯作者:\s*(.+?)\n', block)
                        if ca_match:
                            ca_str = ca_match.group(1).strip()
                            ca_list = [a.strip() for a in ca_str.split(',')]
                            authors.extend(ca_list)
                        else:
                            # 回退到从"作者:"字段中查找标记，复用同一套正则
                            author_match = re.search(r'作者:\s*(.+?)\n', block)
                            if author_match:
                                author_str = author_match.group(1).strip()
                                # 复用 extract_corresponding_authors_from_text 的同一套正则
                                authors.extend(extract_corresponding_authors_from_text(author_str))

        authors = list(set(authors))
        print(f"从日志中提取到 {len(authors)} 位高评分论文的通讯作者")
        return authors

    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"从日志提取作者时出错: {e}")
        return []


def save_author_keywords(authors: list, keywords_file: str = "author_keywords.json"):
    """
    保存作者关键词到文件。仅保留合理的英文人名，过滤数字/符号。

    :param authors: 作者列表
    :param keywords_file: 保存文件路径
    """
    try:
        existing_authors = load_author_keywords(keywords_file)

        for new_author in authors:
            if new_author not in existing_authors and _is_valid_author_name(new_author):
                existing_authors.append(new_author)

        with open(keywords_file, 'w', encoding='utf-8') as f:
            json.dump(existing_authors, f, ensure_ascii=False, indent=2)

        print(f"已保存 {len(existing_authors)} 位作者关键词到 {keywords_file}")

    except (OSError, IOError, json.JSONDecodeError, TypeError) as e:
        print(f"保存作者关键词时出错: {e}")


def load_author_keywords(keywords_file: str = "author_keywords.json") -> list:
    """
    从文件加载作者关键词

    :param keywords_file: 关键词文件路径
    :return: 作者列表
    """
    try:
        if os.path.exists(keywords_file):
            with open(keywords_file, 'r', encoding='utf-8') as f:
                authors = json.load(f)
            print(f"从 {keywords_file} 加载了 {len(authors)} 位作者关键词")
            return authors
        else:
            return []
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"加载作者关键词时出错: {e}")
        return []


# =============================================================================
#  论文收藏与询问
# =============================================================================
FAVORITES_FILE = os.path.join(PAPERS_DIR, 'data', 'favorites.json')

def save_favorite_paper(paper: Dict):
    """
    保存感兴趣的论文到收藏列表

    :param paper: 论文信息字典
    """
    try:
        favorites = load_favorites()
        # 检查是否已存在
        exists = any(f['link'] == paper['link'] for f in favorites)
        if not exists:
            favorites.append(paper)
            with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                json.dump(favorites, f, ensure_ascii=False, indent=2)
            print(f"已将论文「{paper['title']}」添加到收藏")
        else:
            print(f"论文「{paper['title']}」已在收藏列表中")
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"保存收藏论文时出错: {e}")

def load_favorites() -> List[Dict]:
    """
    加载收藏的论文列表

    :return: 收藏的论文列表
    """
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"加载收藏列表时出错: {e}")
        return []

def remove_favorite_paper(link: str):
    """
    从收藏列表中移除论文

    :param link: 论文链接
    """
    try:
        favorites = load_favorites()
        favorites = [f for f in favorites if f['link'] != link]
        with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(favorites, f, ensure_ascii=False, indent=2)
        print("已从收藏列表中移除")
    except (OSError, IOError, json.JSONDecodeError) as e:
        print(f"移除收藏论文时出错: {e}")

def ask_paper_question(paper: Dict, question: str, scoring_prompt=None, ai_service: str = "deepseek", include_analysis: bool = False) -> str:
    """
    对指定论文进行 AI 询问

    :param paper: 论文信息字典
    :param question: 用户的问题
    :param scoring_prompt: 可选，评分规则模板（用于系统上下文）
    :param ai_service: AI 服务名称，支持 'deepseek', 'openai'
    :param include_analysis: 是否包含之前的分析结果
    :return: AI 的回答
    """
    # 获取论文 PDF 内容（已下载则直接读取，否则尝试下载）
    pdf_text, _ = get_paper_pdf_text(paper)
    if not pdf_text:
        print("无法下载 PDF，将仅基于论文元数据回答")

    # 构建提示词
    prompt = f"请分析以下论文并回答问题：\n\n"
    prompt += f"论文标题：{paper['title']}\n"
    prompt += f"作者：{', '.join(paper['authors'])}\n"
    prompt += f"发布时间：{paper.get('published', '')}\n"
    prompt += f"论文链接：{paper['link']}\n\n"

    if include_analysis:
        if 'relevance_score' in paper or 'summary' in paper:
            prompt += "=== 之前的 AI 分析结果 ===\n"
            if 'relevance_score' in paper:
                prompt += f"相关性评分：{paper['relevance_score']}/10\n"
            if 'summary' in paper:
                prompt += f"AI 总结：{paper['summary']}\n"
            prompt += "========================\n\n"

    if pdf_text:
        prompt += f"论文内容摘要：\n{pdf_text[:5000]}\n\n"

    prompt += f"用户问题：{question}\n"
    prompt += "请给出详细、专业的回答。"

    messages = [
        {"role": "system", "content": "你是一位专业的学术论文分析助手，擅长分析量子物理和超导相关论文。请基于提供的论文信息，准确、详细地回答用户问题。"},
        {"role": "user", "content": prompt}
    ]

    # 根据AI服务调用不同的API
    if ai_service == "deepseek":
        return call_deepseek_api(messages, max_tokens=2000, timeout=30)
    elif ai_service == "openai":
        return call_openai_api(messages, max_tokens=2000, timeout=30)
    elif ai_service == "claude":
        return call_claude_api(messages, max_tokens=2000, timeout=30)
    elif ai_service == "gemini":
        return call_gemini_api(messages, max_tokens=2000, timeout=30)
    else:
        raise Exception(f"不支持的AI服务: {ai_service}")

def display_favorites_list(favorites):
    """显示收藏论文列表"""
    print("\n=== 我的收藏列表 ===")
    for i, paper in enumerate(favorites, 1):
        print(f"{i}. {paper['title']}")
        print(f"   作者: {', '.join(paper['authors'])}")
        print(f"   发布: {paper.get('published', '')}")
        print(f"   链接: {paper['link']}")
        print()


def handle_favorite_question(selected_paper, scoring_prompt=None, ai_service: str = "deepseek"):
    """处理对收藏论文的提问子菜单"""
    has_analysis = 'relevance_score' in selected_paper or 'summary' in selected_paper

    print("\n1. 直接提问")
    if has_analysis:
        print("2. 基于之前的分析结果提问")
    else:
        print("2. 先获取 AI 分析再提问")
    print("3. 返回菜单")

    try:
        sub_choice = int(input("请选择操作（1-3）："))
    except ValueError:
        print("请输入有效的数字")
        return

    if sub_choice == 1:
        question = input("请输入你的问题：")
        if question.strip():
            print("\nAI 正在分析中...")
            answer = ask_paper_question(selected_paper, question, scoring_prompt, include_analysis=False)
            print("\n=== AI 回答 ===")
            print(answer)
        else:
            print("问题不能为空")
    elif sub_choice == 2:
        if has_analysis:
            question = input("请输入你的问题：")
            if question.strip():
                print("\nAI 正在分析中...")
                answer = ask_paper_question(selected_paper, question, scoring_prompt, ai_service=ai_service, include_analysis=True)
                print("\n=== AI 回答 ===")
                print(answer)
            else:
                print("问题不能为空")
        else:
            print("\n正在获取 AI 分析...")
            try:
                score, summary = analyze_paper_with_ai(selected_paper, scoring_prompt, ai_service=ai_service)
                selected_paper['relevance_score'] = score
                selected_paper['summary'] = summary
                print(f"\n分析完成：评分={score}, 总结={summary}")
                save_favorite_paper(selected_paper)
                continue_ask = input("是否要基于分析结果继续提问？(y/n): ")
                if continue_ask.lower() == 'y':
                    question = input("请输入你的问题：")
                    if question.strip():
                        print("\nAI 正在分析中...")
                        answer = ask_paper_question(selected_paper, question, scoring_prompt, ai_service=ai_service, include_analysis=True)
                        print("\n=== AI 回答 ===")
                        print(answer)
                    else:
                        print("问题不能为空")
            except Exception as e:
                print(f"分析失败：{e}")
    else:
        print("返回菜单")


def show_favorites_and_ask(scoring_prompt=None, ai_service: str = "deepseek"):
    """显示收藏列表并允许用户选择论文进行询问"""
    favorites = load_favorites()
    if not favorites:
        print("收藏列表为空，请先收藏感兴趣的论文")
        return

    display_favorites_list(favorites)

    try:
        choice = int(input("请输入要询问的论文编号（输入 0 退出）："))
        if choice == 0:
            return
        if 1 <= choice <= len(favorites):
            selected_paper = favorites[choice - 1]
            print(f"\n已选择：{selected_paper['title']}")
            handle_favorite_question(selected_paper, scoring_prompt, ai_service=ai_service)
        else:
            print("无效的编号")
    except ValueError:
        print("请输入有效的数字")
    except Exception as e:
        print(f"询问时出错: {e}")


# =============================================================================
#  交互式模式
# =============================================================================

def show_main_menu():
    """显示交互式主菜单"""
    print("\n=== arXiv论文助手 - 交互式模式 ===")
    print("1. 浏览已筛选论文（从最近一次检索结果加载）")
    print("2. 查看收藏列表")
    print("3. 退出")


def handle_paper_action(paper, scoring_prompt=None, ai_service: str = "deepseek"):
    """处理对单篇论文的操作：收藏 / 提问 / AI分析后提问"""
    print("\n1. 添加到收藏")
    print("2. 对这篇论文提问")
    print("3. 先获取 AI 分析再提问")
    try:
        sub_choice = int(input("请选择操作（1-3）："))
    except ValueError:
        print("请输入有效的数字")
        return

    if sub_choice == 1:
        save_favorite_paper(paper)
    elif sub_choice == 2:
        question = input("请输入你的问题：")
        if question.strip():
            print("\nAI 正在分析中...")
            answer = ask_paper_question(paper, question, scoring_prompt, ai_service, include_analysis=False)
            print("\n=== AI 回答 ===")
            print(answer)
        else:
            print("问题不能为空")
    elif sub_choice == 3:
        print("\n正在获取 AI 分析...")
        try:
            score, summary = analyze_paper_with_ai(paper, scoring_prompt, ai_service=ai_service)
            paper['relevance_score'] = score
            paper['summary'] = summary
            print(f"\n分析完成：评分={score}, 总结={summary}")
            save_favorite_paper(paper)
            continue_ask = input("是否要基于分析结果继续提问？(y/n): ")
            if continue_ask.lower() == 'y':
                question = input("请输入你的问题：")
                if question.strip():
                    print("\nAI 正在分析中...")
                    answer = ask_paper_question(paper, question, scoring_prompt, ai_service, include_analysis=True)
                    print("\n=== AI 回答 ===")
                    print(answer)
                else:
                    print("问题不能为空")
        except Exception as e:
            print(f"分析失败：{e}")
    else:
        print("无效选择")


def handle_fetch_papers(keywords, scoring_prompt=None, ai_service: str = "deepseek",
                        start_date: str = None, end_date: str = None, days: int = 1,
                        log_file: str = None):
    """处理论文浏览流程：从累积日志解析全部已筛选论文，按日期范围过滤"""

    # ---- 计算有效的日期范围 ----
    if start_date and end_date:
        date_start = start_date
        date_end = end_date
    elif start_date:
        date_start = start_date
        date_end = datetime.now().strftime('%Y-%m-%d')
    elif end_date:
        date_start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        date_end = end_date
    else:
        date_start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        date_end = datetime.now().strftime('%Y-%m-%d')

    papers = None

    # ---- 优先从累积文本日志解析全部已筛选论文（跨越多次运行） ----
    if log_file:
        papers, src = load_papers_from_log(log_file, date_start, date_end)
        if papers:
            # 按分数降序
            papers.sort(key=lambda p: p.get('relevance_score', 0), reverse=True)
            push = [p for p in papers if p.get('relevance_score', 0) >= 5]
            watch = [p for p in papers if 2 <= p.get('relevance_score', 0) < 5]
            print(f"\n📂 从 {src} 加载已筛选论文")
            print(f"   日期范围: {date_start} ~ {date_end}")
            print(f"   共 {len(papers)} 篇（🟢推送 {len(push)} 篇 | 🟡关注 {len(watch)} 篇）\n")
            for i, paper in enumerate(papers, 1):
                score = paper.get('relevance_score', '?')
                tier = "🟢" if score >= 5 else ("🟡" if score >= 2 else "⚪")
                print(f"{i}. {tier} [{score}分] {paper['title']}")
                print(f"   作者: {', '.join(paper['authors'][:3])}")
                print(f"   发布: {paper.get('published', '?')}")
                print(f"   总结: {paper.get('summary', '(无)')}")
                print()
            success = True
        else:
            print("\n⚠️  日志中未找到日期范围内的已筛选论文。")
            print("   请先运行一次非交互模式的检索。")
            print(f"   例如: python papers/retrieve_fab.py --start-date {date_start} --end-date {date_end}")
            choice = input("\n是否改为实时获取论文？(y/n): ")
            if choice.lower() != 'y':
                return
            success, papers, error_msg = fetch_arxiv_papers(keywords, max_results=9999,
                                                             start_date=start_date, end_date=end_date, days=days)
            if not success:
                print(f"获取失败: {error_msg}")
                return
            print(f"\n获取到 {len(papers)} 篇论文：")
            for i, paper in enumerate(papers, 1):
                print(f"{i}. {paper['title']}")
                print(f"   作者: {', '.join(paper['authors'])}")
                print(f"   发布: {paper['published']}")
                print(f"   链接: {paper['link']}")
                print()
    else:
        # 无 log_file 时直接实时获取（旧行为）
        print("\n正在获取最新论文...")
        success, papers, error_msg = fetch_arxiv_papers(keywords, max_results=9999,
                                                         start_date=start_date, end_date=end_date, days=days)
        if not success:
            print(f"获取失败: {error_msg}")
            return
        if not papers:
            print("未找到新论文")
            return
        print(f"\n找到 {len(papers)} 篇论文：")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper['title']}")
            print(f"   作者: {', '.join(paper['authors'])}")
            print(f"   发布: {paper['published']}")
            print(f"   链接: {paper['link']}")
            print()

    if not papers:
        return

    try:
        action = int(input("请输入要操作的论文编号（输入0返回菜单）："))
        if action == 0:
            return
        if 1 <= action <= len(papers):
            handle_paper_action(papers[action - 1], scoring_prompt, ai_service)
        else:
            print("无效的编号")
    except ValueError:
        print("请输入有效的数字")


def interactive_mode(keywords, scoring_prompt=None, ai_service: str = "deepseek",
                     start_date: str = None, end_date: str = None, days: int = 1,
                     log_file: str = None):
    """交互式模式：让用户浏览和询问论文"""
    show_main_menu()
    while True:
        try:
            choice = int(input("\n请选择操作（1-3）："))
            if choice == 1:
                handle_fetch_papers(keywords, scoring_prompt, ai_service,
                                    start_date=start_date, end_date=end_date, days=days,
                                    log_file=log_file)
            elif choice == 2:
                show_favorites_and_ask(scoring_prompt, ai_service=ai_service)
            elif choice == 3:
                print("退出程序")
                break
            else:
                print("请输入1-3之间的数字")
        except ValueError:
            print("请输入有效的数字")
        except Exception as e:
            print(f"发生错误: {e}")


# =============================================================================
#  Embedding 语义筛选（本地模型，免费，毫秒级）
# =============================================================================

_EMBED_MODEL = None


def _get_embed_model():
    """获取全局单例 embedding 模型（通过 HF 镜像下载，避免墙问题）"""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        import os as _os
        _os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    return _EMBED_MODEL


def prefilter_papers_by_similarity(
    papers: List[Dict],
    target_description: str,
    min_similarity: float = 0.4,
) -> List[Dict]:
    """
    基于 embedding 语义相似度的论文预筛选。

    对每篇论文的标题+摘要与目标描述计算 cosine similarity，
    低于阈值的直接跳过，不下载 PDF 也不调用 AI 评分。

    :param papers: 论文列表（需含 title 和 summary 字段）
    :param target_description: 目标领域描述文本
    :param min_similarity: 最低相似度阈值（0~1，默认0.4）
    :return: 通过筛选的论文列表
    """
    if not papers or not target_description:
        return papers

    model = _get_embed_model()
    from torch import nn as _nn

    target_emb = model.encode(target_description, convert_to_tensor=True)
    texts = [(p.get('title', '') + ' ' + p.get('summary', '')).strip() for p in papers]
    paper_embs = model.encode(texts, convert_to_tensor=True)

    passed = []
    skipped = 0
    cos = _nn.CosineSimilarity(dim=1)
    for i, paper in enumerate(papers):
        sim = float(cos(target_emb.unsqueeze(0), paper_embs[i].unsqueeze(0))[0])
        if sim >= min_similarity:
            paper['_embedding_similarity'] = round(sim, 3)
            passed.append(paper)
        else:
            skipped += 1

    if skipped > 0:
        print(f"[语义筛选] {len(passed)}/{len(papers)} 篇通过 (min_sim={min_similarity}, 跳过{skipped}篇)")
    return passed


# =============================================================================
#  综合评分 + Top-K 排序截断
# =============================================================================

def compute_paper_score(
    paper: Dict,
    keywords: List[str],
    target_emb=None,
    good_categories: List[str] = None,
    bad_categories: List[str] = None,
) -> float:
    """
    计算论文的综合评分，用于排序截断（不硬过滤）。

    final_score = 0.6 * embedding_similarity
                + 0.2 * keyword_overlap
                + 0.1 * category_score
                + 0.1 * title_boost

    category_score = (GOOD ? +0.1 : 0) + (BAD ? -0.15 : 0)
    """
    import fnmatch as _fnmatch

    title = (paper.get('title', '') or '').lower()
    abstract = (paper.get('summary', '') or '').lower()
    text = title + ' ' + abstract
    cats = paper.get('categories', [])
    kw_lower = [k.lower() for k in keywords]

    # --- embedding_similarity (0~1) ---
    emb_sim = paper.get('_embedding_similarity', 0.0)
    if emb_sim == 0.0 and target_emb is not None:
        model = _get_embed_model()
        from torch import nn as _nn
        paper_text = (title + ' ' + abstract).strip()
        if paper_text:
            paper_emb = model.encode(paper_text, convert_to_tensor=True)
            cos = _nn.CosineSimilarity(dim=0)
            emb_sim = float(cos(target_emb, paper_emb))
            paper['_embedding_similarity'] = round(emb_sim, 3)

    # --- keyword_overlap (0~1) ---
    hits = sum(1 for kw in kw_lower if kw in text)
    kw_overlap = hits / len(kw_lower) if kw_lower else 0.0

    # --- category_score ---
    cat_score = 0.0
    if good_categories:
        if any(c in good_categories for c in cats):
            cat_score += 0.1
    if bad_categories:
        for c in cats:
            if any(_fnmatch.fnmatch(c, p) for p in bad_categories):
                cat_score -= 0.15
                break

    # --- title_boost (0~1) ---
    title_hits = sum(1 for kw in kw_lower if kw in title)
    if title_hits >= 2:
        title_b = 1.0
    elif title_hits >= 1:
        title_b = 0.5
    else:
        title_b = 0.0

    final = 0.6 * emb_sim + 0.2 * kw_overlap + 0.1 * cat_score + 0.1 * title_b
    paper['_score_detail'] = {
        'emb': round(emb_sim, 3),
        'kw': round(kw_overlap, 3),
        'cat': round(cat_score, 3),
        'title_b': round(title_b, 3),
    }
    paper['_final_score'] = round(final, 4)
    return final


def get_top_k(total: int) -> int:
    """根据总论文数计算 Top-K：取 20%，向下取整，最少 10 篇"""
    return max(10, int(total * 0.2))


def rank_and_select_top_papers(
    papers: List[Dict],
    keywords: List[str],
    target_description: str = None,
    good_categories: List[str] = None,
    bad_categories: List[str] = None,
) -> List[Dict]:
    """
    对论文综合评分、排序，返回 Top-K 篇（20%，最少10篇）。
    """
    if not papers:
        return papers

    # 预计算 target embedding
    target_emb = None
    if target_description:
        model = _get_embed_model()
        target_emb = model.encode(target_description, convert_to_tensor=True)

    # 计算每篇论文的分数
    for p in papers:
        compute_paper_score(p, keywords, target_emb, good_categories, bad_categories)

    # 排序
    papers.sort(key=lambda p: p.get('_final_score', 0), reverse=True)

    k = get_top_k(len(papers))
    top = papers[:k]

    if k < len(papers):
        cutoff = top[-1].get('_final_score', 0) if top else 0
        print(f"[排序截断] Top-{k}/{len(papers)} 篇 (20%, cutoff={cutoff:.3f})")
    return top


# =============================================================================
#  主流程工具
# =============================================================================

def setup_logging(log_file: str):
    """设置 Tee 输出：同时写入日志文件和 stdout"""
    log = open(log_file, 'a', encoding='utf-8')
    original_stdout = sys.stdout
    sys.stdout = Tee(sys.stdout, log)
    return log, original_stdout


def load_keywords_and_authors(base_keywords, author_keywords_file):
    """加载关键词列表，合并默认关键词和作者关键词"""
    keywords = base_keywords.copy()
    existing_authors = load_author_keywords(author_keywords_file)
    if existing_authors:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 加载到 {len(existing_authors)} 位作者关键词")
        keywords.extend(existing_authors)
    return keywords


def run_retrieval_pipeline(
    keywords, scoring_prompt, requirement, log_file, author_keywords_file,
    domain_label="",
    ai_service="deepseek", start_date=None, end_date=None,
    days=3, max_results=9999, max_selected=10, min_score=5,
    push_score=None,  # ≥push_score 推送，min_score~push_score-1 关注
    interactive=False, push_papers=False,
    fetch_negative_keywords=None, rss_keywords=None,
    good_categories=None, bad_categories=None,
    target_description=None,
    prefilter_blacklist=None,
):
    """
    统一的论文检索流水线：获取 → 去重 → 综合评分排序 → Top-K → AI分析 → 保存。
    供各领域检索脚本的 main() 调用。

    :param keywords: 关键词列表
    :param scoring_prompt: AI 评分 Prompt 模板
    :param requirement: 筛选要求描述
    :param log_file: 日志文件路径
    :param author_keywords_file: 作者关键词文件路径
    :param domain_label: 领域标签
    :param ai_service: AI 服务名称
    :param start_date: 开始日期
    :param end_date: 结束日期
    :param days: 天数（默认 3），用于 Top-K 计算
    :param max_results: 最大获取论文数
    :param max_selected: 最大筛选论文数
    :param min_score: 最低评分阈值
    :param interactive: 是否进入交互模式
    :param push_papers: 是否立即推送
    :param fetch_negative_keywords: arXiv 查询层 ANDNOT 排除关键词
    :param rss_keywords: RSS Feed 回退关键词
    :param good_categories: 论文分类 GOOD（评分 +0.1）
    :param bad_categories: 论文分类 BAD（评分 -0.15，支持通配符）
    :param target_description: embedding 目标描述（用于评分）
    :return: 筛选后的论文列表
    """
    if interactive:
        interactive_mode(keywords, scoring_prompt, ai_service,
                         start_date=start_date, end_date=end_date, days=days,
                         log_file=log_file)
        return []

    # 设置日志分流
    log, original_stdout = setup_logging(log_file)

    print(f"\n{'='*60}")
    print(f"arXiv论文监控启动 [{domain_label}] - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print('='*60)

    # 加载关键词
    all_keywords = load_keywords_and_authors(keywords, author_keywords_file)

    # 获取论文
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在从arXiv获取论文...")
    if start_date and end_date:
        success, papers, error_msg = fetch_arxiv_papers(all_keywords, max_results=max_results, start_date=start_date, end_date=end_date, negative_keywords=fetch_negative_keywords)
    else:
        success, papers, error_msg = fetch_arxiv_papers(all_keywords, max_results=max_results, days=days, negative_keywords=fetch_negative_keywords)

    # OAI-PMH 回退：API 持续 429 → 降级到 OAI-PMH 元数据收割
    if not success and error_msg == "RATELIMITED":
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ /api/query 持续限流，启用 OAI-PMH 回退接口...")
        success, papers, error_msg = fetch_arxiv_papers_rss(
            rss_keywords if rss_keywords else all_keywords,
            max_results=max_results,
            days=days,
            start_date=start_date,
            end_date=end_date,
        )

    if success:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 从arXiv获取到 {len(papers)} 篇论文")
        # 去重：过滤已推送的论文
        sent_ids = load_sent_papers()
        papers = deduplicate_papers(papers, sent_ids)

        # 关键词黑名单硬过滤（在评分前排除无关领域论文）
        if prefilter_blacklist:
            papers = prefilter_papers_by_keywords(
                papers,
                whitelist=None,
                blacklist=prefilter_blacklist,
            )

        # 综合评分 + 排序 + Top-K 截断
        papers = rank_and_select_top_papers(
            papers,
            keywords=keywords,
            target_description=target_description,
            good_categories=good_categories,
            bad_categories=bad_categories,
        )
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 获取arXiv论文失败: {error_msg}")

    # 处理论文：AI分析、分线保存
    ai_papers = analyze_papers_with_ai(papers, scoring_prompt, min_score=min_score, max_selected=max_selected, ai_service=ai_service) if papers else []

    response = None
    if ai_papers:
        # 分线：推送线 (≥push_score) vs 关注线 (min_score ~ push_score-1)
        actual_push = push_score if push_score is not None else min_score
        push_tier = [p for p in ai_papers if p.get('relevance_score', 0) >= actual_push]
        watch_tier = [p for p in ai_papers if min_score <= p.get('relevance_score', 0) < actual_push]

        save_titles_to_file(ai_papers, log_file, requirement, len(papers), len(ai_papers))
        if push_tier or watch_tier:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🟢 推送线(≥{actual_push}分): {len(push_tier)} 篇  🟡 关注线(2~{actual_push-1}分): {len(watch_tier)} 篇")

        # 推送线 → _latest.json（供 push_papers.py 统一推送，合并模式不覆盖已有队列）
        if push_tier:
            save_latest_json(push_tier, log_file, domain_label, all_keywords, requirement)

        # 关注线 → _watch.json（供交互模式浏览，合并模式不覆盖已有队列）
        if watch_tier:
            save_latest_json(watch_tier, log_file.replace('.txt', '_watch.txt'), domain_label, all_keywords, requirement)

        # 独立推送模式（仅推送 push_tier）
        if push_papers and push_tier:
            markdown_content = generate_markdown_content(push_tier, domain_label, all_keywords, requirement, ai_service)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在发送到钉钉...")
            robot = DingTalkRobot(
                "https://oapi.dingtalk.com/robot/send?access_token=****",
                "****"
            )
            response = robot.send_markdown(f"🔬 arXiv论文更新 — {domain_label}", markdown_content)

        # 提取高评分论文的通讯作者（仅推送线）
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在提取高评分论文的通讯作者...")
        authors_from_log = extract_authors_from_log(log_file, min_score=actual_push)
        if authors_from_log:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 提取到 {len(authors_from_log)} 位通讯作者")
            save_author_keywords(authors_from_log, author_keywords_file)
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 未找到符合要求的新论文")
        save_titles_to_file([], log_file, requirement, len(papers), 0)

    if response:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 推送结果: {response}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 最终 🟢推送{len([p for p in ai_papers if p.get('relevance_score', 0) >= (push_score or min_score)])}篇 🟡关注{len([p for p in ai_papers if min_score <= p.get('relevance_score', 0) < (push_score or min_score)])}篇")

    # 恢复 stdout 并关闭日志
    sys.stdout = original_stdout
    log.close()
    return ai_papers
