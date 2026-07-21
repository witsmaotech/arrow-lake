# Showcase 页面合并改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把原型首页 `prototype/index.html` 作为骨架、整合 showcase 三王牌,产出新的 `console/showcase.html`(替换现有)。

**Architecture:** 整页骨架 + 渲染 JS 来自 `prototype/index.html`(复制),三王牌 `<section id="cards">` 整块从旧 `console/showcase.html` 搬入,资源引用指向 `console/assets/`(tokens+app+landing+showcase CSS / layout+showcase.js + d3 CDN)。静态 mock 展示页,不走 renderShell。

**Tech Stack:** 原生 JS + ES 模块、零构建、d3 7.9.0 CDN(SRI)、canvas 2D、playwright + chromium-1217 验证。

**Spec:** `docs/superpowers/specs/2026-07-21-showcase-merge-design.md`

## Global Constraints

- 零外部 npm 依赖、零构建(浏览器原生加载 ES 模块/脚本)。
- 允许的 CDN 仅 d3 7.9.0(SRI integrity 沿用旧 showcase 现有值)。
- showcase 是 mock 静态展示页,**不接 `/api/v1`**,**不走 `console-layout.js` renderShell**(独立 sc-nav)。
- 资源相对路径 `assets/...`(相对 `console/showcase.html` → `console/assets/`,已确认全齐)。
- trunk-based:每任务 commit 到 master,conventional commits + `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 验证基线:`.venv/bin/python3` + playwright chromium-1217(`~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome`)。
- 前端 dev server:5189(`python3 -m http.server 5189 --directory console`,已起,task ID `brdptr8zh`)。
- **此为静态 HTML 合成,无 unit test;以 playwright 渲染断言作为每任务的测试 gate**(替代 TDD red-green)。

---

## Task 1: 提取旧三王牌 + 搭首页骨架 + 资源/nav 调整

**Files:**
- Create(临时备份):`/tmp/showcase-cards.html`(旧三王牌 section)
- Overwrite:`console/showcase.html`(复制 `prototype/index.html` 后就地编辑)

**Interfaces:**
- Consumes: `docs/frontend-prototype/index.html`(骨架源)、旧 `console/showcase.html` L62-159(三王牌源,本任务备份)、`console/assets/{tokens,app,landing}.css` + `console/assets/layout.js`
- Produces: 新 `console/showcase.html`(首页骨架 + 调整后的 head/nav;此阶段**尚未**含三王牌 section 与 showcase.js,故暂不引 showcase.js/d3,避免 null 报错)

- [ ] **Step 1: 提取旧三王牌 section 到临时备份(覆盖前抢救)**

Run:
```bash
sed -n '62,159p' console/showcase.html > /tmp/showcase-cards.html
wc -l /tmp/showcase-cards.html
```
Expected: 98 行左右(`<section id="cards">` 到 `</section>`)。

- [ ] **Step 2: 复制首页骨架覆盖 showcase.html**

Run:
```bash
cp docs/frontend-prototype/index.html console/showcase.html
```

- [ ] **Step 3: 改 title 为旗舰展示风格**

Edit `console/showcase.html`,把 `<title>Arrow Lake · 多模态数据湖仓</title>` 改为:
```html
<title>Arrow Lake · 旗舰展示 · 首页 + 三王牌</title>
```

- [ ] **Step 4: head 追加 showcase.css(三王牌样式,本任务先引上不冲突)**

在 `console/showcase.html` 的 `<link rel="stylesheet" href="assets/landing.css"/>` 之后加一行:
```html
<link rel="stylesheet" href="assets/showcase.css"/>
```

- [ ] **Step 5: 替换 nav — 首页 `.lnav` → showcase `.sc-nav`(锚点适配新 section)**

把 `console/showcase.html` 里 `<!-- ===== TOP NAV ===== -->` 到对应 `</nav>`(原 L15-29)整块替换为:
```html
<!-- ===== TOP NAV ===== -->
<nav class="sc-nav">
  <a class="sc-brand" href="index.html"><span class="sc-mark">AL</span><span><b>Arrow Lake</b><span class="sc-sub">旗舰展示</span></span></a>
  <div class="sc-nav-links">
    <a href="#architecture">架构全景</a>
    <a href="#capabilities">六能力</a>
    <a href="#cards">三张王牌</a>
    <a href="#stack">技术栈</a>
    <a href="#deploy">部署</a>
  </div>
  <div class="sc-nav-cta">
    <a class="btn btn-ghost btn-sm" href="narrative.html">叙事版</a>
    <a class="btn btn-primary btn-sm" href="index.html">控制台 →</a>
  </div>
