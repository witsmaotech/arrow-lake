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
        ls_public_url: 浏览器可达的 LS 地址(如 ``http://127.0.0.1:8085``)。
            ``ls_url`` 是 api 容器内网络地址,console 前端拿去打不开;此
            字段供 list_projects 响应外露给前端渲染「打开 LS」链接,空 =
            回退 ls_url(宿主直通部署时两者可同值)。
        ls_api_token: PAT(D1)。空 → dispatch 503。
        default_sample_total: 一次派发默认采样行数。
        max_sample_total: 单次派发行数上限(防误把全表派去标注)。
        candidate_pool_cap: 采样候选池帽(读表后截断;大表数据集的
            dispatch 内存护栏——已知限制,分页 scan 留试点后迭代)。
        import_batch_size: LS import API 分批大小(限速 1 req/s)。
        require_masking: True 时 dispatch 必须带脱敏配置(entity_names 或
            generalize_rules),否则 422——PII 数据集的强制门(review S1)。
        webhook_secret: LS webhook 共享密钥;空 = webhook 端点整体禁用
            (默认安全,review S2)。LS 侧配置 webhook URL 时带 ?secret=。
    """

    ls_url: str = ""
    ls_public_url: str = ""
    ls_api_token: str = ""
    require_masking: bool = False
    webhook_secret: str = ""
    default_sample_total: int = 20
    max_sample_total: int = 200
    candidate_pool_cap: int = 5000
    import_batch_size: int = 50
    adjudicate_min_annotators: int = 2
    """裁决最少标注人数(H4):2=双标注一致才 approved(默认);单人标注
    试点设 1(单标注直接 approved——docstring 一直宣称的风险表缓解,
    四维 review 后真正接线)。recover_one/project_status 均透传。"""

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
