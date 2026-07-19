/* ============================================================
   Arrow Lake · Mission Control · 双线同步调度
   依赖: GSAP+ScrollTrigger (可选, 失败回退 IntersectionObserver)
   被 narrative.html 直接加载
   ============================================================ */

// ============ 数据 ============
// 11 个架构里程碑（与产品介绍「版本演进」表一致）
const VERSIONS = [
  ['v1.5.2', '安全基线 8C+13H'],
  ['v1.6.0', 'Lake facade + 9 mixin'],
  ['v1.6.1', '死锁修复 + 异步任务'],
  ['v1.6.2', 'Redis 任务共享'],
  ['v1.6.3', '优雅降级'],
  ['v1.7.0', 'hyper-extract KG'],
  ['v1.7.1', 'lancedb0.33 调优'],
  ['v1.8.0', 'Reranker/CLIP/branches'],
  ['v1.8.3', 'HA readiness'],
  ['v1.8.5', '上传修复'],
  ['v1.8.6', 'per-dataset 分图'],
  ['v1.8.7', 'Docling+SQL Worksheet'],
  ['v1.8.8', 'per-dataset KA'],
  ['v1.8.9', 'Reranker+双LLM'],
  ['v1.9.0', '控制面 libSQL'],
];

// 请求深度 → 双轨光标位置（%）· 对应五层架构
const REQ_POS = {
  'hero': 2, '①接入': 9, '②能力·检索': 24, '②能力·KG旁路': 38,
  '③计算': 50, '④引擎': 64, '⑤持久化': 76, '①接入·回': 88, '⟂横切面': 94, '全景': 99,
};

// step → 左管道层映射 + 请求粒子在管道里的纵向位置(%) + 标签
// 粒子贯穿全程，整体五层始终在场，当前层高亮
const STEP_LAYER = { 0: 'hero', 1: 'L1', 2: 'L2', 3: 'L2', 4: 'L2', 5: 'L2', 6: 'L3', 7: 'L4', 8: 'L5', 9: 'L1', 10: 'CC', 11: 'ALL' };
const LAYER_POS = { hero: 0, L1: 10, L2: 30, L3: 50, L4: 70, L5: 90, CC: 96, ALL: 50 };
const LAYER_LABEL = { hero: '入口', L1: '① 接入层', L2: '② 能力层', L3: '③ 计算层', L4: '④ 引擎层', L5: '⑤ 持久化', CC: '⟂ 横切面', ALL: '全景' };

// ============ helpers ============
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const findStepOfVer = (ver) => {
  const steps = $$('.step');
  const i = steps.findIndex(s => (s.dataset.vers || '').split('|').includes(ver));
  return i >= 0 ? i : 0;
};

// ============ 初始化顶部双轨 / 版本轴 / 结尾 ============
function initRails() {
  $('#reqRailSegs').innerHTML = [1, 2, 3, 4, 5].map(() => '<span></span>').join('');
  $('#verRailSegs').innerHTML = VERSIONS.map(() => '<span></span>').join('');
}
function initRailList() {
  $('#railList').innerHTML = VERSIONS.map(([v, note]) => `
    <li class="rail-item" data-ver="${v}" data-step="${findStepOfVer(v)}">
      <span class="rail-dot"></span>
      <div class="rail-body">
        <div class="rail-ver">${v}</div>
        <div class="rail-note">${note}</div>
      </div>
    </li>`).join('');
}
function initFinale() {
  $('#finaleAxis').innerHTML = VERSIONS.map(([v, note]) => `
    <div class="fnode" data-step="${findStepOfVer(v)}" title="${v} · ${note}">
      <div class="fnode-dot"></div>
      <div class="fnode-ver">${v}</div>
      <div class="fnode-note">${note}</div>
    </div>`).join('');
}

// ============ 激活章节：切换 viz + 移动双轨 + 高亮版本 ============
let currentStep = -1;
function activateStep(idx) {
  if (idx === currentStep) return;
  currentStep = idx;
  const step = $$('.step')[idx];
  if (!step) return;
  const viz = step.dataset.viz;
  const req = step.dataset.req;
  const vers = (step.dataset.vers || '').split('|').filter(Boolean);

  $$('.step').forEach(s => s.classList.toggle('is-active', +s.dataset.step === idx));
  $$('.viz-card').forEach(c => c.classList.toggle('is-active', c.dataset.viz === viz));
  if (window.renderViz) window.renderViz(viz);

  // 请求轨
  $('#reqCursor').style.left = (REQ_POS[req] ?? 0) + '%';
  $('#reqRailVal').textContent = req || '';
  // 版本轨
  const firstIdx = VERSIONS.findIndex(([v]) => v === vers[0]);
  $('#verCursor').style.left = (firstIdx >= 0 ? (firstIdx + 0.5) / VERSIONS.length * 100 : 0) + '%';
  $('#verRailVal').textContent = vers[0] || '';
  // 版本轴高亮
  $$('.rail-item').forEach(li => li.classList.toggle('is-active', vers.includes(li.dataset.ver)));
  // 左管道：激活当前层 + 移动请求粒子（整体始终在场，当前层高亮）
  const layer = STEP_LAYER[idx];
  $$('.layer').forEach(el => el.classList.toggle('is-active', layer === 'ALL' || el.dataset.layer === layer));
  $$('.crosscut').forEach(el => el.classList.toggle('is-active', layer === 'CC'));
  const fp = $('#flowParticle'); if (fp) fp.style.top = (LAYER_POS[layer] ?? 0) + '%';
  const mf = $('#mapFoot'); if (mf) mf.textContent = '请求在 · ' + (LAYER_LABEL[layer] || '');
}

// ============ 滚动同步：GSAP 优先，失败回退 IO ============
function initScroll() {
  // IntersectionObserver 中线判定：视口中间 10% 横条，配合 .step min-height≈1屏，
  // 确保同时一刻只一个 step active（比 ScrollTrigger 区间 onToggle 更稳，避免矮 step 多个同 active）
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) activateStep(+e.target.dataset.step); });
  }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
  $$('.step').forEach(s => io.observe(s));
}

// ============ 点击跳转 ============
function initClicks() {
  $$('.rail-item, .fnode').forEach(el => {
    el.addEventListener('click', () => {
      const target = $$('.step')[+el.dataset.step];
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  });
}

// ============ boot ============
document.addEventListener('DOMContentLoaded', () => {
  initRails(); initRailList(); initFinale(); initClicks();
  activateStep(0);
  setTimeout(initScroll, 80);
});
