# Showcase 页面合并改造设计 · 2026-07-21

> **状态**:设计已确认(方案 B 去重重组),待写实施计划
> **产物**:替换 `console/showcase.html`
> **基准**:`docs/frontend-prototype/index.html`(原型首页骨架)+ 现 `console/showcase.html` 三王牌
> **相关**:`docs/v1.9.1-frontend-core-impl-plan.md` §13.7(旗舰展示页)、`docs/frontend-design/`

---

## 1. 背景

用户反馈"旗舰展示页好像换了",要求:**把原型首页作为旗舰页,把三王牌整合进去,改造成新 showcase.html**。

事实核查(`git diff` + 文件对比):
- `console/showcase.html` 与 `docs/frontend-prototype/showcase.html` **完全相同**(7-19 移植后零改动),客观上未"换"。
- 真正诉求:把**信息更丰富的原型首页**(index.html,含 HERO live console / 架构全景 / 六能力 / DEMO / STACK WALL / USE CASES / DEPLOY / CTA)作为旗舰页骨架,把现有 showcase 的**三王牌深度交互**(检索宇宙 / 湖仓时间机器 / KG 探索)整合进去,产出一个更完整、更有冲击力的旗舰展示页。

## 2. 目标 & 非目标

**目标**:
- 新 `console/showcase.html` = 原型首页骨架 + 三王牌整合(去重)。
- 保留首页的架构/能力/技术栈/场景/部署信息丰富度。
- 保留三王牌的可交互深度(canvas 向量漏斗 / Lance 时间机器 / d3 KG 子图)。
- 仍是 mock 静态展示页,零后端依赖,不走 `console-layout.js` renderShell。

**非目标**:
- 不接真 `/api/v1`(展示页保持 mock;接真数据属"升级",另议)。
- 不改全局 NAV(showcase 保持独立 sc-nav,不并入 console-layout)。
- 不动 narrative.html / index.html / 其他页。

## 3. 设计:section 编排(方案 B 去重重组)

```
NAV (sc-nav,锚点适配)
HERO (首页 live console + QPS)
TRUST STRIP
架构全景 (首页 archDiagram)
六能力主线 (capGrid)
┌─ 三王牌 (核心交互高潮) ──┐
│  检索宇宙 (canvas sq-canvas)│
│  湖仓时间机器              │
│  KG 探索 (d3 kg-svg)       │
└────────────────────────────┘
DEMO STRIP (GraphRAG)
STACK WALL
USE CASES
DEPLOY
CTA
```

- **基准**:整页骨架 + 渲染 JS 来自 `docs/frontend-prototype/index.html`(复制其 body section + 内联 script)。
- **整合**:三王牌 section 来自现 `console/showcase.html`(`<section id="cards">` 整块),插入位置在"六能力主线"之后、"DEMO STRIP"之前。

## 4. 依赖合并(console/assets/ 全有,无需复制文件)

**CSS**:
- `tokens.css` + `app.css`(首页与 showcase 本共用)
- `landing.css`(首页骨架样式)
- `showcase.css`(三王牌 `sc-*` 样式)
- 前缀不冲突:`sc-*`(showcase)vs 普通类名(首页/landing),可共存全引入。

**JS**:
- `layout.js`(首页骨架图标 / sparkline / donut 渲染)
- `showcase.js`(三王牌交互:canvas 检索宇宙 + Lance 时间机器 + d3 KG 子图)
- d3 7.9.0 CDN(SRI 沿用 showcase 现有 integrity)
- 首页内联渲染 script(archDiagram / capGrid / stackWall / useGrid / deployGrid / depthMini / miniKg)

**DOM id 不冲突**:首页(archDiagram / capGrid / useGrid / deployGrid / depthMini / miniKg)vs 三王牌(sq-canvas / kg-svg)。

## 5. 去重规则

从 showcase 删除(用首页更丰富版本替代):
- ❌ showcase HERO → 用首页 live console + QPS 版
- ❌ showcase 架构全景(`<section id="arch">`)→ 用首页 archDiagram 版
- ❌ showcase "版本演进"(`<section id="ver">`)→ 不纳入(与 STACK WALL 主题重复,B 方案未列)

保留 showcase 的:
- ✅ NAV(sc-nav,锚点适配新 section)
- ✅ 三王牌 section(`<section id="cards">` 整块)
- ✅ showcase.js + d3 CDN

## 6. NAV

- 保留 showcase 现有 `sc-nav`(品牌 AL + 锚点:架构全景 / 三王牌 + 返回 index 入口)。
- 锚点 href 适配新 section id:`#architecture`(首页架构)/ `#cards`(三王牌)等。
- 不走 `console-layout.js` renderShell(旗舰展示页为公开 mock,无 auth guard,memory 已确认)。

## 7. 验证标准

playwright + chromium-1217(沿用项目验证基线):
- [ ] 渲染无横向溢出(全 section)
- [ ] 0 console error(含 d3 / canvas 加载)
- [ ] d3 KG 子图(`#kg-svg`)正常渲染节点 + 边
- [ ] canvas 检索宇宙(`#sq-canvas`)动画跑
- [ ] 首页骨架 JS 全部渲染(archDiagram / capGrid / stackWall / useGrid / deployGrid 非空)
- [ ] NAV 锚点跳转生效
- [ ] 像素级:与设计一致(非肉眼看截图)
- mock 数据保持,无 `/api/v1` 调用

## 8. 风险 & 对策

| 风险 | 对策 |
|---|---|
| css 命名冲突(landing vs showcase) | 前缀隔离(sc-* vs 普通),全引入;验证时检查视觉错乱 |
| 两套 JS 都跑、id 冲突 | id 已确认不冲突;验证 0 console error |
| d3 CDN 离线不可达 | SRI 沿用;离线降级(KG 子图空,不阻塞) |
| 首页内联 script 依赖 layout.js 全局函数 | 确保 layout.js 在内联 script 之前加载 |
| section 过长信息过载 | B 方案已去重;视觉分段(空白 / 标题)保证节奏 |

## 9. 实施路径(概要,细节交 writing-plans)

1. 复制 `docs/frontend-prototype/index.html` → 新 `console/showcase.html`(保留 sc-nav 风格 nav)
2. 调整 css/js 引入(tokens + app + landing + showcase / layout + showcase.js + d3)
3. 在"六能力主线"后插入三王牌 section(从旧 showcase.html L62-159 搬)
4. 去重(复制时即用首页版替代 showcase 旧 HERO / 架构 / 版本演进)
5. NAV 锚点适配
6. playwright 验证(§7)
7. 替换 commit
