"""F4.1/F4.4 — Label Studio REST client(v1.11.3 MS4 W1.3)。

旁路模块。认证(D1,W5 live 校准):legacy token 直接 ``Authorization:
Token`` 头(refresh 端点在 LS 1.13.1 部署 404);仅 401 才走 PAT refresh
→ ``Bearer``(短时 access,失效重换一次)。内网服务显式零代理直连
(api 容器 HTTP_PROXY 会把容器名打上代理 → 502,W5 实证)。HTTP 失败
统一 ``LSClientError`` 携带 status + body 摘要(排障面,不吞)。

零新依赖(version-plan 红线):urllib.request;``opener`` 可注入供测试
mock(FakeOpener 模式,与 tests/unit/annotation/test_dispatch_client.py 对应)。
LS 是 transient 工作区——本 client 不持有任何业务状态,重启即重建。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from arrow_lake.annotation.masking import apply_annotation_masking
from arrow_lake.annotation.preannotate import to_ls_prediction
from arrow_lake.annotation.sampler import SampleBudget, SampledRow, sample_rows

__all__ = [
    "DispatchOutcome",
    "LSClient",
    "LSClientError",
    "run_dispatch",
    "stable_row_id",
]

# row_id 稳定键(review C1):内容 hash 而非池序号——re-dispatch 时池位移
# (新数据/死信增长)不再让 r5 指向不同源行,ADL 溯源跨批次稳定。
# 同文本不同行会共用 row_id:标注语义上同文本=同样本,可接受。
_ROW_ID_LEN = 12


def stable_row_id(text: str | None, index: int) -> str:
    """行 → 稳定标识:text 非空取 sha1 前 12 hex;空文本退回池序号。"""
    if text:
        return "h" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:_ROW_ID_LEN]
    return f"r{index}"

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_BODY_SNIPPET = 200


class LSClientError(RuntimeError):
    """LS REST 调用失败(网络/HTTP/解码);message 带 status 与 body 摘要。

    ``status`` 供调用方区分语义(404=project 没了可重建;5xx/网络=瞬时,
    重建会孤儿化已有 tasks——review C2)。网络错误 status=None。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LSClient:
    """Label Studio REST API 薄封装(project CRUD + task import)。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = _TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        if opener is None:
            # LS 是内网服务:显式零代理直连(urllib 默认读 HTTP_PROXY 环境变量
            # → api 容器代理打内网容器名 → 502;W5 live 实证)。零新依赖。
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open
        else:
            self._opener = opener
        self._access: str | None = None

    # --- low-level ---------------------------------------------------------

    def _open(self, method: str, path: str, body: Any, auth: str | None) -> Any:
        url = f"{self._base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", auth)
        try:
            return self._opener(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:_BODY_SNIPPET].decode("utf-8", "replace")
            raise LSClientError(
                f"LS {method} {path} → {exc.code}: {detail}", status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise LSClientError(f"LS {method} {path} unreachable: {exc.reason}") from exc

    @staticmethod
    def _read_json(resp: Any) -> Any:
        raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LSClientError(f"LS returned non-JSON body: {raw[:_BODY_SNIPPET]!r}") from exc

    def _refresh_access(self) -> str:
        resp = self._open("POST", "/api/token/refresh", {"refresh": self._token}, auth=None)
        payload = self._read_json(resp)
        access = (payload or {}).get("access")
        if not access:
            raise LSClientError("LS token refresh returned no access token")
        self._access = str(access)
        return self._access

    def _api(self, method: str, path: str, body: Any = None) -> Any:
        """认证调用:legacy ``Token`` 头直连(W5 live 实证,refresh 端点
        在 LS 1.13.1 部署 404);仅当 401 才走 PAT refresh→``Bearer``。"""
        auth = f"Token {self._token}"
        if self._access:
            auth = f"Bearer {self._access}"
        resp = self._open(method, path, body, auth)
        if getattr(resp, "status", 200) == 401:
            logger.debug("LS 401 on %s %s — refreshing access token", method, path)
            self._refresh_access()
            resp = self._open(method, path, body, f"Bearer {self._access}")
        return self._read_json(resp)

    # --- projects ----------------------------------------------------------

    def create_project(self, title: str, labeling_config: str) -> dict[str, Any]:
        """POST /api/projects LS 1.13 returns 201 with the project record."""
        out = self._api(
            "POST", "/api/projects", {"title": title, "label_config": labeling_config}
        )
        return out if isinstance(out, dict) else {}

    def get_project(self, project_id: int) -> dict[str, Any]:
        out = self._api("GET", f"/api/projects/{project_id}")
        return out if isinstance(out, dict) else {}

    def list_projects(self) -> list[dict[str, Any]]:
        out = self._api("GET", "/api/projects?page_size=100")
        if isinstance(out, dict) and isinstance(out.get("results"), list):
            return out["results"]
        return out if isinstance(out, list) else []

    # --- tasks -------------------------------------------------------------

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /api/projects/{id}/import — tasks 可内嵌静态 predictions(S4)。"""
        out = self._api("POST", f"/api/projects/{project_id}/import", tasks)
        return out if isinstance(out, dict) else {}

    def list_tasks(self, project_id: int, *, page: int = 1, page_size: int = 100) -> Any:
        return self._api(
            "GET", f"/api/tasks?project={project_id}&page={page}&page_size={page_size}"
        )

    def export_tasks(self, project_id: int) -> list[dict[str, Any]]:
        """GET /api/projects/{id}/export(JSON,全量含 annotations)。

        W5 live 实证:``/api/tasks`` 列表视图**裁剪 annotations/predictions
        数组**(只留计数)——回收必须走 export(设计 §6.1 原口径)。
        """
        out = self._api(
            "GET",
            f"/api/projects/{project_id}/export?exportType=JSON&download_all_tasks=true",
        )
        if isinstance(out, dict):
            tasks = out.get("tasks")
            return tasks if isinstance(tasks, list) else []
        return out if isinstance(out, list) else []


