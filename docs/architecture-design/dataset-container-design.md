# 多表数据集容器设计(dataset = schema 容器)— DR14

> 版本:v1.1 · 日期:2026-08-25 · 状态:**已批准**(方向路线 C + 版本落位 v1.11.0.1,均用户 2026-08-25 决策)
> 版本基线:v1.11.0.1(规划落地版本,与 DR13 数据集契约合并实施)
> 上游:Master Plan DR14;DR13(表侧本体·数据集契约)的物理层延伸
> 关联:MS2 F2.1/F2.2(标识规范/语义对齐)以容器为地基;业务文档(Object Set/操作语义/DQM 2.0)的领域闭合诉求
> 实施计划:`docs_offline/v1.11.0.1-version-plan.md`(契约+容器合并任务分解)

---

## 0. 决策背景

用户方向决策(2026-08-25):**一个数据集要能装多张结构化表,在单数据集内完整建设某个领域业务场景的本体规则数据,而不是散布在多个单表数据集里**。三条候选路线中选定路线 C(真·多表数据集容器);同日二次决策:**与 DR13 契约合并进 v1.11.0.1 实施**(不再单列 v1.12.0)。

合并实施的依据:契约与容器互为地基——容器契约的自然形态就是"一域一档、每表一节",分两个版本会多一次 V011 语义升级与摄入接线返工;一次发布列车、一次版本对齐;MS2(F2.1/F2.2)一次消费两者。代价是 v1.11.0.1 从「本体规则补充批」(约 1.5-2 周)扩展为「契约+容器批」(约 4-5 周),本版定位与工期随之更新。

### 路线对比记录(为什么是 C)

| 路线 | 形态 | 结论 |
|---|---|---|
| A. DR13 原样 | 多数据集各立契约 + `references.to_dataset` 跨数据集引用 | 领域本体散布 N 份契约,无单一治理入口;跨数据集引用完整性依赖时点(目标集须先存在),乱序到达脆弱 |
| B. 域级契约 | 物理一集一表,契约文档一域一档 | **契约形态被 C 吸收**(C 的容器契约=同款 YAML);单独做 B 会多一次范式转换 |
| **C. 多表数据集容器(选定)** | dataset = schema 容器,N 张 Lance 表 + 域契约 + 文档表 + KG 图同容器 | 领域闭合、引用完整性别容器内闭环、MS2 Object Set 天然地基、Gravitino schema 语义对齐;代价=全链路寻址改造 |

## 1. 现状:一数据集一表是全链路原子假设

已核实的代码级耦合面(不是单点限制):

| 层 | 耦合点 | 位置 |
|---|---|---|
| 存储 | dataset CRUD 全部 `name → _get_dataset_path(name)` 单路径;create/append/read/restore/evolve schema 按单表 | `ingest/_storage_crud.py`(create_dataset/append_dataset/read_dataset) |
| OLAP | `_register_dataset(conn, dataset_name, …)` 表名=数据集名,**6 处调用**(olap/metadata/graph 端点);scan_mode_overrides 键=数据集名;熔断器 Redis 键 `arrow-lake:lance_cb:{dataset}` | `query/olap.py:206/294/466/549/607/646/788`、`query/scan_breaker.py:33` |
| RBAC | grants/acl/deny 全部按数据集键(`grants:{dataset}`);row_filter/visible_columns 单表语义;rbac_sql 源头改写按"表名=数据集名"解析 | `system_db/stores/rbac.py:113-137`、`quality/rbac_sql.py` |
| 摄入 | 13 种 source + 10 异步端点,目标=单数据集;质量门/死信 `{ds}_dead_letter`/embed 回填(`arrow-lake:embed_status:{ds}`)/任务去重 guard 全按数据集名 | `api/routers/async_tasks.py`、`quality/`、`api/tasks.py` |
| 元数据 | Gravitino sync 遍历 `lake.list_datasets()` 把每个数据集当一张表;catalog store(system_db)按数据集登记 | `catalog/gravitino_sync.py:19` |
| KG/KA | per-dataset 图 `kg_{ds}`、KA dump `he_ka_base_dir/{ds}/ka/` | `_lake_kg.py`、`knowledge_graph/` |
| 治理 | 系统表 `sys_*`、级联删除(cascade 清 KA/KG/catalog/RBAC/模板绑定)、backup/restore 按数据集 | `_system_tables.py`、`ops/backup.py` |
| 契约(DR13) | V011 `scope = 数据集名`,契约按单 object_class 起草 | v1.11.0.1 规划(未开工,同版本吸收) |

## 2. 目标形态

```
数据集(容器,= DuckDB schema 语义 = Gravitino schema)
├── tables/
│   ├── segments        # 管段(Lance 表)
│   ├── stations        # 场站
│   ├── valves          # 阀门
│   └── measurements    # 监测点
├── contract            # 域契约(一域一档,N 个表节;DR13 契约的容器形态)
├── kg_{ds}             # 域级 KG 图(现有粒度不变,正好=域)
└── ka/{ds}/ka/         # KA dump(不变)
```

- **寻址**:`{dataset}.{table}` 两段名。SQL 侧映射到 DuckDB `schema.table` 二段名;REST/SDK `/datasets/{ds}/tables/{table}`。
- **旧数据集零迁移**:现有单表数据集视为"含一张隐式默认表"(表名=数据集名),物理路径不动,所有既有 API/SQL/语义透明兼容。
- **文档侧不动**:documents/chunks/embedding 数据集继续单默认表;KG 图、KA dump 按容器粒度天然就是域级。

## 3. 设计决策

