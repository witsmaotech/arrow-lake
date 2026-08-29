"""Annotation loop (MS4) configuration — v1.11.3.

旁路段配置:LS CE 地址/PAT(D1:凭据走 env,不入 compose)、派发批量与
采样帽。红线:annotation 链路不进 ingest/query 热路径——此配置只被
``/api/v1/annotation/*`` 与 annotation/ 旁路模块读取。
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class AnnotationConfig(BaseModel):
    """Label Studio dispatch 面配置。

    Attributes:
        ls_url: LS CE base URL(如 ``http://label-studio:8080``)。空 →
            dispatch 503(未部署 profile 时明确拒绝而非静默失败)。
        ls_api_token: PAT(D1)。空 → dispatch 503。
        default_sample_total: 一次派发默认采样行数。
        max_sample_total: 单次派发行数上限(防误把全表派去标注)。
        candidate_pool_cap: 采样候选池帽(读表后截断;大表数据集的
            dispatch 内存护栏——已知限制,分页 scan 留试点后迭代)。
        import_batch_size: LS import API 分批大小(限速 1 req/s)。
    """

    ls_url: str = ""
    ls_api_token: str = ""
    default_sample_total: int = 20
    max_sample_total: int = 200
    candidate_pool_cap: int = 5000
    import_batch_size: int = 50

    @field_validator("default_sample_total", "max_sample_total", "import_batch_size")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator("candidate_pool_cap")
    @classmethod
    def _cap_floor(cls, v: int) -> int:
        if v < 100:
            raise ValueError(f"candidate_pool_cap must be >= 100, got {v}")
        return v
