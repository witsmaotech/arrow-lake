// 数据准备 · ops 模块:5 个操作的配置表单 + 请求构造(纯描述,不碰网络)
import { colOptions } from "./profile.js";

const _esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const val = (form, id) => (form.querySelector("#" + id)?.value ?? "").trim();

// DuckDB 文本规整表达式(列引用用双引号)
const TIDY = {
  trim: { label: "去首尾空白 trim()", sql: (c) => `trim("${c}")` },
  lower: { label: "转小写 lower()", sql: (c) => `lower("${c}")` },
  ws: { label: "折叠多余空白 \\s+→' '", sql: (c) => `regexp_replace("${c}", '\\s+', ' ')` },
  url: { label: "URL → [URL]", sql: (c) => `regexp_replace("${c}", 'https?://\\S+', '[URL]')` },
  email: { label: "邮箱 → [EMAIL]", sql: (c) => `regexp_replace("${c}", '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+', '[EMAIL]')` },
  phone: { label: "手机号 → [PHONE]", sql: (c) => `regexp_replace("${c}", '1[3-9]\\d{9}', '[PHONE]')` },
};

export const OPS = [
  // ── 1. 质量规则 ──────────────────────────────────────────
  {
    key: "rules", label: "质量规则", endpint: "POST /quality/rules",
    desc: "长度 / 正则 / 重复 → 标记或删除",
    ic: '<path d="M5 12l5 5 9-9"/>',
    render(tc) {
      return `
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>检查列</label><select class="select" id="rCol">${colOptions(tc, tc[0])}</select></div>
        <div><label>检查类型</label><select class="select" id="rCheck"><option value="length">length 长度区间</option><option value="regex">regex 正则</option><option value="duplicate">duplicate 重复</option></select></div>
      </div>
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>min(长度下限)</label><input class="input mono" id="rMin" placeholder="如 10"></div>
        <div><label>max(长度上限)</label><input class="input mono" id="rMax" placeholder="如 8000"></div>
      </div>
      <div class="cfg-row"><label>正则 pattern(仅 regex 用)</label><input class="input mono" id="rPat" placeholder="如 ^[\\u4e00-\\u9fa5]+$"></div>
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>动作</label><select class="select" id="rAct"><option value="flag">flag 标记</option><option value="remove">remove 删除</option><option value="reject">reject 拒绝</option></select></div>
        <div><label>规则名(可选)</label><input class="input" id="rName" placeholder="valid_length"></div>
      </div>
      <div class="muted" style="font-size:.72rem">length 用 min/max;regex 用 pattern;duplicate 无需参数。flag 不会删数据,安全可先试。</div>`;
    },
    previewCols(f) { return [val(f, "rCol")]; },
    buildRequest(f, ds) {
      const check = val(f, "rCheck");
      const params = {};
      if (check === "length") { if (val(f, "rMin")) params.min = +val(f, "rMin"); if (val(f, "rMax")) params.max = +val(f, "rMax"); }
      if (check === "regex") params.pattern = val(f, "rPat") || ".*";
      return {
        method: "POST", path: `/datasets/${encodeURIComponent(ds)}/quality/rules`, async: false,
        body: { rules: [{ name: val(f, "rName") || `rule_${check}`, column: val(f, "rCol"), check, params, action: val(f, "rAct") }] },
      };
    },
    parseResp(r) {
      const tot = r.total_affected_rows ?? 0;
      return { input_rows: null, affected: tot, output: `${r.applied_rules ?? 1} 规则`, detail: r.results || r };
    },
  },

  // ── 2. 文本规整 ──────────────────────────────────────────
  {
    key: "tidy", label: "文本规整", endpint: "POST /schema/migrate (add_column)",
    desc: "DuckDB SQL 安全菜单(替 UDF)→ 新列",
    ic: '<path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v16"/>',
    render(tc) {
      return `
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>源列(文本)</label><select class="select" id="tCol">${colOptions(tc, tc[0])}</select></div>
        <div><label>新列名</label><input class="input mono" id="tNew" value="text_clean"></div>
      </div>
      <div class="cfg-row"><label>规整操作</label><select class="select" id="tOp">${Object.entries(TIDY).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("")}</select></div>
      <div class="muted" style="font-size:.72rem">服务端 DuckDB 执行,无任意 Python(避免 RCE)。生成 SQL:<code class="mono" id="tSql" style="color:var(--teal-bright)"></code></div>`;
    },
    previewCols(f) { return [val(f, "tCol")]; },
    buildRequest(f, ds) {
      const col = val(f, "tCol"), op = val(f, "tOp"), newC = val(f, "tNew") || `${col}_clean`;
      const sql = TIDY[op] ? TIDY[op].sql(col) : `trim("${col}")`;
      return {
        method: "POST", path: `/datasets/${encodeURIComponent(ds)}/schema/migrate`, async: false,
        body: { actions: [{ operation: "add_column", column_name: newC, sql_expr: sql }], dry_run: false },
      };
    },
    parseResp(r) {
      return { input_rows: null, affected: r.applied_count ?? 1, output: "新列已加", detail: r.issues?.length ? r.issues : { ok: true } };
    },
  },

  // ── 3. 去重 ──────────────────────────────────────────────
  {
    key: "dedup", label: "去重", endpint: "POST /quality/deduplicate",
    desc: "exact(SHA-256) / minhash(语义近重复)",
    ic: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    render(tc) {
      return `
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>策略</label><select class="select" id="dStrat"><option value="exact">exact 完全相同(SHA-256)</option><option value="minhash">minhash 语义近重复(MinHash)</option></select></div>
        <div><label>动作</label><select class="select" id="dAct"><option value="flag">flag 标记</option><option value="remove">remove 删除</option></select></div>
      </div>
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>文本列(minhash 必填)</label><select class="select" id="dCol">${colOptions(tc, tc[0])}</select></div>
        <div><label>相似阈值(仅 minhash,概念)</label><input class="input mono" id="dThr" value="0.8" disabled></div>
      </div>
      <div class="muted" style="font-size:.72rem">exact 对二进制/文本完全匹配;minhash 对文本列做字符 n-gram MinHash LSH,抓释义型近重复。<b>flag 不删数据,建议先 flag 看 is_duplicate 列。</b></div>`;
    },
    previewCols(f) { return [val(f, "dCol")]; },
    buildRequest(f, ds) {
      const strategy = val(f, "dStrat");
      const body = { strategy, action: val(f, "dAct") };
      if (strategy === "minhash") body.text_column = val(f, "dCol");
      return { method: "POST", path: `/datasets/${encodeURIComponent(ds)}/quality/deduplicate`, async: false, body };
    },
    parseResp(r) {
      const rep = r.report || r;
      return { input_rows: rep.total_rows ?? null, affected: rep.duplicates_found ?? rep.duplicates ?? 0, output: rep.unique_rows ?? null, detail: { strategy: rep.strategy, action: rep.action } };
    },
  },

  // ── 4. LLM 标注(异步) ───────────────────────────────────
  {
    key: "label", label: "LLM 标注", endpint: "POST /quality/llm_label (异步)",
    desc: "prompt() 批量打标 → 新列(异步任务)",
    ic: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="1.5"/>',
    render(tc) {
      return `
      <div class="cfg-row" style="grid-template-columns:1fr 1fr">
        <div><label>标注列(文本)</label><select class="select" id="lCol">${colOptions(tc, tc[0])}</select></div>
        <div><label>新列名</label><input class="input mono" id="lNew" value="sentiment"></div>
      </div>
      <div class="cfg-row"><label>指令模板(含 {text})</label><textarea class="input mono" id="lPrompt" style="min-height:64px">给这段文本的情感打标,只返回一个词:正向 / 负向 / 中性。文本:{text}</textarea></div>
      <div class="cfg-row" style="grid-template-columns:1fr 1fr 1fr">
        <div><label>模型(可选)</label><input class="input mono" id="lModel" placeholder="留空=用配置"></div>
        <div><label>max_rows 上限</label><input class="input mono" id="lMax" value="5000"></div>
        <div><label>并发</label><input class="input mono" id="lConc" value="8"></div>
      </div>
      <div class="muted" style="font-size:.72rem">全量提交为<b>异步任务</b>(返回 task_id),逐行 LLM 调用,失败行留空。预览仅看列内容。</div>`;
    },
    previewCols(f) { return [val(f, "lCol")]; },
    buildRequest(f, ds) {
      const body = {
        column: val(f, "lCol"), new_column: val(f, "lNew"),
        prompt_template: f.querySelector("#lPrompt").value,
        concurrency: +val(f, "lConc") || 8,
      };
      if (val(f, "lModel")) body.model = val(f, "lModel");
      const mr = +val(f, "lMax"); if (mr) body.max_rows = mr;
      return { method: "POST", path: `/datasets/${encodeURIComponent(ds)}/quality/llm_label`, async: true, body };
    },
  },

  // ── 5. 结构化抽取(异步) ─────────────────────────────────
  {
    key: "extract", label: "结构化抽取", endpint: "POST /quality/extract (异步)",
    desc: "非结构化文本 → 多个字段列(异步任务)",
    ic: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/>',
    render(tc) {
      return `
      <div class="cfg-row"><label>源列(非结构化文本)</label><select class="select" id="eCol">${colOptions(tc, tc[0])}</select></div>
      <div class="cfg-row">
        <label>抽取字段(每个 → 一个新 string 列)</label>
        <div id="extractFields">
          <div class="field-mini"><input class="input x" data-f="name" placeholder="字段名(如 日期)" style="flex:1"><select class="select" data-f="type" style="max-width:110px"><option>string</option><option>number</option><option>integer</option><option>boolean</option></select><input class="input x" data-f="desc" placeholder="说明(可选)" style="max-width:180px"></div>
          <div class="field-mini"><input class="input x" data-f="name" placeholder="字段名(如 金额)" style="flex:1"><select class="select" data-f="type" style="max-width:110px"><option>string</option><option>number</option><option>integer</option><option>boolean</option></select><input class="input x" data-f="desc" placeholder="说明(可选)" style="max-width:180px"></div>
        </div>
        <button class="btn btn-ghost btn-sm" type="button" id="addFieldBtn" style="margin-top:6px">+ 加字段</button>
      </div>
      <div class="cfg-row" style="grid-template-columns:1fr 1fr 1fr">
        <div><label>模型(可选)</label><input class="input mono" id="eModel" placeholder="留空=用配置"></div>
        <div><label>max_rows 上限</label><input class="input mono" id="eMax" value="5000"></div>
        <div><label>并发</label><input class="input mono" id="eConc" value="8"></div>
      </div>
      <div class="muted" style="font-size:.72rem">LLM 按字段 schema 输出 JSON,解析为多列落盘。异步任务。</div>`;
    },
    previewCols(f) { return [val(f, "eCol")]; },
    buildRequest(f, ds) {
      const rows = [...f.querySelectorAll("#extractFields .field-mini")].map((r) => ({
        name: r.querySelector('[data-f="name"]')?.value.trim(),
        type: r.querySelector('[data-f="type"]')?.value.trim() || "string",
        description: r.querySelector('[data-f="desc"]')?.value.trim() || "",
      })).filter((x) => x.name);
      const body = { column: val(f, "eCol"), fields: rows, concurrency: +val(f, "eConc") || 8 };
      if (val(f, "eModel")) body.model = val(f, "eModel");
      const mr = +val(f, "eMax"); if (mr) body.max_rows = mr;
      return { method: "POST", path: `/datasets/${encodeURIComponent(ds)}/quality/extract`, async: true, body };
    },
  },
];

export function renderOpList(into, textCols, onPick) {
  into.innerHTML = OPS.map((op, i) => `
    <button class="op-btn ${i === 0 ? "active" : ""}" data-op="${op.key}">
      <span class="op-ic"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${op.ic}</svg></span>
      <span><span class="op-t">${_esc(op.label)}</span><span class="op-s">${_esc(op.desc)}</span></span>
    </button>`).join("");
  into.querySelectorAll(".op-btn").forEach((b) => {
    b.onclick = () => {
      into.querySelectorAll(".op-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      onPick(OPS.find((o) => o.key === b.dataset.op));
    };
  });
}

export function icon() { return window.icon; }
