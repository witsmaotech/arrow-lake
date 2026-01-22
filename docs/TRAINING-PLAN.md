# DIntelliHub 技术培训计划

**制定日期**: 2026-01-22
**培训负责人**: Winston (架构师)
**培训对象**: 全体开发团队
**培训周期**: Week 0-1 (两周)

---

## 📚 培训目标

### 主要目标
1. **快速上手**: 团队在2周内掌握Daft、LanceDB、LanceDB基础
2. **技能提升**: 建立团队的技术共识和知识体系
3. **实战能力**: 能够完成Sprint 1的基础设施搭建任务
4. **持续学习**: 建立长期学习机制

### 成功标准
- [ ] 团队成员能够独立完成Daft数据处理任务
- [ ] 团队成员能够独立完成LanceDB向量操作
- [ ] 团队成员掌握Kubernetes基础操作
- [ ] 培训材料完整，可供新成员学习

---

## 📅 培训时间表

### Week 0: 基础学习 (自学+准备)

**时间**: Week 0 (1月22-28日)
**方式**: 自学 + 文档准备 + 讨论

#### Day 1 (1月22日) - 项目启动
- [x] 了解项目背景和目标
- [ ] 阅读项目文档 (PRD、架构文档)
- [ ] 熟悉技术栈

#### Day 2-3 (1月23-24日) - 核心技术自学
- [ ] Daft基础学习 (2-3小时)
- [ ] LanceDB基础学习 (2-3小时)
- [ ] Kubernetes基础学习 (2-3小时)

#### Day 4-5 (1月25-26日) - 深入学习
- [ ] Daft高级功能学习
- [ ] LanceDB索引和性能优化
- [ ] Kubernetes实战练习

---

### Week 1: 实战培训 (边做边学)

**时间**: Week 1 (1月29日-2月4日)
**方式**: 实战 + 指导 + 讨论

#### Day 1-2: K8s集群部署实践
- [ ] EKS/GKE集群部署
- [ ] Helm Chart配置
- [ ] Pod部署和配置

#### Day 3-4: Daft数据处理实践
- [ ] Daft ETL pipeline开发
- [ ] 数据摄取和处理
- [ ] AI函数集成

#### Day 5: LanceDB向量检索实践
- [ ] 向量索引构建
- [ ] 向量搜索测试
- [ ] 性能调优

---

## 📚 核心技术课程

### 课程1: Daft数据处理引擎 ⭐⭐⭐⭐⭐

#### 学习目标
- 掌握Daft的基本API和操作
- 理解Daft的懒执行和优化
- 能够使用Daft进行ETL开发

#### 学习内容

**Day 1: 基础入门** (2-3小时)
- 官方文档: https://docs.daft.ai/en/stable/
- 内容:
  - Daft安装和快速开始
  - DataFrame API基础操作
  - 数据读取 (CSV, Parquet, JSON)
  - 数据过滤和转换

**练习**:
```python
import daft

# 读取数据
df = daft.read_csv("s3://bucket/data.csv")
df = df.filter(df["column"] > 100)
df.show()

# 写入数据
df.write_parquet("s3://bucket/output.parquet")
```

**Day 2: 进阶功能** (2-3小时)
- 多模态数据处理
- AI函数集成
- 懒执行优化
- 分布式执行 (Ray)

**练习**:
```python
# AI函数集成
df = df.with_column("embedding", df["text"].embed.openai())

# 懒执行优化
df = df.filter(df["score"] > 0.5).lazy()
df = df.collect()
```

#### 学习资源
- 官方文档: https://docs.daft.ai/en/stable/
- GitHub: https://github.com/Eventual/daft
- 示例代码: https://github.com/Eventual/daft/tree/main/python/examples/

---

### 课程2: LanceDB向量数据库 ⭐⭐⭐⭐⭐

#### 学习目标
- 掌握LanceDB的基本操作
- 理解向量索引的原理和使用
- 能够进行向量检索和性能优化

#### 学习内容

**Day 1: 基础入门** (2小时)
- 官方文档: https://lancedb.github.io/lancedb/
- 内容:
  - LanceDB安装和快速开始
  - 数据表创建和管理
  - 向量数据导入
  - 向量搜索基础

