# %%
"""
arXiv 论文检索 — 芯片加工工艺领域
检索与超导量子芯片制造工艺、Josephson结制备、晶圆级QPU集成相关的论文。
"""
import os
import sys
from common import run_retrieval_pipeline

# =============================================================================
#  领域配置：芯片加工工艺
# =============================================================================

DOMAIN_LABEL = "芯片加工工艺"

FAB_KEYWORDS = [
    "fab",
    "fabrication",
    "process",
    "wafer",
    "Junction",
    "CMOS",
    "yield",
    "QPU"
]

# 预筛选黑名单：标题或摘要命中任一关键词的论文将被直接排除
# （在下载 PDF 和调用 AI 之前生效，节省 API 额度）
FAB_NEGATIVE_KEYWORDS = [
    "llm",
    "language model",
    "reinforcement learning",
    "graph neural",
    "auction",
    "causal",
    "mathematics",
    "topology",
    "social network",
    "vision",
    "robot",
    "protein",
    "biology",
    "astrophysics",
    "cosmology",
]

# 服务端 ANDNOT 黑名单：精选短词在 arXiv 查询层直接排除（受限于255字符查询长度）
# 此处仅放已证实高噪音、短关键词、命中率高的词，完整黑名单见 FAB_NEGATIVE_KEYWORDS
FAB_SERVER_NEGATIVE = [
    "llm",
    "robot",
    "protein",
    "biology",
    "vision",
    "auction",
    "causal",
    "cosmology",
]

# RSS Feed 回退时使用的关键词（RSS 只能匹配标题+摘要，需要更宽的搜索词）
FAB_RSS_KEYWORDS = [
    "superconducting",
    "Josephson",
    "junction",
    "qubit",
    "transmon",
    "quantum",
    "fabrication",
    "lithography",
    "deposition",
    "wafer",
    "QPU",
    "electron beam",
    "thin film",
    "ALD",
]

# arXiv 学科分类过滤：仅保留相关物理领域，排除 AI/ML/数学/统计等
FAB_GOOD_CATEGORIES = [
    "quant-ph",
    "cond-mat",
    "cond-mat.supr-con",
    "cond-mat.mes-hall",
    "cond-mat.mtrl-sci",
    "physics",
    "physics.app-ph",
]
FAB_BAD_CATEGORIES = [
    "cs.LG",
    "cs.AI",
    "cs.CV",
    "cs.CL",
    "math.*",
    "stat.*",
    "econ.*",
]

# Embedding 语义筛选目标描述（定义理想论文的样子，用于 cosine similarity 计算）
FAB_TARGET_DESCRIPTION = """
Research related to superconducting quantum computing hardware,
especially fabrication, materials, interfaces, packaging,
process integration, coherence improvement, Josephson junctions,
superconducting films, cryogenic devices, and low-loss quantum circuits.

Includes:

superconducting qubits
transmon and fluxonium devices
superconducting materials
quantum chip manufacturing
low-loss dielectrics
interface engineering
coherence optimization
quantum packaging
cryogenic integration
microwave quantum devices
"""

FAB_MIN_SIMILARITY = 0.20  # 语义相似度阈值（低于此值的论文不进入 AI 评分）

AI_REQUIREMENT = "与超导量子芯片加工工艺相关"