</nav>
```
(去掉旧 `#ver` 版本演进锚点 —— 不纳入;`#arch`→`#architecture` 适配首页 section id)

- [ ] **Step 6: hero CTA "进入控制台" 链接保持 dashboard.html?改 index.html**

说明:首页原 `<a class="btn btn-primary" href="dashboard.html">进入控制台</a>`(hero)。console 的首页是 `index.html`(dashboard 已并入)。把 hero 区所有 `href="dashboard.html"` 改为 `href="index.html"`(Edit,replace_all)。

- [ ] **Step 7: 验证骨架渲染(此阶段不引 showcase.js)**

写验证脚本 `/tmp/verify_showcase_skeleton.py`:
```python
from playwright.sync_api import sync_playwright
import pathlib
CHROME = str(pathlib.Path.home()/".cache/ms-playwright/chromium-1217/chrome-linux64/chrome")
URL = "http://localhost:5189/showcase.html"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page()
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(1200)
    assert pg.locator("#archDiagram > *").count() > 0, "archDiagram 未渲染"
    assert pg.locator("#capGrid > *").count() > 0, "capGrid 未渲染"
    assert pg.locator("#stackWall > *").count() > 0, "stackWall 未渲染"
    assert pg.title().startswith("Arrow Lake · 旗舰展示"), f"title 异常: {pg.title()}"
    assert not errors, f"console errors: {errors[:5]}"
    print("PASS skeleton")
    b.close()
```
Run: `cd /home/witshine/wits-projs/wits-infra-dintellihub && NO_PROXY=127.0.0.1,localhost .venv/bin/python3 /tmp/verify_showcase_skeleton.py`
Expected: `PASS skeleton`。若 capGrid 等为空 → 检查 layout.js 是否在 `</body>` 前加载(首页原引用 `assets/layout.js`,console/assets/layout.js 在,路径对)。

- [ ] **Step 8: Commit**

```bash
git add console/showcase.html
git commit -m "$(cat <<'EOF'
feat(showcase): 以原型首页为骨架重建(去重重组 B)

复制 prototype/index.html 为新 console/showcase.html,调整 title/css 引入,
nav 换 sc-nav 适配 #architecture/#cards。三王牌与 showcase.js 待 Task 2 插入。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 插入三王牌 section + 引入 showcase.js / d3

**Files:**
- Modify:`console/showcase.html`(插入 section + head 加 d3 + body 加 showcase.js)
- Read(源):`/tmp/showcase-cards.html`(Task 1 备份的旧三王牌)

**Interfaces:**
- Consumes:`/tmp/showcase-cards.html`、`console/assets/showcase.js`、d3 7.9.0 CDN、`console/assets/showcase.css`(Task 1 已引)
- Produces:完整新 `console/showcase.html`(骨架 + 三王牌均可渲染)

- [ ] **Step 1: 在 capabilities 与 demo-strip 之间插入三王牌 section**

定位 `console/showcase.html` 中:
```
<!-- ===== CAPABILITIES ===== -->
<section id="capabilities" class="section">
  ...
  <div class="grid g-3 cap-grid" id="capGrid"></div>
</section>

