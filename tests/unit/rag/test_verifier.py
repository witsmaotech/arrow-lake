"""Tests for arrow_lake/rag/verifier.py (v1.9.6 P0-1 faithfulness check)."""

from __future__ import annotations

from arrow_lake.rag.verifier import VerificationResult, _split_sentences, verify


class TestVerifier:
    # AAA pattern: Arrange → Act → Assert

    def test_supported_and_unverified_mix(self) -> None:
        # Arrange
        answer = "AIGC 规模 143 亿元[1]。发展阶段培育摸索期[2]。未标注的句子内容。"
        # Act
        r = verify(answer, 10)
        # Assert
        assert r.valid_refs == 2
        assert r.invalid_refs == 0
        labels = [s.label for s in r.sentences]
        assert "supported" in labels
        assert r.support_ratio > 0.0

    def test_invalid_ref_out_of_range(self) -> None:
        # Arrange — ref [99] exceeds chunk_count=10
        # Act
        r = verify("数据来源见[99]。", 10)
        # Assert
        assert r.invalid_refs == 1
        assert r.valid_refs == 0

    def test_empty_answer_returns_full_support(self) -> None:
        # Act
        r = verify("", 10)
        # Assert
        assert r.support_ratio == 1.0
        assert r.sentences == ()

    def test_no_refs_all_unverified(self) -> None:
        # Arrange — answer with no [n] refs, long enough to pass sentence filter
        # Act
        r = verify("这是一段完全没有引用标注的回答内容,超过六个字符长度。", 10)
        # Assert
        assert r.support_ratio == 0.0
        assert all(s.label == "unverified" for s in r.sentences)

    def test_cjk_sentence_split(self) -> None:
        # Arrange — sentences >6 chars (filter skips short fragments)
        text = "第一句话内容较长。第二句话也很长！第三句话同样长？"
        # Act
        sents = _split_sentences(text)
        # Assert — CJK terminators split without trailing space
        assert len(sents) == 3

    def test_ascii_sentence_split_requires_space(self) -> None:
        # Arrange — "3.14" should NOT split (decimal, no space after .)
        # Act
        sents = _split_sentences("Value is 3.14. Next sentence here.")
        # Assert
        assert len(sents) == 2

    def test_verification_result_is_frozen(self) -> None:
        # Act
        r = verify("带引用[1]的句子。", 5)
        # Assert — dataclass frozen
        try:
            r.support_ratio = 0.5  # type: ignore[misc]
            assert False, "should be frozen"
        except AttributeError:
            pass  # expected — frozen dataclass