FAB_SCORING_PROMPT_TEMPLATE = """
你是一个超导量子芯片加工工艺领域的技术评审助手。
你的任务是阅读论文PDF全文，提取事实并逐项判定。

重要：你只负责输出事实和判定。分数由外部程序自动计算，你不需要关心分数。
============================================================
核心反幻觉规则（必须遵守）
============================================================
默认原则：

除非论文正文中存在明确原文证据，
否则：
- 直接工艺 = NO
- 工艺可迁移 = NO
- 量子硬件相关性 = NO

禁止基于：
- 标题
- 作者背景
- 领域常识
- 超导/量子/微波等宽泛术语
进行推断。

1. 禁止推测、脑补、根据常识补全。
  禁止跨句拼接推理。
  必须存在单句或相邻句中的直接证据，
  才能给出：
  - YES
  - 强可迁移
  - 直接相关
  不得把论文不同部分的信息自行组合后得出结论。
2. 禁止在总结中加入论文未明确出现的术语。
3. 论文标题中的"superconducting""metamaterial""quantum"等词不能作为判定依据。
   必须从论文正文中找到原文证据后才给出非NO的判定。
4. 总结必须引用论文实际研究对象，而非你猜测它可能是什么。

错误示例（禁止）：
  "论文研究了Josephson junction fabrication工艺"
  （论文实际是超材料吸波器，未提及JJ → 正确应为"论文提出光学超表面吸收器设计方法，与超导量子芯片制造工艺无关"）

  "论文开发了超导量子比特的微纳加工工艺"
  （论文实际是触觉显示器，未提及qubit → 正确应为"论文研究超材料波聚焦用于触觉显示，与超导量子芯片制造工艺无关"）

正确示例：
  "论文研究超导微波谐振器中Ta薄膜的沉积工艺与低损耗表征"
  "论文提出光学超表面吸收器的逆向设计方法，与超导量子芯片制造工艺无关"

============================================================
证据规则
============================================================

Q2、Q3、Q4：如果答案不是 NO，必须附上论文中的原文句子作为证据。
如果找不到原文证据 → 必须回答 NO。
证据必须是从论文中引用的实际句子，不是你的归纳或改写。

============================================================
判定项目
============================================================

Q1 实际研究对象:
论文中明确出现的器件/材料/工艺/系统名称（列举即可，不要归纳或推测）。

Q2 直接工艺: 论文核心贡献是否直接涉及超导量子芯片的制造/加工/表征？
  YES（满足任一）：
    (a) 超导量子比特的微纳加工工艺（transmon/fluxonium/Xmon等制备流程）
    (b) Josephson结的制备技术（Dolan bridge、Manhattan、阴影蒸镀、氧化、离子铣等）
    (c) 超导量子芯片的晶圆级制造、良率分析或缺陷表征
    (d) CMOS兼容的量子比特加工或QPU集成封装
  NO（以下不算直接工艺）：
    - 制造了超导器件但不是量子比特（如SQUID磁强计、SNSPD探测器）
    - 纯理论提出超导器件概念但没有实际制造/加工
    - 量子比特的操控/读出/门保真度优化 → 属于测控领域
  [如果是YES，必须附原文证据]

Q3 工艺可迁移性: 论文的工艺/材料/器件技术能否迁移到超导量子芯片制造？
  强可迁移：技术可直接用于超导量子芯片制造
    - 超导薄膜沉积与表征（Ta/Nb/TiN/Al等量子计算常用材料）
    - 低损耗介质研究、TLS缺陷分析与抑制
    - 超导微波谐振器/滤波器/耦合器的制备工艺
    - 低温封装与3D集成（flip-chip、TSV、bump bonding）
    - 与量子器件直接相关的纳米加工（EBL、光刻、RIE刻蚀）
    - 量子芯片相关的界面工程、表面处理、衬底工艺
    - 超导量子芯片的低温微波表征
    - SQUID/SNSPD等超导量子器件的微纳加工工艺
  中可迁移：技术有明确迁移路径但非量子计算语境
    - 量子材料（拓扑绝缘体、二维材料等）的薄膜生长与器件加工
    - 低温电子学/超导数字电路（SFQ、RSFQ）的制造工艺
    - 其他量子平台（硅自旋、离子阱、光量子、NV色心）的微纳加工
    - 面向其他应用的精密超导/低温器件制造工艺
  弱可迁移：远期启发价值，无直接工艺迁移路径
    - 超导材料基础物性研究（非量子计算常用材料）
    - 超导量子器件的新物理效应（如量子点JJ中的超导二极管效应）
    - 与量子器件无关但涉及精密微纳加工的新技术/新方法
    - 应用于电磁/器件/工艺设计的AI驱动方法论（如基于GAN/扩散模型/强化学习的
      逆向设计框架、物理约束生成模型、代理模型加速仿真、贝叶斯工艺参数优化），
      其方法学框架可迁移到量子芯片的版图生成、工艺窗口优化或器件参数调优。
      注意：仅当论文的核心贡献是方法学本身的创新（而不仅仅是把现成AI工具
      套用到某个应用场景）时才适用本条。
  NO：论文不涉及任何可迁移到超导量子芯片制造的工艺/材料/器件/设计方法学技术
  [如果不是NO，必须附原文证据]

Q4 量子硬件相关性: 论文研究的器件/系统是否与超导量子计算硬件相关？
  直接相关：论文研究的器件/系统是超导量子计算核心元件
    - 超导量子比特（transmon/fluxonium/Xmon等）
    - Josephson结（任何类型）
    - 超导量子谐振器/耦合器/读出腔
    - 量子芯片/量子处理器/QPU
  临近相关：论文处于超导/低温电子学领域，与量子计算临近
    - cQED
    - 低温电子学器件与系统
    - 超导量子干涉器件（SQUID）或超导单光子探测器（SNSPD）
    - 其他量子计算平台的硬件
  NO：论文不在量子/超导硬件语境下
  [如果不是NO，必须附原文证据]

Q5 实验验证: 论文是否包含真实的实验数据？
  YES：有实测数据、器件照片、SEM/TEM图、电学/微波测量曲线
  部分：以仿真为主但有部分实验验证
  NO：纯理论推导、纯数值仿真、或无新实验数据的综述

Q6 领域偏离: 论文主题是否明显偏离超导量子芯片制造？

  判定前必须先做检查（强制）：
  查看上面 Q3 的答案。如果 Q3 = 弱可迁移/中可迁移/强可迁移，且 Q3 给出的
  证据涉及"方法学框架""设计方法""优化算法""生成模型""代理模型""逆向设计"
  等描述，说明论文的迁移价值在设计方法学上。
  此时：Q6 最高只能判"远缘"（-2），严禁判"完全无关"（-5）。
      - 若应用领域涉及超导/量子/微波器件 → Q6 = NO（0）
      - 否则 → Q6 = 远缘（-2）

  不满足上述方法学例外时，按以下列表判定：

  完全无关：论文属于以下明确无关的领域
    - 纯量子算法 / 量子纠错理论 / 量子信息理论（不涉及任何制造工艺）
    - 纯数学 / 纯物理理论（即使有"superconducting""quantum"字样但无任何器件制造内容）
    - 经典半导体CMOS且明确不涉及量子芯片（CPU/GPU/DRAM/FPGA）
    - 光学超材料/超表面（metamaterial/metasurface）用于天线/吸收器/触觉显示/成像等
    - 非量子器件（太阳能电池、LED、MEMS、自旋电子学、忆阻器）
    - 天体物理、高能物理、核物理、生物医学
    - LLM、机器学习、图神经网络（且未用于fab工艺优化/缺陷检测/yield分析）
    - 纯软件工程、需求工程、通信协议
  远缘：论文涉及量子/超导相关理论或计算，但无任何工艺/器件制造内容
    - 量子控制/量子门优化（纯脉冲/算法层面，无硬件制造）
    - 超导/凝聚态理论（纯公式推导，无材料/器件实验）
    - 量子计算架构/编译/路由（纯软件层面）
  NO：不触发扣分

============================================================
输出格式（严格遵守，不要增减字段，不要输出分数）
============================================================

实际研究对象: (论文中明确出现的器件/材料/工艺名称，列举即可)

直接工艺: YES / NO
[如果是YES] 证据: "..."

工艺可迁移: 强可迁移 / 中可迁移 / 弱可迁移 / NO
[如果不是NO] 证据: "..."

量子硬件相关性: 直接相关 / 临近相关 / NO
[如果不是NO] 证据: "..."

实验验证: YES / 部分 / NO

领域偏离: 完全无关 / 远缘 / NO

一句话总结: (不超过60字，必须基于论文实际内容)
--------------------------------------------------
"""