<!-- ===== DEMO STRIP ===== -->
```
在 `</section>`(capabilities 结束)与 `<!-- ===== DEMO STRIP ===== -->` 之间插入 `/tmp/showcase-cards.html` 的全部内容(`<section id="cards" class="sc-section">…</section>`)。

操作:用 Edit 把 `<!-- ===== DEMO STRIP ===== -->` 作为锚点,在其前插入 /tmp/showcase-cards.html 内容(先 Read 该临时文件拿到确切 HTML,再作为 new_string 前置拼接 `<!-- ===== DEMO STRIP ===== -->`)。

- [ ] **Step 2: head 加 d3 CDN(showcase.js 依赖,须在 showcase.js 之前加载)**

在 `console/showcase.html` 的 `<title>` 之后、`</head>` 之前(或紧跟 `<link>` 之后)加:
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js" integrity="sha384-CjloA8y00+1SDAUkjs099PVfnY2KmDC2BZnws9kh8D/lX1s46w6EPhpXdqMfjK6i" crossorigin="anonymous"></script>
```
(SRI integrity 与旧 showcase.html 完全一致)

- [ ] **Step 3: body 末尾引入 showcase.js(在 layout.js 之后)**

定位 `console/showcase.html` 末尾 `<script src="assets/layout.js"></script>`,在其后加:
```html
<script src="assets/showcase.js"></script>
```

- [ ] **Step 4: 验证三王牌渲染**

写 `/tmp/verify_showcase_cards.py`:
```python
from playwright.sync_api import sync_playwright
import pathlib
CHROME = str(pathlib.Path.home()/".cache/ms-playwright/chromium-1217/chrome-linux64/chrome")
URL = "http://localhost:5189/showcase.html"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page()
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2000)  # d3/canvas 动画
    assert pg.locator("#cards").count() == 1, "三王牌 section 缺失"
    assert pg.locator("#sq-canvas").count() == 1, "检索宇宙 canvas 缺"
    assert pg.locator("#kg-svg > *").count() > 0, "KG 子图 d3 未渲染节点"
    # 骨架仍渲染
    assert pg.locator("#capGrid > *").count() > 0, "capGrid 退化"
    assert not errors, f"console errors: {errors[:5]}"
    print("PASS cards")
    b.close()
```
Run: `NO_PROXY=127.0.0.1,localhost .venv/bin/python3 /tmp/verify_showcase_cards.py`
Expected: `PASS cards`。常见失败:showcase.js 报 `d3 is not defined` → 检查 d3 CDN 是否在 showcase.js 之前;报 canvas null → showcase.js 执行时 DOM 未就绪(确认 showcase.js 在 body 末尾、layout.js 之后)。

- [ ] **Step 5: Commit**

```bash
git add console/showcase.html
git commit -m "$(cat <<'EOF'
feat(showcase): 插入三王牌 section + 引入 showcase.js/d3

检索宇宙(canvas)/ 湖仓时间机器 / KG 探索(d3)整合到六能力之后。
showcase.js + d3 7.9.0(SRI)加载,d3 先于 showcase.js。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: NAV 锚点验证 + 去重核查 + 完整 playwright 验收

**Files:**
- Verify only(必要时修):`console/showcase.html`

**Interfaces:**
- Consumes:Task 1/2 产物
- Produces:验证通过的新 `console/showcase.html`(对齐 spec §7 全 checklist)

- [ ] **Step 1: 去重视觉核查(grep 确认无残留重复 section)**

Run:
```bash
echo "--- section 清单(确认无重复 HERO/架构/版本演进)---"
grep -nE "<!-- =====|<!-- ====|<section id=" console/showcase.html
```
Expected:仅一个 HERO(`<!-- ===== HERO ===== -->`)、一个架构(`id="architecture"`)、无 `id="ver"`(版本演进)、有三王牌 `id="cards"`。

- [ ] **Step 2: 写完整验收脚本(对齐 spec §7)**

写 `/tmp/verify_showcase_final.py`:
```python
from playwright.sync_api import sync_playwright
import pathlib
CHROME = str(pathlib.Path.home()/".cache/ms-playwright/chromium-1217/chrome-linux64/chrome")
URL = "http://localhost:5189/showcase.html"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    # 骨架 JS 全渲染
    for sid in ["archDiagram", "capGrid", "stackWall", "useGrid", "deployGrid"]:
        assert pg.locator(f"#{sid} > *").count() > 0, f"{sid} 未渲染"
    # 三王牌
    assert pg.locator("#sq-canvas").count() == 1, "检索宇宙 canvas 缺"
    assert pg.locator("#kg-svg > *").count() > 0, "KG 子图未渲染"
    # 无 /api/v1 调用(mock 展示页)
    assert "api/v1" not in pg.content(), "误接真 API"
    # 横向溢出
    sw = pg.evaluate("document.documentElement.scrollWidth")
    cw = pg.evaluate("document.documentElement.clientWidth")
    assert sw <= cw, f"横向溢出 {sw-cw}px"
    # 锚点跳转
    pg.click('a[href="#cards"]')
    pg.wait_for_timeout(500)
    assert pg.evaluate("window.scrollY") > 100, "锚点未跳转"
    # console error
    assert not errors, f"console errors: {errors[:5]}"
    print("PASS final")
    b.close()
