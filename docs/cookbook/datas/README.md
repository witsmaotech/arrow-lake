# 示例数据文件

本目录包含 Arrow Lake Cookbook 各章节所需的**两类代表性示例数据源**，外加多媒体示例：

- **文本型主数据源** `reports/`：一份合成的 AIGC 行业研究报告 PDF，贯穿「文档摄入 → 切块 → 向量检索 → 全文检索 → 混合检索 → RAG → 知识图谱」的完整文本处理主线（第 02 / 04 / 05 / 06 / 08 / 09 章）。
- **结构化主数据源** `ontime/`：美国交通部 BTS 公开的 2022 年航班数据（160 万行 × 109 列），用于 OLAP 分析与 SQL 查询演示（第 02 / 07 章）。
- **多媒体示例** `photos/` 与 `videos/`：多模态摄入与图像/视频检索演示（第 02 / 10 / 16 章）。

> **版权说明**：AIGC 报告为项目合成的示例文档（基于公开资料整理，版权归本项目所有）；ontime 为美国交通部公共领域数据；photos/videos 为项目自带示例多媒体。三者均可自由用于开源项目。

## 目录结构

```
datas/
├── reports/
│   ├── generate_articles.py         # AIGC 文章元数据 CSV 生成器（第 04-07 章）
│   ├── aigc_articles.csv            # 144 篇 AIGC 文章元数据（8 类 × 18 篇）
│   ├── generate_report.py           # AIGC 行业报告 PDF 生成器（第 08-09 章）
│   └── aigc_industry_report.pdf     # 合成 AIGC 行业研究报告（14 页，8 章 + 附录）
├── ontime/
│   ├── ontime_2022.parquet          # 2022 年美国航班数据（1,598,468 行 × 109 列）
│   └── README.md                    # 字段说明与查询示例
├── photos/                          # 6 张示例图片（多模态摄入演示）
├── videos/                          # 3 个示例视频（多模态摄入演示）
├── download_data.py                 # 独立多媒体下载器（Wikimedia 等，可选）
└── README.md                        # 本文件
```

---

## reports/ — 文本型主数据源（AIGC）

reports/ 下有两个互补的 AIGC 文本数据源：

- **aigc_articles.csv** — 144 篇 AIGC 文章元数据，列与经典论文库一致，支撑第 04-07 章的向量 / 全文 / 混合检索与 facet 切片。
- **aigc_industry_report.pdf** — 行业报告全文，支撑第 08-09 章的文档摄入与 RAG / 知识图谱。

### aigc_articles.csv — AIGC 文章元数据（第 04-07 章）

144 篇 AIGC 文章元数据（8 个类别 × 18 篇），列与经典论文库对齐，适合演示带结构化过滤（`where category=...`、`where year>=2023`）的向量 / 全文 / 混合检索与 facet：

| 列名 | 说明 |
|---|---|
| id | 文章编号（a001–a144）|
| title | 标题 |
| text_content | 摘要正文（摄入时自动嵌入为 text_embedding）|
| category | 类别（大语言模型 / 多模态 / 扩散模型 / 智能体 / 检索增强生成 / 算力基础设施 / AIGC应用 / AI治理）|
| year | 年份（2020–2024）|
| venue | 来源（NeurIPS / ICML / ACL / CVPR / 行业白皮书 …）|
| authors | 作者 / 机构 |
| word_count | 正文字数 |

**重新生成**：`python docs/cookbook/datas/reports/generate_articles.py`

**摄入**：

```bash
arrow-lake --base-uri ./aigc_lake ingest files aigc_articles docs/cookbook/datas/reports/aigc_articles.csv
```

### aigc_industry_report.pdf — AIGC 行业报告全文（第 08-09 章）

`aigc_industry_report.pdf` 是用 reportlab 合成的中文 AIGC 行业研究报告示例文档，共 14 页，含 8 个正文章节与 1 个附录（术语表 + 发展大事记）。内容覆盖行业概述、市场规模、核心技术（Transformer / RLHF / 扩散模型 / RAG / Agent）、产业链、典型企业、应用场景、挑战治理与趋势展望。

> 本文档为示例数据，内容由公开资料整理合成，仅用于技术演示，不代表任何机构立场。

**实体丰富，适配知识图谱抽取**：文档含大量公司名（OpenAI / 百度 / 阿里 / 腾讯 / 字节 / 智谱 / 月之暗面 / MiniMax 等）、技术名（Transformer / GPT-4 / RLHF / 扩散模型 / CLIP）、时间节点与数据指标，非常适合演示 KG 实体与关系抽取（见 [09 知识图谱](../09-knowledge-graph-zh.md)）。

**重新生成**（需 `reportlab`，`uv pip install reportlab`）：

```bash
python docs/cookbook/datas/reports/generate_report.py
```

**摄入**：

```bash
# CLI
arrow-lake --base-uri ./aigc_lake ingest docs aigc_report docs/cookbook/datas/reports/aigc_industry_report.pdf

# REST（容器内同源）
curl -X POST http://127.0.0.1:8000/api/v1/datasets/aigc_report/ingest/documents \
  -H "X-API-Key: $API_KEY" \
  -F "files=@docs/cookbook/datas/reports/aigc_industry_report.pdf"
```

---

## ontime/ — 结构化主数据源（航班数据）

`ontime_2022.parquet` 是美国交通部交通统计局（BTS）On-Time 航班数据的 2022 年子集，共 **1,598,468 行 × 109 列**。字段涵盖日期、航班、出发/到达机场、起降时间、延误、取消、备降、飞行距离与延误成因等，适合演示聚合、分组、窗口函数、JOIN 等各类 OLAP 查询。

详细字段说明与查询示例见 [ontime/README.md](ontime/README.md)。

**摄入**：

```bash
# CLI
arrow-lake --base-uri ./ontime_lake ingest files ontime docs/cookbook/datas/ontime/ontime_2022.parquet

# REST
curl -X POST http://127.0.0.1:8000/api/v1/datasets/ontime/ingest/files \
  -H "X-API-Key: $API_KEY" \
  -F "files=@docs/cookbook/datas/ontime/ontime_2022.parquet"
```

---

## photos/ 与 videos/ — 多媒体示例

`photos/` 含 6 张示例图片（日落 / 山景 / 海浪 / 森林 / 城市夜景 / 花园），`videos/` 含 3 个示例视频（讲座 / 访谈 / 产品评测）。用于第 02 章多模态摄入、第 10 章 `/embed/image` / `/embed/clip-text` 端点与第 16 章 CLIP 特性演示。

```bash
arrow-lake --base-uri ./media_lake ingest files photos docs/cookbook/datas/photos/*.jpg
arrow-lake --base-uri ./media_lake ingest videos clips docs/cookbook/datas/videos/*.mp4
```

---

## download_data.py（可选）

独立的真实多媒体下载器，从 Wikimedia Commons、Project Gutenberg 等公共来源下载图片 / 文本 / 视频 / 音频。与上述示例数据相互独立，按需运行：

```bash
python docs/cookbook/datas/download_data.py --small      # 少量示例
python docs/cookbook/datas/download_data.py --images-only # 仅图片
```
