#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic Chinese AIGC industry research report PDF.

This is sample data for the Arrow Lake cookbook. Content is compiled from
publicly available AIGC industry information and is provided for technical
demonstration only (ingestion, chunking, vector search, full-text search,
RAG, knowledge-graph extraction). It is not a real research report and does
not represent the position of any organization.

Usage:
    python generate_report.py
Output:
    aigc_industry_report.pdf  (next to this script)

Requires: reportlab (the cookbook venv installs it; or `uv pip install reportlab`).
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import os

from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Chinese font: STSong-Light is a CJK CID font bundled with reportlab,
# so no external .ttf is required.
# ---------------------------------------------------------------------------
CJK = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(CJK))

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigc_industry_report.pdf")
TITLE = "2024 中国 AIGC 产业发展研究报告"
SUBTITLE = "技术演进、产业链与应用场景深度分析"


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _styles():
    base = getSampleStyleSheet()
    s = {}
    s["cover_title"] = ParagraphStyle(
        "cover_title", parent=base["Title"], fontName=CJK,
        fontSize=26, leading=36, alignment=TA_CENTER, spaceAfter=18,
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub", parent=base["Normal"], fontName=CJK,
        fontSize=15, leading=22, alignment=TA_CENTER, textColor=colors.HexColor("#444444"),
    )
    s["cover_note"] = ParagraphStyle(
        "cover_note", parent=base["Normal"], fontName=CJK,
        fontSize=10.5, leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#888888"),
        leftIndent=2 * cm, rightIndent=2 * cm,
    )
    s["toc"] = ParagraphStyle(
        "toc", parent=base["Normal"], fontName=CJK,
        fontSize=12.5, leading=24,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName=CJK,
        fontSize=20, leading=30, spaceBefore=18, spaceAfter=12,
        textColor=colors.HexColor("#1a3a6b"),
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName=CJK,
        fontSize=14.5, leading=22, spaceBefore=12, spaceAfter=6,
        textColor=colors.HexColor("#2a5298"),
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["BodyText"], fontName=CJK,
        fontSize=10.5, leading=19, alignment=TA_JUSTIFY,
        firstLineIndent=21, spaceAfter=7,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=base["BodyText"], fontName=CJK,
        fontSize=10.5, leading=18, leftIndent=18, bulletIndent=6, spaceAfter=4,
    )
    s["cell"] = ParagraphStyle(
        "cell", parent=base["BodyText"], fontName=CJK,
        fontSize=9.5, leading=14,
    )
    return s


ST = _styles()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def P(a, b=None):
    # P("正文")  or  P("style名", "文本")
    if b is None:
        return Paragraph(a, ST["body"])
    return Paragraph(b, ST[a])


def bullets(items):
    return [Paragraph(f"• {it}", ST["bullet"]) for it in items]


def table(rows, col_widths=None):
    data = [[Paragraph(str(c), ST["cell"]) for c in row] for row in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a6b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), CJK),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9c4d6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


# ---------------------------------------------------------------------------
# Page furniture: header + footer with page number
# ---------------------------------------------------------------------------
def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(CJK, 8.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(2 * cm, A4[1] - 1.2 * cm,
                      "AIGC 产业发展研究报告（示例文档 · 仅供技术演示）")
    canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 1.2 * cm, "Arrow Lake Cookbook")
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"— {doc.page} —")
    canvas.restoreState()


# ===========================================================================
# CONTENT  (compiled from publicly available AIGC industry information)
# ===========================================================================
CHAPTERS = []


def ch(title, blocks):
    CHAPTERS.append((title, blocks))