```
Run: `NO_PROXY=127.0.0.1,localhost .venv/bin/python3 /tmp/verify_showcase_final.py`
Expected: `PASS final`。

- [ ] **Step 3: 像素截图存档(肉眼复核设计一致性)**

Run(在 final 脚本通过后,可加截图):
```bash
NO_PROXY=127.0.0.1,localhost .venv/bin/python3 -c "
from playwright.sync_api import sync_playwright
import pathlib
CHROME=str(pathlib.Path.home()/'.cache/ms-playwright/chromium-1217/chrome-linux64/chrome')
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=CHROME)
    pg=b.new_page(viewport={'width':1440,'height':900})
    pg.goto('http://localhost:5189/showcase.html', wait_until='networkidle')
    pg.wait_for_timeout(2000)
    pg.screenshot(path='/tmp/showcase_full.png', full_page=True)
    print('saved /tmp/showcase_full.png')
    b.close()
"
```
人工/Read 图片复核:section 节奏、三王牌视觉、无样式错乱。

- [ ] **Step 4: 若 Step 1-3 发现问题,修复后重跑;无问题则收尾**

常见修复点:sc-nav 与首页 hero 间距(landing.css 未覆盖 sc-nav → showcase.css 已定义 sc-nav,应正常);三王牌 section 宽度溢出(检查 sc-section 宽度约束)。

- [ ] **Step 5: Commit(若有修复)**

```bash
git add console/showcase.html
git commit -m "$(cat <<'EOF'
fix(showcase): 锚点/去重/溢出收尾(验收通过)

spec §7 全 checklist 通过:骨架+三王牌渲染、0 console error、无溢出、锚点跳转。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```
(若 Step 4 无修复,跳过此 commit,Task 2 即终态。)

---

## Self-Review(plan 自审)

**1. Spec coverage:**
- §3 section 编排 → Task 1(骨架+nav)+ Task 2(插入三王牌)✓
- §4 依赖合并(tokens+app+landing+showcase / layout+showcase.js+d3)→ Task 1 Step4/Task2 Step2-3 ✓
- §5 去重(删 showcase HERO/架构/版本演进)→ 复制首页即天然去重 + Task 3 Step1 核查 ✓
- §6 NAV sc-nav 锚点适配 → Task 1 Step5 + Task 3 Step2 锚点断言 ✓
- §7 验证全 checklist → Task 3 Step2 final 脚本 ✓

**2. Placeholder scan:** 无 TBD/TODO;"插入 /tmp/showcase-cards.html 内容"是精确文件操作(非 placeholder,执行时 Read 该文件拿确切 HTML);d3 SRI integrity 是确切值。

**3. Type/命名一致:** section id 全程一致(`#architecture`/`#capabilities`/`#cards`/`#stack`/`#deploy`);canvas/svg id 一致(`#sq-canvas`/`#kg-svg`);脚本名一致(`showcase.js`/`layout.js`)。✓
