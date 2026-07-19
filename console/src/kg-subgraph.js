// 可复用 KG 实体子图渲染:在指定容器里渲染一个 vis-network 小图,
// 节点/边来自 POST /kg/search(dataset + query 的语义实体检索)。
// 页面需先 <script src="vendor/vis-network.bundle.min.js"> 引入 vis 全局。
import { request } from "./api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// type → 颜色(与 kg.html 主图保持一致)
const COLORS = {
  系统: "#60a5fa", 算法: "#a78bfa", 模型: "#f472b6", 组件: "#fbbf24", 应用: "#34d399",
  数据: "#22d3ee", 方法: "#fb923c", 过程: "#94a3b8", 事件: "#f87171", 组织: "#c084fc",
  角色: "#a3e635", 产品: "#2dd4bf", 属性: "#cbd5e1", 概念: "#e879f9",
  entity: "#2dd4bf", person: "#60a5fa", organization: "#c084fc", location: "#4ade80",
  concept: "#fb923c", event: "#f87171",
};

/** 在 container 渲染 dataset+query 命中的 KG 实体子图。 */
export async function renderKgSubgraph(container, { dataset, query, limit = 40 } = {}) {
  if (!window.vis) { container.innerHTML = '<div class="muted" style="font-size:.74rem;padding:8px">图库未加载(vis-network)</div>'; return; }
  if (!dataset || !query) { container.innerHTML = '<div class="muted" style="font-size:.74rem;padding:8px">无查询/数据集</div>'; return; }
  container.innerHTML = '<div class="muted" style="font-size:.74rem;padding:8px">加载实体子图…</div>';
  let r;
  try {
    r = await request("POST", "/kg/search", { body: { dataset, query, top_k: limit } });
  } catch (e) {
    const emsg = Array.isArray(e?.detail) ? e.detail.map((x) => x?.msg || "").join("; ") : (e?.detail || e?.message || e);
    container.innerHTML = `<div class="muted" style="font-size:.74rem;padding:8px">实体检索失败(KG 未建?):${esc(emsg)}</div>`;
    return;
  }
  const V = window.vis;
  if (container._net) { container._net.destroy(); container._net = null; }
  const ns = (r.nodes || []).map((n) => ({
    id: n.id ?? n.name,
    label: n.name || n.id || "?",
    group: n.type || n.label || "entity",
    title: `${n.name || n.id}${n.type ? " [" + n.type + "]" : ""}${n.definition ? "\n" + n.definition : ""}`,
  }));
  const es = (r.edges || [])
    .map((e) => ({ s: e.source ?? e.from ?? e.subject, t: e.target ?? e.to ?? e.object, label: e.relation_type || e.label || e.type || "", id: e.id }))
    .filter((e) => e.s && e.t)
    .map((e, i) => ({
      id: e.id ?? ("e" + i), from: e.s, to: e.t,
      label: e.label, arrows: "to", font: { size: 9, color: "#64748b", strokeWidth: 0 },
    }));
  if (!ns.length) { container.innerHTML = '<div class="muted" style="font-size:.74rem;padding:8px">无相关实体</div>'; return; }
  const groups = {};
  for (const k of Object.keys(COLORS)) groups[k] = { color: { background: COLORS[k], border: COLORS[k] } };
  container.innerHTML = "";
  container._net = new V.Network(container, { nodes: new V.DataSet(ns), edges: new V.DataSet(es) }, {
    height: "100%", width: "100%", autoResize: true,
    layout: { improvedLayout: ns.length <= 80 },
    physics: { stabilization: { iterations: 120 }, barnesHut: { gravitationalConstant: -6000, springLength: 90 } },
    nodes: { shape: "dot", size: 11, font: { size: 11, color: "#e2e8f0" }, borderWidth: 2 },
    edges: { color: { color: "#475569", opacity: 0.5 }, smooth: { type: "continuous" } },
    groups,
    interaction: { hover: true, tooltipDelay: 150 },
  });
}