**练习**:
```python
import lancedb

# 连接数据库
db = lancedb.connect("~/.lancedb")

# 创建表
table = db.create_table("documents", data=[
    {"id": "doc1", "text": "hello world", "vector": [0.1, 0.2, ...]}
])

# 向量搜索
results = table.search("hello", limit=5).to_pandas()
```

**Day 2: 进阶功能** (2小时)
- 向量索引构建 (HNSW, IVF_PQ)
- 混合检索
- 性能调优
- LangChain集成

**练习**:
```python
# 索引配置
table.create_index(
    "vector",
    index_type="IVF_PQ",
    num_partitions=256,
    nsubvectors=16
)

# 混合检索
results = (
    table.search("query")
    .limit(20)
    .where("category", "AI")
    .rerank(reranker)
    .limit(5)
)
```

#### 学习资源
- 官方文档: https://lancedb.github.io/lancedb/
- GitHub: https://github.com/lancedb/lancedb
- 教程: https://lancedb.github.io/lancedb/tutorials/

---

### 课程3: Kubernetes基础 ⭐⭐⭐⭐

#### 学习目标
- 理解Kubernetes基本概念
- 掌握Pod、Deployment、Service的配置
- 能够使用kubectl管理集群

#### 学习内容

**Day 1: 基础入门** (2小时)
- 官方文档: https://kubernetes.io/docs/tutorials/
- 内容:
  - K8s架构和基本概念
  - Pod创建和管理
  - Deployment配置
  - Service配置
  - ConfigMap和Secret

**练习**:
```yaml
# Pod示例
apiVersion: v1
kind: Pod
metadata:
  name: daft-processing
spec:
  containers:
  - name: processor
    image: daft-image:latest
    resources:
      requests:
        cpu: "4"
        memory: "16Gi"
      limits:
        cpu: "8"
        memory: "32Gi"
```

**Day 2: 实战练习** (2小时)
- 部署应用到K8s
- 配置健康检查
- 配置服务发现
- 监控和日志

#### 学习资源
- 官方文档: https://kubernetes.io/docs/
- Kubernetes基础教程: https://kubernetes.io/docs/tutorials/kubernetes-basics/
- Minikube本地练习

---

## 🎯 培训方式

### 自学为主
- **学习时间**: 每天下午2-3小时
- **学习资料**: 官方文档、教程、示例代码
- **练习**: 动手实践，完成练习题

### 讨论和指导
- **Daily Standup**: 每天15分钟，分享学习心得
- **技术讨论会**: 每周1小时，深入讨论技术难点
- **问题解答**: 随时通过Slack解答技术问题

### 实战项目
- **Sprint 1实战**: Week 1完成K8s集群部署
- **POC验证**: Week 2完成Daft+LanceDB POC

---

## 📋 学习检查清单

### Daft学习检查清单

Week 0 (自学):
- [ ] 完成官方教程
- [ ] 运行示例代码
- [ ] 理解核心概念 (DataFrame, 懒执行, 分布式)
- [ ] 完成练习题

Week 1 (实战):
- [ ] 部署Daft环境
- [ ] 创建数据处理pipeline
- [ ] 集成AI函数
- [ ] 完成小项目 (数据处理流程)

---

### LanceDB学习检查清单

Week 0 (自学):
- [ ] 完成官方教程
- [ ] 运行示例代码
- [ ] 理解索引原理 (HNSW, IVF_PQ)
- [ ] 完成练习题

Week 1 (实战):
- [ ] 部署LanceDB环境
- [ ] 创建向量表
- [ ] 构建向量索引
- [ ] 完成小项目 (向量检索系统)

---

### Kubernetes学习检查清单

Week 0 (自学):
- [ ] 完成官方教程
- [ ] 运行Minikube本地练习
- [ ] 理解基本概念 (Pod, Service, Deployment)
- [ ] 完成练习题

Week 1 (实战):
- [ ] 部署应用到K8s
- [ ] 配置健康检查
- [ ] 配置服务发现
- [ ] 完成小项目 (部署完整应用)