# ---- Chapter 1 ----
ch("第一章  行业概述与背景", [
    P("h1", "第一章  行业概述与背景"),
    P("h2", "1.1  AIGC 的定义与内涵"),
    P("AIGC（Artificial Intelligence Generated Content，人工智能生成内容）是指利用人工智能技术，"
      "尤其是以大语言模型、扩散模型为代表的大规模预训练模型，自动生成文本、图像、音频、视频、代码"
      "乃至三维资产等内容的新型生产方式。AIGC 区别于传统的 PGC（专业生成内容）与 UGC（用户生成内容），"
      "其核心特征是由模型而非人直接创作内容，从而显著降低内容生产的边际成本。"),
    P("从技术内涵上看，AIGC 建立在深度学习的长期演进之上。2017 年 Google 提出的 Transformer 架构"
      "奠定了现代大模型的基石；2018 年 OpenAI 发布 GPT、Google 发布 BERT，开启了预训练语言模型时代；"
      "2020 年 GPT-3 以 1750 亿参数展示了规模带来的涌现能力；2022 年 ChatGPT 与 Stable Diffusion 的"
      "相继问世，标志着 AIGC 从实验室走向大众。"),
    P("h2", "1.2  发展历程"),
    P("AIGC 的发展可大致划分为四个阶段。第一阶段（2014—2017）以生成对抗网络（GAN）为代表，"
      "Ian Goodfellow 在 2014 年提出的 GAN 开创了深度生成模型的先河。第二阶段（2017—2020）以 "
      "Transformer 与预训练为核心，GPT、BERT 等模型确立了“预训练 + 微调”的范式。"),
    P("第三阶段（2020—2022）是大规模语言模型的爆发期，GPT-3 展示了少样本学习能力，2022 年发布的"
      "ChatGPT 通过强化学习人类反馈（RLHF）实现了与人类意图的对齐，上线两个月月活用户突破一亿。"
      "同期，基于扩散模型的 Stable Diffusion、Midjourney 让高质量图像生成走向普及。第四阶段"
      "（2023 至今）以多模态融合与智能体（Agent）为核心，GPT-4、Gemini、Claude 等模型具备图文"
      "理解能力，2024 年 OpenAI 发布的 Sora 将文本到视频生成推向新高度。"),
    P("h2", "1.3  AIGC 与传统人工智能的区别"),
    P("传统人工智能多聚焦于判别式任务，如分类、检测、推荐，模型从输入到输出是一个从数据到标签的映射。"
      "AIGC 则是生成式的，模型学习数据分布并从中采样，创造此前不存在的内容。这一范式转变使得 AI "
      "从“理解世界”走向“创造世界”，应用边界从分析决策扩展到内容生产、代码编写、科学研究等创造性领域。"),
    P("理解 AIGC 的关键在于把握三要素：数据、算力与算法。海量高质量数据是训练的基础，以 GPU 为代表"
      "的算力提供了规模化的训练能力，而 Transformer、扩散模型等算法创新则决定了生成质量的上限。"
      "三者相互促进，共同驱动了 AIGC 产业的快速扩张。"),
])

# ---- Chapter 2 ----
ch("第二章  市场规模与增长", [
    P("h1", "第二章  市场规模与增长"),
    P("h2", "2.1  全球市场规模"),
    P("AIGC 已成为全球科技领域最具增长潜力的赛道之一。据多家研究机构估算，2023 年全球 AIGC 市场规模"
      "约为数百亿美元，并有望在未来十年保持高速增长。生成式 AI 正在重塑从内容创作到企业服务的多个"
      "万亿级市场，麦肯锡等咨询机构预测，生成式 AI 每年有望为全球经济创造数万亿美元的价值。"),
    P("h2", "2.2  中国市场规模"),
    P("中国 AIGC 市场起步略晚但增长迅猛。根据公开行业数据，2023 年中国 AIGC 产业规模约为 143 亿元，"
      "在企业服务、内容创作、智能硬件等需求驱动下，预计到 2030 年市场规模将向万亿级别迈进。"
      "资本市场对 AIGC 赛道保持高度关注，2023 年中国 AIGC 领域融资事件数百起，智谱 AI、月之暗面、"
      "MiniMax 等头部大模型公司估值快速攀升。"),
    P("h2", "2.3  产业链价值分布"),
    P("从价值链分布看，AIGC 产业呈现“中间厚、两端薄”的格局：基础层的算力与数据环节由少数厂商主导，"
      "价值集中度高；模型层是大模型公司的核心阵地，竞争最为激烈；应用层门类繁多但单点价值相对分散。"
      "下表给出各层的代表性企业与其核心产品。"),
    table([
        ["产业链层级", "核心环节", "代表性企业", "代表产品"],
        ["基础层", "算力（GPU）", "英伟达（NVIDIA）", "H100 / A100 GPU"],
        ["基础层", "云算力服务", "阿里云、腾讯云、华为云", "智算中心、弹性算力"],
        ["模型层", "通用大模型", "OpenAI、Anthropic、百度", "GPT-4、Claude、文心一言"],
        ["模型层", "行业大模型", "科大讯飞、商汤、智谱", "星火、日日新、GLM"],
        ["应用层", "内容生成", "字节跳动、Midjourney", "豆包、Midjourney"],
        ["应用层", "企业服务", "金山的 WPS AI、微软 Copilot", "办公助手"],
    ], col_widths=[2.6 * cm, 3.2 * cm, 4.4 * cm, 4.6 * cm]),
    Spacer(1, 6),
    P("可以看到，算力层的英伟达凭借其在 GPU 市场的主导地位，成为这一轮 AIGC 浪潮中确定性最高的受益者。"
      "模型层竞争激烈，国内外厂商纷纷推出自研大模型；应用层则呈现出百花齐放的格局，各类垂直场景应用"
      "不断涌现。"),
])

