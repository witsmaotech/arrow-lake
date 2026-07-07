# vendor/

预构建第三方库(离线 + 防供应链篡改)。当前为空。

## apache-arrow(`stream` 模式用)

当前 `olap.html` 通过 importmap **pin 到固定版本** `apache-arrow@21.1.0`,运行时从 esm.sh CDN 加载(仅 `stream=true` 时才 dynamic import)。pin 版本消除版本重定向/漂移攻击;但仍信任 esm.sh 不被攻陷。

### 完整 vendor 化(消除运行时 CDN 依赖)

需 Node 18+ 与打包工具。两种方式:

```bash
# 方式 1:esbuild 本地打包(推荐,产物自包含)
npm init -y && npm i esbuild apache-arrow
node -e "require('esbuild').buildSync({entryPoints:['node_modules/apache-arrow/...'],bundle:true,format:'esm',outfile:'apache-arrow.bundle.js'})"

# 方式 2:下载 jsDelivr 官方 ESM 单文件
curl -fsSL https://cdn.jsdelivr.net/npm/apache-arrow@21.1.0/+esm -o apache-arrow.bundle.js
# 注意校验自包含(grep 'from .*https' 应为 0)
```

vendor 化后,改 `olap.html` 的 importmap:
```json
{"imports":{"apache-arrow":"./vendor/apache-arrow.bundle.js"}}
```

`stream.js` 的 `await import("apache-arrow")` 无需改动。

参见设计文档 `docs/architecture-design/duckdb-sql-worksheet.md` ADR-6(supply-chain 权衡)。
