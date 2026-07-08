# Arrow Lake Console · Design System (MASTER)

> 由 `/ui-ux-pro-max` 生成并裁剪 · 企业级数据湖仓控制台 · 2026-07-04
> 真值源 = `assets/tokens.css`。所有页面只消费 token，不写裸 hex。

## 来源与裁决

| 维度 | skill 推荐 | 裁决（本系统） |
|---|---|---|
| Pattern | Real-Time / Operations Landing | ✅ 采用：hero + 实时预览 + 指标 + how-it-works + CTA |
| Style | Soft UI Evolution（AA+，Excellent） | ✅ 采用**暗变体**：柔影 + 微凸表面 + AA+ |
| 字体 | Plus Jakarta Sans（enterprise/b2b/admin） | ✅ UI 用 Jakarta；**数据/代码加 JetBrains Mono** |
| 主色 | 绿 `#15803D`（light） | 🔧 调暗为 **lake teal `#14B8A6`**（呼应"湖仓"，暗底可读） |
| 强调/CTA | 琥珀 `#D97706` | ✅ 采用，作主动作/进行中 |
| 状态色 | green/amber/red | ✅ ok `#22C55E` · warn `#F59E0B` · danger `#EF4444` · info `#38BDF8` |
| 密度 dial | 8/10 Dense | ✅ sidebar 248 · header 56 · row 36 · gap 8 |
| 动效 dial | 5/10 Standard | ✅ 200ms · `cubic-bezier(.16,1,.3,1)` · reduced-motion 降级 |

## 反模式（skill 明令 + 自定）

- ❌ AI 紫/粉渐变 · ❌ emoji 当图标（用内联 SVG）· ❌ 仅颜色编码状态（形状+文字）
- ❌ 次要文本对比 < 4.5:1 · ❌ 常驻不可关动画 · ❌ 触控目标 < 44px（触控断点）

## 颜色（暗）— 见 tokens.css

背景层级 ink-950→ink-700；文本 hi/md/lo；主色 teal、CTA amber、四态色。
所有文本 token on 暗底均 ≥ 4.5:1（AA），主文本 ≥ 7:1（AAA）。

## 字阶

readout `clamp(2.4rem,1.6rem+3vw,3.75rem)` · h1 `1.5rem/600` · h2 `1.125rem/600` · h3 `0.9375rem/600` · body `0.8125rem/400` · mono `0.8125rem` · cap `0.6875rem/大写`.

## 间距 / 半径 / 影

sp 4/8/12/16/24/32/48 · radius 6/8/12 · 软影 3 档（`--shadow-1/2/3`，Soft UI 微凸）。

## 签名元素（产品特征 → 视觉）

1. **深度遥测轨**：长任务穿五层（接入/能力/计算/引擎/持久化）逐段点亮；KG 旁路虚线分叉。
2. **状态灯语言**：`●` ok / `▲` warn / `✕` danger / `◐` partial / `○` pending（形状+颜色）。
3. **遥测条**：页顶等宽读数（QPS · p50/p99 · 负载 · 在线任务）。
4. **柔面面板**：soft shadow + 1px 边 + 微渐变表面（Soft UI Evolution 签名）。
5. **`</> View API`**：每个写动作旁，弹 cURL + Python SDK。

## 页面清单（本原型）

| 页面 | 文件 | 角色 |
|---|---|---|
| Landing | `index.html` | 公开·能力落地 |
| Login | `login.html` | JWT/API Key |
| Dashboard | `dashboard.html` | 总览·遥测 |
| Datasets | `datasets.html` | 目录 |
| Dataset 工作区 | `dataset-detail.html` | 9-Tab 核心 |
| Search | `search.html` | 5 模式检索 |
| RAG | `rag.html` | 对话+引用 |
| KG | `kg.html` | 图谱浏览器 |
| OLAP | `olap.html` | SQL 分析 |
