#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic AIGC article-metadata CSV for the Arrow Lake cookbook.

Output columns mirror the legacy `papers` metadata layout so chapters 04–07
keep the same field names (category / year / venue / authors / word_count):
    id, title, text_content, category, year, venue, authors, word_count

Content is compiled from publicly available AIGC information and is sample
data for technical demonstration only.

Usage:
    python generate_articles.py
Output:
    aigc_articles.csv  (next to this script)
"""

import csv
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigc_articles.csv")

# Each category seeds a deterministic set of articles.
# titles: rotating title seeds; sentences: pool joined into text_content.
SEEDS = [
    ("大语言模型", {
        "titles": [
            "GPT-4 技术能力评测与分析",
            "大语言模型预训练范式综述",
            "基于人类反馈的强化学习对齐方法",
            "稀疏混合专家模型的训练与实践",
            "大模型长上下文建模的进展与挑战",
            "开源大语言模型的生态与现状",
        ],
        "sentences": [
            "大语言模型（LLM）通过在海量语料上的自监督预训练获得通用的语言理解与生成能力。",
            "自 Transformer 架构提出以来，模型规模从亿级扩展到千亿、万亿级，规模法则持续得到验证。",
            "GPT-4、Claude、Gemini 等闭源模型在推理与多步任务上表现领先，Llama、ChatGLM 等开源模型推动了技术的普及。",
            "强化学习人类反馈（RLHF）是使模型输出对齐人类意图的关键技术，显著提升了回答的有用性与安全性。",
            "稀疏激活的混合专家（MoE）架构在不显著增加推理开销的前提下扩大了模型参数容量。",
        ],
        "entities": ["Transformer", "GPT-4", "RLHF", "MoE", "ChatGLM", "Llama"],
    }),
    ("多模态", {
        "titles": [
            "多模态大模型的统一表示学习",
            "图文跨模态检索的 CLIP 范式",
            "原生多模态架构的设计与训练",
            "视觉语言模型在内容理解中的应用",
            "多模态对齐与融合策略综述",
            "面向视频理解的多模态预训练",
        ],
        "sentences": [
            "多模态大模型统一处理文本、图像、音频、视频等多种模态，更接近人类感知世界的方式。",
            "CLIP 通过对比学习将图像与文本映射到同一表示空间，奠定了跨模态检索的基础。",
            "GPT-4 与 Gemini 能够同时理解图像与文本，回答关于图片内容的复杂问题。",
            "原生多模态架构从设计之初即联合多种模态训练，优于简单的模态拼接。",
            "多模态对齐的关键在于设计有效的跨模态损失与高质量的对齐数据。",
        ],
        "entities": ["CLIP", "GPT-4", "Gemini", "对比学习", "跨模态检索"],
    }),
    ("扩散模型", {
        "titles": [
            "潜在扩散模型与高质量图像生成",
            "文生图扩散模型的工程优化",
            "扩散模型采样加速方法综述",
            "视频生成的扩散架构探索",
            "条件扩散模型的可控生成",
            "扩散模型的理论基础与去噪过程",
        ],
        "sentences": [
            "扩散模型通过前向加噪与反向去噪过程学习数据分布，生成质量高且训练稳定。",
            "潜在扩散模型（Latent Diffusion）将扩散过程迁移到低维潜在空间，大幅降低了计算开销。",
            "Stable Diffusion 与 Midjourney 让高质量文生图走向普通用户，重塑了内容创作流程。",
            "采样加速技术如 DDIM、一致性模型显著减少了生成所需的迭代步数。",
            "OpenAI 的 Sora 将扩散思想扩展到视频生成，能根据文本生成长达一分钟的高清视频。",
        ],
        "entities": ["扩散模型", "Stable Diffusion", "Latent Diffusion", "DDIM", "Sora", "Midjourney"],
    }),
    ("智能体", {
        "titles": [
            "大模型驱动的智能体框架综述",
            "基于工具调用的任务型 Agent",
            "多智能体协作系统的研究进展",
            "智能体的记忆与规划机制",
            "自主智能体的反思与自我改进",
            "企业场景下的 Agent 应用实践",
        ],
        "sentences": [
            "智能体（Agent）以大模型为大脑，结合工具调用、记忆与规划能力，自主完成多步骤的复杂任务。",
            "相比单轮问答，Agent 能够分解任务、调用外部工具并根据反馈调整策略，是 AIGC 应用的更高形态。",
            "多智能体协作系统通过角色分工与通信，在软件开发、科学研究等场景展现出潜力。",
            "记忆机制让 Agent 能跨越会话保持上下文，规划机制则支持长程任务的分解与调度。",
            "从 AutoGPT 到各类 Agent 框架，AI 正从回答问题走向解决问题。",
        ],
        "entities": ["Agent", "AutoGPT", "工具调用", "规划", "多智能体"],
    }),
    ("检索增强生成", {
        "titles": [
            "检索增强生成缓解大模型幻觉的研究",
            "混合检索在 RAG 系统中的应用",
            "向量数据库支撑的企业级 RAG 实践",
            "RAG 中的重排序与上下文压缩",
            "知识库构建与 RAG 的端到端管线",
            "GraphRAG：知识图谱增强的检索生成",
        ],
        "sentences": [
            "检索增强生成（RAG）通过在生成前从外部知识库检索相关文档并注入上下文，缓解大模型的幻觉与知识过期问题。",
            "RAG 通常结合向量检索与全文检索的混合策略，再用重排序模型提升最终召回质量。",
            "向量数据库是 RAG 的基础设施，支持高维向量的高效近似最近邻检索。",
            "重排序与上下文压缩能在有限 token 预算内显著提升回答的相关性与准确性。",
            "GraphRAG 引入知识图谱的结构化关系，在实体关系密集的问题上优于纯向量检索。",
        ],
        "entities": ["RAG", "向量数据库", "重排序", "GraphRAG", "混合检索"],
    }),
    ("算力基础设施", {
        "titles": [
            "GPU 集群上的大模型分布式训练",
            "智算中心与弹性算力服务",
            "大模型推理的显存与吞吐优化",
            "模型量化与部署加速技术",
            "算力国产化与供应链分析",
            "云原生 AI 训练平台架构",
        ],
        "sentences": [
            "算力是 AIGC 产业的地基，大模型训练依赖大规模 GPU 集群进行分布式计算。",
            "英伟达的 H100、A100 等 GPU 在 AI 算力市场占据主导，CUDA 生态构成深厚护城河。",
            "推理优化通过量化、KV 缓存、连续批处理等技术提升吞吐并降低延迟。",
            "模型量化将权重从 16 位压缩到 8 位甚至 4 位，在精度损失可控的前提下大幅降低部署成本。",
            "面对激增的算力需求，国内外云厂商纷纷建设智算中心提供弹性算力。",
        ],
        "entities": ["英伟达", "H100", "A100", "CUDA", "量化", "分布式训练"],
    }),
    ("AIGC应用", {
        "titles": [
            "AIGC 在智能客服中的落地实践",
            "代码生成工具提升研发效率分析",
            "内容创作领域的生成式 AI 应用",
            "教育场景下的大模型个性化辅导",
            "金融行业的 AIGC 风险与机遇",
            "医疗领域的生成式 AI 辅助诊断",
        ],
        "sentences": [
            "内容创作是 AIGC 落地最快的场景，覆盖文案、图像、视频等多种模态的内容生产。",
            "智能客服结合大模型与企业知识库，能够理解用户意图并给出个性化的准确回答。",
            "代码生成工具如 Copilot 能根据自然语言注释自动补全代码，显著提升开发者效率。",
            "教育场景中，大模型可扮演一对一智能导师，提供定制化的讲解与练习。",
            "医疗与金融对准确性与合规性要求极高，通常需要行业大模型与严格的事实校验。",
        ],
        "entities": ["Copilot", "智能客服", "代码生成", "知识库", "个性化"],
    }),
    ("AI治理", {
        "titles": [
            "生成式人工智能服务的合规监管",
            "大模型偏见与对齐技术研究",
            "深度伪造的检测与溯源方法",
            "AIGC 训练数据的版权争议",
            "人工智能安全与红队评估",
            "可解释人工智能的实践路径",
        ],
        "sentences": [
            "AIGC 的快速发展带来伦理、安全与合规挑战，需要技术、产业与监管三方协同应对。",
            "深度伪造（Deepfake）滥用风险促使水印、内容溯源与检测技术成为研究热点。",
            "训练数据中的偏见会被模型放大，对齐技术旨在使模型行为符合人类价值观。",
            "中国《生成式人工智能服务管理暂行办法》对训练数据合规与安全评估提出明确要求。",
            "欧盟《人工智能法案》按风险等级对 AI 应用分级监管，影响全球产业格局。",
        ],
        "entities": ["对齐", "Deepfake", "水印", "红队评估", "人工智能法案"],
    }),
]

VENUES = ["NeurIPS", "ICML", "ACL", "CVPR", "ICLR", "行业白皮书", "技术博客", "学术综述"]
YEARS = [2020, 2021, 2022, 2023, 2024]
AUTHORS = [
    "OpenAI", "Google DeepMind", "Meta AI", "百度", "阿里达摩院",
    "腾讯 AI Lab", "智谱 AI", "清华大学", "MIT", "斯坦福大学",
    "Anthropic", "字节跳动", "月之暗面", "商汤研究院", "中国科学院",
]
# Per-category venue pool (rotated deterministically).
PER_CAT_VENUE = {
    "大语言模型": ["NeurIPS", "ICML", "ACL", "技术博客", "学术综述"],
    "多模态": ["CVPR", "NeurIPS", "ICLR", "技术博客", "行业白皮书"],
    "扩散模型": ["CVPR", "NeurIPS", "ICML", "技术博客", "学术综述"],
    "智能体": ["NeurIPS", "ICML", "技术博客", "行业白皮书", "学术综述"],
    "检索增强生成": ["ACL", "NeurIPS", "技术博客", "行业白皮书", "学术综述"],
    "算力基础设施": ["行业白皮书", "技术博客", "ICML", "NeurIPS", "学术综述"],
    "AIGC应用": ["行业白皮书", "技术博客", "ACL", "CVPR", "学术综述"],
    "AI治理": ["行业白皮书", "学术综述", "NeurIPS", "ICML", "技术博客"],
}


def build_rows():
    rows = []
    aid = 0
    for cat, seed in SEEDS:
        titles = seed["titles"]
        sents = seed["sentences"]
        ents = seed["entities"]
        venues = PER_CAT_VENUE[cat]
        # 18 articles per category: 6 titles × 3 year variants
        for i, title in enumerate(titles):
            for y_idx in range(3):
                aid += 1
                year = YEARS[(i + y_idx) % len(YEARS)]
                venue = venues[(i + y_idx) % len(venues)]
                author = AUTHORS[(aid) % len(AUTHORS)]
                # Compose 4 sentences from the pool, rotated by offset.
                offset = (i * 3 + y_idx) % len(sents)
                picked = [sents[(offset + k) % len(sents)] for k in range(4)]
                # Sprinkle a category entity into the 2nd sentence for FTS richness.
                ent = ents[(i + y_idx) % len(ents)]
                picked[1] = picked[1][: picked[1].rfind("。")] + f" 以 {ent} 为代表的技术路线被广泛采用。"
                content = "".join(picked)
                wc = len(content)
                rows.append({
                    "id": f"a{aid:03d}",
                    "title": title,
                    "text_content": content,
                    "category": cat,
                    "year": year,
                    "venue": venue,
                    "authors": author,
                    "word_count": wc,
                })
    return rows


def main():
    rows = build_rows()
    fields = ["id", "title", "text_content", "category", "year", "venue", "authors", "word_count"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Generated {OUT}  ({len(rows)} rows, {len(fields)} columns)")


if __name__ == "__main__":
    main()
