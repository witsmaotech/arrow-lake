"""MS1 (v1.11.0) — 本体形式化模块。

模板的隐式本体(ontology: 结构化段 + fields 约束)→ SHACL shapes →
pyshacl 校验(KG build 收尾,shadow→enforce)。三条红线(Semantica 评估):
① 不做本体运行时(不进查询热路径);② 不引第二图库(RDF 文档非图存储);
③ 不动抽取链路(只读校验,prompt 零改动)。
"""

from arrow_lake.ontology.template_adapter import OntologySpec, adapt_template

__all__ = ["OntologySpec", "adapt_template"]
