"""Generate synthetic test documents for E2E and integration tests.

Creates various document types:
- Plain text (.txt)
- Markdown (.md)
- JSON lines (.jsonl)
- CSV (.csv)

Usage:
    python tests/fixtures/documents/generate_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def gen_plain_text_en() -> None:
    _write(FIXTURES_DIR / "plain_text_en.txt", """\
Machine Learning Fundamentals

Machine learning is a subset of artificial intelligence that focuses on building systems that learn from data. There are three main types of machine learning: supervised learning, unsupervised learning, and reinforcement learning.

Supervised Learning uses labeled data to train models. Common algorithms include linear regression, decision trees, random forests, and neural networks. The model learns to map input features to output labels.

Unsupervised Learning works with unlabeled data. Clustering algorithms like K-means, DBSCAN, and hierarchical clustering group similar data points together. Dimensionality reduction techniques like PCA and t-SNE help visualize high-dimensional data.

Reinforcement Learning trains agents through rewards and penalties. Applications include game playing (AlphaGo), robotics, and autonomous driving.

Deep Learning uses neural networks with many layers. Convolutional neural networks (CNNs) excel at image recognition. Recurrent neural networks (RNNs) and transformers are used for natural language processing. The transformer architecture, introduced in "Attention Is All You Need" (2017), revolutionized NLP and led to models like BERT, GPT, and their successors.

Transfer learning allows pre-trained models to be fine-tuned for specific tasks, reducing the need for large labeled datasets. This has democratized access to powerful AI capabilities.

Key evaluation metrics include accuracy, precision, recall, F1 score, AUC-ROC, and mean squared error. Cross-validation provides robust estimates of model performance.
""")


def gen_plain_text_zh() -> None:
    _write(FIXTURES_DIR / "plain_text_zh.txt", """\
机器学习基础

机器学习是人工智能的一个分支，专注于构建从数据中学习的系统。机器学习主要分为三种类型：监督学习、无监督学习和强化学习。

监督学习使用带标签的数据来训练模型。常见算法包括线性回归、决策树、随机森林和神经网络。模型学习将输入特征映射到输出标签。

无监督学习处理无标签数据。聚类算法如K均值、DBSCAN和层次聚类将相似的数据点分组。降维技术如主成分分析（PCA）和t-SNE帮助可视化高维数据。

强化学习通过奖励和惩罚来训练智能体。应用包括游戏对弈（AlphaGo）、机器人和自动驾驶。

深度学习使用具有多层结构的神经网络。卷积神经网络（CNN）在图像识别方面表现优异。循环神经网络（RNN）和Transformer用于自然语言处理。Transformer架构自2017年提出以来，彻底改变了NLP领域，催生了BERT、GPT等模型。

迁移学习允许对预训练模型进行微调，减少了对大规模标注数据集的需求，使强大的AI能力更加普及。
""")


def gen_markdown_tech() -> None:
    _write(FIXTURES_DIR / "tech_report.md", """\
# Arrow Lake Technical Report

## Architecture Overview

Arrow Lake is a production-ready data lake platform built on Apache Arrow, Lance, and DuckDB. It provides end-to-end capabilities for data ingestion, processing, search, and retrieval-augmented generation (RAG).

### Core Components

1. **Storage Layer**: Lance format for columnar storage with versioning. Supports local filesystem and S3/MinIO backends.
2. **Query Engine**: DuckDB for OLAP queries, vector search, and full-text search.
3. **Embedding Pipeline**: Local (HuggingFace SentenceTransformer) and API-based (OpenAI-compatible) embedding generation.
4. **Knowledge Graph**: HugeGraph integration for entity-relationship modeling and GraphRAG.
5. **API Layer**: FastAPI with JWT authentication and rate limiting.

### Performance Characteristics

| Operation | Latency (p50) | Throughput |
|-----------|---------------|------------|
| SELECT 10k rows | ~1ms | 1000 ops/s |
| GROUP BY aggregation | ~1.2ms | 850 ops/s |
| Full-text search | ~0.5ms | 2100 ops/s |
| Document chunking | ~0.35ms | 2900 ops/s |

### Security Features

- SQL injection prevention via centralized validation
- Gremlin injection prevention in knowledge graph queries
- SSRF protection for external service calls
- JWT-based API authentication
- Rate limiting per client

## Deployment

Arrow Lake supports Docker Compose deployment with MinIO, HugeGraph, and optional OCR service (TurboOCR).

## API Endpoints

### Dataset Management
- `POST /api/v1/datasets/{name}/ingest` — Ingest documents
- `GET /api/v1/datasets` — List datasets
- `DELETE /api/v1/datasets/{name}` — Delete dataset

### Search
- `POST /api/v1/search/vector` — Vector similarity search
- `POST /api/v1/search/fts` — Full-text search
- `POST /api/v1/search/hybrid` — Hybrid RRF-fused search

