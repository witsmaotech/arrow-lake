#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic Chinese AIGC industry research report PDF.

Sample data for the Arrow Lake cookbook. Content is compiled from publicly
available AIGC industry information and is for technical demonstration only.
Usage:  python generate_report.py   ->   aigc_industry_report.pdf
Requires: reportlab
NOTE: emphasis quotes inside Chinese text use 「」 (never ASCII "), so they
never clash with Python string delimiters.
"""
import os
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

CJK = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(CJK))
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigc_industry_report.pdf")
TITLE = "2024 中国 AIGC 产业发展研究报告"
SUBTITLE = "技术演进、产业链与应用场景深度分析"


def _styles():
    base = getSampleStyleSheet()
    s = {}
    s["cover_title"] = ParagraphStyle("cover_title", parent=base["Title"], fontName=CJK, fontSize=26, leading=36, alignment=TA_CENTER, spaceAfter=18)
    s["cover_sub"] = ParagraphStyle("cover_sub", parent=base["Normal"], fontName=CJK, fontSize=15, leading=22, alignment=TA_CENTER, textColor=colors.HexColor("#444444"))
    s["cover_note"] = ParagraphStyle("cover_note", parent=base["Normal"], fontName=CJK, fontSize=10.5, leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#888888"), leftIndent=2 * cm, rightIndent=2 * cm)
    s["toc"] = ParagraphStyle("toc", parent=base["Normal"], fontName=CJK, fontSize=12.5, leading=24)
    s["h1"] = ParagraphStyle("h1", parent=base["Heading1"], fontName=CJK, fontSize=20, leading=30, spaceBefore=18, spaceAfter=12, textColor=colors.HexColor("#1a3a6b"))
    s["h2"] = ParagraphStyle("h2", parent=base["Heading2"], fontName=CJK, fontSize=14.5, leading=22, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#2a5298"))
    s["body"] = ParagraphStyle("body", parent=base["BodyText"], fontName=CJK, fontSize=10.5, leading=19, alignment=TA_JUSTIFY, firstLineIndent=21, spaceAfter=7)
    s["bullet"] = ParagraphStyle("bullet", parent=base["BodyText"], fontName=CJK, fontSize=10.5, leading=18, leftIndent=18, bulletIndent=6, spaceAfter=4)
    s["cell"] = ParagraphStyle("cell", parent=base["BodyText"], fontName=CJK, fontSize=9.5, leading=14)
    return s


ST = _styles()


def P(a, b=None):
    if b is None:
        return Paragraph(a, ST["body"])
    return Paragraph(b, ST[a])


def bullets(items):
    return [Paragraph("• " + it, ST["bullet"]) for it in items]


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


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(CJK, 8.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(2 * cm, A4[1] - 1.2 * cm, "AIGC 产业发展研究报告（示例文档 · 仅供技术演示）")
    canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 1.2 * cm, "Arrow Lake Cookbook")
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, "— %d —" % doc.page)
    canvas.restoreState()


CHAPTERS = []


def ch(title, blocks):
    CHAPTERS.append((title, blocks))


# ---- Chapter 1 ----
ch("第一章  行业概述与背景", [
    P("h1", "第一章  行业概述与背景"),
    P("h2", "1.1  AIGC 的定义与内涵"),
    P("AIGC（Artificial Intelligence Generated Content，人工智能生成内容）是指利用人工智能技术，尤其是以大语言模型、扩散模型为代表的大规模预训练模型，"
      "自动生成文本、图像、音频、视频、代码乃至三维资产等内容的新型生产方式。AIGC 区别于传统的 PGC（专业生成内容）与 UGC（用户生成内容），"
      "其核心特征是由模型而非人直接创作内容，从而显著降低内容生产的边际成本，使个性化、规模化内容供给成为可能。"),
    P("从内容生产范式的演进看，互联网时代经历了从 PGC 到 UGC 再到 AIGC 的三阶段跃迁。PGC 时代由专业创作者主导，产量受限于人力；"
      "UGC 时代以博客、视频平台为代表，降低了创作门槛但仍依赖人的创意与劳动；AIGC 时代则将创作主体从人扩展到模型，内容供给的瓶颈"
      "从生产力转向算力与算法。这一跃迁的深层意义在于，内容首次成为一种可规模化、低成本生产的资源。"),
    P("从技术内涵上看，AIGC 建立在深度学习的长期演进之上。2017 年 Google 提出的 Transformer 架构奠定了现代大模型的基石；2018 年 OpenAI 发布 GPT、"
      "Google 发布 BERT，开启了预训练语言模型时代；2020 年 GPT-3 以 1750 亿参数展示了规模带来的涌现能力；2022 年 ChatGPT 与 Stable Diffusion 的"
      "相继问世，标志着 AIGC 从实验室走向大众。"),
    P("h2", "1.2  发展历程"),
    P("AIGC 的发展可大致划分为四个阶段。第一阶段（2014—2017）以生成对抗网络（GAN）为代表，Ian Goodfellow 在 2014 年提出的 GAN 开创了深度生成模型的"
      "先河，此后 StyleGAN 等变体在图像生成上取得突破。第二阶段（2017—2020）以 Transformer 与预训练为核心，GPT、BERT 等模型确立了「预训练 + 微调」的范式，"
      "模型能力随规模持续提升。"),
    P("第三阶段（2020—2022）是大规模语言模型的爆发期。GPT-3 展示了少样本与零样本学习能力，证明了规模法则（Scaling Law）。2022 年 11 月发布的 ChatGPT "
      "通过强化学习人类反馈（RLHF）实现了与人类意图的对齐，上线两个月月活用户突破一亿，成为史上增长最快的消费级应用。同期，基于扩散模型的 Stable Diffusion、"
      "Midjourney 让高质量图像生成走向普及。第四阶段（2023 至今）以多模态融合与智能体（Agent）为核心，GPT-4、Gemini、Claude 等模型具备图文理解能力，"
      "2024 年 OpenAI 发布的 Sora 将文本到视频生成推向新高度。下表梳理了关键里程碑。"),
    table([
        ["年份", "里程碑事件", "意义"],
        ["2014", "Ian Goodfellow 提出 GAN", "开创深度生成模型方向"],
        ["2017", "Google 提出 Transformer", "奠定大模型架构基石"],
        ["2018", "GPT-1、BERT 发布", "预训练范式确立"],
        ["2020", "GPT-3（1750 亿参数）", "验证规模法则与少样本学习"],
        ["2021", "CLIP、DALL·E 发布", "推动多模态与文生图"],
        ["2022", "ChatGPT、Stable Diffusion", "AIGC 走向大众"],
        ["2023", "GPT-4、Llama 2、Gemini", "多模态与开源爆发"],
        ["2024", "Sora、Llama 3、Claude 3", "视频生成与 Agent 兴起"],
    ], col_widths=[2.0 * cm, 6.5 * cm, 7.0 * cm]),
    Spacer(1, 6),
    P("h2", "1.3  AIGC 与传统人工智能的区别"),
    P("传统人工智能多聚焦于判别式任务，如分类、检测、推荐、预测，模型从输入到输出是一个从数据到标签或决策的映射。AIGC 则是生成式的，模型学习数据的"
      "内在分布并从中采样，创造此前不存在的内容。这一范式转变使得 AI 从「理解世界」走向「创造世界」，应用边界从分析决策扩展到内容生产、代码编写、"
      "科学研究等创造性领域。"),
    table([
        ["维度", "判别式 AI（传统）", "生成式 AI（AIGC）"],
        ["目标", "分类 / 预测 / 决策", "生成新内容"],
        ["输出", "标签、概率、分数", "文本、图像、音频、代码"],
        ["典型任务", "垃圾邮件识别、推荐", "写作、绘图、对话、编程"],
        ["代表模型", "ResNet、BERT（判别）", "GPT、Stable Diffusion"],
        ["价值定位", "提升决策效率", "扩展创造力与生产力"],
    ], col_widths=[3.0 * cm, 5.5 * cm, 7.0 * cm]),
    Spacer(1, 6),
    P("h2", "1.4  三要素：数据、算力与算法"),
    P("理解 AIGC 产业的关键在于把握驱动其发展的三要素：数据、算力与算法。海量高质量数据是训练的基础，公开网页、书籍、代码、多模态语料构成了预训练的「燃料」；"
      "以 GPU 为代表的算力提供了规模化训练能力，是模型规模的物理上限；而 Transformer、扩散模型、RLHF 等算法创新则决定了生成质量与效率的上限。"
      "三者相互促进——更多数据需要更大算力，更大算力支撑更强算法，更强算法又催生更多应用与数据。"),
    P("然而，三要素也带来结构性挑战：高质量数据的逐渐枯竭使合成数据、专业语料成为新焦点；算力的高昂成本与供给集中度催生了算力国产化与推理优化需求；"
      "算法层面的同质化竞争则迫使企业走向差异化（如长上下文、多模态、垂直领域）。这些挑战将在后续章节深入讨论。"),
    P("h2", "1.5  AIGC 的能力分类"),
    P("按生成模态，AIGC 能力可分为若干类别。文本生成是大语言模型的核心能力，涵盖对话、写作、摘要、翻译、代码生成等；图像生成以扩散模型为主，包括文生图、"
      "图像编辑、风格迁移；音频生成涉及语音合成（TTS）、音乐生成；视频生成是当前前沿，Sora、Runway 等能根据文本生成连贯视频；此外还有 3D 资产生成、"
      "代码生成等垂直方向。不同模态的成熟度差异显著：文本与图像已大规模商用，视频与 3D 仍在快速迭代。"),
])

# ---- Chapter 2 ----
ch("第二章  市场规模与增长", [
    P("h1", "第二章  市场规模与增长"),
    P("h2", "2.1  全球市场规模"),
    P("AIGC 已成为全球科技领域最具增长潜力的赛道之一。据多家研究机构估算，2023 年全球生成式 AI 市场规模约为数百亿美元，并有望在未来十年保持年均 30%—50% 的"
      "高速增长。麦肯锡等咨询机构预测，生成式 AI 每年有望为全球经济创造数万亿美元的价值，显著提升知识工作者的生产效率。"),
    P("从区域分布看，美国凭借 OpenAI、Anthropic、Google、Meta 等头部企业，以及英伟达主导的算力优势，在全球 AIGC 竞争中处于领先地位。中国依托庞大市场、"
      "丰富应用场景与政策支持快速追赶，欧洲则在开源研究与合规监管上发力。全球资本持续涌入，2023 年生成式 AI 领域融资总额创下历史新高。"),
    P("h2", "2.2  中国市场规模"),
    P("中国 AIGC 市场起步略晚但增长迅猛。根据公开行业数据，2023 年中国 AIGC 产业规模约为 143 亿元，在企业服务、内容创作、智能硬件等需求驱动下，"
      "预计到 2025 年将突破 700 亿元，2030 年向万亿级别迈进。下表给出近年来中国 AIGC 产业规模的估算与增长趋势。"),
    table([
        ["年份", "产业规模（亿元）", "同比增速", "主要驱动力"],
        ["2021", "约 50", "—", "大模型研究起步"],
        ["2022", "约 85", "约 70%", "ChatGPT 引发关注"],
        ["2023", "约 143", "约 68%", "百模大战、应用落地"],
        ["2024", "约 250（估）", "约 75%", "多模态、Agent、企业级"],
        ["2025E", "约 700（估）", "约 180%", "B 端规模化采购"],
    ], col_widths=[2.0 * cm, 3.5 * cm, 2.8 * cm, 7.2 * cm]),
    Spacer(1, 6),
    P("h2", "2.3  细分市场结构"),
    P("从细分赛道看，AIGC 市场可按生成模态与应用场景划分。文本生成占据最大份额，主要来自对话助手、营销文案、客服等场景；图像与视频生成增速最快，"
      "受益于内容创作与广告行业的需求；代码生成在企业研发提效中渗透率快速提升。下表给出主要细分赛道的市场结构估算。"),
    table([
        ["细分赛道", "市场份额", "成熟度", "典型场景"],
        ["文本生成", "约 45%", "成熟", "对话、写作、客服、翻译"],
        ["图像生成", "约 20%", "成长期", "设计、广告、电商素材"],
        ["代码生成", "约 12%", "成长期", "Copilot、研发提效"],
        ["音频生成", "约 8%", "成长期", "语音合成、音乐"],
        ["视频生成", "约 10%", "早期", "短视频、影视、广告"],
        ["其他（3D 等）", "约 5%", "萌芽", "游戏、元宇宙"],
    ], col_widths=[3.2 * cm, 2.6 * cm, 2.4 * cm, 7.3 * cm]),
    Spacer(1, 6),
    P("h2", "2.4  投融资分析"),
    P("资本市场对 AIGC 赛道保持高度关注。2023 年中国 AIGC 领域融资事件数百起，头部大模型公司估值快速攀升。智谱 AI、月之暗面、MiniMax、百川智能、"
      "阶跃星辰等成为资本追捧的明星企业，多轮融资后估值跻身百亿级别。下表汇总了部分代表性企业的融资情况（基于公开信息）。"),
    table([
        ["企业", "代表产品", "估值量级（公开）", "主要投资方"],
        ["智谱 AI", "GLM、ChatGLM", "百亿级", "国家队、互联网巨头"],
        ["月之暗面", "Kimi", "百亿级", "互联网巨头、美元基金"],
        ["MiniMax", "海螺 AI、abab", "百亿级", "互联网巨头、VC"],
        ["百川智能", "Baichuan", "数十亿级", "互联网巨头、国资"],
        ["阶跃星辰", "Step 系列", "数十亿级", "美元基金、互联网"],
        ["OpenAI", "GPT、Sora", "千亿美元级", "微软等"],
        ["Anthropic", "Claude", "千亿亿美元级", "亚马逊、Google"],
    ], col_widths=[2.8 * cm, 3.0 * cm, 3.4 * cm, 6.3 * cm]),
    Spacer(1, 6),
    P("从资本格局看，AIGC 投融资呈现「头部集中、应用分散」的特征：资金向具备底层模型能力的头部企业集中，而应用层则百花齐放，各类垂直场景创业公司"
      "不断涌现。与此同时，国资与产业资本的介入，使 AIGC 投融资兼具市场活力与战略属性。"),
])

# ---- Chapter 3 ----
ch("第三章  核心技术演进", [
    P("h1", "第三章  核心技术演进"),
    P("h2", "3.1  Transformer 与注意力机制"),
    P("Transformer 是现代大模型的基石。2017 年，Google 的 Ashish Vaswani 等人在论文《Attention Is All You Need》中提出了完全基于自注意力机制的 Transformer 架构，"
      "摒弃了循环神经网络的序列依赖，实现了高度并行化的训练。自注意力机制让模型能够同时关注输入序列中的所有位置，捕捉长距离依赖关系。"),
    P("Transformer 的意义不仅在于机器翻译性能的提升，更在于其架构成为后续 GPT、BERT、ViT 乃至扩散模型的通用骨架。大规模并行训练能力使得模型参数从亿级"
      "跃升到千亿、万亿级，规模法则（Scaling Law）在实践中得到反复验证：模型能力随参数量、数据量、算力的增长而可预期地提升。可以说，没有 Transformer，"
      "就没有今天的 AIGC 浪潮。"),
    P("h2", "3.2  预训练与大规模语言模型"),
    P("预训练范式是 AIGC 的另一关键创新。模型首先在海量无标注文本上进行自监督预训练，学习语言的通用表示，再通过微调或提示工程适配下游任务。GPT 系列采用 "
      "decoder-only 架构进行自回归生成，BERT 则采用 encoder-only 架构进行掩码语言建模，两者分别代表了生成式与判别式预训练的两条路线。"),
    P("2020 年发布的 GPT-3 拥有 1750 亿参数，展现出强大的少样本与零样本学习能力——只需在提示中给出少量示例，模型就能完成翻译、问答、写作等任务。"
      "这种「提示即接口」的能力使得大模型成为通用的任务引擎，奠定了 AIGC 应用的技术基础。此后，Chinchilla、Llama 等模型进一步优化了训练效率，推动开源大模型的普及。"),
    P("h2", "3.3  强化学习人类反馈（RLHF）"),
    P("尽管 GPT-3 能力强大，但其输出常与人类期望存在偏差，可能生成不安全或不准确的内容。为解决这一问题，OpenAI 在 InstructGPT 中引入了强化学习人类反馈"
      "（RLHF，Reinforcement Learning from Human Feedback）技术：首先训练一个奖励模型来刻画人类偏好，再用近端策略优化（PPO）算法优化生成模型，使其输出更符合人类意图。"),
    P("RLHF 是 ChatGPT 取得突破性成功的关键。经过 RLHF 对齐的模型在有用性、诚实性、无害性上均有显著提升，回答更加自然、准确、可控。此后，Anthropic 提出了"
      "基于 AI 反馈的 RLAIF，Meta 发布了 Llama 系列开源模型并推动 RLHF 方法的普及，DPO（Direct Preference Optimization）等新方法降低了 RLHF 的工程复杂度，"
      "对齐技术成为大模型研发的标配环节。"),
    P("h2", "3.4  扩散模型"),
    P("在图像生成领域，扩散模型（Diffusion Model）在 2022 年实现了革命性突破。扩散模型通过前向加噪与反向去噪两个过程学习数据分布，相比 GAN 具有训练稳定、"
      "生成多样性高的优势。2020 年提出的 DDPM 奠定了现代扩散模型的基础，其后潜在扩散模型（Latent Diffusion）将扩散过程迁移到低维潜在空间，大幅降低了计算开销。"),
    P("2022 年发布的 Stable Diffusion 将高质量图像生成的门槛降到普通消费者可及的水平，Midjourney、DALL·E 等产品让「文生图」成为大众化的创作方式。"
      "扩散模型的思想也被迁移到视频生成，OpenAI 在 2024 年发布的 Sora 能够根据文本生成长达一分钟的高清视频，展现了世界模型的雏形。"),
    P("h2", "3.5  多模态融合"),
    P("单一模态的大模型逐渐演进为多模态大模型。GPT-4 能够同时理解图像与文本，回答关于图片内容的问题；Google 的 Gemini 从设计之初即为原生多模态；"
      "CLIP 模型通过对齐图像与文本的表示空间，为跨模态检索奠定了基础。多模态融合让模型更接近人类感知世界的方式，是通向通用人工智能的重要路径。"),
    P("h2", "3.6  检索增强生成与智能体"),
    P("大模型存在知识截止、幻觉与领域知识不足等问题。检索增强生成（RAG，Retrieval-Augmented Generation）通过在生成前从外部知识库检索相关文档并注入上下文，"
      "显著缓解了这些问题，是企业落地大模型的主流方案。RAG 通常结合向量数据库与混合检索（向量 + 全文）来提升召回质量，再用重排序模型精排。"),
    P("智能体（Agent）则是 AIGC 应用的更高形态。Agent 以大模型为大脑，结合工具调用、记忆与规划能力，能够自主分解任务、调用外部工具并完成多步骤的复杂工作。"
      "从 AutoGPT 到各类 Agent 框架，AI 正从「回答问题」走向「解决问题」，这一趋势被视为下一代人机交互的核心。下表对比了主流大模型的关键参数。"),
    table([
        ["模型", "机构", "主要模态", "特点"],
        ["GPT-4 / GPT-4o", "OpenAI", "文本 + 图像 + 语音", "综合能力强，多模态领先"],
        ["Claude 3", "Anthropic", "文本 + 图像", "长上下文，安全对齐"],
        ["Gemini", "Google", "文本 + 图像 + 视频", "原生多模态"],
        ["Llama 3", "Meta", "文本", "开源，广泛使用"],
        ["文心一言", "百度", "文本 + 图像", "中文优化，知识增强"],
        ["通义千问", "阿里", "文本 + 图像", "开源系列，企业服务"],
        ["GLM", "智谱 AI", "文本 + 图像", "开源，中英双语"],
        ["Kimi", "月之暗面", "文本", "超长上下文"],
    ], col_widths=[3.2 * cm, 2.8 * cm, 3.8 * cm, 5.7 * cm]),
    Spacer(1, 6),
])

# ---- Chapter 4 ----
ch("第四章  产业链结构", [
    P("h1", "第四章  产业链结构"),
    P("h2", "4.1  基础层：算力与数据"),
    P("算力是 AIGC 产业的地基。大模型训练需要海量的矩阵运算，GPU 因其高度的并行计算能力成为首选。英伟达凭借 H100、A100 等数据中心 GPU 在 AI 算力市场占据主导地位，"
      "其 CUDA 生态构成了深厚的护城河。面对激增的算力需求，国内外云厂商纷纷建设智算中心，提供弹性的 AI 算力服务，阿里云、腾讯云、华为云等均推出了大模型训练专用的算力产品。"),
    P("算力的供给集中度与高昂成本，催生了推理优化（量化、蒸馏）、国产算力替代、算力调度等方向。华为昇腾、寒武纪、摩尔线程等国产 AI 芯片在政策驱动下加速迭代，"
      "逐步在特定场景形成替代。下表对比了主流 AI 算力芯片。"),
    table([
        ["芯片", "厂商", "定位", "备注"],
        ["H100 / H200", "英伟达", "数据中心训练 / 推理", "市场主导"],
        ["A100", "英伟达", "数据中心训练", "上一代旗舰"],
        ["昇腾 910B", "华为", "国产训练 / 推理", "国产替代主力"],
        ["MI300", "AMD", "数据中心 AI", "挑战英伟达"],
        ["TPU v5", "Google", "内部 + 云推理", "自研 ASIC"],
    ], col_widths=[3.0 * cm, 2.8 * cm, 4.2 * cm, 5.5 * cm]),
    Spacer(1, 6),
    P("数据是训练大模型的「燃料」。高质量的预训练语料决定了模型的能力上限。常见的语料来源包括网页（Common Crawl）、书籍、代码（GitHub）、百科（Wikipedia）等。"
      "随着公开语料逐渐耗尽，高质量、专业化的数据集日益稀缺，数据治理与合成数据成为新的研究热点。"),
    P("h2", "4.2  模型层：通用大模型与行业大模型"),
    P("模型层是 AIGC 产业的核心。通用大模型（Foundation Model）追求跨领域的通用智能，参数规模通常在百亿到万亿级别，代表性的国际模型有 OpenAI 的 GPT-4、"
      "Anthropic 的 Claude、Google 的 Gemini、Meta 的 Llama；国内则有百度的文心一言、阿里的通义千问、腾讯的混元、字节跳动的豆包、智谱的 GLM、月之暗面的 Kimi "
      "以及 MiniMax 等多款模型同台竞争。"),
    P("行业大模型则针对医疗、金融、法律、教育等垂直领域，在通用模型基础上注入专业知识进行微调，在特定任务上表现更优。科大讯飞的星火模型深耕教育，"
      "商汤的日日新面向企业服务，各类行业模型共同构成了多层次的大模型供给体系。"),
    P("h2", "4.3  应用层：场景化落地"),
    P("应用层是 AIGC 价值变现的最终环节。围绕内容创作、办公提效、智能客服、代码开发等场景，涌现出大量应用产品。微软将 GPT-4 集成到 Copilot，重塑了 Office 与"
      "开发工具的体验；金山办公的 WPS AI 为中文用户提供写作、排版与数据分析辅助；字节跳动的豆包则面向 C 端用户提供聊天与创作服务。"),
    P("应用层的繁荣依赖于模型层能力的持续提升与成本下降。随着开源模型（如 Llama、ChatGLM）的成熟与推理成本的降低，越来越多中小开发者能够基于大模型构建垂直应用，"
      "AIGC 的应用生态呈现爆发式增长。"),
    P("h2", "4.4  产业链价值分布"),
    P("从价值链分布看，AIGC 产业呈现「中间厚、两端薄」的格局：基础层的算力与数据环节由少数厂商主导，价值集中度高；模型层是大模型公司的核心阵地，竞争最为激烈；"
      "应用层门类繁多但单点价值相对分散。下表给出各层的代表性企业与其核心产品。"),
    table([
        ["产业链层级", "核心环节", "代表性企业", "代表产品"],
        ["基础层", "算力（GPU）", "英伟达", "H100 / A100"],
        ["基础层", "云算力服务", "阿里云、腾讯云、华为云", "智算中心"],
        ["模型层", "通用大模型", "OpenAI、百度、阿里", "GPT-4、文心、通义"],
        ["模型层", "行业大模型", "科大讯飞、商汤、智谱", "星火、日日新、GLM"],
        ["应用层", "内容生成", "字节跳动、Midjourney", "豆包、Midjourney"],
        ["应用层", "企业服务", "金山 WPS、微软", "WPS AI、Copilot"],
    ], col_widths=[2.6 * cm, 3.0 * cm, 4.6 * cm, 4.3 * cm]),
    Spacer(1, 6),
])

# ---- Chapter 5 ----
ch("第五章  典型企业分析", [
    P("h1", "第五章  典型企业分析"),
    P("h2", "5.1  国际领先企业"),
    P("OpenAI 是这一轮 AIGC 浪潮的引领者。公司由 Sam Altman 领导，先后发布了 GPT-3、ChatGPT、GPT-4 等里程碑产品，并于 2024 年发布视频生成模型 Sora。"
      "OpenAI 的成功推动了生成式 AI 的产业化，其与微软的深度合作使其获得了稳定的算力与资金支持。"),
    P("Anthropic 由前 OpenAI 成员 Dario Amodei 创立，其 Claude 系列模型以安全性与长上下文能力著称，强调负责任的 AI。Google 在 AI 领域布局深远，"
      "从提出 Transformer 架构到推出 Gemini 多模态模型，一直是基础研究的重镇。Meta 则通过开源 Llama 系列模型推动了 AIGC 的民主化，Llama 已成为全球使用最广泛的开源大模型之一。"
      "英伟达在 CEO 黄仁勋的带领下，凭借 GPU 算力垄断成为 AIGC 时代最大的赢家之一。"),
    P("h2", "5.2  国内主要企业"),
    P("中国 AIGC 企业形成了互联网巨头与创业新锐并存的格局。百度推出文心一言与文心大模型系列，在搜索与云服务中深度集成；阿里云发布通义千问，并将大模型能力开放给企业客户，"
      "通义系列也以开源形式回馈社区；腾讯推出混元大模型，服务于社交、游戏与广告业务；字节跳动的豆包依托其内容生态快速获取海量用户。"),
    P("在大模型创业领域，智谱 AI 源自清华大学，其 GLM 系列模型与开源的 ChatGLM 在学术界与产业界均有广泛影响；月之暗面推出的 Kimi 以超长上下文能力著称；"
      "MiniMax 专注于多模态与角色扮演类应用；此外商汤、科大讯飞、百川智能、阶跃星辰等企业也各具特色。下表汇总了主要企业及其代表模型。"),
    table([
        ["企业", "代表模型 / 产品", "技术特色", "定位"],
        ["OpenAI", "GPT-4、ChatGPT、Sora", "通用能力强，多模态", "全球引领者"],
        ["Anthropic", "Claude 3", "安全对齐，长上下文", "安全优先"],
        ["Google", "Gemini", "原生多模态", "基础研究 + 应用"],
        ["Meta", "Llama 3", "开源生态", "开源领导者"],
        ["百度", "文心一言", "搜索 + 知识增强", "搜索 + 云"],
        ["阿里云", "通义千问", "企业服务，开源", "云 + 开源"],
        ["腾讯", "混元", "社交 / 游戏 / 广告", "场景驱动"],
        ["字节跳动", "豆包", "C 端内容生态", "C 端流量"],
        ["智谱 AI", "GLM、ChatGLM", "开源，学术渊源", "产学研结合"],
        ["月之暗面", "Kimi", "超长上下文", "长文本场景"],
        ["MiniMax", "abab、海螺 AI", "多模态，角色扮演", "C 端 + API"],
        ["英伟达", "H100 / A100", "AI 算力主导", "算力基础设施"],
    ], col_widths=[2.6 * cm, 3.6 * cm, 4.0 * cm, 4.3 * cm]),
    Spacer(1, 6),
    P("h2", "5.3  开源生态"),
    P("开源是推动 AIGC 普及的重要力量。Meta 的 Llama 系列、智谱的 ChatGLM、阿里的通义千问开源版、DeepSeek、百川等开源模型，让中小开发者和研究机构能够低成本地"
      "使用与改进大模型，加速了技术创新与应用创新。DeepSeek 以高性价比的训练与推理在 2024 年引发广泛关注，证明了开源路线在效率上的竞争力。"),
    P("繁荣的开源生态降低了 AIGC 的准入门槛，但也带来模型安全、合规与质量控制的新挑战。如何在开放与治理之间取得平衡，是开源社区与监管方共同面对的课题。"
      "总体而言，国际厂商在通用大模型的基础能力上仍具领先优势，而国内企业在中文理解、行业落地与成本控制上具有本地化优势，差距正不断缩小。"),
])

# ---- Chapter 6 ----
ch("第六章  应用场景", [
    P("h1", "第六章  应用场景"),
    P("h2", "6.1  内容创作"),
    P("内容创作是 AIGC 落地最快、最广泛的场景。在文本创作领域，大模型能够辅助撰写文案、新闻、小说与营销内容，显著提升创作者的生产效率。在图像领域，"
      "Stable Diffusion、Midjourney 等工具让设计师能够通过文字描述快速生成高质量插画。视频领域，Sora、Runway 等产品正在降低视频制作的门槛。"),
    P("h2", "6.2  智能客服"),
    P("传统客服系统依赖关键词匹配与固定话术，难以应对复杂问题。基于大模型的智能客服能够理解用户意图，结合企业知识库给出准确的个性化回答。借助检索增强生成（RAG）"
      "技术，客服机器人可以在私有知识库中检索答案，既保证了回复的专业性，又避免了模型幻觉，大幅降低了人工客服成本。"),
    P("h2", "6.3  代码生成"),
    P("代码生成是 AIGC 极具价值的 B 端场景。GitHub Copilot、Cursor 等工具能够根据自然语言注释或上下文自动补全代码、生成函数、编写测试，大幅提升了程序员的开发效率。"
      "研究表明，使用 AI 编程助手的开发者完成任务的速度显著加快。代码能力也成为评估大模型的重要维度，国际主流评测榜单（如 HumanEval、SWE-Bench）持续追踪这一能力。"),
    P("h2", "6.4  教育"),
    P("在教育领域，AIGC 可用于个性化辅导、作业批改、内容生成与语言学习。大模型能够根据学生的学习进度提供定制化的讲解与练习，扮演「一对一」智能导师的角色。"
      "然而，教育场景对内容的准确性要求极高，模型幻觉可能误导学生，因此在教育应用中，知识库与事实校验机制尤为关键。"),
    P("h2", "6.5  医疗与金融"),
    P("在医疗领域，AIGC 辅助医生进行病历总结、文献检索与初步诊断建议，但考虑到医疗安全，目前多用于辅助而非决策。在金融领域，大模型用于研报生成、风险评估、"
      "智能投顾与反欺诈，提升分析与决策效率。这两个领域都对专业性与合规性有严格要求，行业大模型与严格的审核流程不可或缺。"),
    P("h2", "6.6  办公提效"),
    P("办公场景是 AIGC 最贴近普通白领的应用。微软 Copilot 将 GPT-4 接入 Word、Excel、PowerPoint，实现文档起草、数据分析与演示制作；金山办公的 WPS AI 为中文用户提供"
      "类似的智能办公能力。通过将大模型嵌入日常工作流，办公效率得到显著提升，AIGC 正成为新一代生产力工具的核心。"),
    P("h2", "6.7  行业落地案例"),
    P("下表汇总了 AIGC 在若干行业的典型落地案例与价值。"),
    table([
        ["行业", "典型应用", "代表实践", "核心价值"],
        ["电商", "商品文案、素材生成", "淘宝、京东智能文案", "降本提效"],
        ["传媒", "新闻写作、视频生成", "新华社、字节", "内容规模化"],
        ["金融", "研报、风控、客服", "工行、招行智能客服", "效率 + 体验"],
        ["医疗", "病历摘要、辅助诊断", "平安好医生、卫宁", "医生提效"],
        ["教育", "辅导、批改、内容", "好未来、科大讯飞", "个性化教学"],
        ["软件", "代码生成、测试", "GitHub Copilot、Cursor", "研发提效"],
    ], col_widths=[2.4 * cm, 3.6 * cm, 4.4 * cm, 4.1 * cm]),
    Spacer(1, 6),
])

# ---- Chapter 7 ----
ch("第七章  挑战与治理", [
    P("h1", "第七章  挑战与治理"),
    P("h2", "7.1  伦理与安全"),
    P("AIGC 的快速发展也带来一系列伦理与安全挑战。大模型可能生成带有偏见、歧视或有害的内容，对齐技术（如 RLHF）正是为了缓解这些问题。然而，完全消除偏见与风险"
      "仍十分困难，模型的安全对齐是一个持续迭代的过程。此外，强大的生成能力也引发了关于人工智能潜在失控风险的深层讨论。"),
    P("h2", "7.2  版权争议"),
    P("AIGC 的训练依赖海量数据，其中包含大量受版权保护的作品，引发了关于训练数据合法性的争议。《纽约时报》等机构对 OpenAI 提起诉讼，指控其未经授权使用受版权保护的内容"
      "训练模型。生成内容本身的著作权归属也尚无定论。多起诉讼表明，如何在技术创新与版权保护之间取得平衡，是行业必须面对的课题。"),
    P("h2", "7.3  深度伪造与虚假信息"),
    P("高质量的图像、视频与语音生成能力，使得伪造逼真的虚假内容变得容易，带来深度伪造（Deepfake）的滥用风险。虚假信息可能被用于诈骗、操纵舆论或损害个人名誉。"
      "水印、内容溯源与检测技术成为应对深度伪造的重要手段，各国正在推动相关技术标准与法律法规的制定。"),
    P("h2", "7.4  监管合规"),
    P("各国正加紧构建 AIGC 的监管框架。欧盟的《人工智能法案》（AI Act）按照风险等级对 AI 应用进行分级监管，对高风险应用提出严格要求；美国以行政命令与行业自律为主，"
      "鼓励创新的同时关注安全；中国于 2023 年 8 月起施行《生成式人工智能服务管理暂行办法》，要求提供者对训练数据合规性、生成内容真实性与用户个人信息保护承担责任，"
      "并对算法备案与安全评估提出要求。下表对比了主要经济体的监管取向。"),
    table([
        ["经济体", "代表性法规", "监管取向", "重点"],
        ["中国", "生成式 AI 服务管理暂行办法（2023.8）", "服务准入 + 内容合规", "算法备案、安全评估"],
        ["欧盟", "人工智能法案（AI Act）", "按风险分级监管", "高风险应用严格监管"],
        ["美国", "行政命令 + 行业自律", "鼓励创新 + 安全", "自愿承诺、标准"],
        ["英国", "分散式监管", "原则导向", "行业监管机构协同"],
    ], col_widths=[2.4 * cm, 5.0 * cm, 3.8 * cm, 4.3 * cm]),
    Spacer(1, 6),
    P("h2", "7.5  数据隐私"),
    P("AIGC 应用的广泛落地也带来数据隐私挑战。用户在与大模型交互时可能无意中泄露敏感信息，企业部署大模型时也面临商业机密保护的压力。联邦学习、差分隐私、"
      "本地化部署等隐私保护技术，以及数据使用合规审计，成为企业落地 AIGC 的必要配套。"),
    P("面对上述挑战，负责任的 AIGC 发展需要技术、产业与监管三方协同：技术上持续改进对齐与检测能力，产业上建立自律规范，监管上完善法律法规。只有在创新与治理之间"
      "取得平衡，AIGC 才能实现可持续的健康发展。"),
])

# ---- Chapter 8 ----
ch("第八章  趋势展望", [
    P("h1", "第八章  趋势展望"),
    P("h2", "8.1  多模态深度融合"),
    P("未来大模型将进一步打通文本、图像、音频、视频等多种模态，实现真正的统一理解与生成。原生多模态架构将取代简单的模态拼接，模型能够像人类一样综合感知与表达。"
      "视频生成与具身智能的结合，有望催生具备世界模型能力的新一代 AI。"),
    P("h2", "8.2  智能体（Agent）的兴起"),
    P("智能体被视为 AIGC 应用的下一个范式。未来的 Agent 将具备更强的规划、记忆与工具调用能力，能够自主完成复杂的多步骤任务，从「对话助手」演变为「数字员工」。"
      "多 Agent 协作系统可能在软件开发、科学研究、企业运营等领域带来生产力的跨越式提升。"),
    P("h2", "8.3  端侧部署与算力下沉"),
    P("随着模型轻量化（如量化、蒸馏、LoRA 微调）与端侧芯片的发展，大模型正从云端走向手机、PC 与边缘设备。端侧部署能够降低延迟、保护隐私并减少对云算力的依赖，"
      "使 AIGC 能力无处不在。端云协同将成为主流部署模式。"),
    P("h2", "8.4  行业垂直化"),
    P("通用大模型之外，面向特定行业的垂直大模型将持续发展。医疗、法律、金融、制造等领域的专业模型，通过注入行业知识与合规约束，在专业场景中提供更准确、更可靠的服务。"
      "行业模型的繁荣将推动 AIGC 在实体经济中的深度落地。"),
    P("h2", "8.5  开源生态"),
    P("开源是推动 AIGC 普及的重要力量。Llama、ChatGLM、通义千问开源版、DeepSeek 等开源模型让中小开发者和研究机构能够低成本地使用与改进大模型，加速了技术创新与应用创新。"
      "繁荣的开源生态与活跃的研究社区，将持续降低 AIGC 的门槛，让生成式 AI 惠及更广泛的群体。"),
    P("h2", "8.6  通往 AGI 的展望"),
    P("AIGC 的长远愿景是通用人工智能（AGI）——具备与人类相当甚至更全面的智能能力。尽管业界对 AGI 的时间表尚无共识，但多模态、长上下文、推理、自我改进与具身智能等方向"
      "的持续突破，正在逐步逼近这一目标。与此同时，AGI 的潜在影响也呼唤更深层的对齐研究与全球治理协作，确保其发展符合人类的长远利益。"),
    P("h2", "8.7  结语"),
    P("AIGC 正处于快速演进之中。从 Transformer 架构到多模态大模型，从 ChatGPT 到智能体，技术迭代的速度前所未有。在算力、数据与算法的协同驱动下，生成式 AI 有望重塑"
      "内容生产、知识工作与人机交互的方式。面对机遇与挑战并存的前景，唯有坚持技术创新与负责任的发展并重，AIGC 才能真正成为推动社会进步的积极力量。"),
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
        ["AI 反馈强化学习", "RLAIF", "用 AI 反馈代替人类反馈的对齐方法"],
        ["直接偏好优化", "DPO", "Direct Preference Optimization，简化 RLHF"],
        ["低秩适配", "LoRA", "Low-Rank Adaptation，高效参数微调"],
        ["检索增强生成", "RAG", "Retrieval-Augmented Generation，检索后生成"],
        ["智能体", "Agent", "能规划、调用工具、完成多步任务的 AI"],
        ["扩散模型", "Diffusion Model", "通过去噪过程学习数据分布的生成模型"],
        ["生成对抗网络", "GAN", "Generative Adversarial Network（2014）"],
        ["对比语言图像预训练", "CLIP", "对齐图像与文本表示的多模态模型（2021）"],
        ["多模态", "Multimodal", "同时处理文本、图像、音频等多种模态"],
        ["混合专家", "MoE", "Mixture of Experts，稀疏激活的模型结构"],
        ["嵌入", "Embedding", "将离散对象映射为稠密向量表示"],
        ["向量数据库", "Vector Database", "存储与检索高维向量的数据库"],
        ["幻觉", "Hallucination", "模型生成看似合理但与事实不符的内容"],
        ["对齐", "Alignment", "使模型行为符合人类意图与价值观"],
        ["规模法则", "Scaling Law", "模型能力随规模增长而提升的规律"],
        ["知识图谱", "Knowledge Graph", "结构化表示实体与关系的数据网络"],
        ["端侧部署", "On-device", "在终端设备本地运行模型"],
    ], col_widths=[3.6 * cm, 3.6 * cm, 7.6 * cm]),
    Spacer(1, 8),
    P("h2", "附录 B  AIGC 发展大事记"),
    table([
        ["年份", "标志性事件"],
        ["2014", "Ian Goodfellow 提出生成对抗网络（GAN），开创深度生成模型方向"],
        ["2017", "Google 发表《Attention Is All You Need》，提出 Transformer 架构"],
        ["2018", "OpenAI 发布 GPT-1、Google 发布 BERT，预训练语言模型范式确立"],
        ["2019", "GPT-2 发布，展现零样本生成能力，引发对 AI 滥用的讨论"],
        ["2020", "GPT-3（1750 亿参数）发布；DDPM 奠定现代扩散模型基础"],
        ["2021", "OpenAI 发布 CLIP 与 DALL·E，推动多模态与文生图发展"],
        ["2022.4", "PaLM 等千亿参数模型涌现；Chinchilla 修正规模法则"],
        ["2022.8", "Stable Diffusion 开源，高质量文生图走向普及"],
        ["2022.11", "ChatGPT 上线，两个月月活破亿"],
        ["2023.2", "LLaMA 泄露引发开源微调热潮；ControlNet 强化可控生成"],
        ["2023.3", "GPT-4 发布，具备多模态理解能力"],
        ["2023.7", "Llama 2 开源；中国《生成式 AI 服务管理暂行办法》发布"],
        ["2023.8", "国内智谱、月之暗面、MiniMax 等大模型密集发布"],
        ["2023.12", "Gemini 发布；Mistral 等高效开源模型崛起"],
        ["2024.2", "OpenAI 发布文生视频模型 Sora，展现世界模型雏形"],
        ["2024.4", "Llama 3 开源；Meta 加大开源投入"],
        ["2024下半年", "Agent 框架与端侧大模型兴起，AI 应用加速走向生产落地"],
    ], col_widths=[2.0 * cm, 12.8 * cm]),
    Spacer(1, 8),
    P("h2", "附录 C  主要企业名录"),
    table([
        ["企业", "国别", "主要领域", "代表产品"],
        ["OpenAI", "美国", "通用大模型", "GPT-4、ChatGPT、Sora"],
        ["Anthropic", "美国", "通用大模型", "Claude"],
        ["Google DeepMind", "美国", "通用大模型", "Gemini"],
        ["Meta", "美国", "开源大模型", "Llama"],
        ["英伟达", "美国", "算力芯片", "H100、A100"],
        ["百度", "中国", "通用 / 搜索", "文心一言"],
        ["阿里巴巴", "中国", "通用 / 云", "通义千问"],
        ["腾讯", "中国", "通用 / 社交", "混元"],
        ["字节跳动", "中国", "通用 / 内容", "豆包"],
        ["智谱 AI", "中国", "通用 / 开源", "GLM、ChatGLM"],
        ["月之暗面", "中国", "长文本", "Kimi"],
        ["MiniMax", "中国", "多模态", "abab、海螺 AI"],
        ["科大讯飞", "中国", "行业（教育）", "星火"],
        ["商汤", "中国", "行业（视觉）", "日日新"],
    ], col_widths=[3.0 * cm, 2.0 * cm, 3.6 * cm, 6.2 * cm]),
    Spacer(1, 8),
    P("从 2014 年的 GAN 到 2024 年的 Agent 与端侧大模型，AIGC 用十年时间完成了从学术研究到产业落地的跨越。这份大事记既是一条技术演进的时间线，"
      "也是理解当下 AIGC 产业格局的线索。"),
])


def build():
    doc = BaseDocTemplate(OUTPUT, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm, title=TITLE, author="Arrow Lake Cookbook")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_on_page)])
    story = []
    story.append(Spacer(1, 5 * cm))
    story.append(Paragraph(TITLE, ST["cover_title"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(SUBTITLE, ST["cover_sub"]))
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("本文档为 <b>Arrow Lake</b> 项目的示例数据，内容由公开资料整理合成，仅用于技术演示（文档摄入、切块、向量检索、全文检索、RAG、知识图谱构建等），并非真实研究报告，不代表任何机构的观点与立场。", ST["cover_note"]))
    story.append(PageBreak())
    story.append(Paragraph("目  录", ST["h1"]))
    for _, (title, _) in enumerate(CHAPTERS):
        story.append(Paragraph(title, ST["toc"]))
    story.append(PageBreak())
    for _, blocks in CHAPTERS:
        story.extend(blocks)
        story.append(PageBreak())
    doc.build(story)
    print("Generated " + OUTPUT)


if __name__ == "__main__":
    build()
