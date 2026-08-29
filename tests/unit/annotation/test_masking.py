"""W2.3 — annotation/masking:L2 泛化 + L3 假名(设计 §8 / S7)。

契约:
* L2 泛化:regex 规则按序应用(re.sub,值域收窄如精确地址→区县);
* L3 假名:HMAC 稳定假名(同 key 同实体同假名,保留等值连接性);
  算法/密钥复用 quality/masking_engine 同款约定
  (``ARROW_LAKE__MASKING__HMAC_KEY``,hmac-sha256);
* 透传:无规则且无实体 → 原文不动(零成本路径);
* fail-closed:有实体要假名但 HMAC key 缺失 → raise(标注者永远不见
  原始敏感值,宁拒发不裸奔);
* 组合顺序:先泛化后假名(dispatch 链:脱敏 → 预标注,span 自洽)。
"""

from __future__ import annotations

import pytest
from arrow_lake.annotation.masking import (
    AnnotationMaskingError,
    apply_annotation_masking,
)

KEY = b"test-hmac-key"


class TestGeneralize:
    def test_regex_rule_applied(self):
        out = apply_annotation_masking(
            "住在合肥市蜀山区望江西路 100 号",
            generalize_rules=[(r"合肥市蜀山区\S+", "蜀山区<地址>")],
            hmac_key=KEY,
        )
        assert "望江西路" not in out
        assert "蜀山区<地址>" in out

    def test_rules_applied_in_order(self):
        out = apply_annotation_masking(
            "电话 13812345678",
            generalize_rules=[
                (r"1[3-9]\d{9}", "1************"),
                (r"1\*{12}", "<手机号>"),
            ],
            hmac_key=KEY,
        )
        assert out == "电话 <手机号>"


class TestPseudonymize:
    def test_entity_replaced_with_stable_alias(self):
        text = "张三负责 A 段,李四负责 B 段。张三汇报。"
        out1 = apply_annotation_masking(text, entity_names=["张三", "李四"], hmac_key=KEY)
        out2 = apply_annotation_masking(text, entity_names=["张三", "李四"], hmac_key=KEY)
        assert out1 == out2  # 稳定
        assert "张三" not in out1 and "李四" not in out1
        assert "汇报" in out1  # 非实体文本不动
        # 同实体两处出现 → 同一假名
        alias = out1.split("负责")[0].strip()
        assert out1.count(alias) == 2

    def test_alias_keeps_first_char(self):
        out = apply_annotation_masking("张三到此一游", entity_names=["张三"], hmac_key=KEY)
        assert out.startswith("张")

    def test_missing_key_fail_closed(self):
        with pytest.raises(AnnotationMaskingError, match="HMAC"):
            apply_annotation_masking("张三", entity_names=["张三"], hmac_key=None)

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ARROW_LAKE__MASKING__HMAC_KEY", "env-key")
        out = apply_annotation_masking("王五在场", entity_names=["王五"])
        assert "王五" not in out


class TestPassthrough:
    def test_no_rules_no_entities_unchanged(self):
        text = "凤凰花园小区调压站压力异常"
        assert apply_annotation_masking(text, hmac_key=KEY) == text

    def test_generalize_then_pseudonymize_order(self):
        """泛化(改写文本)先于假名(实体在泛化后文本中替换)。"""
        out = apply_annotation_masking(
            "张三住在望江西路 100 号",
            generalize_rules=[(r"望江西路 \d+ 号", "城西<门牌>")],
            entity_names=["张三"],
            hmac_key=KEY,
        )
        assert out.startswith("张")  # 假名首字
        assert out.endswith("城西<门牌>")