### RAG
- `POST /api/v1/rag/query` — RAG question answering
- `POST /api/v1/rag/stream` — Streaming RAG responses
""")


def gen_markdown_chinese_lit() -> None:
    _write(FIXTURES_DIR / "chinese_literature.md", """\
# 平凡的世界

## 人物关系

孙少安是孙玉厚的长子，双水村的生产队长。他与田润叶青梅竹马，但最终娶了贺秀莲为妻。少安勤劳踏实，带领全村人办砖厂致富。

孙少平是孙玉厚的次子，与哥哥性格不同，他渴望走出农村，追求精神世界的丰富。他在煤矿工作，与田晓霞相恋。田晓霞在一次洪水中牺牲，少平在悲痛中继续奋斗。

田晓霞是田福军的女儿，新闻记者。她与孙少平在高中相识，两人的感情跨越了城乡界限。晓霞勇敢善良，最终在抗洪报道中殉职。

## 主题分析

### 奋斗与命运

路遥通过孙家兄弟的对比，展现了中国改革开放时期农村青年的命运选择。少安代表扎根乡土的务实派，少平代表追求理想的漂泊者。两种选择没有对错之分，都体现了面对命运的不屈精神。

### 爱情与牺牲

少安与润叶的爱情因现实压力而分离，少平与晓霞的爱情因意外而终止。路遥用悲剧性的爱情描写，展现了理想与现实的永恒冲突。

### 社会变迁

小说以1975年至1985年的中国农村为背景，记录了从人民公社到家庭联产承包责任制的历史转变。生产队、公社、粮站等时代符号构成了丰富的历史画卷。
""")


def gen_jsonl_documents() -> None:
    docs = [
        {"id": "doc_001", "title": "Introduction to Vector Databases", "category": "database",
         "content": "Vector databases store data as high-dimensional vectors, enabling similarity search. Unlike traditional databases that use exact matches, vector databases use approximate nearest neighbor (ANN) algorithms to find similar items efficiently."},
        {"id": "doc_002", "title": "HNSW Algorithm Explained", "category": "algorithm",
         "content": "Hierarchical Navigable Small World (HNSW) is a graph-based ANN algorithm. It builds a multi-layer graph where each layer is a navigable small world graph. Search starts from the top layer and navigates down, achieving O(log N) search time."},
        {"id": "doc_003", "title": "RAG Architecture Patterns", "category": "architecture",
         "content": "Retrieval-Augmented Generation combines retrieval and generation. The retriever finds relevant documents, which are then used as context for the language model. Key patterns include: query rewriting, hybrid search (vector + keyword), re-ranking, and chunking strategies."},
        {"id": "doc_004", "title": "DuckDB Analytics Features", "category": "database",
         "content": "DuckDB is an embedded analytical database optimized for OLAP workloads. It supports window functions, CTEs, spatial extensions, and direct Parquet/CSV querying. DuckDB's columnar engine achieves high compression ratios and fast aggregations."},
        {"id": "doc_005", "title": "Lance Format Benefits", "category": "storage",
         "content": "Lance is a columnar data format designed for ML workloads. It supports automatic versioning, vector search indexes, and efficient updates. Unlike Parquet, Lance allows row-level operations without rewriting the entire file."},
        {"id": "doc_006", "title": "GraphRAG vs Vector RAG", "category": "architecture",
         "content": "GraphRAG enhances traditional RAG by incorporating knowledge graph relationships. While vector RAG finds semantically similar text chunks, GraphRAG can traverse entity relationships to provide multi-hop reasoning. This is especially valuable for questions involving complex relationships."},
        {"id": "doc_007", "title": "MinIO Object Storage", "category": "storage",
         "content": "MinIO is an S3-compatible object storage server. It supports erasure coding for data protection, lifecycle policies for automated tiering, and server-side encryption. In data lake architectures, MinIO stores raw documents, processed data, and backup snapshots."},
        {"id": "doc_008", "title": "Sentence Transformers", "category": "embedding",
         "content": "Sentence Transformers (SBERT) produce fixed-size vector representations of text. Models like all-MiniLM-L6-v2 (384D) and bge-large-en-v1.5 (1024D) balance quality and speed. Multi-lingual models support cross-lingual retrieval without translation."},
    ]
    _write(FIXTURES_DIR / "documents.jsonl", "\n".join(json.dumps(d, ensure_ascii=False) for d in docs))


def gen_csv_dataset() -> None:
    rows = [
        ["id", "title", "category", "abstract", "year"],
        ["1", "Attention Is All You Need", "transformer", "Introduces the transformer architecture based on self-attention mechanisms", "2017"],
        ["2", "BERT: Pre-training of Deep Bidirectional Transformers", "language_model", "Bidirectional encoder representations from transformers for NLP tasks", "2018"],
        ["3", "GPT-3: Language Models are Few-Shot Learners", "language_model", "175 billion parameter autoregressive language model with few-shot learning", "2020"],
        ["4", "DALL-E: Creating Images from Text", "multimodal", "Generative model that creates images from text descriptions", "2021"],
        ["5", "CLIP: Learning Transferable Visual Models", "multimodal", "Contrastive language-image pre-training for zero-shot image classification", "2021"],
        ["6", "PaLM: Scaling Language Modeling with Pathways", "language_model", "540 billion parameter model using Pathways system for efficient training", "2022"],
        ["7", "LLaMA: Open and Efficient Foundation Language Models", "language_model", "Collection of foundation models ranging from 7B to 65B parameters", "2023"],
        ["8", "Qwen Technical Report", "language_model", "Large language model series with strong multilingual capabilities", "2023"],
        ["9", "GraphRAG: Unlocking LLM Discovery", "knowledge_graph", "Uses knowledge graphs to enhance LLM reasoning on private datasets", "2024"],
        ["10", "RAG: Retrieval-Augmented Generation", "architecture", "Combines retrieval systems with generative models for knowledge-grounded generation", "2020"],
        ["11", "DuckDB: An Analytical In-Process Database", "database", "Embeddable analytical database for fast OLAP queries on large datasets", "2019"],
        ["12", "Apache Arrow: A Cross-Language Development Platform", "infrastructure", "Columnar in-memory data format for zero-copy data exchange", "2016"],
    ]
    _write(FIXTURES_DIR / "research_papers.csv", "\n".join(",".join(row) for row in rows))


def gen_mixed_content() -> None:
    _write(FIXTURES_DIR / "mixed_content.txt", """\
