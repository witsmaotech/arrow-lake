"""Cover missing lines in arrow_lake.quality.nemo_curator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.quality.nemo_curator import (
    NeMoCuratorFilter,
    NeMoDeduplicator,
    _aesthetic_heuristic,
    _nsfw_heuristic,
    _text_quality_heuristic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_with_text(*texts: str) -> pa.Table:
    return pa.table({"text_content": list(texts)})


def _table_with_img(**kw: object) -> pa.Table:
    return pa.table(kw)


# ---------------------------------------------------------------------------
# NeMoCuratorFilter._load_model branches
# ---------------------------------------------------------------------------


class TestLoadModel:
    def test_already_loaded(self) -> None:
        f = NeMoCuratorFilter()
        f._model = MagicMock()  # already loaded
        f._load_model()
        # no further action taken

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", False)
    def test_no_nemo_fallback(self) -> None:
        f = NeMoCuratorFilter()
        assert f._model is None
        f._load_model()
        assert f._using_fallback is True
        assert f._model is None

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_gpu_path(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_cls = MagicMock()
        model_inst = MagicMock()
        mock_cls.return_value = model_inst
        with patch.dict("sys.modules", {
            "torch": mock_torch,
            "nemo_curator": MagicMock(),
            "nemo_curator.filters": MagicMock(QualityClassifier=mock_cls),
        }):
            f = NeMoCuratorFilter(use_gpu=True)
            f._load_model()
            assert f._device == "cuda"
            model_inst.to.assert_called_once_with("cuda")

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_cpu_path(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_cls = MagicMock()
        with patch.dict("sys.modules", {
            "torch": mock_torch,
            "nemo_curator": MagicMock(),
            "nemo_curator.filters": MagicMock(QualityClassifier=mock_cls),
        }):
            f = NeMoCuratorFilter(use_gpu=True)
            f._load_model()
            assert f._device == "cpu"

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_import_error_fallback(self) -> None:
        f = NeMoCuratorFilter(use_gpu=True)
        # Patch torch to not exist so the import in _load_model fails
        with patch.dict("sys.modules", {"torch": None, "nemo_curator.filters": None}):
            f._load_model()
        assert f._using_fallback is True

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_runtime_error_raises(self) -> None:
        from arrow_lake.exceptions import QualityError
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_cls = MagicMock(side_effect=OSError("model missing"))
        with patch.dict("sys.modules", {
            "torch": mock_torch,
            "nemo_curator": MagicMock(),
            "nemo_curator.filters": MagicMock(QualityClassifier=mock_cls),
        }):
            f = NeMoCuratorFilter(use_gpu=True)
            with pytest.raises(QualityError):
                f._load_model()


# ---------------------------------------------------------------------------
# _compute_scores GPU path + inference fallbacks
# ---------------------------------------------------------------------------


class TestComputeScores:
    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_gpu_text_quality(self) -> None:
        f = NeMoCuratorFilter(classifiers=("text_quality",))
        f._using_fallback = False
        f._model = MagicMock()
        f._model.score.return_value = [0.8]
        f._device = "cpu"
        tbl = _table_with_text("hello")
        scores = f._compute_scores(tbl)
        assert "quality_text_score" in scores
        assert "quality_composite_score" in scores

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_gpu_nsfw(self) -> None:
        f = NeMoCuratorFilter(classifiers=("nsfw",))
        f._using_fallback = False
        f._model = MagicMock()
        f._model.score.return_value = [0.1]
        f._device = "cpu"
        tbl = _table_with_text("clean text")
        scores = f._compute_scores(tbl)
        assert "quality_nsfw_score" in scores

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_gpu_aesthetic(self) -> None:
        f = NeMoCuratorFilter(classifiers=("aesthetic",))
        f._using_fallback = False
        f._model = MagicMock()
        f._device = "cpu"
        tbl = pa.table({"image_width": [1920], "image_height": [1080]})
        scores = f._compute_scores(tbl)
        assert "quality_aesthetic_score" in scores

    def test_fallback_no_scores(self) -> None:
        f = NeMoCuratorFilter(classifiers=())
        f._using_fallback = True
        tbl = _table_with_text("hi")
        scores = f._compute_scores(tbl)
        assert "quality_composite_score" in scores
        assert len(scores["quality_composite_score"]) == 1


class TestRunGpuInference:
    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_inference_failure_fallback_text(self) -> None:
        f = NeMoCuratorFilter(classifiers=("text_quality",))
        f._model = MagicMock()
        f._model.score.side_effect = RuntimeError("GPU OOM")
        f._device = "cpu"
        texts = ["hello"]
        result = f._run_gpu_inference(texts, classifier_type="quality")
        assert len(result) == 1
        assert f._using_fallback is True

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_inference_failure_fallback_nsfw(self) -> None:
        f = NeMoCuratorFilter(classifiers=("nsfw",))
        f._model = MagicMock()
        f._model.score.side_effect = OSError("err")
        f._device = "cpu"
        texts = ["explicit xxx content"]
        result = f._run_gpu_inference(texts, classifier_type="nsfw")
        assert len(result) == 1

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_gpu_inference_with_nulls(self) -> None:
        f = NeMoCuratorFilter()
        f._model = MagicMock()
        f._model.score.return_value = [0.9]
        f._device = "cpu"
        texts = ["hello", None, "world"]
        result = f._run_gpu_inference(texts)
        assert len(result) == 3
        assert result[1] == 0.0  # None → 0.0

    def test_run_gpu_image_inference(self) -> None:
        f = NeMoCuratorFilter()
        result = f._run_gpu_image_inference([1920, None], [1080, None])
        assert len(result) == 2
        assert result[0] > 0.0
        assert result[1] == 0.0


# ---------------------------------------------------------------------------
# NeMoDeduplicator
# ---------------------------------------------------------------------------


class TestNeMoDeduplicator:
    def test_empty_table(self) -> None:
        d = NeMoDeduplicator()
        tbl = pa.table({"text_content": []})
        uniq, dup = d.deduplicate(tbl)
        assert uniq.num_rows == 0

    def test_no_text_column(self) -> None:
        d = NeMoDeduplicator()
        tbl = pa.table({"id": [1, 2]})
        uniq, dup = d.deduplicate(tbl)
        assert uniq.num_rows == 2

    def test_exact_dedup(self) -> None:
        d = NeMoDeduplicator()
        tbl = _table_with_text("a", "b", "a", "c", "b")
        with patch("arrow_lake.quality.nemo_curator.HAS_NEMO", False):
            uniq, dup = d.deduplicate(tbl)
        assert uniq.num_rows == 3
        assert dup.num_rows == 2

    def test_exact_dedup_with_none(self) -> None:
        d = NeMoDeduplicator()
        tbl = _table_with_text("a", None, None)
        with patch("arrow_lake.quality.nemo_curator.HAS_NEMO", False):
            uniq, dup = d.deduplicate(tbl)
        assert uniq.num_rows >= 1

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_try_gpu_no_cuda(self) -> None:
        d = NeMoDeduplicator()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert d._try_gpu() is False

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_try_gpu_with_cuda(self) -> None:
        d = NeMoDeduplicator()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert d._try_gpu() is True

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_try_gpu_import_error(self) -> None:
        d = NeMoDeduplicator()
        with patch.dict("sys.modules", {}):
            with patch("builtins.__import__", side_effect=ImportError):
                assert d._try_gpu() is False

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_minhash_dedup(self) -> None:
        d = NeMoDeduplicator()
        mock_mh = MagicMock()
        mock_lsh = MagicMock()
        mock_mh_inst = MagicMock()
        mock_mh.return_value = mock_mh_inst
        mock_lsh.return_value = MagicMock(
            insert=MagicMock(), query=MagicMock(return_value=["0"]),
        )
        with patch.dict("sys.modules", {
            "torch": MagicMock(cuda=MagicMock(is_available=MagicMock(return_value=True))),
            "datasketch": MagicMock(MinHash=mock_mh, MinHashLSH=mock_lsh),
        }):
            tbl = _table_with_text("hello world foo bar baz", "hello world foo bar baz")
            uniq, dup = d.deduplicate(tbl)
            # at least some dedup happened
            total = uniq.num_rows + dup.num_rows
            assert total == 2

    @patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True)
    def test_minhash_fallback_on_error(self) -> None:
        d = NeMoDeduplicator()
        with patch.dict("sys.modules", {
            "torch": MagicMock(cuda=MagicMock(is_available=MagicMock(return_value=True))),
        }):
            with patch("arrow_lake.quality.nemo_curator.HAS_NEMO", True):
                # Force _try_gpu True, then _dedup_minhash will hit import error
                with patch.object(d, "_try_gpu", return_value=True):
                    tbl = _table_with_text("a", "b")
                    uniq, dup = d.deduplicate(tbl)
                    assert uniq.num_rows == 2

    def test_name_property(self) -> None:
        d = NeMoDeduplicator()
        assert d.name == "nemo_dedup"

    def test_using_gpu_default(self) -> None:
        d = NeMoDeduplicator()
        assert d.using_gpu is False


# ---------------------------------------------------------------------------
# Heuristic functions
# ---------------------------------------------------------------------------


class TestHeuristics:
    def test_text_quality_none(self) -> None:
        assert _text_quality_heuristic(None) == 0.0

    def test_text_quality_short(self) -> None:
        assert _text_quality_heuristic("hi") > 0.0

    def test_nsfw_clean(self) -> None:
        assert _nsfw_heuristic("normal text") == 0.0

    def test_nsfw_flagged(self) -> None:
        assert _nsfw_heuristic("explicit xxx content") == 0.9

    def test_nsfw_none(self) -> None:
        assert _nsfw_heuristic(None) == 0.0

    def test_aesthetic_none(self) -> None:
        assert _aesthetic_heuristic(None, None) == 0.0

    def test_aesthetic_zero(self) -> None:
        assert _aesthetic_heuristic(0, 0) == 0.0

    def test_aesthetic_valid(self) -> None:
        score = _aesthetic_heuristic(1920, 1080)
        assert 0.0 < score <= 1.0


# ---------------------------------------------------------------------------
# Filter properties
# ---------------------------------------------------------------------------


class TestFilterProperties:
    def test_name(self) -> None:
        f = NeMoCuratorFilter()
        assert f.name == "nemo_curator"

    def test_classifiers_default(self) -> None:
        f = NeMoCuratorFilter()
        assert f.classifiers == ("text_quality",)

    def test_using_fallback_default(self) -> None:
        f = NeMoCuratorFilter()
        assert f.using_fallback is False

    def test_filter_empty_table(self) -> None:
        f = NeMoCuratorFilter()
        tbl = pa.table({"text_content": pa.array([], type=pa.string())})
        passed, rejected = f.filter(tbl)
        assert passed.num_rows == 0
        assert rejected.num_rows == 0

    def test_filter_fallback_pass(self) -> None:
        f = NeMoCuratorFilter(threshold=0.01)
        f._using_fallback = True
        tbl = _table_with_text("hello world this is a test of quality scoring")
        passed, rejected = f.filter(tbl)
        assert passed.num_rows == 1

    def test_filter_fallback_reject(self) -> None:
        f = NeMoCuratorFilter(threshold=0.99)
        f._using_fallback = True
        tbl = _table_with_text("hi")
        passed, rejected = f.filter(tbl)
        assert rejected.num_rows == 1
        assert "_rejection_reason" in rejected.column_names
