/* ============================================================
   Arrow Lake · 旗舰展示 · 三张王牌交互 + 架构/版本渲染
   依赖: d3 (王牌③ KG; 可选) · 所有数据为硬编码 mock 常量
   ============================================================ */
const C = { teal: '#2DD4BF', amber: '#F59E0B', info: '#38BDF8', violet: '#A78BFA', ok: '#22C55E', danger: '#EF4444', fgHi: '#EDF1F8', fgMd: '#A7B2C8', fgLo: '#8593AC', line: '#243149', mono: "'JetBrains Mono',ui-monospace,monospace" };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

// —— 数据 ——
const ARCH = [
  ['①', '接入层', 'SDK Lake facade · 9 mixin · REST 106 routes · CLI 16 组', 'v1.6.0'],
  ['②', '能力层', '摄取 · 检索(向量/全文/混合/分面/集成) · RAG · KG', 'v1.7→v1.8'],
  ['③', '计算层', 'Daft 多模态 DataFrame · Ray 集群 · 嵌入 CLIP/Qwen3', 'v1.8.0'],
  ['④', '引擎层', 'LanceDB IVF_PQ · DuckDB SQL · DuckLake 物化视图', 'v1.7→v1.8'],
  ['⑤', '持久化', 'MinIO/S3 · Redis · HugeGraph kg_{dataset}', 'v1.6→v1.8'],
];
const CAPS = [
  ['📥', '多模态摄入', '文件/HTTP/URL · docling OCR · 7 种分块 · 多模型嵌入', 'v1.6-8'],
  ['🔍', '五种检索', '向量 IVF_PQ/HNSW · 全文 Tantivy+jieba · RRF 混合 · 分面 · 集成', 'v1.7-8'],
  ['📊', 'OLAP 分析', 'DuckDB SQL · Daft 分布式 · DuckLake 物化 · SQL-PGQ 图查询', 'v1.7-8'],
  ['💬', 'RAG 管线', '多 Provider 流式 · 会话 · 引用溯源 · GraphRAG', 'v1.8.0'],
  ['🕸️', '知识图谱', 'HugeGraph per-dataset 分图 · 8 traverser · hyper-extract', 'v1.7-8.6'],
  ['🛡️', '治理安全', 'Gravitino 元数据 · 血缘 · HMAC 审计 · RBAC · 优雅降级', 'v1.5.2+'],
];
const VERSIONS = [
  ['v1.5.2', '安全基线 8C+13H'], ['v1.6.0', 'Lake facade+9 mixin'], ['v1.6.1', '死锁修复+异步'],
  ['v1.6.2', 'Redis 任务共享'], ['v1.6.3', '优雅降级'], ['v1.7.0', 'hyper-extract KG'],
  ['v1.7.1', 'lancedb0.33 调优'], ['v1.8.0', 'Reranker/CLIP/branches'], ['v1.8.3', 'HA readiness'],
  ['v1.8.5', '上传修复'], ['v1.8.6', 'per-dataset 分图'], ['v1.8.7', 'Docling+SQL Worksheet'],
  ['v1.8.8', 'per-dataset KA 抽取'], ['v1.8.9', 'Reranker 回归+双 LLM'], ['v1.9.0', '控制面 libSQL'],
];

// —— 架构渲染 ——
function renderArch() {
  const layers = ARCH.map(a => `
    <div class="arch-layer"><div class="arch-ln">${a[0]}</div>
      <div class="arch-info"><b>${a[1]}</b><small>${a[2]}</small></div>
      <span class="arch-tag">${a[3]}</span></div>`).join('');
  const side = `
    <aside class="arch-side">
      <div class="arch-side-h">⟂ 横切面</div>
      <div class="arch-side-i"><b>治理</b><small>Gravitino 元数据 · 血缘</small></div>
      <div class="arch-side-i"><b>安全</b><small>RBAC · JWT · HMAC 审计</small></div>
      <div class="arch-side-i"><b>降级</b><small>Ray/KG/Gremlin 回退</small></div>
      <div class="arch-side-i"><b>可观测</b><small>OTel · readiness gate</small></div>
      <div class="arch-side-tag">贯穿五层 · v1.5.2+</div>
    </aside>`;
  $('#archStack').innerHTML = `<div class="arch-main">${layers}</div>${side}`;
}
function renderCaps() {
  $('#capGrid').innerHTML = CAPS.map(c => `
    <div class="cap"><div class="cap-ic">${c[0]}</div><b>${c[1]}</b>
      <small>${c[2]}</small><span class="cap-v">${c[3]}</span></div>`).join('');
}
function renderVer() {
  $('#ver-axis').innerHTML = VERSIONS.map(([v, n]) => `
    <div class="vnode" title="${v} · ${n}"><div class="vnode-dot"></div>
      <div class="vnode-ver">${v}</div><div class="vnode-note">${n}</div></div>`).join('');
}