# ---- Chapter 3 ----
ch("第三章  核心技术演进", [
    P("h1", "第三章  核心技术演进"),
    P("h2", "3.1  Transformer 与注意力机制"),
    P("Transformer 是现代大模型的基石。2017 年，Google 的 Ashish Vaswani 等人在论文"
      "《Attention Is All You Need》中提出了完全基于自注意力机制的 Transformer 架构，摒弃了循环神经"
      "网络的序列依赖，实现了高度并行化的训练。自注意力机制让模型能够同时关注输入序列中的所有位置，"
      "捕捉长距离依赖关系。"),
    P("Transformer 的意义不仅在于机器翻译性能的提升，更在于其架构成为后续 GPT、BERT、ViT 乃至扩散模型"
      "的通用骨架。大规模并行训练能力使得模型参数从亿级跃升到千亿、万亿级，规模法则（Scaling Law）"
      "在实践中得到反复验证：模型能力随参数量、数据量、算力的增长而可预期地提升。"),
    P("h2", "3.2  预训练与大规模语言模型"),
    P("预训练范式是 AIGC 的另一关键创新。模型首先在海量无标注文本上进行自监督预训练，学习语言的通用"
      "表示，再通过微调或提示工程适配下游任务。GPT 系列采用 decoder-only 架构进行自回归生成，BERT 则"
      "采用 encoder-only 架构进行掩码语言建模。两者分别代表了生成式与判别式预训练的两条路线。"),
    P("2020 年发布的 GPT-3 拥有 1750 亿参数，展现出强大的少样本与零样本学习能力——只需在提示中给出"
      "少量示例，模型就能完成翻译、问答、写作等任务。这种“提示即接口”的能力使得大模型成为通用的"
      "任务引擎，奠定了 AIGC 应用的技术基础。"),
    P("h2", "3.3  强化学习人类反馈（RLHF）"),
    P("尽管 GPT-3 能力强大，但其输出常与人类期望存在偏差，可能生成不安全或不准确的内容。为解决这一"
      "问题，OpenAI 在 InstructGPT 中引入了强化学习人类反馈（RLHF，Reinforcement Learning from Human "
      "Feedback）技术：首先训练一个奖励模型来刻画人类偏好，再用近端策略优化（PPO）算法优化生成模型，"
      "使其输出更符合人类意图。"),
    P("RLHF 是 ChatGPT 取得突破性成功的关键。经过 RLHF 对齐的模型在有用性、诚实性、无害性上均有显著"
      "提升，回答更加自然、准确、可控。此后，Anthropic 提出了基于 AI 反馈的 RLAIF，Meta 发布了 Llama "
      "系列开源模型并推动 RLHF 方法的普及，对齐技术成为大模型研发的标配环节。"),
    P("h2", "3.4  扩散模型"),
    P("在图像生成领域，扩散模型（Diffusion Model）在 2022 年实现了革命性突破。扩散模型通过前向加噪"
      "与反向去噪两个过程学习数据分布，相比 GAN 具有训练稳定、生成多样性高的优势。2020 年提出的 DDPM"
      "奠定了现代扩散模型的基础，其后潜在扩散模型（Latent Diffusion）将扩散过程迁移到低维潜在空间，"
      "大幅降低了计算开销。"),
    P("2022 年发布的 Stable Diffusion 将高质量图像生成的门槛降到普通消费者可及的水平，Midjourney、"
      "DALL·E 2 等产品让“文生图”成为大众化的创作方式。扩散模型的思想也被迁移到视频生成，OpenAI 在 "
      "2024 年发布的 Sora 能够根据文本生成长达一分钟的高清视频，展现了世界模型的雏形。"),
    P("h2", "3.5  多模态融合"),
    P("单一模态的大模型逐渐演进为多模态大模型。GPT-4 能够同时理解图像与文本，回答关于图片内容的问题；"
      "Google 的 Gemini 从设计之初即为原生多模态；CLIP 模型通过对齐图像与文本的表示空间，为跨模态检索"
      "奠定了基础。多模态融合让模型更接近人类感知世界的方式，是通向通用人工智能的重要路径。"),
    P("h2", "3.6  检索增强生成与智能体"),
    P("大模型存在知识截止、幻觉与领域知识不足等问题。检索增强生成（RAG，Retrieval-Augmented Generation）"
      "通过在生成前从外部知识库检索相关文档并注入上下文，显著缓解了这些问题，是企业落地大模型的主流"
      "方案。RAG 通常结合向量数据库与混合检索（向量 + 全文）来提升召回质量。"),
    P("智能体（Agent）则是 AIGC 应用的更高形态。Agent 以大模型为大脑，结合工具调用、记忆与规划能力，"
      "能够自主分解任务、调用外部工具并完成多步骤的复杂工作。从 AutoGPT 到各类 Agent 框架，AI 正从"
      "“回答问题”走向“解决问题”，这一趋势被视为下一代人机交互的核心。"),
])