---

## 📚 学习资源汇总

### Daft资源
- 官方文档: https://docs.daft.ai/en/stable/
- GitHub: https://github.com/Eventual/daft
- 示例: https://github.com/Eventual/daft/tree/main/python/examples/
- 教程: https://docs.daft.ai/en/stable/learn/

### LanceDB资源
- 官方文档: https://lancedb.github.io/lancedb/
- GitHub: https://github.com/lancedb/lancedb
- 教程: https://lancedb.github.io/lancedb/tutorials/
- 示例: https://github.com/lancedb/lancedb/tree/main/examples/

### Kubernetes资源
- 官方文档: https://kubernetes.io/docs/
- 教程: https://kubernetes.io/docs/tutorials/kubernetes-basics/
- Minikube: https://minikube.sigs.k8s.io/docs/
- 最佳实践: https://kubernetes.io/docs/concepts/

---

## 🎓 培训评估

### 知识检查
- [ ] **Week 0结束**: 基础知识测试
- [ ] **Week 1结束**: 实战项目验收

### 实战能力评估
- [ ] 能够独立完成Daft数据处理任务
- [ ] 能够独立完成LanceDB向量操作
- [ ] 能够独立部署K8s应用
- [ ] 能够解决常见技术问题

---

## 💡 学习技巧

### 高效学习方法
1. **70/20/10法则**: 70%实践，20%阅读，10%交流
2. **边学边做**: 不要只看不动手
3. **做笔记**: 记录关键概念和问题
4. **及时提问**: 遇到问题及时求助

### 学习资源优先级
1. **官方文档** (最权威)
2. **示例代码** (最实用)
3. **教程和指南** (系统学习)
4. **社区讨论** (问题解答)

---

## 📊 培训时间分配

| 技术 | Week 0 | Week 1 | 总计 |
|------|--------|--------|------|
| **Daft** | 6小时 | 8小时 | 14小时 |
| **LanceDB** | 4小时 | 6小时 | 10小时 |
| **Kubernetes** | 4小时 | 8小时 | 12小时 |
| **总计** | 14小时 | 22小时 | 36小时 |

---

## 🎯 Week 0 学习目标

### 必须完成
- [ ] 完成Daft基础教程
- [ ] 完成LanceDB基础教程
- [ ] 完成Kubernetes基础教程
- [ ] 运行所有示例代码

### 期望完成
- [ ] 理解核心概念和架构
- [ ] 掌握基本API和操作
- [ ] 完成实战练习
- [ ] 准备好Sprint 1实战

---

## 📝 学习笔记模板

### 每日学习记录
```
## 学习记录 - [日期]

### 今日学习内容
- 技术: [Daft/LanceDB/K8s]
- 章节: [具体章节]
- 时间: [学习时长]

### 关键概念
- [概念1]: [理解]
- [概念2]: [理解]

### 遇到的问题
- [问题1]: [描述]
- [解决方案]: [方法]

### 明日计划
- [学习目标]
- [计划时间]
```

---

## 🚀 下一步行动

### 立即行动
1. [ ] 阅读官方文档
2. [ ] 运行示例代码
3. [ ] 加入Slack频道讨论

### 本周目标
1. [ ] 完成Daft基础学习
2. [ ] 完成LanceDB基础学习
3. [ ] 完成Kubernetes基础学习

### Week 1目标
1. [ ] 完成K8s集群部署
2. [ ] 完成Daft数据处理实战
3. [ ] 完成LanceDB向量检索实战

---

## 📧 支持和帮助

### 技术支持
- **架构师**: Winston
- **问题渠道**: Slack #datalake-dev
- **响应时间**: 工作时间 < 2小时

### 学习资源
- **Daft Slack**: https://daft-slack.vercel.app/
- **LanceDB Discord**: https://discord.gg/lancedb
- **Kubernetes Slack**: https://kubernetes.slack.com/

---

**培训状态**: 🟢 进行中
**开始日期**: 2026-01-22
**结束日期**: 2026-02-04

**让我们快速掌握核心技术栈，为Sprint 1做好准备！** 💪
