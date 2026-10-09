# %%
"""
arXiv 论文统一推送脚本
汇总多个领域的论文检索结果，生成合并报告后推送到钉钉。

扩展方式：在 DOMAINS 列表中添加新领域即可，无需修改推送逻辑。
"""
import os
import sys
from datetime import datetime
from common import (DingTalkRobot, DingTalkConfigError, load_dingtalk_credentials, generate_markdown_content, load_latest_json,
                    mark_papers_sent, load_sent_papers, deduplicate_papers)

# =============================================================================
#  推送日志
# =============================================================================
PUSH_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "push.log")


def _push_log(msg: str):
    """追加一条带时间戳的推送日志。"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(PUSH_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except (OSError, IOError) as e:
        print(f"[{ts}] 写推送日志失败: {e}")


# =============================================================================
#  领域注册（扩展新领域只需在此列表添加一项）
# =============================================================================
# 每项格式:
#   {
#       "label": "领域名称（显示在推送消息中）",
#       "log_file": "对应检索脚本的日志文件路径",
#       "retrieve_script": "检索脚本文件名（仅用于文档/提示）",
#   }
DOMAINS = [
    {
        "label": "超导量子器件",
        "log_file": os.path.join(os.path.dirname(__file__), "data", "qubit_paper_log.txt"),
        "retrieve_script": "retrieve_qubit.py",
    },
    {
        "label": "芯片加工工艺",
        "log_file": os.path.join(os.path.dirname(__file__), "data", "fab_paper_log.txt"),
        "retrieve_script": "retrieve_fab.py",
    },
    # 添加新领域示例：
    # {
    #     "label": "量子纠错码",
    #     "log_file": os.path.join(os.path.dirname(__file__), "qec_paper_log.txt"),
    #     "retrieve_script": "retrieve_qec.py",
    # },
]


# =============================================================================
#  推送逻辑
# =============================================================================

def collect_results():
    """
    从所有注册领域的 JSON 摘要中收集最新论文。
    :return: [(domain_label, papers, keywords, requirement), ...]
    """
    domain_results = []

    for domain in DOMAINS:
        label = domain["label"]
        log_file = domain["log_file"]
        retrieve_script = domain["retrieve_script"]

        data = load_latest_json(log_file)
        if data and data.get("papers"):
            papers = data["papers"]
            keywords = data.get("keywords", [])
            requirement = data.get("requirement", "")
            print(f"[{label}] 加载到 {len(papers)} 篇论文 (来源: {retrieve_script})")
            domain_results.append((label, papers, keywords, requirement))
        else:
            print(f"[{label}] 暂无新论文 (来源: {retrieve_script})")
            domain_results.append((label, [], [], ""))

    return domain_results


def push(ai_service: str = "deepseek", dry_run: bool = False):
    """
    收集各领域结果并推送。每个领域作为一条独立钉钉消息发送。

    :param ai_service: AI 服务名称
    :param dry_run: True=仅生成内容不推送，打印到 stdout
    """
    _push_log("=" * 50)
    _push_log("推送任务开始" + (" (dry-run)" if dry_run else ""))

    domain_results = collect_results()

    total_before = sum(len(papers) for _, papers, _, _ in domain_results)
    if total_before == 0:
        _push_log("所有领域均无新论文，跳过推送。")
        return

    # ---- 推送前去重：检查 sent_papers，过滤已推送论文 ----
    sent_ids = load_sent_papers()
    deduped_results = []
    total_after = 0
    total_skipped = 0

    for label, papers, keywords, requirement in domain_results:
        if not papers:
            deduped_results.append((label, [], keywords, requirement))
            continue
        fresh = deduplicate_papers(papers, sent_ids)
        skipped = len(papers) - len(fresh)
        total_skipped += skipped
        total_after += len(fresh)
        deduped_results.append((label, fresh, keywords, requirement))

    if total_skipped > 0:
        _push_log(f"去重: 跳过 {total_skipped} 篇已推送论文，剩余 {total_after} 篇待推送")

    if total_after == 0:
        _push_log("去重后无新论文需要推送。")
        return

    if dry_run:
        _push_log(f"预览模式，共 {total_after} 篇，分 {len(deduped_results)} 条消息")
        for label, papers, keywords, requirement in deduped_results:
            if not papers:
                print(f"\n=== [{label}] 无新论文（全部已推送或未筛选出），跳过 ===\n")
                continue
            md = generate_markdown_content(papers, label, keywords, requirement, ai_service)
            print(f"\n--- [{label}] 消息内容 ---")
            print(md)
        _push_log("预览结束")
        return

    try:
        credentials = load_dingtalk_credentials()
    except DingTalkConfigError as e:
        _push_log(f"ERROR 创建钉钉机器人失败: {e}")
        return None
    try:
        robot = DingTalkRobot(*credentials)
    except Exception:
        _push_log("ERROR 创建钉钉机器人失败")
        return None

    responses = []
    for label, papers, keywords, requirement in deduped_results:
        if not papers:
            _push_log(f"[{label}] 无新论文，跳过推送。")
            continue
        try:
            md = generate_markdown_content(papers, label, keywords, requirement, ai_service)
            _push_log(f"[{label}] 正在推送 {len(papers)} 篇论文到钉钉...")
            resp = robot.send_markdown(f"🔬 arXiv论文更新 — {label}", md)
            responses.append((label, resp))
            if resp.get("errcode") == 0:
                mark_papers_sent(papers)
                titles = ', '.join(p.get('title', '?')[:60] for p in papers)
                _push_log(f"[{label}] 推送成功: {len(papers)} 篇 → {titles}")
            else:
                _push_log(f"[{label}] 推送失败: errcode={resp.get('errcode')} errmsg={resp.get('errmsg')}")
        except Exception:
            _push_log(f"[{label}] 推送异常，请检查生成内容或钉钉服务")
            responses.append((label, {"errcode": -1, "errmsg": "推送异常"}))

    _push_log("推送任务完成")
    return responses


# =============================================================================
#  命令行入口
# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='arXiv论文统一推送 — 汇总多领域结果推送到钉钉')
    parser.add_argument('--ai', type=str, default='deepseek',
                      help='AI 服务名称（仅用于脚注标识）')
    parser.add_argument('--dry-run', action='store_true',
                      help='仅预览推送内容，不实际发送')
    parser.add_argument('--list-domains', action='store_true',
                      help='列出所有注册的领域')

    args = parser.parse_args()

    if args.list_domains:
        print("=== 已注册的领域 ===")
        for i, d in enumerate(DOMAINS, 1):
            print(f"{i}. {d['label']}")
            print(f"   检索脚本: {d['retrieve_script']}")
            print(f"   日志文件: {d['log_file']}")
            json_file = d['log_file'].replace('.txt', '_latest.json')
            exists = os.path.exists(json_file)
            print(f"   摘要文件: {json_file} {'✅ 存在' if exists else '❌ 不存在（请先运行检索脚本）'}")
            print()
        sys.exit(0)

    push(ai_service=args.ai, dry_run=args.dry_run)