# ---- Chapter 4 ----
ch("第四章  产业链结构", [
    P("h1", "第四章  产业链结构"),
    P("h2", "4.1  基础层：算力与数据"),
    P("算力是 AIGC 产业的地基。大模型训练需要海量的矩阵运算，GPU 因其高度的并行计算能力成为首选。"
      "英伟达凭借 H100、A100 等数据中心 GPU 在 AI 算力市场占据主导地位，其 CUDA 生态构成了深厚的护城河。"
      "面对激增的算力需求，国内外云厂商纷纷建设智算中心，提供弹性的 AI 算力服务，阿里云、腾讯云、华为云"
      "等均推出了大模型训练专用的算力产品。"),
    P("数据是训练大模型的“燃料”。高质量的预训练语料决定了模型的能力上限。常见的语料来源包括网页"
      "（Common Crawl）、书籍、代码（GitHub）、百科（Wikipedia）等。随着公开语料逐渐耗尽，高质量、"
      "专业化的数据集日益稀缺，数据治理与合成数据成为新的研究热点。"),
    P("h2", "4.2  模型层：通用大模型与行业大模型"),
    P("模型层是 AIGC 产业的核心。通用大模型（Foundation Model）追求跨领域的通用智能，参数规模通常在"
      "百亿到万亿级别，代表性的国际模型有 OpenAI 的 GPT-4、Anthropic 的 Claude、Google 的 Gemini、"
      "Meta 的 Llama；国内则有百度的文心一言、阿里的通义千问、腾讯的混元、字节跳动的豆包、智谱的 GLM、"
      "月之暗面的 Kimi 以及 MiniMax 等多款模型同台竞争。"),
    P("行业大模型则针对医疗、金融、法律、教育等垂直领域，在通用模型基础上注入专业知识进行微调，"
      "在特定任务上表现更优。科大讯飞的星火模型深耕教育，商汤的日日新面向企业服务，各类行业模型"
      "共同构成了多层次的大模型供给体系。"),
    P("h2", "4.3  应用层：场景化落地"),
    P("应用层是 AIGC 价值变现的最终环节。围绕内容创作、办公提效、智能客服、代码开发等场景，涌现出"
      "大量应用产品。微软将 GPT-4 集成到 Copilot，重塑了 Office 与开发工具的体验；金山办公的 WPS AI "
      "为中文用户提供写作、排版与数据分析辅助；字节跳动的豆包则面向 C 端用户提供聊天与创作服务。"),
    P("应用层的繁荣依赖于模型层能力的持续提升与成本下降。随着开源模型（如 Llama、GLM）的成熟与推理"
      "成本的降低，越来越多中小开发者能够基于大模型构建垂直应用，AIGC 的应用生态呈现爆发式增长。"),
])

