# 周转箱清洁追溯平台

面向小型食品工厂的轻量级全栈系统。维护周转箱台账、登记流转事件，根据当前状态自动识别使用风险，并闭环处理问题。

## 功能

- 周转箱：新增、编辑 API、停用，维护唯一编号、名称、位置、清洁状态与备注。
- 备用编号：破损标签更换后旧箱以新编号继续流转。管理员在台账为箱体绑定可选备用编号，编号在主、备编号全集中唯一，冲突返回占用提示；绑定后单笔与批量登记均可直接输入备用编号，后端解析到同一箱体，事件响应与列表仍返回主编号，状态推导与风险识别落到原箱体；批量按解析后的箱体去重，同一箱体以两个编号混入即整批回滚。
- 流转记录：入库、领用、归还、清洗、检查、隔离；事件编号唯一。
- 批量登记：登记流转页支持批量模式，填写批次编号后粘贴或逐项录入箱号（主编号或已绑定备用编号均可，事件编号按“批次编号-主编号”生成），提交前提示重复箱号；后端单事务校验并逐箱执行与单笔一致的状态推导与风险识别，任一箱不存在、已停用、编号冲突或同一箱体以主备编号混入即整批回滚并返回具体箱号。
- 状态推导：事件登记后更新位置、清洁状态、隔离状态及最近检查时间。
- 风险识别：未清洗再次领用、已隔离仍领用、检查超过 30 天有效期仍使用。
- 问题闭环：待处理、已确认、误报、已关闭，支持处理说明；已确认问题可“登记整改”，按问题类型预填建议事件（未清洗领用→清洗，检查过期/隔离领用→检查）与箱号，提交后在同一事务内执行状态推导、关联整改事件并关闭问题，问题响应追加可空的整改事件编号。
- 检查计划：按基准日期与未来天数生成在用箱体的复检计划，从未检查、已过期、即将到期分组排序；一键预填登记检查单，提交后自动退出计划。
- 首页指标及编号、位置、清洁状态、问题状态筛选；完整加载、成功和失败反馈；响应式窄屏布局。
- 首次容器启动自动装入 4 个周转箱及流转/风险示例数据。

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

- `GET/POST /api/crates`；`PUT /api/crates/{id}`；`POST /api/crates/{id}/deactivate`；`POST /api/crates/{id}/backup-code`（绑定备用编号，响应追加可空 `backup_code`；新箱主编号与已绑定备用编号冲突返回 409 占用提示）
- `GET/POST /api/events`（单笔登记，事件体可选 `issue_id`：校验问题存在、已确认、与箱号同箱体且事件类型符合整改建议，通过则单事务关联整改事件并关闭该问题，否则 404/409 拒绝且事件与问题均不变；事件响应与列表均返回箱体主编号 `crate_code`，备用编号登记的事件同样归入主编号）；`POST /api/events/batch`（批量登记，事件编号为“批次编号-主编号”，整批单事务校验，任一失败全部回滚）
- `GET /api/issues`（响应仅追加可空字段 `rectification_event_no`）；`PATCH /api/issues/{id}`
- `GET /api/inspection-plan?base_date=YYYY-MM-DD&days_ahead=N`（只读检查计划：按配置的有效天数计算到期日、剩余天数与分类，停用箱不进入，非法参数返回 422）
- `GET /api/dashboard`；`GET /api/health`

参数校验失败返回 422，非法箱号返回 404，重复箱号/事件编号与停用箱登记返回 409。