| # | 决策 | 理由 | 否决的备选 |
|---|---|---|---|
| D1 | **寻址 = 两段名 `{ds}.{table}`,DuckDB 侧 dataset→schema 映射** | DuckDB 原生 `schema.table` 二段名;无新语法;与 Gravitino metalake→catalog→schema→table 对齐 | flat 引号名 `"ds.table"`(易错、rbac_sql 解析歧义)/前缀拼接 `ds__table`(不透明) |
| D2 | **存储布局:新多表集 `{base_uri}/{ds}/{table}/`;旧单表集原路径保留** | 零物理迁移;容器化是登记行为不是搬数据 | 存量集物理搬迁(高风险无收益) |
| D3 | **容器身份由控制面登记**(system_db catalog 记 `is_container` + 表清单),不靠目录猜测 | minio 前缀列举有最终一致性问题;catalog 是权威 | 目录结构嗅探(歧义:单表集本身也是目录) |
| D4 | **ACL 分层:数据集级=默认,表级=覆盖,deny 优先** | 域内表常有不同敏级别(监测数据 vs 台账);现 RBAC store 键扩展 `acl:{ds}::{table}` | 只有容器级(粒度倒退)/只有表级(每表重配,运营负担) |
| D5 | **摄入目标带表名;门禁/死信/embed 回填/熔断/scan overrides 全部按 `{ds}.{table}` 键** | 粒度自然下沉;不同表不同 scan 模式反而更精准(ontime 类大表单独 native) | 容器级统一配置(丢精度) |
| D6 | **多表集上 `FROM {ds}` 无表名 → 422**(可配 `default_table` 豁免);单表集上裸名继续工作 | 显式优于隐含;避免静默选表 | 静默默认表(查询语义漂移) |
| D7 | **Gravitino:dataset→schema、table→table 的 sync 映射升级**(现把每数据集当表) | 语义对齐;tag→ACL 同步随表列走 | 保持 dataset=table 映射(多表集在 catalog 失真) |
| D8 | **任务去重 guard 放宽到表粒度**:同容器不同表可并发摄入 | 现按数据集名拦"进行中的摄入任务",多表下会误拦 | 保持容器级互斥(并发倒退) |

### 与 DR13 契约的关系(同版本实施,合流形态)

契约与容器在 v1.11.0.1 内合并交付,契约模型从第一天就按**表节建模**:

```yaml
dataset: gas_network            # 容器
tables:
  segments:
    object_class: 管段
    identifier: {column: seg_id, pattern: "GAS.SEGMENT.{区域}.{序列}"}
    columns: [...]
  stations:
    object_class: 场站
    columns: [...]
references:                     # 容器内引用省 to_dataset;跨容器写全
  - {from: segments.station_id, to: stations.id}
  - {from: segments.owner_org, to_dataset: gas_orgs, to_column: org_id}
```

- 单表数据集契约 = 恰好一节(顶层单数旧形态自动包装为默认表节,V011 兼容)。
- 分级语义、SQL/Arrow 编译、quality gate 挂载、版本链/Diff 全部沿 DR13 已批设计,仅 scope 语义放宽为"数据集(容器)"。

## 4. 分阶段交付(S1→S3,并入 v1.11.0.1 任务分解)

| 阶段 | 范围 | 验收 | 估计 |
|---|---|---|---|
| **S1 容器地基** | 存储层 `_get_dataset_path(ds, table)` 两段化;catalog 登记 `is_container`+表清单;摄入目标带表名(csv/parquet/json/clickhouse/sql 结构化源先行);单表集零迁移回归(全量现有测试绿) | 建容器集+多表摄入+按表读;旧集全语义不变 | ~1 周 |
| **S2 查询与权限** | OLAP 二段名注册(6 处)+ rbac_sql 二段名解析(大小写语义沿承)+ ACL 分层 + breaker/scan overrides/embed 键升级 + D6/D8 | `FROM ds.table` 全链路(RBAC 改写/行过滤/列 ACL/deny)工作;ontime 级大表 per-table scan mode 生效 | ~1-1.5 周 |
| **S3 面与治理** | console(数据集详情=表列表/表 schema/查询表选择器/摄入表名)+ Gravitino sync 映射 + backup/级联删除容器化 + 契约聚合视图(DR13 消费) | console 全流程;Gravitino schema 对齐;容器删除级联 N 表 | ~1 周 |

**红线沿承**:查询热路径语义不变(只是寻址多一段);quality/ontology 门禁机制不变;不做域级事务/快照(触发条件=跨表一致性硬需求出现)。

## 5. 风险与回退

| 风险 | 缓解 |
|---|---|
| 全链路回归面大(13 摄入源/console 8+ 页/cookbook/SDK) | S1 的"单表集零迁移"是硬验收;每阶段独立可回退(容器是增量能力,不动存量) |
| 标识符冲突:`{ds}.{table}` 二段名 vs 现有单表名引用 | D6 显式 422;rbac_sql 解析器补二段名测试(大小写语义沿 v1.10.7 纪律) |
| cookbook 示例全按单表写 | 增量章节演示容器;存量示例不动(单表兼容=设计目标) |
| 并发摄入/心跳/僵尸回收按数据集名的隐式假设 | D8 表粒度化;reap_orphaned_tasks 逻辑不变(键升级) |
| Gravitino sync 映射变更期间 tag→ACL 断档 | sync 升级原子切;断档窗口 tag 保状态语义沿承(v1.10.7 纪律:拉取失败不回收) |
| 容器层引入后 sys_ 表/内部表命名空间混淆 | `_system_tables.py` 判定升级为"容器+表"两级;sys_ 保护语义不变 |
| 契约+容器合并发布的范围膨胀 | S1-S3 每阶段独立验收;若 v1.11.0.1 周期内容器未收口,可降级为"容器仅 shadow 能力(建/读可用,查询走显式表名)"发版,S2/S3 顺延——契约交付不被容器阻塞 |