# ---- Chapter 5 ----
ch("第五章  典型企业分析", [
    P("h1", "第五章  典型企业分析"),
    P("h2", "5.1  国际领先企业"),
    P("OpenAI 是这一轮 AIGC 浪潮的引领者。公司由 Sam Altman 领导，先后发布了 GPT-3、ChatGPT、GPT-4 "
      "等里程碑产品，并于 2024 年发布视频生成模型 Sora。OpenAI 的成功推动了生成式 AI 的产业化。"
      "Anthropic 由前 OpenAI 成员 Dario Amodei 创立，其 Claude 系列模型以安全性与长上下文能力著称。"),
    P("Google 在 AI 领域布局深远，从提出 Transformer 架构到推出 Gemini 多模态模型，一直是基础研究的"
      "重镇。Meta 则通过开源 Llama 系列模型推动了 AIGC 的民主化，Llama 已成为全球使用最广泛的开源大"
      "模型之一。英伟达在 CEO 黄仁勋的带领下，凭借 GPU 算力垄断成为 AIGC 时代最大的赢家之一。"),
    P("h2", "5.2  国内主要企业"),
    P("中国 AIGC 企业形成了互联网巨头与创业新锐并存的格局。百度推出文心一言与文心大模型系列，在搜索"
      "与云服务中深度集成；阿里云发布通义千问，并将大模型能力开放给企业客户；腾讯推出混元大模型，"
      "服务于社交、游戏与广告业务；字节跳动的豆包依托其内容生态快速获取海量用户。"),
    P("在大模型创业领域，智谱 AI 源自清华大学，其 GLM 系列模型与开源的 ChatGLM 在学术界与产业界均有"
      "广泛影响；月之暗面推出的 Kimi 以超长上下文能力著称；MiniMax 专注于多模态与角色扮演类应用；"
      "此外商汤、科大讯飞、百川智能等企业也各具特色。下表汇总了主要企业及其代表模型。"),
    table([
        ["企业", "代表模型 / 产品", "技术特色"],
        ["OpenAI", "GPT-4、ChatGPT、Sora", "通用能力强，多模态领先"],
        ["Anthropic", "Claude 3", "安全对齐，长上下文"],
        ["Google", "Gemini", "原生多模态"],
        ["Meta", "Llama 3", "开源生态，广泛使用"],
        ["百度", "文心一言", "搜索 + 知识增强"],
        ["阿里云", "通义千问", "企业服务，开源系列"],
        ["腾讯", "混元", "社交 / 游戏 / 广告"],
        ["字节跳动", "豆包", "C 端内容生态"],
        ["智谱 AI", "GLM、ChatGLM", "开源，学术渊源"],
        ["月之暗面", "Kimi", "超长上下文"],
        ["MiniMax", "abab 系列", "多模态，角色扮演"],
        ["英伟达", "H100 / A100 GPU", "AI 算力主导"],
    ], col_widths=[3.0 * cm, 4.6 * cm, 7.2 * cm]),
    Spacer(1, 6),
    P("总体而言，国际厂商在通用大模型的基础能力上仍具领先优势，而国内企业在中文理解、行业落地与成本"
      "控制上具有本地化优势。随着开源模型的快速迭代与算力的逐步国产化，国内外技术差距正不断缩小。"),
])