// ============================================================
// 王牌 ① 检索宇宙
// ============================================================
const QUERIES = [
  { q: 'attention 机制的核心结论', x: .50, y: .42, thr: .60 },
  { q: 'transformer 编码器结构', x: .74, y: .32, thr: .58 },
  { q: '多模态 embedding 对比', x: .28, y: .68, thr: .57 },
  { q: 'softmax 归一化原理', x: .58, y: .62, thr: .59 },
];
const FUNNEL = [
  ['全量向量', 9432881], ['IVF nprobes=12', 141493], ['PQ 近似排序', 2000], ['Reranker 精排', 148], ['top_k = 10', 10],
];
const SearchCosmos = (() => {
  let canvas, ctx, dpr, W, H, pts, cur = 0, raf, t0, mode = 'vector';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  function genPts() {
    const arr = []; const cl = [[.28, .36], [.72, .6], [.5, .5], [.22, .72], [.8, .26], [.45, .8]];
    for (let i = 0; i < 540; i++) { const c = cl[i % 6], a = Math.random() * 6.28, r = Math.random() * Math.random() * .17;
      arr.push({ bx: c[0] + Math.cos(a) * r, by: c[1] + Math.sin(a) * r, sim: Math.random() }); }
    return arr;
  }
  function resize() {
    dpr = Math.min(2, devicePixelRatio || 1);
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function draw(t) {
    const Q = QUERIES[cur], qx = Q.x * W, qy = Q.y * H;
    ctx.clearRect(0, 0, W, H);
    pts.forEach(p => {
      const cand = p.sim > Q.thr;
      const bx = p.bx * W, by = p.by * H;
      const tx = cand ? qx + (bx - qx) * 0.22 : bx, ty = cand ? qy + (by - qy) * 0.22 : by;
      const x = bx + (tx - bx) * t, y = by + (ty - by) * t;
      ctx.beginPath(); ctx.arc(x, y, cand ? 2.8 : 1.5, 0, 6.29);
      ctx.fillStyle = cand ? `rgba(45,212,191,${.45 + .5 * t})` : `rgba(133,147,172,${.3 - .12 * t})`; ctx.fill();
      if (cand && t > .35) { ctx.strokeStyle = `rgba(20,184,166,${.14 * (t - .35) / .65})`; ctx.lineWidth = .6; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(qx, qy); ctx.stroke(); }
    });
    ctx.beginPath(); ctx.arc(qx, qy, 7, 0, 6.29); ctx.fillStyle = C.amber;
    ctx.shadowColor = C.amber; ctx.shadowBlur = 18; ctx.fill(); ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(245,158,11,.45)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(qx, qy, 15, 0, 6.29); ctx.stroke();
    ctx.fillStyle = C.fgHi; ctx.font = `600 11px ${C.mono}`; ctx.textAlign = 'center';
    ctx.fillText('query · ' + Q.q.slice(0, 26), qx, qy - 22);
  }
  function run() {
    if (raf) cancelAnimationFrame(raf);
    t0 = performance.now();
    const tick = () => { const t = reduced ? 1 : Math.min(1, (performance.now() - t0) / 1100); draw(t); if (t < 1) raf = requestAnimationFrame(tick); };
    tick();
  }
  function lightFunnel() {
    $$('#sq-funnel .fstep').forEach((el, i) => {
      setTimeout(() => el.classList.add('on'), i * 180);
      setTimeout(() => { if (i === 4) {} }, 0);
    });
  }
  function setQuery(i) {
    cur = i;
    $$('#sq-presets .qpreset').forEach((b, j) => b.classList.toggle('on', j === i));
    run(); lightFunnel();
    $('#sq-legend').innerHTML = `<span>● <b>候选 top-${mode === 'hybrid' ? '20' : '40'}</b> 向 query 聚拢</span><span>● 其余向量</span><span>● <b style="color:${C.amber}">query</b></span>`;
  }
  function init() {
    canvas = $('#sq-canvas'); if (!canvas) return; ctx = canvas.getContext('2d');
    resize(); pts = genPts();
    $('#sq-presets').innerHTML = QUERIES.map((q, i) => `<button class="qpreset${i === 0 ? ' on' : ''}" data-i="${i}"><span>${q.q}</span><span class="qp-s">sim>${q.thr.toFixed(2)}</span></button>`).join('');
    $('#sq-funnel').innerHTML = FUNNEL.map(f => `<div class="fstep"><span class="fd"></span><span class="fn">${f[0]}</span><span class="fv">${f[1].toLocaleString()}</span></div>`).join('');
    $$('#sq-presets .qpreset').forEach(b => b.onclick = () => { $$('#sq-funnel .fstep').forEach(s => s.classList.remove('on')); setQuery(+b.dataset.i); });
    $$('#sq-modes .mode').forEach(b => b.onclick = () => { mode = b.dataset.m; $$('#sq-modes .mode').forEach(x => x.classList.toggle('on', x === b)); setQuery(cur); });
    window.addEventListener('resize', () => { resize(); draw(1); });
    setQuery(0);
  }
  return { init };
})();

// ============================================================
// 王牌 ② 湖仓时间机器
// ============================================================
const TM = [
  { v: 'v1.5.2', n: '安全基线', rows: '7.8M', schema: 'v3', idx: 'IVF_PQ(8)', kg: '单图混存', acl: '全局', br: '—' },
  { v: 'v1.6.0', n: 'facade 成型', rows: '7.9M', schema: 'v3', idx: 'IVF_PQ(8)', kg: '单图', acl: '全局', br: '—' },
  { v: 'v1.7.0', n: 'hyper-extract', rows: '8.1M', schema: 'v4', idx: 'IVF_PQ(8)+标量', kg: '单图+抽取', acl: '全局', br: '—' },
  { v: 'v1.7.1', n: '湖仓调优', rows: '8.2M', schema: 'v4', idx: 'IVF_PQ(12)+标量', kg: '单图', acl: '全局', br: '—' },
  { v: 'v1.8.0', n: 'Reranker/CLIP', rows: '8.5M', schema: 'v5(+clip)', idx: '+reranker bridge', kg: '单图', acl: '全局+dataset', br: '2(main,exp)' },
  { v: 'v1.8.3', n: '生产 HA', rows: '9.0M', schema: 'v5', idx: '+reranker', kg: '单图', acl: 'dataset', br: '2', ha: 'readiness gate ✅' },
  { v: 'v1.8.6', n: 'per-dataset 分图', rows: '9.4M', schema: 'v5', idx: '+reranker', kg: 'kg_papers 独立', acl: 'per-dataset ✅', br: '2', ha: 'readiness ✅', trav: '8×REST' },
];
const TM_KEYS = [['rows', 'rows'], ['schema', 'schema'], ['idx', 'index'], ['kg', 'kg 图'], ['acl', 'ACL'], ['br', 'branches'], ['ha', 'HA'], ['trav', 'traverser']];
const TimeMachine = (() => {
  function render(target) {
    const base = TM[0], cur = target;
    const keys = TM_KEYS.filter(([k]) => base[k] !== undefined || cur[k] !== undefined);
    const oldRows = keys.map(([k, label]) => {
      const v = base[k] ?? '—'; const isChg = base[k] !== cur[k];
      return `<div class="drow${isChg ? ' chg' : ''}"><span class="dk">${label}</span><span class="dv">${v}</span></div>`;
    }).join('');
    const newRows = keys.map(([k, label]) => {
      const v = cur[k] ?? '—'; const isChg = base[k] !== cur[k]; const isAdd = base[k] === undefined && cur[k] !== undefined;
      return `<div class="drow${isChg ? (isAdd ? ' add' : ' chg') : ''}"><span class="dk">${label}</span><span class="dv">${v}</span></div>`;
    }).join('');
    $('#tm-old').innerHTML = oldRows; $('#tm-new').innerHTML = newRows;
    $('#tm-old-ver').textContent = base.v; $('#tm-new-ver').textContent = cur.v;
    $('#tm-cur').textContent = cur.v;
  }
  function init() {
    const sl = $('#tm-slider'); if (!sl) return;
    sl.oninput = () => render(TM[+sl.value]); render(TM[6]);
  }
  return { init };
})();

// ============================================================
// 王牌 ③ 知识图谱探索
// ============================================================
const KG = {
  papers: {
    name: 'kg_papers', nodes: [
      ['attn', 'Attention', 19, C.amber, 1], ['tf', 'Transformer', 15, C.teal, 1], ['qkv', 'Q·K·V', 12, C.teal, 1], ['sm', 'Softmax', 11, C.teal, 1],
      ['vaswani', 'Vaswani 2017', 14, C.info, 0], ['bhd', 'Bahdanau 2014', 13, C.info, 0], ['bert', 'BERT', 12, C.violet, 1], ['gpt', 'GPT', 12, C.violet, 1],
      ['wiki', 'Wikipedia', 10, C.fgLo, 0], ['arxiv', 'arXiv', 10, C.fgLo, 0], ['lab', 'Google Brain', 10, C.fgLo, 0],
    ], links: [['attn', 'tf'], ['tf', 'qkv'], ['tf', 'sm'], ['attn', 'bert'], ['attn', 'gpt'], ['qkv', 'sm'], ['vaswani', 'attn'], ['bhd', 'attn'], ['vaswani', 'tf'], ['bert', 'gpt'], ['wiki', 'bert'], ['arxiv', 'vaswani'], ['arxiv', 'bhd'], ['lab', 'vaswani']],
    qs: [['attention 是什么', ['attn', 'qkv', 'sm', 'tf']], ['谁提出了 transformer', ['tf', 'vaswani', 'lab', 'arxiv']], ['attention 用在哪些模型', ['attn', 'bert', 'gpt']]],
  },
  finance: {
    name: 'kg_finance', nodes: [
      ['risk', '风控引擎', 19, C.danger, 1], ['txn', '交易', 14, C.amber, 1], ['acct', '账户', 13, C.teal, 1], ['cust', '客户', 13, C.info, 1],
      ['card', '信用卡', 11, C.violet, 1], ['mcht', '商户', 11, C.amber, 1], ['alert', '预警', 12, C.danger, 1], ['kyc', 'KYC', 11, C.info, 0], ['prod', '产品', 10, C.fgLo, 0],
    ], links: [['risk', 'txn'], ['txn', 'acct'], ['acct', 'cust'], ['txn', 'mcht'], ['cust', 'card'], ['card', 'txn'], ['risk', 'alert'], ['alert', 'txn'], ['cust', 'kyc'], ['acct', 'prod'], ['card', 'prod']],
    qs: [['哪些交易触发风控', ['risk', 'txn', 'alert', 'mcht']], ['账户和客户的关系', ['acct', 'cust', 'kyc', 'card']], ['信用卡用到哪', ['card', 'txn', 'mcht', 'prod']]],
  },
};
let kgSim = null, kgSvg = null, kgCur = 'papers', kgHl = null;
function renderKG() {
  const data = KG[kgCur]; if (!window.d3 || !kgSvg) return;
  kgSvg.selectAll('*').remove();
  const W = kgSvg.node().clientWidth, H = kgSvg.node().clientHeight;
  kgSvg.attr('viewBox', `0 0 ${W} ${H}`);
  kgSvg.append('text').attr('x', 12).attr('y', 22).attr('font-family', C.mono).attr('font-size', 11).attr('fill', C.amber).text(`${data.name} · per-dataset 分图`);
  const nodes = data.nodes.map(n => ({ id: n[0], lbl: n[1], r: n[2], c: n[3], g: n[4] }));
  const links = data.links.map(l => ({ source: l[0], target: l[1] }));
  const link = kgSvg.append('g').selectAll('line').data(links).join('line').attr('stroke', C.line).attr('stroke-width', 1).attr('stroke-opacity', .65);
  const node = kgSvg.append('g').selectAll('g').data(nodes).join('g').call(d3.drag()
    .on('start', (e, d) => { if (!e.active) kgSim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on('end', (e, d) => { kgSim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.append('circle').attr('r', d => d.r).attr('fill', d => d.c + '22').attr('stroke', d => d.c).attr('stroke-width', 1.5);
  node.append('text').text(d => d.lbl).attr('text-anchor', 'middle').attr('dy', d => d.r + 12).attr('font-family', C.mono).attr('font-size', 9).attr('fill', C.fgMd);
  node.append('title').text(d => d.lbl);
  node.on('mouseenter', (e, d) => highlight(d.id)).on('mouseleave', () => highlight(kgHl));
  kgSim = d3.forceSimulation(nodes).force('link', d3.forceLink(links).id(d => d.id).distance(56))
    .force('charge', d3.forceManyBody().strength(-190)).force('center', d3.forceCenter(W / 2, H / 2)).force('collide', d3.forceCollide().radius(d => d.r + 8));
  kgSim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
  // 缓存引用用于高亮
  kgSim._link = link; kgSim._node = node; kgSim._links = links;
}
function highlight(id) {
  if (!kgSim) return;
  const links = kgSim._links; const neigh = new Set([id]);
  links.forEach(l => { if (l.source.id === id) neigh.add(l.target.id); if (l.target.id === id) neigh.add(l.source.id); });
  kgSim._link.attr('stroke-opacity', d => (d.source.id === id || d.target.id === id) ? .9 : .15)
    .attr('stroke', d => (d.source.id === id || d.target.id === id) ? C.teal : C.line);
  kgSim._node.attr('opacity', d => (neigh.has(d.id) || !id) ? 1 : .25);
  kgSim._node.select('circle').attr('stroke-width', d => neigh.has(d.id) ? 2.4 : 1.5);
  $('#kg-stat').textContent = id ? `选中 · ${id} · 高亮 ${neigh.size} 个相关节点` : '点击节点看关系 · 拖拽节点重排';
}
function setKG(ds) {
  kgCur = ds; kgHl = null;
  $$('#kg-ds .ds').forEach(b => b.classList.toggle('on', b.dataset.ds === ds));
  const data = KG[ds];
  $('#kg-presets').innerHTML = data.qs.map((q, i) => `<button class="qpreset${i === 0 ? ' on' : ''}" data-i="${i}"><span>${q[0]}</span><span class="qp-s">GraphRAG</span></button>`).join('');
  $$('#kg-presets .qpreset').forEach(b => b.onclick = () => {
    $$('#kg-presets .qpreset').forEach(x => x.classList.toggle('on', x === b));
    const sub = data.qs[+b.dataset.i][1]; kgHl = sub[0];
    // 高亮子图：临时用第一个节点触发，扩展到整个子图集合
    highlightSet(new Set(sub));
  });
  renderKG();
}
function highlightSet(set) {
  if (!kgSim) return;
  const links = kgSim._links;
  kgSim._link.attr('stroke-opacity', d => (set.has(d.source.id) && set.has(d.target.id)) ? .9 : .12)
    .attr('stroke', d => (set.has(d.source.id) && set.has(d.target.id)) ? C.teal : C.line);
  kgSim._node.attr('opacity', d => set.has(d.id) ? 1 : .2);
  kgSim._node.select('circle').attr('stroke-width', d => set.has(d.id) ? 2.6 : 1.5);
  $('#kg-stat').textContent = `GraphRAG 子图 · ${set.size} 个实体 · 遍历高亮`;
}
function initKG() {
  if (!window.d3) { $('#kg-svg').parentNode.innerHTML = '<div style="display:grid;place-items:center;height:100%;color:var(--fg-lo);font-family:var(--font-mono)">d3 加载失败 · 知识图谱不可用</div>'; return; }
  kgSvg = d3.select('#kg-svg');
  $$('#kg-ds .ds').forEach(b => b.onclick = () => setKG(b.dataset.ds));
  $$('#kg-trav .trav').forEach(b => b.onclick = () => { $$('#kg-trav .trav').forEach(x => x.classList.toggle('on', x === b)); $('#kg-stat').textContent = `traverser: ${b.textContent} · 已切换`; });
  window.addEventListener('resize', () => { if (kgCur) renderKG(); });
  setKG('papers');
}

// —— boot ——
document.addEventListener('DOMContentLoaded', () => {
  renderArch(); renderCaps(); renderVer();
  SearchCosmos.init(); TimeMachine.init(); initKG();
});
