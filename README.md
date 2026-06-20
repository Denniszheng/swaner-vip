# Swaner POD Wave Viewer

POD（Print on Demand）波次生产管理系统。对接领星 WMS，实现波次查询、面单打印、SKU 主数据管理、产品图片管理。

## 项目结构

```
├── index.html              # 前端主页（部署到 Cloudflare Pages）
├── wms_server.py           # 后端 API 服务（部署到阿里云香港服务器）
├── cloudflare-worker.js    # Cloudflare Worker（WMS 签名代理，备用）
├── wrangler.toml           # Cloudflare Worker 配置
├── swaner-用户指南.html     # 员工操作指南
└── README.md
```

## 功能模块

- **波次查询**：从领星 WMS 自动拉取波次订单，展示 SKU 明细
- **面单打印**：一键打开/打印快递面单 PDF
- **SKU Master**：产品 SKU 云端主数据管理（增删改查 + 批量同步）
- **图片管理中心**：产品图片上传、缺图概览、批量管理

## 技术栈

- 前端：Vanilla HTML/CSS/JS（部署在 Cloudflare Pages）
- 后端：Python HTTP Server + SQLite（部署在阿里云香港 ECS）
- 代理：Cloudflare Worker（WMS API 签名 + KV 存储）
- 数据：领星 WMS OpenAPI v2

## 部署

### 前端
上传 `index.html` 到 Cloudflare Pages

### 后端
```bash
scp wms_server.py root@47.79.19.2:/opt/swaner/backend/
ssh root@47.79.19.2 "systemctl restart swaner"
```

### Worker
```bash
cd worker && npx wrangler deploy
```