# ---- Chapter 6 ----
ch("第六章  应用场景", [
    P("h1", "第六章  应用场景"),
    P("h2", "6.1  内容创作"),
    P("内容创作是 AIGC 落地最快、最广泛的场景。在文本创作领域，大模型能够辅助撰写文案、新闻、小说与"
      "营销内容，显著提升创作者的生产效率。在图像领域，Stable Diffusion、Midjourney 等工具让设计师"
      "能够通过文字描述快速生成高质量插画。视频领域，Sora、Runway 等产品正在降低视频制作的门槛。"),
    P("h2", "6.2  智能客服"),
    P("传统客服系统依赖关键词匹配与固定话术，难以应对复杂问题。基于大模型的智能客服能够理解用户意图，"
      "结合企业知识库给出准确的个性化回答。借助检索增强生成（RAG）技术，客服机器人可以在私有知识库中"
      "检索答案，既保证了回复的专业性，又避免了模型幻觉。"),
    P("h2", "6.3  代码生成"),
    P("代码生成是 AIGC 极具价值的 B 端场景。GitHub Copilot、Cursor 等工具能够根据自然语言注释或上下文"
      "自动补全代码、生成函数、编写测试，大幅提升了程序员的开发效率。研究表明，使用 AI 编程助手的开发者"
      "完成任务的速度显著加快。代码能力也成为评估大模型的重要维度。"),
    P("h2", "6.4  教育"),
    P("在教育领域，AIGC 可用于个性化辅导、作业批改、内容生成与语言学习。大模型能够根据学生的学习进度"
      "提供定制化的讲解与练习，扮演“一对一”智能导师的角色。然而，教育场景对内容的准确性要求极高，"
      "模型幻觉可能误导学生，因此在教育应用中，知识库与事实校验机制尤为关键。"),
    P("h2", "6.5  医疗与金融"),
    P("在医疗领域，AIGC 辅助医生进行病历总结、文献检索与初步诊断建议，但考虑到医疗安全，目前多用于"
      "辅助而非决策。在金融领域，大模型用于研报生成、风险评估、智能投顾与反欺诈，提升分析与决策效率。"
      "这两个领域都对专业性与合规性有严格要求，行业大模型与严格的审核流程不可或缺。"),
    P("h2", "6.6  办公提效"),
    P("办公场景是 AIGC 最贴近普通白领的应用。微软 Copilot 将 GPT-4 接入 Word、Excel、PowerPoint，"
      "实现文档起草、数据分析与演示制作；金山办公的 WPS AI 为中文用户提供类似的智能办公能力。"
      "通过将大模型嵌入日常工作流，办公效率得到显著提升，AIGC 正成为新一代生产力工具的核心。"),
])

# ---- Chapter 7 ----
ch("第七章  挑战与治理", [
    P("h1", "第七章  挑战与治理"),
    P("h2", "7.1  伦理与安全"),
    P("AIGC 的快速发展也带来一系列伦理与安全挑战。大模型可能生成带有偏见、歧视或有害的内容，"
      "对齐技术（如 RLHF）正是为了缓解这些问题。然而，完全消除偏见与风险仍十分困难，模型的安全"
      "对齐是一个持续迭代的过程。此外，强大的生成能力也引发了关于人工智能潜在失控风险的深层讨论。"),
    P("h2", "7.2  版权争议"),
    P("AIGC 的训练依赖海量数据，其中包含大量受版权保护的作品，引发了关于训练数据合法性的争议。"
      "生成内容本身的著作权归属也尚无定论。多起针对 AIGC 企业的版权诉讼表明，如何在技术创新与版权"
      "保护之间取得平衡，是行业必须面对的课题。"),
    P("h2", "7.3  深度伪造与虚假信息"),
    P("高质量的图像、视频与语音生成能力，使得伪造逼真的虚假内容变得容易，带来深度伪造（Deepfake）"
      "的滥用风险。虚假信息可能被用于诈骗、操纵舆论或损害个人名誉。水印、内容溯源与检测技术成为"
      "应对深度伪造的重要手段，各国正在推动相关技术标准与法律法规的制定。"),
    P("h2", "7.4  监管合规"),
    P("各国正加紧构建 AIGC 的监管框架。欧盟的《人工智能法案》（AI Act）按照风险等级对 AI 应用进行"
      "分级监管；中国于 2023 年 8 月起施行《生成式人工智能服务管理暂行办法》，要求提供者对训练数据"
      "合规性、生成内容真实性与用户个人信息保护承担责任，并对算法备案与安全评估提出要求。合规已成为"
      "AIGC 企业不可回避的义务。"),
    P("面对上述挑战，负责任的 AIGC 发展需要技术、产业与监管三方协同：技术上持续改进对齐与检测能力，"
      "产业上建立自律规范，监管上完善法律法规。只有在创新与治理之间取得平衡，AIGC 才能实现可持续的"
      "健康发展。"),
])

