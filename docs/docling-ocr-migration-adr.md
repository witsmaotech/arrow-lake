# ADR: Docling + 多 OCR 后端 替代 kreuzberg 文档解析

- **日期**: 2026-07-03
- **状态**: Accepted（库内嵌实施中，2026-07-04）
- **实施更新 (2026-07-04)**: 集成方式从 docling-serve **sidecar 改为 Python SDK 库内嵌**（决策：更简单，单进程无 REST 跳转，无需额外容器）。RapidOCR ONNX 轻量、避开 paddlepaddle 安装坑，库内嵌代价小。
  - ✅ 代码落盘 + 验证通过：`ingest/document.py`（`_parse_docling` Python SDK + `DocumentConverter` 懒加载单例 + `_build_docling_pipeline` OCR 切换）、`config/_enums.py`（`OcrBackend.DOCLING` + `DoclingOcrEngine`）、`config/document.py`（docling 字段，无 endpoint）、`pyproject.toml`（`docling` extra）、`deploy/Dockerfile`（extras 加 docling）
  - ✅ compose sidecar 服务已回退（库内嵌不需要容器，`docker compose config` 通过）
  - ⏳ 待：重建镜像装 docling + 端到端验证（芜湖 PDF 文字版/扫描页 + Office）
  - 原 §5/§6 的 sidecar 设计保留作历史；实际以库内嵌为准（`from docling import DocumentConverter`，进程内调用，无 REST）
- **决策者**: wits_sunpw
- **相关**: v1.8.6 端到端测试（`docs/cookbook/examples_busi/`）、paddleocr 镜像集成搁置（task9 pending）
- **替代**: 当前 `arrow-lake:1.8.6-full` 内嵌 kreuzberg + paddleocr 方案

---

## 1. 背景与动机

v1.8.6 端到端测试暴露当前文档解析栈的结构性问题：

1. **镜像无 kreuzberg** → PDF 摄入靠宿主 `prepare_pdf.py`（pypdf）绕过，非产品能力
2. **手工塞 paddleocr 到镜像** → libGL 缺失 → 模型预下载 → `/app` 权限 → 镜像膨胀到 16.3GB，连环踩坑
3. **kreuzberg 只认 PDF/图片** → Office/HTML/邮件/代码等格式全无
4. **OCR 单一后端、无 fallback** → 扫描件质量差即失败
5. **解析与湖仓强耦合** → 升级解析能力必须重建 arrow-lake 镜像

核心矛盾：**把"文档解析+OCR"这个独立关注点硬塞进 arrow-lake 单体镜像**。

## 2. 决策

**采用 Docling 作为文档解析框架，RapidOCR + EasyOCR + Tesseract 三后端按语言自动切换**，以 **docling-serve sidecar 容器**形态接入 arrow-lake，arrow-lake 镜像回归湖仓本职（瘦身）。