=== SECTION 1: TECHNICAL OVERVIEW ===

Arrow Lake v1.2 Release Notes

This release includes the following major improvements:
- Kreuzberg PDF parser (Rust-core, 91+ format support)
- TurboOCR GPU acceleration with circuit breaker
- Knowledge Graph integration via HugeGraph
- GraphRAG pipeline for multi-hop reasoning
- PaddleOCR as default OCR backend

=== SECTION 2: CONFIGURATION ===

# arrow_lake.yaml
storage:
  backend: local
  base_uri: ./data

llm:
  provider: ollama
  model: qwen3.5:9b
  api_base: http://localhost:11434

hugegraph:
  enabled: true
  host: localhost
  port: 8089

=== SECTION 3: API USAGE ===

# Create a dataset
curl -X POST http://localhost:8000/api/v1/datasets/my_data/ingest \\
  -H "X-API-Key: my-secret-key" \\
  -H "Content-Type: application/json" \\
  -d '{"file_paths": ["./docs/report.pdf"]}'

# Search
curl -X POST http://localhost:8000/api/v1/search/hybrid \\
  -H "Content-Type: application/json" \\
  -d '{"query": "machine learning algorithms", "top_k": 10}'

=== SECTION 4: 数据统计 ===

Total processed documents: 15,234
Average chunks per document: 12.5
Search latency (p99): 45ms
Embedding generation rate: 2,800 chunks/sec
Knowledge graph entities: 89,421
Knowledge graph relations: 234,567
""")


def gen_short_documents() -> None:
    """Generate very short documents for edge case testing."""
    _write(FIXTURES_DIR / "short_single_sentence.txt", "This is a very short document with only one sentence.")
    _write(FIXTURES_DIR / "empty_sections.txt", """
Section 1:


Section 2:

Some content here.

Section 3:



End of document.
""".strip())


def gen_multilingual() -> None:
    _write(FIXTURES_DIR / "multilingual.txt", """\
# Multilingual Test Document

## English
Natural language processing (NLP) is a field of artificial intelligence that focuses on the interaction between computers and humans using natural language.

## 中文
自然语言处理是人工智能的一个分支，研究计算机与人类自然语言之间的交互。

## 日本語
自然言語処理は、コンピュータと人間の自然言語の相互作用に焦点を当てた人工知能の分野です。

## 한국어
자연어 처리는 컴퓨터와 인간의 자연어 상호작용에 중점을 둔 인공 지능 분야입니다.

## Français
Le traitement du langage naturel est un domaine de l'intelligence artificielle qui se concentre sur l'interaction entre les ordinateurs et les langues humaines.
""")


def main() -> None:
    print("Generating test document fixtures...")
    gen_plain_text_en()
    gen_plain_text_zh()
    gen_markdown_tech()
    gen_markdown_chinese_lit()
    gen_jsonl_documents()
    gen_csv_dataset()
    gen_mixed_content()
    gen_short_documents()
    gen_multilingual()
    count = len(list(FIXTURES_DIR.glob("*")))
    print(f"Generated {count} fixture files in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