FAB_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "fab_paper_log.txt")
FAB_AUTHOR_KEYWORDS_FILE = os.path.join(os.path.dirname(__file__), "data", "fab_author_keywords.json")
FAB_PUSH_SCORE = 5  # ≥5 分推送，2-4 分关注（人工浏览）


# =============================================================================
#  主函数 & 命令行入口
# =============================================================================

def main(ai_service: str = "deepseek", start_date: str = None, end_date: str = None,
         days: int = 3, max_results: int = 200, interactive: bool = False):
    """主函数：执行芯片加工工艺论文监控任务"""
    return run_retrieval_pipeline(
        keywords=FAB_KEYWORDS,
        scoring_prompt=FAB_SCORING_PROMPT_TEMPLATE,
        requirement=AI_REQUIREMENT,
        log_file=FAB_LOG_FILE,
        author_keywords_file=FAB_AUTHOR_KEYWORDS_FILE,
        domain_label=DOMAIN_LABEL,
        ai_service=ai_service,
        start_date=start_date,
        end_date=end_date,
        days=days,
        max_results=max_results,
        max_selected=10,
        min_score=2,  # 关注线：≥2分保留供人工浏览
        push_score=FAB_PUSH_SCORE,  # 推送线：≥5分自动推送
        interactive=interactive,
        push_papers=False,  # 关闭独立推送，由 push_papers.py 统一推送
        fetch_negative_keywords=FAB_SERVER_NEGATIVE,  # arXiv 查询层 ANDNOT 排除
        rss_keywords=FAB_RSS_KEYWORDS,              # RSS 回退：更宽的标题摘要搜索词
        good_categories=FAB_GOOD_CATEGORIES,        # arXiv 分类白名单
        bad_categories=FAB_BAD_CATEGORIES,          # arXiv 分类黑名单（math.*, cs.*, stat.*等）
        target_description=FAB_TARGET_DESCRIPTION,  # Embedding 评分目标
        prefilter_blacklist=FAB_NEGATIVE_KEYWORDS,    # 关键词黑名单硬过滤
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f'arXiv论文监控 — {DOMAIN_LABEL}')
    parser.add_argument('--ai', type=str, default='deepseek', choices=['deepseek', 'openai', 'claude'],
                      help='选择使用的AI服务 (默认: deepseek)')
    parser.add_argument('--start-date', type=str, default=None,
                      help='开始日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=None,
                      help='结束日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=3,
                      help='搜索过去N天的论文 (默认: 3)')
    parser.add_argument('--max-results', type=int, default=200,
                      help='从arXiv获取的最大论文数 (默认: 200, fab领域噪音大建议限制)')
    parser.add_argument('--interactive', '-i', action='store_true',
                      help='启动交互式模式，可以浏览和询问论文')

    args = parser.parse_args()
    main(ai_service=args.ai, start_date=args.start_date, end_date=args.end_date,
         days=args.days, max_results=args.max_results, interactive=args.interactive)