# ---- Chapter 8 ----
ch("第八章  趋势展望", [
    P("h1", "第八章  趋势展望"),
    P("h2", "8.1  多模态深度融合"),
    P("未来大模型将进一步打通文本、图像、音频、视频等多种模态，实现真正的统一理解与生成。原生多模态"
      "架构将取代简单的模态拼接，模型能够像人类一样综合感知与表达。视频生成与具身智能的结合，有望催生"
      "具备世界模型能力的新一代 AI。"),
    P("h2", "8.2  智能体（Agent）的兴起"),
    P("智能体被视为 AIGC 应用的下一个范式。未来的 Agent 将具备更强的规划、记忆与工具调用能力，能够自主"
      "完成复杂的多步骤任务，从“对话助手”演变为“数字员工”。多 Agent 协作系统可能在软件开发、科学研究、"
      "企业运营等领域带来生产力的跨越式提升。"),
    P("h2", "8.3  端侧部署与算力下沉"),
    P("随着模型轻量化（如量化、蒸馏、LoRA 微调）与端侧芯片的发展，大模型正从云端走向手机、PC 与边缘"
      "设备。端侧部署能够降低延迟、保护隐私并减少对云算力的依赖，使 AIGC 能力无处不在。端云协同将成为"
      "主流部署模式。"),
    P("h2", "8.4  行业垂直化"),
    P("通用大模型之外，面向特定行业的垂直大模型将持续发展。医疗、法律、金融、制造等领域的专业模型，"
      "通过注入行业知识与合规约束，在专业场景中提供更准确、更可靠的服务。行业模型的繁荣将推动 AIGC "
      "在实体经济中的深度落地。"),
    P("h2", "8.5  开源生态"),
    P("开源是推动 AIGC 普及的重要力量。Llama、ChatGLM、通义千问开源版、DeepSeek 等开源模型让中小开发者和"
      "研究机构能够低成本地使用与改进大模型，加速了技术创新与应用创新。繁荣的开源生态与活跃的研究社区，"
      "将持续降低 AIGC 的门槛，让生成式 AI 惠及更广泛的群体。"),
    P("h2", "8.6  结语"),
    P("AIGC 正处于快速演进之中。从 Transformer 架构到多模态大模型，从 ChatGPT 到智能体，技术迭代的速度"
      "前所未有。在算力、数据与算法的协同驱动下，生成式 AI 有望重塑内容生产、知识工作与人机交互的方式。"
      "面对机遇与挑战并存的前景，唯有坚持技术创新与负责任的发展并重，AIGC 才能真正成为推动社会进步的"
      "积极力量。"),
])


