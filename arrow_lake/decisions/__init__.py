"""决策层(v1.11.2 MS3,F3.1)——研判引擎。

``decisions.assess``:对象取数(W3.1 共享管线,对齐后口径+ACL 同路)→
ontology_rules active 规则求值(谓词 DSL 同源编译;失败→unruly,S8)→
conclusions/matched_rules/confidence=1.0(S10)/actionable(行动目录反查)。
旁路模块,零热路径影响。
"""

from arrow_lake.decisions.assess import assess_object

__all__ = ["assess_object"]
