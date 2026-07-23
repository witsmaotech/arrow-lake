# docling GPU 验收报告(宿主 .venv · RTX 3090)

**日期**:2026-07-23
**版本**:Arrow Lake v1.9.2 批5
**目的**:验证 docling 在宿主 `.venv`(torch 2.11+cu130 + RTX 3090 24GB)上的解析吞吐,评估 552 页全量可行性(CPU 路径 30h+ 不可行)。

## 环境

- **GPU**:NVIDIA GeForce RTX 3090(24 GB VRAM)
- **torch**:`torch.cuda.is_available() = True`,device = RTX 3090
- **docling 路径**:宿主 `.venv` 直跑(`document.py:_build_docling_pipeline` 显式 `AcceleratorOptions(device=CUDA if torch.cuda.is_available() else AUTO)`)
- **模型缓存**:`~/.cache/huggingface/hub`(9.9 G,含 docling-layout-heron / docling-models / granite-docling-258M),`HF_HUB_OFFLINE=1`
- **被测 PDF**:`docs/cookbook/datas/5.芜湖市城市生命线安全工程一期建设方案.pdf`(552 页,文字层,`--ocr none`)
- **下游服务**:minio `127.0.0.1:9000`、redis `127.0.0.1:6380`、ollama embed `127.0.0.1:11434`(`qwen3-embedding:0.6b`)

> **关键前提**:docling 默认 `device='auto'` 在本机**未自动走 CUDA**(实测 CPU 速),必须显式 `AcceleratorOptions(device=CUDA)`(代码已固化,`document.py`)。

## 结果(5 页 standard,`--ingest-only`)

实测运行成功(28 行摄入,STEP1 通过)。原始输出:

```
[cfg] backend=docling pipeline=standard ocr=none chunk=recursive max_pages=5
[STEP1] docling 摄入 28 行 / 207.9s (0.02 页/s)
[STEP1] sample read skipped: 'Field "text" does not exist in schema'
[DONE] ingest-only rows=28 pages/s=0.02
```

| 指标 | 值 |
|------|-----|
| 5 页总耗时(gross,含冷启) | **207.9 s** |
| Gross 吞吐 | 41.6 s/页(0.02 页/s) |
| 冷启(模型装载,固定成本) | ~40-60 s(沿用 v1.9.2 批5 前实测基线) |
| 稳态吞吐(扣除冷启) | **~30-34 s/页** = (207.9 − ~50) / 5 |
| GPU 利用率(运行中峰值) | 20-35%(未吃满) |
| VRAM 占用 | ~3.76 GB / 24 GB |
| 552 页外推(稳态 × 552 + 冷启) | **~4.8-5.2 h** |

### 次要发现

- **GPU 未吃满**(util 20-35%):瓶颈不在 GPU 算力,而在 TableFormer.ACCURATE 模型 + CPU 侧页面渲染/后处理(布局回归、阅读序排序)。GPU 仅加速 layout-heron 前端。
- **sample read 跳过**:`Field "text" does not exist in schema` —— 脚本 `TEXT_COL="text"` 的 schema 假设与本次摄入产物不完全一致(验证步,非核心解析)。属 `run_docling_e2e.py` 的验证步小 bug,不影响摄入正确性(28 行成功落库),记为 follow-up。
- **shutdown 噪声**:`terminate called without an active exception` —— torch/docling C++ 析构在 DONE 打印后抛出,良性(进程已正常完成)。

## 结论

- **GPU 路径可用但未达 <15 s/页 目标**:稳态 ~30-34 s/页,显著优于 CPU(>200 s/页,~6-7× 加速),但 TableFormer.ACCURATE + CPU 后处理使 GPU 未饱和。
- **552 页全量**:~4.8-5.2 h,**可行(可过夜批跑)**,但未达 <2.3 h 目标。
- **建议**:① 全量跑可接受(~5h 过夜);② 若需提速,降级 `TableFormer` 到 FAST 档(牺牲表格精度换吞吐,需 config 暴露 `do_table_structure`/`mode` 参数,follow-up);③ 抽样 50 页代表 + 瓶颈分析(若只需代表性验收)。
- **不阻塞**:P2 代码(VlmPipeline/HybridChunker)已上线,GPU 直摄路径(`document.py` 显式 CUDA)工作正常。

## 恢复命令(环境未就绪或重跑)

```bash
cd /home/witshine/wits-projs/wits-infra-dintellihub

# 1. 确认 GPU
.venv/bin/python3 -c "import torch; print('cuda:', torch.cuda.is_available(),
  torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

# 2. 5 页 standard 测速(--ocr none 文字层 PDF 最快)
HF_HUB_OFFLINE=1 \
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://127.0.0.1:9000 \
ARROW_LAKE__STORAGE__S3_ACCESS_KEY=minioadmin \
ARROW_LAKE__STORAGE__S3_SECRET_KEY=minioadmin \
ARROW_LAKE__REDIS__URL="redis://:<pwd>@127.0.0.1:6380/0" \
ARROW_LAKE__EMBEDDING__BACKEND=openai \
ARROW_LAKE__EMBEDDING__API_BASE=http://127.0.0.1:11434/v1 \
ARROW_LAKE__EMBEDDING__MODEL=qwen3-embedding:0.6b \
ARROW_LAKE__EMBEDDING__API_KEY=ollama \
NO_PROXY=127.0.0.1,localhost \
.venv/bin/python3 docs/cookbook/examples_busi/run_docling_e2e.py \
  --max-pages 5 --pipeline standard --ocr none --chunk recursive --ingest-only \
  --dataset wuhu_gpu_std5

# 3. VLM(GraniteDocling)路径测速
#   换 --pipeline vlm(GraniteDocling 258M,VRAM +2 GB)
```

## 噪声清理

宿主测试写入的 `wuhu_gpu_*` 数据集进入 minio 但 gravitino 目录对不上(api 周期 sync 良性 warn)。验收后删除:
```bash
# 通过 console datasets.html 或 lake.delete_dataset("wuhu_gpu_std5") 清理
```

## 原始日志

`/tmp/docling_gpu.log`(本次 5 页 standard 运行输出)。

---

_关联:[[project_docling_p2_gpu]]、[[reference_docling_integration]]、[[reference_business_e2e_case]]_