# ---- Appendix ----
ch("附录  术语表与发展大事记", [
    P("h1", "附录  术语表与发展大事记"),
    P("本附录梳理 AIGC 领域的核心术语与关键发展节点，便于读者快速回顾全文要点。"),
    P("h2", "附录 A  核心术语表"),
    table([
        ["术语", "英文", "简要释义"],
        ["注意力机制", "Attention", "让模型动态关注输入中不同位置信息的方法"],
        ["Transformer", "Transformer", "基于自注意力的通用神经网络架构（Google, 2017）"],
        ["大语言模型", "LLM", "Large Language Model，大规模预训练语言模型"],
        ["预训练", "Pre-training", "在海量无标注数据上先训练通用表示"],
        ["微调", "Fine-tuning", "在预训练模型上针对特定任务继续训练"],
        ["提示工程", "Prompt Engineering", "通过设计输入提示引导模型输出"],
        ["人类反馈强化学习", "RLHF", "Reinforcement Learning from Human Feedback"],
        ["基于 AI 反馈的强化学习", "RLAIF", "用 AI 反馈代替人类反馈的对齐方法"],
        ["低秩适配", "LoRA", "Low-Rank Adaptation，高效参数微调方法"],
        ["检索增强生成", "RAG", "Retrieval-Augmented Generation，检索后生成"],
        ["智能体", "Agent", "能规划、调用工具、完成多步任务的 AI"],
        ["扩散模型", "Diffusion Model", "通过去噪过程学习数据分布的生成模型"],
        ["生成对抗网络", "GAN", "Generative Adversarial Network（Goodfellow, 2014）"],
        ["对比语言图像预训练", "CLIP", "对齐图像与文本表示的多模态模型（OpenAI, 2021）"],
        ["多模态", "Multimodal", "同时处理文本、图像、音频等多种模态"],
        ["混合专家", "MoE", "Mixture of Experts，稀疏激活的模型结构"],
        ["嵌入", "Embedding", "将离散对象映射为稠密向量表示"],
        ["向量数据库", "Vector Database", "存储与检索高维向量的数据库"],
        ["幻觉", "Hallucination", "模型生成看似合理但与事实不符的内容"],
        ["对齐", "Alignment", "使模型行为符合人类意图与价值观"],
    ], col_widths=[3.8 * cm, 3.8 * cm, 7.2 * cm]),
    Spacer(1, 8),
    P("h2", "附录 B  AIGC 发展大事记"),
    table([
        ["年份", "标志性事件"],
        ["2014", "Ian Goodfellow 提出生成对抗网络（GAN），开创深度生成模型方向"],
        ["2017", "Google 发表《Attention Is All You Need》，提出 Transformer 架构"],
        ["2018", "OpenAI 发布 GPT、Google 发布 BERT，预训练语言模型范式确立"],
        ["2019", "GPT-2 发布，展现零样本生成能力，引发对 AI 滥用的讨论"],
        ["2020", "GPT-3（1750 亿参数）发布；DDPM 奠定现代扩散模型基础"],
        ["2021", "OpenAI 发布 CLIP 与 DALL·E，推动多模态与文生图发展"],
        ["2022", "ChatGPT 上线，两个月月活破亿；Stable Diffusion 开源文生图"],
        ["2023", "GPT-4、Claude、Gemini 发布；中国《生成式人工智能服务管理暂行办法》施行"],
        ["2023", "Meta 开源 Llama 2；国内智谱、月之暗面、MiniMax 等大模型密集发布"],
        ["2024", "OpenAI 发布文生视频模型 Sora；Meta 开源 Llama 3"],
        ["2024", "Agent 框架与端侧大模型兴起，AI 应用加速走向生产落地"],
    ], col_widths=[2.2 * cm, 12.6 * cm]),
    Spacer(1, 8),
    P("从 2014 年的 GAN 到 2024 年的 Agent 与端侧大模型，AIGC 用十年时间完成了从学术研究到产业落地的"
      "跨越。这份大事记既是一条技术演进的时间线，也是理解当下 AIGC 产业格局的线索。"),
])


# ===========================================================================
# Render
# ===========================================================================
def build():
    doc = BaseDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=TITLE, author="Arrow Lake Cookbook",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_on_page)])

    story = []
    # ---- Cover ----
    story.append(Spacer(1, 5 * cm))
    story.append(Paragraph(TITLE, ST["cover_title"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(SUBTITLE, ST["cover_sub"]))
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(
        "本文档为 <b>Arrow Lake</b> 项目的示例数据，内容由公开资料整理合成，"
        "仅用于技术演示（文档摄入、切块、向量检索、全文检索、RAG、知识图谱构建等），"
        "并非真实研究报告，不代表任何机构的观点与立场。", ST["cover_note"]))
    story.append(PageBreak())

    # ---- Table of contents ----
    story.append(Paragraph("目  录", ST["h1"]))
    for idx, (title, _) in enumerate(CHAPTERS, 1):
        story.append(Paragraph(title, ST["toc"]))
    story.append(PageBreak())

    # ---- Chapters ----
    for _, blocks in CHAPTERS:
        story.extend(blocks)
        story.append(PageBreak())

    doc.build(story)
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    build()
