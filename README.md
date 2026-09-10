# 周转箱清洁追溯平台

面向小型食品工厂的轻量级全栈系统。维护周转箱台账、登记流转事件，根据当前状态自动识别使用风险，并闭环处理问题。

## 功能

- 周转箱：新增、编辑 API、停用，维护唯一编号、名称、位置、清洁状态与备注。
- 流转记录：入库、领用、归还、清洗、检查、隔离；事件编号唯一。
- 批量登记：登记流转页支持批量模式，填写批次编号后粘贴或逐项录入箱号，事件编号按“批次编号-箱号”生成，提交前提示重复箱号；后端单事务校验并逐箱执行与单笔一致的状态推导与风险识别，任一箱不存在、已停用或编号冲突即整批回滚并返回具体箱号。
- 状态推导：事件登记后更新位置、清洁状态、隔离状态及最近检查时间。
- 风险识别：未清洗再次领用、已隔离仍领用、检查超过 30 天有效期仍使用。
- 问题闭环：待处理、已确认、误报、已关闭，支持处理说明。
- 首页指标及编号、位置、清洁状态、问题状态筛选；完整加载、成功和失败反馈；响应式窄屏布局。
- 首次容器启动自动装入 3 个周转箱及流转/风险示例数据。

## 一键启动

```bash
docker compose up --build
```

打开 http://localhost:3001 。API 文档位于 http://localhost:3001/api/docs（通过前端代理）；健康检查为 `/api/health`。SQLite 数据保存在 Docker 命名卷 `crate_data`。

停止服务：`docker compose down`。如需同时清空示例与运行数据：`docker compose down -v`。

## 本地开发

后端（Python 3.9+）：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.seed
uvicorn app.main:app --reload
```

前端（Node 20+；本地开发时将 `vite.config.ts` 中代理目标改为 `http://localhost:8000`）：

```bash
cd frontend
npm install
npm run dev
```

## 测试

```bash
cd backend && pytest
cd frontend && npm test && npm run build
```

## API 摘要

- `GET/POST /api/crates`；`PUT /api/crates/{id}`；`POST /api/crates/{id}/deactivate`
- `GET/POST /api/events`；`POST /api/events/batch`（批量登记，事件编号为“批次编号-箱号”，整批单事务校验，任一失败全部回滚）
- `GET /api/issues`；`PATCH /api/issues/{id}`
- `GET /api/dashboard`；`GET /api/health`

参数校验失败返回 422，非法箱号返回 404，重复箱号/事件编号与停用箱登记返回 409。
