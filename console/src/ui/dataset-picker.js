/* 数据集选择器(v1.11.2):对象层专用下拉 —— 只列"有契约"的数据集。
 * 原生 datalist 不可过滤不可定制,故自绘:console 设计语言、键入即滤、
 * 键盘导航(↑↓/Enter/Esc)。契约探测 = GET /objects/types 的 has_contract
 * (VIEWER;无读权/无契约的数据集不上列表)。
 * 用法: mountDatasetPicker(inputEl, { onPick(name) })
 */
import { request } from "../api.js";

let styleInjected = false;
function injectStyle() {
  if (styleInjected) return;
  styleInjected = true;
  const st = document.createElement("style");
  st.textContent = `
.dsp-wrap{position:relative}
.dsp-panel{position:absolute;top:calc(100% + 4px);left:0;min-width:240px;max-width:360px;max-height:300px;overflow-y:auto;background:var(--ink-900);border:1px solid var(--line);border-radius:var(--r-sm);box-shadow:0 10px 32px rgba(0,0,0,.45);z-index:70;padding:4px 0}
.dsp-row{display:flex;gap:8px;align-items:center;padding:7px 12px;font-size:.76rem;cursor:pointer}
.dsp-row:hover,.dsp-row.sel{background:var(--ink-850)}
.dsp-row.sel{box-shadow:inset 2px 0 0 var(--teal-bright)}
.dsp-row .nm{flex:1;min-width:0;font-family:var(--font-mono);color:var(--fg-hi);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dsp-row .bd{font-size:.62rem;color:var(--fg-md);font-family:var(--font-mono);flex-shrink:0}
.dsp-empty{padding:12px;font-size:.74rem;color:var(--fg-md);text-align:center}`;
  document.head.appendChild(st);
}

export function mountDatasetPicker(input, { onPick } = {}) {
  injectStyle();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // 包一层 relative 容器(下拉锚定其下)
  const wrap = document.createElement("span");
  wrap.className = "dsp-wrap";
  input.replaceWith(wrap);
  wrap.appendChild(input);
  input.setAttribute("autocomplete", "off");

  const panel = document.createElement("div");
  panel.className = "dsp-panel";
  panel.style.display = "none";
  wrap.appendChild(panel);

  const state = { items: null, sel: -1, open: false };

  function close() {
    state.open = false;
    panel.style.display = "none";
  }

  function visible() {
    return (state.items || []).filter((d) =>
      !input.value.trim() ||
      d.name.toLowerCase().includes(input.value.trim().toLowerCase()));
  }

  function render() {
    const list = visible();
    state.sel = Math.min(state.sel, list.length - 1);
    panel.innerHTML = list.length
      ? list.map((d, i) => `
        <div class="dsp-row${i === state.sel ? " sel" : ""}" data-i="${i}">
          <span class="nm">${esc(d.name)}</span><span class="bd">${d.types} 类</span>
        </div>`).join("")
      : '<div class="dsp-empty">无匹配数据集(仅有契约者可选)</div>';
    panel.querySelectorAll(".dsp-row").forEach((el) =>
      el.addEventListener("mousedown", (e) => {  // mousedown 先于 input blur
        e.preventDefault();
        pick(list[+el.dataset.i].name);
      }));
  }

  function open() {
    state.open = true;
    state.sel = -1;
    panel.style.display = "";
    if (state.items === null) {
      panel.innerHTML = '<div class="dsp-empty">探测契约中…</div>';
      load();
    } else render();
  }

  async function load() {
    try {
      const data = await request("GET", "/datasets?limit=1000");
      const names = (data?.datasets || []).map((d) => d.name).filter(Boolean).sort();
      // 并行探测契约(VIEWER;失败=无读权或无契约 → 不上列表)
      const probes = await Promise.allSettled(names.map((n) =>
        request("GET", `/objects/types?dataset=${encodeURIComponent(n)}`)));
      state.items = probes
        .map((p, i) => (p.status === "fulfilled" && p.value?.has_contract
          ? { name: names[i], types: (p.value?.types || []).length } : null))
        .filter(Boolean);
    } catch (_) {
      state.items = [];
    }
    if (state.open) render();
  }

  function pick(name) {
    input.value = name;
    close();
    if (onPick) onPick(name);
  }

  input.addEventListener("focus", open);
  input.addEventListener("click", (e) => { e.stopPropagation(); if (!state.open) open(); });
  input.addEventListener("input", () => { state.sel = -1; if (state.open) render(); });
  input.addEventListener("blur", () => setTimeout(close, 120)); // 让 mousedown 生效
  input.addEventListener("keydown", (e) => {
    if (!state.open) return;
    const list = visible();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      state.sel = e.key === "ArrowDown"
        ? Math.min(state.sel + 1, list.length - 1)
        : Math.max(state.sel - 1, 0);
      render();
    } else if (e.key === "Enter" && state.sel >= 0 && list[state.sel]) {
      e.preventDefault();
      pick(list[state.sel].name);
    } else if (e.key === "Escape") close();
  });
  document.addEventListener("click", close);
  panel.addEventListener("click", (e) => e.stopPropagation());
}
