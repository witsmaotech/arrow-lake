"""PII 分级自动建议(v1.11.6.5,搁置 W2 #3 复活)。

确定性内容扫描(零模型依赖、理由可解释):列内容模式匹配(身份证含 GB11643
校验位/手机号/银行卡 Luhn/邮箱/车牌/详细地址)+ 列名语义(地址/坐标/联系人
列)+ 自由文本列提示 → 四档建议(public/internal/confidential/restricted)
+ 逐条证据(列/模式/命中率/脱敏样本)。

产品锚:分级登记不校验(v1.11.5 W2 #4),corpus 导出分级-脱敏绑定校验
(W2 #5)建立在人工申报上——本模块给治理面提供证据侧。红线:建议≠自动写入
(登记不校验原则不变);docling/GLiNER 模型面按产品锚准则裁掉(中文质量
未验证+模型成本,硬 PII 正则已确定性覆盖;触发条件:文档型中文人名/住址
检测需求实测出现)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)

__all__ = ["suggest_classification"]

# 采样与判定阈值(小保守:命中即低阈值也计入理由,档位聚合另有量门槛)
_SAMPLE_ROWS = 500          # 每列扫描行数上限(前 N 行采样)
_HIT_RATIO_THRESHOLD = 0.01  # 内容命中占比达此值视为有效证据
_HIT_ABS_THRESHOLD = 3       # 或绝对命中数达此值
_FREE_TEXT_AVG_LEN = 50      # 平均长度超此值的字符串列视为自由文本列
_MAX_SAMPLES = 3             # 每条证据最多带回的脱敏样本数
# M3(v1.11.6.6):单值截断——PII 模式最长 ~30 字符,截断零检出损失;文档型
# chunk 列单值几十-几百 KB,全值正则 7 趟 ×500 行可耗尽 120s run_sync。
_MAX_VALUE_CHARS = 4096

# severity → 建议档位(high>medium>low 取最高;零证据 → public)
_SEVERITY_ORDER = ("high", "medium", "low")
_SEVERITY_TIER = {"high": "restricted", "medium": "confidential", "low": "internal"}


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _id_card_checksum_ok(v: str) -> bool:
    """GB11643 mod 11-2 校验位(身份证 FP 极低:结构+校验双约束)。"""
    if len(v) != 18:
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_map = "10X98765432"
    try:
        total = sum(int(v[i]) * weights[i] for i in range(17))
    except ValueError:
        return False
    return check_map[total % 11] == v[17].upper()


@dataclass(frozen=True)
class _Pattern:
    """内容模式:正则结构 + 可选校验器(身份证/银行卡)。"""

    key: str
    label: str
    severity: str
    regex: re.Pattern[str]
    validator: Any = None  # Callable[[str], bool] | None


# 身份证:结构(地区码+生日段+顺序码)+ 校验位;手机号:1[3-9] 段词边界;
# 银行卡:16-19 位数字 + Luhn(雪花/时间戳长数字的 Luhn 偶命中 ~9%,故只授
# medium 并在理由标注启发式);邮箱/固话 low;车牌/详细地址 medium。
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        key="id_card", label="身份证号(校验位通过)", severity="high",
        regex=re.compile(
            r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}"
            r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9Xx])"
        ),
        validator=_id_card_checksum_ok,
    ),
    _Pattern(
        key="bank_card", label="疑似银行卡号(Luhn 启发式)", severity="medium",
        regex=re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
        validator=_luhn_ok,
    ),
    _Pattern(
        key="phone", label="手机号", severity="medium",
        regex=re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    _Pattern(
        key="plate", label="车牌号", severity="medium",
        regex=re.compile(
            r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
            r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[挂学警港澳]"
        ),
    ),
    _Pattern(
        key="address", label="详细地址(路/街+门牌)", severity="medium",
        regex=re.compile(
            r"[^,。;、\s]{0,15}(?:路|大道|街|巷)[0-9一二三四五六七八九十百千]+号"
            r"(?:[^,。;、\s]{0,12}(?:栋|幢|单元|室|楼))?"
        ),
    ),
    _Pattern(
        # M3:局部部/域标签按 RFC 5321 上界(64/63)——无界 `+` 在无 @ 的长
        # 字母串上 O(n²) 回退(实测 4KB 截断值 ×500 行仍 15s),有界后线性。
        key="email", label="邮箱", severity="low",
        regex=re.compile(
            r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+"
        ),
    ),
    _Pattern(
        key="landline", label="固定电话", severity="low",
        regex=re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)"),
    ),
)

# 列名语义(弱证据,免采样即得;casefold 匹配)。负面清单先行排除:单位/部门
# 名(org_name/unit_name)不是自然人 PII,防 'name' 语义误吞。
_COLUMN_EXCLUDE = re.compile(
    r"org|unit|company|dept|group|team|tenant|product|file|image|video|url|id$"
    r"|^id$|uid|uuid|task|job|trace|request|version|hash|token|key",
    re.IGNORECASE,
)
_COLUMN_SEMANTICS: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"id_?card|identity|sfz|身份证|证件", re.IGNORECASE),
        "id_card_name", "身份证号列(列名)", "high",
    ),
    (
        re.compile(r"bank|card_?no|卡号|账号", re.IGNORECASE),
        "bank_name", "银行卡/账号列(列名)", "medium",
    ),
    (
        re.compile(r"longitude|latitude|^lon$|^lat$|coord|经度|纬度|坐标", re.IGNORECASE),
        "geo_name", "坐标列(位置敏感)", "medium",
    ),
    (
        re.compile(r"address|addr|street|district|路名|街道|地址|住址", re.IGNORECASE),
        "address_name", "地址/区划列(列名)", "medium",
    ),
    (
        re.compile(r"phone|mobile|tel|电话|手机|联系", re.IGNORECASE),
        "phone_name", "电话/联系人列(列名)", "low",
    ),
    (
        re.compile(r"(^|_)(name|person|contact)(_|$)|姓名|联系人", re.IGNORECASE),
        "person_name", "姓名/联系人列(列名)", "low",
    ),
    (
        re.compile(r"e?mail|邮箱|邮件", re.IGNORECASE),
        "email_name", "邮箱列(列名)", "low",
    ),
)


@dataclass(frozen=True)
class _Evidence:
    column: str
    key: str
    label: str
    severity: str
    hits: int = 0
    hit_ratio: float | None = None  # 列名证据无内容命中率
    masked_samples: tuple[str, ...] = ()


def _mask(value: str) -> str:
    """脱敏样本:保留前 4 后 2,短值更保守。样本仅用于人工核对列内容。"""
    if len(value) <= 8:
        return value[0] + "***"
    return f"{value[:4]}***{value[-2:]}"


def _column_semantic(column: str) -> _Evidence | None:
    if _COLUMN_EXCLUDE.search(column):
        return None
    for pat, key, label, severity in _COLUMN_SEMANTICS:
        if pat.search(column):
            return _Evidence(column=column, key=key, label=label, severity=severity)
    return None


def _scan_string_column(column: str, values: list[str]) -> list[_Evidence]:
    """对一列字符串值跑全部内容模式(命中即低门槛也记录,档位聚合再定量)。"""
    found: list[_Evidence] = []
    for p in _PATTERNS:
        hits = 0
        samples: list[str] = []
        for v in values:
            m = p.regex.search(v)
            if m is None:
                continue
            if p.validator is not None and not p.validator(m.group(0)):
                continue
            hits += 1
            if len(samples) < _MAX_SAMPLES:
                samples.append(_mask(m.group(0)))
        if hits:
            found.append(
                _Evidence(
                    column=column, key=p.key, label=p.label, severity=p.severity,
                    hits=hits, hit_ratio=round(hits / len(values), 4),
                    masked_samples=tuple(samples),
                )
            )
    return found


def _aggregate_tier(evidences: list[_Evidence]) -> str:
    """取最高档:内容证据需达量(比例或绝对数),列名证据恒有效。"""

    def _qualified(ev: _Evidence) -> bool:
        if ev.hit_ratio is None:  # 列名语义证据
            return True
        return ev.hits >= _HIT_ABS_THRESHOLD or (ev.hit_ratio or 0) >= _HIT_RATIO_THRESHOLD

    for sev in _SEVERITY_ORDER:  # high → low,首个命中的档位即建议
        if any(ev.severity == sev and _qualified(ev) for ev in evidences):
            return _SEVERITY_TIER[sev]
    return "public"


def suggest_classification(
    storage: Any,
    dataset_name: str,
    *,
    sample_rows: int = _SAMPLE_ROWS,
    table: str | None = None,
    allowed_columns: Any = None,
    contract_hints: Any = None,
) -> dict[str, Any]:
    """扫描数据集列内容/列名 → 四档分级建议 + 可解释理由(只读,零写入)。

    Args:
        storage: LanceStorageManager(经 ``lake._get_storage()``)。
        dataset_name: 单表数据集名(容器表 ``?table=`` 语义不在本建议面)。
        sample_rows: 每列扫描行数上限(前 N 行;行数不足则全量)。
        table: 容器数据集内表名(透传 read_dataset)。
        allowed_columns: 调用者可见列集(**小写**,H-3 列级 ACL 交集;
            None=无限制)。列名语义与内容扫描均只对允许列生效;受限视角
            下可见字符串列空集=零列采样(不回落全列)。
        contract_hints: 契约字段语义(M11,``{列: "identifier"|"person"}``;
            None=无契约)。标识列/自然人型列各作 LOW 证据(免采样)。

    Returns:
        ``{suggested_tier, engine, scanned_rows, reasons[], hints[]}``;
        reasons 按严重度降序,含脱敏样本。
    """
    allowed_lower = (
        frozenset(c.lower() for c in allowed_columns)
        if allowed_columns is not None
        else None
    )

    def _visible(col: str) -> bool:
        return allowed_lower is None or col.lower() in allowed_lower

    lt = storage.open_dataset(dataset_name, table=table)
    schema = lt.schema
    # 大表安全采样:走 Lance scanner limit(在存储层截断),只物化字符串列
    # ×N 行(read_dataset 全量加载后 slice 对 107M 行表会 OOM)。
    string_cols = [
        f.name for f in schema
        if (pa.types.is_string(f.type) or pa.types.is_large_string(f.type))
        and _visible(f.name)
    ]
    if allowed_lower is None:
        # 收敛(性能 L-3):无 ACL 且无字符串列 → 空表短路。历史行为是
        # columns=None 物化全部列 500 行(含 1024-2560 维向量列 ≈ 2-5MB)
        # 然后一处不用——零字符串列本就无可扫内容。
        scan_cols: list[str] | None = string_cols
    else:
        scan_cols = string_cols  # 受限视角:空集=零列(不回落全列)
    if scan_cols is None or scan_cols:
        data = (
            lt.to_lance()
            .scanner(columns=scan_cols, limit=max(1, sample_rows))
            .to_table()
        )
    else:
        data = pa.table({})  # 受限且零可见字符串列:无内容可扫(scanned_rows=0)

    evidences: list[_Evidence] = []
    hints: list[str] = []
    # 列名语义:全 schema 生效(数值列如经纬度无需内容即可作证;受限视角
    # 只对允许列作证)
    for field in schema:
        if not _visible(field.name):
            continue
        semantic = _column_semantic(field.name)
        if semantic is not None:
            evidences.append(semantic)
    # 契约字段语义(M11):identifier=业务标识列(跟踪面),type 含 person/
    # 自然人=自然人载体列——各作 LOW 证据(列不在 schema 时跳过,契约可能
    # 先于/滞后于物理 schema)
    _contract_labels = {
        "identifier": ("contract_identifier", "业务标识列(契约 identifier)"),
        "person": ("contract_person", "自然人载体列(契约 type)"),
    }
    if contract_hints:
        schema_names = {f.name for f in schema}
        for col, kind in dict(contract_hints).items():
            if col not in schema_names or not _visible(col):
                continue
            key_label = _contract_labels.get(str(kind))
            if key_label is None:
                continue
            evidences.append(
                _Evidence(column=col, key=key_label[0], label=key_label[1], severity="low")
            )
    # 内容扫描 + 自由文本提示:仅字符串列采样(string_cols 为空时 scanner 回
    # 落全列,此处再按类型过滤防数值列进正则)。追踪类系统标识列(uid/hash/
    # 时间戳 id)内容扫描跳过——系统生成 id 不是自然人 PII 载体,且长数字
    # id 撞银行卡 Luhn 的偶命中率 ~9%(live 实证 lpg_danger.uid 45/500);
    # 真 PII 藏在用户输入列(备注/描述),仍全量检出。
    for field in data.schema:
        if not (
            pa.types.is_string(field.type) or pa.types.is_large_string(field.type)
        ):
            continue
        if _COLUMN_EXCLUDE.search(field.name):
            continue
        # M3:入口一次截断(内容扫描 + 自由文本 avg-len 共用截断值)
        values = [
            v[:_MAX_VALUE_CHARS]
            for v in data.column(field.name).to_pylist()
            if v is not None
        ]
        if not values:
            continue
        evidences.extend(_scan_string_column(field.name, values))
        avg_len = sum(len(v) for v in values) / len(values)
        if avg_len >= _FREE_TEXT_AVG_LEN:
            hints.append(
                f"{field.name} 为自由文本列(平均 {int(avg_len)} 字符),建议人工复核"
            )

    tier = _aggregate_tier(evidences)
    order = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
    reasons = sorted(evidences, key=lambda e: order[e.severity])
    return {
        "suggested_tier": tier,
        "engine": "rule_based",
        "scanned_rows": data.num_rows,
        "reasons": [
            {
                "column": ev.column,
                "evidence": ev.key,
                "label": ev.label,
                "severity": ev.severity,
                "hits": ev.hits,
                "hit_ratio": ev.hit_ratio,
                "masked_samples": list(ev.masked_samples),
            }
            for ev in reasons[:20]
        ],
        "hints": hints[:5],
        "note": "规则建议≠自动写入;分级登记不校验原则不变(采纳走 PUT classification)",
    }