# --------------------------------------------------------------------------- #
# dispatch 全链(W2.4):采样 → 脱敏 → 预标注 → LS import                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DispatchOutcome:
    """一次派发的结构化结果(audit payload 的数据面)。"""

    project: str
    dataset: str
    ls_project_id: int
    dispatched: int
    skipped: int = 0
    strategies: dict[str, int] = field(default_factory=dict)


async def run_dispatch(
    *,
    project: str,
    dataset: str,
    labeling_config: str,
    ls_project_id: int | None,
    rows: list[dict[str, Any]],
    text_column: str,
    total: int,
    budget: SampleBudget,
    quality_scores: Mapping[str, float] | None,
    embeddings: Mapping[str, Sequence[float]] | None,
    dead_row_ids: Sequence[str] | None,
    committee: Sequence[str] | None,
    generalize_rules: Sequence[tuple[str, str]],
    entity_names: Sequence[str],
    hmac_key: bytes | None,
    ls_client: LSClient,
    extractor: Any,
    bind_ls_project: Callable[[str, int], None] | None,
    import_batch_size: int = 50,
) -> DispatchOutcome:
    """采样→脱敏→HE 预标注(脱敏文本上,span 自洽)→LS import。

    依赖全注入(LSClient/extractor/bind 回调)——mock 即全链 e2e;行是
    候选池 dicts,``row_id = "r{i}"``(池内序号,回收对账 join 键)。
    LS 懒绑定:``ls_project_id`` 缺/失效 → create + ``bind_ls_project``
    回写注册表(transient 重绑,W1.4 红线)。单行 HE 失败 → 空 prediction
    仍派发(S4 预测是建议非强制);LS import 失败上抛(router → 502)。
    """
    texts = [str(r.get(text_column) or "").strip() for r in rows]
    row_ids = [stable_row_id(t, i) for i, t in enumerate(texts)]
    row_index = {rid: i for i, rid in enumerate(row_ids)}
    sampled: list[SampledRow] = sample_rows(
        total=total, row_ids=row_ids,
        quality_scores=quality_scores, embeddings=embeddings,
        dead_row_ids=dead_row_ids, committee_disagreements=committee,
        budget=budget,
    )

    # 懒绑定:无 id 或 LS 侧确认 404 → (重)创建并回写。瞬时错误
    # (5xx/网络)原样上抛——盲目重建会孤儿化已有 tasks(review C2)。
    if ls_project_id is not None:
        try:
            ls_client.get_project(ls_project_id)
        except LSClientError as exc:
            if getattr(exc, "status", None) != 404:
                raise
            logger.info("annotation.dispatch: LS project %s gone(404) — recreating", ls_project_id)
            ls_project_id = None
    if ls_project_id is None:
        rec = await _maybe_async(
            ls_client.create_project(project, labeling_config))
        ls_project_id = int(rec.get("id", 0))
        if not ls_project_id:
            raise LSClientError("LS create_project returned no id")
        if bind_ls_project is not None:
            bind_ls_project(project, ls_project_id)

    tasks: list[dict[str, Any]] = []
    skipped = 0
    picks = [p for p in sampled
             if str(rows[row_index[p.row_id]].get(text_column) or "").strip()]
    skipped = len(sampled) - len(picks)

    # M16(四维 review):预标注 LLM 并发(Semaphore 8)——此前逐行串行,
    # 200 行 × 1-3s = 5-10 分钟;qwen-turbo 完全可承受并发。gather 保序。
    sem = asyncio.Semaphore(8)

    async def _predict_one(pick: Any) -> dict[str, Any]:
        row = rows[row_index[pick.row_id]]
        text = str(row.get(text_column) or "").strip()
        masked = apply_annotation_masking(
            text, generalize_rules=generalize_rules,
            entity_names=entity_names, hmac_key=hmac_key,
        )
        async with sem:
            try:
                result = await extractor.extract(masked)
            except Exception:  # 单行 HE 失败容错(空 prediction)
                logger.warning(
                    "annotation.dispatch: extract failed for %s", pick.row_id)
                result = None
        prediction = to_ls_prediction(result) if result is not None else {
            "model_version": "hyper-extract", "result": [],
        }
        return {
            "data": {"text": masked, "row_id": pick.row_id,
                     "strategy": pick.strategy},
            "predictions": [prediction],
        }

    tasks = list(await asyncio.gather(*[_predict_one(p) for p in picks]))

    for start in range(0, len(tasks), max(1, import_batch_size)):
        await _maybe_async(
            ls_client.import_tasks(
                ls_project_id, tasks[start:start + max(1, import_batch_size)])
        )

    strategies: dict[str, int] = {}
    for t in tasks:
        st = t["data"]["strategy"]
        strategies[st] = strategies.get(st, 0) + 1
    return DispatchOutcome(
        project=project, dataset=dataset, ls_project_id=ls_project_id,
        dispatched=len(tasks), skipped=skipped, strategies=strategies,
    )


async def _maybe_async(value: Any) -> Any:
    """同步 client(测试替身/urllib 封装)与 async client 通吃。"""
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
