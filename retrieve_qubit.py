# %%
"""
arXiv 论文检索 — 超导量子器件领域
检索与超导量子比特门保真度、T1/T2、读出优化、滤波器设计相关的论文。
"""
import os
import sys
from common import run_retrieval_pipeline

# =============================================================================
#  领域配置：超导量子器件
# =============================================================================

DOMAIN_LABEL = "超导量子器件"

DEFAULT_KEYWORDS = [
    "quantum error correction",
    "superconducting qubits",
    "transmon qubits",
    "superconducting quantum device"
]

AI_REQUIREMENT = "优化单双比特门读取保真度或者T1T2时间相关或者滤波器相关"

SCORING_PROMPT_TEMPLATE = """
你是一个超导量子比特领域的技术评审助手。
你的任务是：
严格按照固定规则判断论文是否与指定技术目标相关，并计算稳定、可重复的分数。
禁止：
- 主观判断
- 模糊语言
- 根据感觉评分
- 每次改变评分标准
必须：
- 先提取事实
- 再计算分数
- 最后生成总结
--------------------------------------------------
目标技术方向（只允许以下）：
1）提高 single-qubit 或 two-qubit gate fidelity
2）提高 T1 或 T2 coherence time
3）优化 readout fidelity 或 measurement error
4）设计或优化 filter / Purcell protection / impedance engineering
--------------------------------------------------
不属于目标方向：
以下内容默认不属于直接优化：
- quantum error correction
- quantum algorithm
- quantum information theory
- error modeling
- calibration method
- bath dynamics
--------------------------------------------------
💡【寻找亮点与启发性评估原则】
如果论文的主要标签属于 quantum error correction, quantum algorithm, calibration 或 error modeling，
不要直接否定。请判断其是否具备"他山之石"的启发价值：
- 如果其提出的物理机制、噪声模型、控制脉冲或纠错方案，能够【迁移或启发】用于超导硬件层面的保真度优化或寿命提升，应视为"间接相关/具备启发性"并予以保留或加分。
--------------------------------------------------
评分规则（必须逐项计算）
加分项：
+5 分：
论文提出了明确的工程方法，直接优化以下指标：
- gate fidelity
- T1
- T2
- readout fidelity
- filter performance
例如：
- pulse shaping
- DRAG
- resonator design
- filter design
- material improvement
- impedance engineering
- Purcell protection
否则：
这一项必须为 0
--------------------------------------------------
+3 分：【间接相关与方法论启发】（亮点保留项）
论文虽然不直接改变上述核心指标，或者属于纠错码/算法/校准方法，但其提出的方法、模型或协议对解决硬件瓶颈（如抑制噪声、减少残余耦合、优化读出控制等）具有【明确的启发性或迁移应用价值】。
否则：
这一项必须为 0
--------------------------------------------------
+2 分：
论文提出了器件或结构设计，并可能影响：
- decoherence
- relaxation
- readout performance
例如：
- resonator structure
- cavity design
- coupling design
- shielding
- packaging
- material engineering
否则：
这一项必须为 0
--------------------------------------------------
+1 分：
论文包含真实实验验证：
例如：
- 实测数据
- 实验平台
- device measurement
- characterization
如果只有 simulation 或 theory：
这一项必须为 0
--------------------------------------------------
+2 分：
论文明确属于：
superconducting qubit 系统
例如：
- transmon
- flux qubit
- superconducting circuit
- resonator
否则：
这一项必须为 0
--------------------------------------------------
扣分项：
-1 分：
不相关，
论文不改变目标指标，如果论文没有改变以下任一指标：
- gate fidelity
- T1
- T2
- readout fidelity
- filter performance

才允许扣 -1
--------------------------------------------------
-3 分：
论文主要贡献属于以下非设备优化方向：
- quantum error correction
- quantum algorithm
- quantum information theory
- quantum simulation
--------------------------------------------------
最终分数计算规则：
Final Score =
直接优化
+ 启发相关
+ 器件设计
+ 实验验证
+ 系统优化
- 扣分
    不相关:
    非设备优化方向
扣分 = sum
分数必须是整数。
禁止：给出小数分数。
--------------------------------------------------
执行步骤（必须按顺序）
Step 1：
提取事实（Fact Extraction）
使用：
YES 或 NO
--------------------------------------------------
Step 2：
计算分数（Score Calculation）
必须逐项列出。
--------------------------------------------------
Step 3：
生成一句话总结。
要求：
- 必须是技术性总结
- 不超过 50 个字
- 必须说明论文的核心方法。如果属于间接相关，必须点出其对硬件优化的【启发点】在哪里。
- 禁止评价性语言
--------------------------------------------------
输出格式（必须完全一致）
--------------------------------------------------
直接优化: YES / NO
启发相关: YES / NO
器件设计: YES / NO
实验验证: YES / NO
超导系统: YES / NO
不相关: YES / NO
非设备优化方向: YES / NO
--------------------------------------------------
分数分解：
直接优化:+5 或 0
启发相关:+3 或 0
器件设计:+2 或 0
实验验证:+1 或 0
超导系统:+2 或 0
扣分:-1 或 -3 或-4 或0
--------------------------------------------------
最终分数：整数
--------------------------------------------------
一句话总结：（不超过 50 个字）
--------------------------------------------------
"""

DEFAULT_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "qubit_paper_log.txt")
DEFAULT_AUTHOR_KEYWORDS_FILE = os.path.join(os.path.dirname(__file__), "data", "author_keywords.json")


# =============================================================================
#  主函数 & 命令行入口
# =============================================================================

def main(ai_service: str = "deepseek", start_date: str = None, end_date: str = None,
         days: int = 3, max_results: int = 9999, interactive: bool = False):
    """主函数：执行超导量子器件论文监控任务"""
    return run_retrieval_pipeline(
        keywords=DEFAULT_KEYWORDS,
        scoring_prompt=SCORING_PROMPT_TEMPLATE,
        requirement=AI_REQUIREMENT,
        log_file=DEFAULT_LOG_FILE,
        author_keywords_file=DEFAULT_AUTHOR_KEYWORDS_FILE,
        domain_label=DOMAIN_LABEL,
        ai_service=ai_service,
        start_date=start_date,
        end_date=end_date,
        days=days,
        max_results=max_results,
        max_selected=10,
        min_score=5,
        interactive=interactive,
        push_papers=False,  # 关闭独立推送，由 push_papers.py 统一推送
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f'arXiv论文监控 — {DOMAIN_LABEL}')
    parser.add_argument('--ai', type=str, default='deepseek', choices=['deepseek', 'openai', 'claude', 'gemini'],
                      help='选择使用的AI服务 (默认: deepseek)')
    parser.add_argument('--start-date', type=str, default=None,
                      help='开始日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=None,
                      help='结束日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=3,
                      help='搜索过去N天的论文 (默认: 3)')
    parser.add_argument('--max-results', type=int, default=9999,
                      help='从arXiv获取的最大论文数 (默认: 9999, 即不限制)')
    parser.add_argument('--interactive', '-i', action='store_true',
                      help='启动交互式模式，可以浏览和询问论文')

    args = parser.parse_args()
    main(ai_service=args.ai, start_date=args.start_date, end_date=args.end_date,
         days=args.days, max_results=args.max_results, interactive=args.interactive)