- 框架：[Docling](https://github.com/docling-project/docling)（IBM，MIT 许可，★62k）
- 中文 OCR：**RapidOCR**（= PaddleOCR PP-OCRv4 模型的 ONNX/Torch 轻量推理版，**无需 paddlepaddle**）
- 多语言 OCR：**EasyOCR**（显式 `lang` 列表可控）
- 英文备选：**Tesseract**

## 3. 备选方案对比

| 方案 | 许可 | 中文 OCR | 格式 | 资源 | 结论 |
|------|------|---------|------|------|------|
| **kreuzberg + paddleocr**（当前 1.8.6-full） | 自定义 | paddlepaddle 重 | PDF/图片 | 16.3GB 镜像 | ❌ 维持现状 |
| **MinerU** | **AGPL-3.0**（商用传染） | VLM 顶级 | PDF/Office | 重型/GPU | ❌ 许可红线（除非买商业许可） |
| **xberg** | MIT | Tesseract/PaddleOCR | 96 格式 | 轻 | ⚠ v1 新线，成熟度低 |
| **Marker** | GPL-3.0 | - | PDF | 中 | ❌ GPL 传染 |
| **Docling + RapidOCR/EasyOCR** | **MIT + Apache-2.0** | PaddleOCR 模型（轻量） | PDF/Office/HTML/图片/邮件 | 中 | ✅ **选定** |

**选定理由**：许可商用零顾虑（MIT）、中文 OCR 质量与 PaddleOCR 一致（同模型）但甩掉 paddlepaddle 安装包袱、多格式覆盖广、IBM 企业级稳定、与 RAG 契合（DoclingDocument + hybrid chunking）。

## 4. OCR 引擎切换策略（核心设计）

### 4.1 三后端定位

| 后端 | 弱项 | 强项 | 适用 |
|------|------|------|------|
| **RapidOCR** | [Issue #3569](https://github.com/docling-project/docling/issues/3569)：忽略 `lang` 设置，**强制中文模型** | PP-OCRv4 中文模型，ONNX 轻量，无 paddlepaddle | **纯中文 / 中英混排（中文主）**（#3569 在此场景歪打正着） |
| **EasyOCR** | 中文略逊 RapidOCR | 多语言原生，`lang=["ch_sim","en","ja","ko"]` 显式可控 | **多语言混排 / 非中文为主** |
| **Tesseract** | 中文一般 | 英文最优，稳定 | **英文为主** / 纯英文扫描件 |

### 4.2 自动切换矩阵（`ocr_engine="auto"`）

```
┌─────────────────────────────┐
│  文档输入（PDF/Office/图片）  │
└──────────────┬──────────────┘
               ▼
   ┌───────────────────────────┐
   │ 语言检测（Docling 前端 /   │
   │ langdetect，取主语言）     │
   └─────────────┬─────────────┘
                 ▼
   ┌───────────────────────────┐
   │  中文系(zh/中英混排)?      │
   │   → RapidOCR（PP-OCRv4）  │
   │  多语言(日/韩/混排非中)?   │
   │   → EasyOCR(lang=显式)    │
   │  英文为主?                 │
   │   → Tesseract(eng)        │
   └───────────────────────────┘
```

| 检测主语言 | 选定后端 | lang 参数 |
|-----------|---------|-----------|
| `zh`（含中英混排，中文为主） | **RapidOCR** | 忽略（强制中文，#3569） |
| `ja` / `ko` / 多语言混排 | **EasyOCR** | `["ch_sim","en","ja"]` 等 |
| `en` 为主 | **Tesseract** | `["eng"]`（备 EasyOCR） |
| 未知 / 无法检测 | **RapidOCR**（默认，中文项目主体） | — |

### 4.3 手动覆盖

`DocumentConfig.ocr_engine` 字段允许显式指定，跳过自动检测：

| 值 | 行为 |
|----|------|
| `"auto"`（默认） | 按语言检测自动选（上表） |
| `"rapidocr"` | 强制中文模型（即使文档非中文，受 #3569 限制） |
| `"easyocr"` | 强制多语言（配合 `ocr_languages`） |
| `"tesseract"` | 强制 Tesseract |
| `"none"` | 不做 OCR（文字版 PDF 直接走文本层，最快） |

### 4.4 关键配置（Docling Python API）

```python
from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions, RapidOcrOptions, EasyOcrOptions, TesseractOcrOptions,
)

def build_pipeline(engine: str, langs: list[str], gpu: bool) -> PdfPipelineOptions:
    if engine == "rapidocr":
        ocr = RapidOcrOptions(backend="torch" if gpu else "onnx")
    elif engine == "easyocr":
        ocr = EasyOcrOptions(lang=langs or ["ch_sim", "en"], use_gpu=gpu)
    elif engine == "tesseract":
        ocr = TesseractOcrOptions(lang=langs or ["eng"])
    else:  # none
        return PdfPipelineOptions(do_ocr=False)
    return PdfPipelineOptions(do_ocr=True, ocr_options=ocr)

# 中文文档
conv_zh = DocumentConverter(format_options={"pdf": {"pipeline_options":
    build_pipeline("rapidocr", ["ch_sim"], gpu=False)}})
# 多语言文档
conv_multi = DocumentConverter(format_options={"pdf": {"pipeline_options":
    build_pipeline("easyocr", ["ch_sim","en","ja"], gpu=True)}})
```

## 5. 架构（docling-serve sidecar，推荐）

```
┌──────────────────────────────────────────────────────────┐
│  deploy_arrow-lake-net                                    │
│                                                            │
│  ┌──────────────┐    HTTP/REST    ┌────────────────────┐  │
│  │ arrow-lake   │ ──────────────▶ │ docling-serve      │  │
│  │ (瘦身 ~11GB) │  POST /v1alpha/ │ (sidecar ~2GB)     │  │
│  │              │   convert/      │                    │  │
│  │ Lake.ingest_ │   document      │ Docling + RapidOCR │  │
│  │  documents   │                 │   + EasyOCR        │  │
│  │ backend=     │ ◀────────────── │   + Tesseract      │  │
│  │  "docling"   │  DoclingDocument│                    │  │
│  └──────────────┘   /Markdown     │ cache 卷持久化:    │  │
│         │                          │  /app/.cache       │  │
│         ▼                          │  (模型 + 内容哈希) │  │
│   Lance/MinIO                       └────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**为何 sidecar 优于库内嵌**：
- arrow-lake 镜像不污染（不装 docling/ocr 模型/libGL）
- OCR 引擎升级独立（不重建 arrow-lake）
- 模型缓存集中管理（docling-serve 卷）
- 水平扩展（多副本分摊 OCR 负载）

**库内嵌（备选）**：仅当 sidecar 网络延迟敏感时考虑，arrow-lake 镜像加 `docling rapidocr_onnxruntime easyocr`。

## 6. 实施计划（分 5 阶段）

### 阶段 1: 部署 docling-serve sidecar

`deploy/docker-compose.prod.yml` 新增服务：

```yaml
docling-serve:
  image: ghcr.io/docling-project/docling-serve:latest
  ports: ["127.0.0.1:8001:8001"]
  environment:
    - RUST_LOG=info
    - DOCLING_SERVE_MAX_UPLOAD_SIZE_MB=500
  volumes:
    - docling-cache:/app/.cache     # 模型 + 内容哈希缓存持久化
  deploy: { resources: { limits: { memory: 4g } } }
  healthcheck:
    test: ["CMD", "curl", "-sf", "http://localhost:8001/health"]
    interval: 30s
  restart: unless-stopped
volumes:
  docling-cache:
```

首次启动预热（下载 RapidOCR 中文 + EasyOCR 多语言模型到卷）：
```bash
docker exec deploy-docling-serve-1 curl -X POST http://localhost:8001/cache/warm
```

### 阶段 2: arrow_lake/ingest/document.py 加 docling backend

新增 `_parse_docling(file_path)`，HTTP 调 `docling-serve`：

```python
import httpx
from arrow_lake.config.document import DoclingBackendConfig

class DoclingParser:
    def __init__(self, cfg: DoclingBackendConfig):
        self.endpoint = cfg.endpoint  # http://docling-serve:8001
        self.engine = cfg.ocr_engine  # auto/rapidocr/easyocr/tesseract/none
        self.langs = cfg.ocr_languages

    def parse(self, file_path: str) -> ParsedDocument:
        # auto → 调 /detect 拿主语言 → 选后端
        engine, langs = self._resolve_engine(file_path)
        with open(file_path, "rb") as f:
            r = httpx.post(
                f"{self.endpoint}/v1alpha/convert/document",
                files={"files": f},
                data={"ocr_engine": engine, "ocr_langs": ",".join(langs)},
                timeout=300,
            )
        d = r.json()["results"][0]
        return ParsedDocument(
            text=d["content"], pages=[(p["page"], p["text"]) for p in d.get("pages", [])],
            page_count=d.get("page_count", 0), backend="docling",
        )

    def _resolve_engine(self, file_path):
        if self.engine != "auto":
            return self.engine, self.langs
        # 调 docling /detect 或 langdetect
        lang = self._detect_lang(file_path)
        if lang == "zh": return "rapidocr", ["ch_sim"]
        if lang in ("ja", "ko") or self._is_multilingual(): return "easyocr", self.langs or ["ch_sim","en","ja"]
        if lang == "en": return "tesseract", ["eng"]
        return "rapidocr", ["ch_sim"]  # 默认中文
```

### 阶段 3: DocumentConfig 加 backend 字段

```python
# arrow_lake/config/document.py
class OcrEngine(str, Enum):
    AUTO = "auto"; RAPIDOCR = "rapidocr"; EASYOCR = "easyocr"
    TESSERACT = "tesseract"; NONE = "none"

class DoclingBackendConfig(BaseModel):
    endpoint: str = "http://docling-serve:8001"
    ocr_engine: OcrEngine = OcrEngine.AUTO
    ocr_languages: list[str] = []

class DocumentConfig(BaseModel):
    # ... 现有字段
    backend: Literal["kreuzberg", "docling"] = "kreuzberg"  # 默认兼容，逐步切 docling
    docling: DoclingBackendConfig = DoclingBackendConfig()
```

### 阶段 4: arrow-lake 镜像瘦身回退

`deploy/Dockerfile` 改回（去掉 paddleocr/paddlepaddle/document extra 重型依赖）：

```dockerfile
# 回退到核心 extras（docling 在 sidecar，不在主镜像）
RUN uv pip install --no-cache-dir ".[fts,otel,rag,he]"
# 删除：document extra + paddleocr 模型预下载 + libGL/libsm6 等 cv2 依赖
# 仅保留 httpx（调 docling-serve REST，arrow-lake 已有）
```

镜像从 16.3GB → ~11GB。

### 阶段 5: 端到端验证清单

- [ ] docling-serve 启动 + health + 模型预热（RapidOCR 中文 + EasyOCR）
- [ ] 芜湖 PDF（文字版）：`backend=docling, ocr_engine=none` → 走文本层，对比 pypdf 输出（应含表格/版面）
- [ ] 芜湖 PDF 扫描页：`ocr_engine=auto` → 检测中文 → RapidOCR → 输出中文文本
- [ ] 英文 PDF：`auto` → Tesseract
- [ ] 多语言 PDF：`auto` → EasyOCR
- [ ] 手动覆盖：`ocr_engine=easyocr` 强制多语言
- [ ] Office 文档：DOCX/PPTX/XLSX 摄入（新能力，kreuzberg 无）
- [ ] run_e2e.py 跑通（STEP1 用 docling backend 替代 prepare_pdf.py）
- [ ] 性能：单页解析延迟（文字版 < 2s，OCR < 10s）

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| docling-serve 镜像/tag 不稳定 | sidecar 不可用 | 固定版本 tag（非 latest），加 health gate |
| RapidOCR #3569（强制中文） | 多语言文档识别错 | auto 检测非中文 → 切 EasyOCR；保留手动 `ocr_engine` 覆盖 |
| sidecar 网络延迟 | 单文档 +几百 ms | 批处理 `/convert/batch`；本地缓存命中走文本层（无 OCR） |
| Docling 版本升级 API 变更 | document.py 改造 | backend 抽象隔离，kreuzberg backend 保留作 fallback |
| 模型首次下载慢 | 首次启动久 | 阶段 1 预热 + 卷持久化（一次下载） |

## 8. 回退策略

- DocumentConfig.backend 默认仍 `"kreuzberg"`（兼容），docling 是新增可选
- 任何阶段失败，回退到当前 1.8.6-full（kreuzberg + paddleocr，文字版 PDF 可用）
- arrow-lake 镜像保留 `arrow-lake:1.8.6-full` tag 作为 fallback

## 9. 后续（可选增强）

- **docling-langchain 集成**：DoclingDocument → LangChain Document，与 Lake RAG 深度整合
- **Docling 表格导出**：TableFormer 结果直接入 Lake 的结构化表（OLAP）
- **URL 爬取**：Docling 支持 URL 输入，扩展 Lake 的数据源

---

## 10. P2 进阶能力（VlmPipeline + HybridChunker）— 已实现

**状态**：2026-07-05 落地（commit `feat(docling): P2 VlmPipeline + HybridChunker`）。

### 10.1 VlmPipeline（GraniteDocling，复杂版面/扫描件）

- **配置**：`DocumentConfig.docling_pipeline_type`（`DoclingPipelineType.STANDARD|VLM`）+
  `docling_vlm_preset`（默认 `granite_docling` = 258M DocTags 模型）。
- **运行时**：本地 `TransformersVlmEngineOptions`（零额外基础设施）。VLM 与标准流水线互斥，
  选中时独占 PDF/IMAGE 的 `pipeline_cls=VlmPipeline`。模型经 HF_HOME 持久卷加载（同 P0 离线路径）。
- **取舍**：CPU 上 ~100s/页（慢但可用），有 GPU 则快。生产高吞吐可后续切远程 API（vLLM/Ollama），
  仅改 `engine_options`——GraniteDocling 的 Ollama gguf 官方仍 TBA，现实远程路径是 vLLM。

### 10.2 HybridChunker（结构感知分块）

- **配置**：`ChunkStrategy.DOCLING_HYBRID` + `docling_chunk_tokenizer`（默认 `BAAI/bge-m3`，与嵌入对齐）。
- **契约**：`ParsedDocument.docling_doc` 透传 `DoclingDocument` 对象（仅 docling 后端）；
  `DocumentChunker.chunk(pages, *, docling_doc=)` 在 DOCLING_HYBRID 时直接吃 DoclingDocument，
  用 `HybridChunker.chunk(dl_doc=)` 做结构感知切分，`contextualize()` 输出带标题/题注的增强文本。
- **降级**：缺 DoclingDocument（非 docling 后端）→ 回退 RECURSIVE；docling extra 未装 → `_validate_strategy` 降级 RECURSIVE。
- **结构感知 chunk 无单一页码** → `page_number=0`（metadata-only 约定）。

### 10.3 测试

`tests/unit/ingest/test_docling_p2.py` 17 例：配置默认值、VLM builder preset/engine 装配、
VLM converter 用 VlmPipeline 作 pipeline_cls、HybridChunker dispatch + 降级。回归 723 测试全绿。
注意：`test_chunker_advanced.py` 用 `importlib.reload(chunker_mod)` 会重建 `Chunk` 类，测试中
`isinstance` 须读 `chunker_mod.Chunk`（模块字典当前值），不能用收集期 from-import 的旧引用。

---

## 决策记录

- **2026-07-03**: 基于 v1.8.6 端到端测试 + MinerU/Docling/xberg 横向评估（GitHub 数据 + Context7 + WebSearch），选定 Docling + RapidOCR/EasyOCR/Tesseract 方案。MinerU 因 AGPL-3.0 许可排除（除非采购商业许可）。xberg 因 v1 成熟度暂不选。paddleocr 直接集成因 paddlepaddle 安装复杂度排除，改用 RapidOCR（同模型轻量推理）。
- **2026-07-05**: P2 落地——VlmPipeline 选本地 Transformers（匹配 P0 离线风格，远程 API 留作后续）；HybridChunker 用 `ParsedDocument.docling_doc` 透传 DoclingDocument（最小契约改动，外科手术式接入现有 parse→chunk 链路）。
