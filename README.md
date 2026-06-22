# 工具借用亭 JSON API

本地工具借用管理系统，提供工具目录、逾期规则、借出归还、损坏上报、权限校验和审计日志，不依赖门禁或库存系统。

## 启动

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 测试

```bash
pip install pytest requests
python -m pytest test_tool_station.py -v
```

## 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/init` | 初始化样例数据 |
| POST | `/api/tools/import` | 导入工具清单（**原子操作**：有重复则整批拒绝） |
| GET | `/api/tools` | 查看所有工具 |
| GET | `/api/tools/{tool_id}` | 查看单件工具详情 |
| POST | `/api/tools/{tool_id}/borrow` | 借出工具 |
| POST | `/api/tools/{tool_id}/return` | 归还工具 |
| POST | `/api/tools/{tool_id}/damage` | 上报损坏（借出状态下不改变 status） |
| POST | `/api/tools/{tool_id}/damage/close` | 关闭损坏报告（仅 admin；借出状态下保留借出状态） |
| POST | `/api/overdue/check` | 检查并标记逾期工具 |
| GET | `/api/rules` | 获取逾期规则 |
| PUT | `/api/rules` | 修改逾期规则（仅 admin） |
| GET | `/api/tools/{tool_id}/history` | 按工具编号查询历史 |
| GET | `/api/audit` | 导出审计日志 |
| POST | `/api/operators` | 注册操作员（仅 admin） |
| GET | `/api/operators` | 查看所有操作员 |

## 工具状态字段

| status 值 | 含义 |
|-----------|------|
| `available` | 可借 |
| `borrowed` | 已借出（可能同时有 `damage_note` 损坏备注） |
| `overdue` | 已逾期（可能同时有 `damage_note` 损坏备注） |
| `damaged` | 已损坏（不在借出中） |
| `overdue_returned` | 逾期已归还 |

**损坏状态说明**：借出状态下上报损坏不会改变 `status`，仅在 `damage_note` 字段记录损坏信息，`current_borrower` 保持不变。归还时若存在损坏备注，工具状态才会转为 `damaged`。

## 工具对象字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool_id` | string | 工具编号（唯一） |
| `name` | string | 工具名称 |
| `category` | string | 分类 |
| `status` | string | 当前状态 |
| `current_borrower` | string\|null | 当前借用人 |
| `borrow_time` | string\|null | 借出时间（ISO 8601） |
| `due_time` | string\|null | 应还时间（ISO 8601） |
| `return_time` | string\|null | 归还时间（ISO 8601） |
| `damage_note` | string\|null | 损坏备注 |
| `damage_reporter` | string\|null | 损坏上报人 |
| `damage_report_time` | string\|null | 损坏上报时间 |
| `is_overdue` | boolean | 是否逾期 |
| `created_at` | string | 创建时间 |

## 逾期规则字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `max_borrow_hours` | integer | 默认最大借出时长（小时） |
| `overdue_check_enabled` | boolean | 是否启用逾期检查 |
| `auto_mark_overdue` | boolean | 是否自动标记逾期 |

## 审计日志字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 日志 ID |
| `action` | string | 操作类型（init / import_tool / borrow / return / damage_report / damage_close / overdue_mark / rules_update / register_operator） |
| `tool_id` | string\|null | 关联工具编号 |
| `operator` | string | 操作人 |
| `detail` | string\|null | 详细信息 |
| `success` | boolean | 操作是否成功 |
| `timestamp` | string | 时间戳（ISO 8601） |

## 借用记录字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 记录 ID |
| `tool_id` | string | 工具编号 |
| `borrower` | string | 借用人 |
| `borrow_time` | string | 借出时间 |
| `due_time` | string | 应还时间 |
| `return_time` | string\|null | 归还时间 |
| `is_overdue` | boolean | 是否逾期 |
| `damage_note` | string\|null | 损坏备注 |
| `damage_reporter` | string\|null | 损坏上报人 |
| `damage_report_time` | string\|null | 损坏上报时间 |

## 操作员字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `operator_id` | string | 操作员 ID |
| `display_name` | string | 显示名 |
| `role` | string | 角色（admin / user） |

## 权限规则

| 操作 | admin | user |
|------|-------|------|
| 导入工具 | Y | N |
| 借出工具 | Y | Y |
| 归还工具 | Y（任意） | Y（仅自己借的） |
| 上报损坏 | Y | Y |
| 关闭损坏报告 | Y | N |
| 修改规则 | Y | N |
| 注册操作员 | Y | N |
| 查看信息 | Y | Y |

## curl 示例

### 1. 初始化样例数据

```bash
curl -X POST http://localhost:8000/api/init
```

返回：

```json
{
  "ok": true,
  "message": "初始化完成，已导入5件工具和3名操作员"
}
```

### 2. 导入工具清单

**原子性**：批量导入是原子操作。只要有任何一个工具编号重复，整批全部拒绝，无部分写入。

```bash
curl -X POST http://localhost:8000/api/tools/import \
  -H "Content-Type: application/json" \
  -d '{
    "tools": [
      {"tool_id": "SAW-001", "name": "手锯", "category": "手动工具"},
      {"tool_id": "WRENCH-001", "name": "重复扳手", "category": "手动工具"},
      {"tool_id": "HAMMER-001", "name": "新锤子", "category": "手动工具"}
    ],
    "operator": "admin"
  }'
```

返回（WRENCH-001 已存在，**整批拒绝**，SAW-001 和 HAMMER-001 均未导入，HTTP 409）：

```json
{
  "detail": {
    "error": "duplicate_tool_ids",
    "message": "整批导入被拒绝：检测到 1 个重复工具编号（WRENCH-001），全部工具均未导入",
    "duplicates": [
      {
        "tool_id": "WRENCH-001",
        "existing_name": "10mm 扳手",
        "existing_status": "available",
        "current_borrower": null
      }
    ],
    "rejected_count": 3,
    "duplicate_count": 1
  }
}
```

无重复时成功返回：

```bash
curl -X POST http://localhost:8000/api/tools/import \
  -H "Content-Type: application/json" \
  -d '{
    "tools": [{"tool_id": "SAW-001", "name": "手锯", "category": "手动工具"}],
    "operator": "admin"
  }'
```

```json
{
  "ok": true,
  "imported": ["SAW-001"],
  "imported_count": 1
}
```

### 3. 查看所有工具

```bash
curl http://localhost:8000/api/tools
```

### 4. 查看单件工具

```bash
curl http://localhost:8000/api/tools/WRENCH-001
```

### 5. 借出工具

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san", "borrower": "zhang_san"}'
```

返回：

```json
{
  "ok": true,
  "tool_id": "WRENCH-001",
  "borrower": "zhang_san",
  "borrow_time": "2026-06-22T10:00:00+00:00",
  "due_time": "2026-06-23T10:00:00+00:00",
  "borrow_hours": 24
}
```

### 6. 已借出工具再次借出（失败）

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "borrower": "li_si"}'
```

返回（409，不覆盖借用人）：

```json
{
  "detail": {
    "error": "tool_not_available",
    "message": "工具 'WRENCH-001' 当前不可借出",
    "tool_id": "WRENCH-001",
    "current_status": "borrowed",
    "current_borrower": "zhang_san"
  }
}
```

### 7. 归还工具

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san"}'
```

### 8. 非借用人归还（失败）

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si"}'
```

返回（403，current_borrower 不被覆盖）：

```json
{
  "detail": {
    "error": "permission_denied",
    "message": "操作员 'li_si' 不是借用人，无法归还",
    "tool_id": "WRENCH-001",
    "current_borrower": "zhang_san",
    "current_status": "borrowed"
  }
}
```

### 9. 上报损坏

借出状态下上报损坏**不会改变 `status`**，也不会清除 `current_borrower`，仅记录损坏信息。借用人仍可正常归还。归还时如果存在损坏记录，工具会自动转为 `damaged` 状态。

```bash
# 借出状态下上报损坏
curl -X POST http://localhost:8000/api/tools/DRILL-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "borrower": "li_si"}'

curl -X POST http://localhost:8000/api/tools/DRILL-001/damage \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "damage_note": "电钻线缆断裂"}'
```

返回（借出状态下上报，`status` 仍为 `borrowed`，`current_borrower` 保留）：

```json
{
  "ok": true,
  "tool_id": "DRILL-001",
  "damage_note": "电钻线缆断裂",
  "damage_reporter": "li_si",
  "damage_report_time": "2026-06-22T10:30:00+00:00",
  "current_status": "borrowed",
  "current_borrower": "li_si"
}
```

可用状态下上报损坏会直接标记为 `damaged`：

```bash
curl -X POST http://localhost:8000/api/tools/LADDER-001/damage \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "damage_note": "梯子铰链松动"}'
```

返回：

```json
{
  "ok": true,
  "tool_id": "LADDER-001",
  "damage_note": "梯子铰链松动",
  "damage_reporter": "li_si",
  "damage_report_time": "2026-06-22T10:30:00+00:00",
  "current_status": "damaged",
  "current_borrower": null
}
```

### 10. 借用人关闭损坏报告（失败）

```bash
curl -X POST http://localhost:8000/api/tools/DRILL-001/damage/close \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si"}'
```

返回（403，当前状态和损坏信息不被覆盖，借出状态下保留借用人）：

```json
{
  "detail": {
    "error": "permission_denied",
    "message": "操作员 'li_si' 无权关闭损坏报告，需要 admin 角色",
    "tool_id": "DRILL-001",
    "current_status": "borrowed",
    "current_borrower": "li_si",
    "damage_note": "电钻线缆断裂",
    "damage_reporter": "li_si"
  }
}
```

### 11. 管理员关闭损坏报告

```bash
curl -X POST http://localhost:8000/api/tools/DRILL-001/damage/close \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin"}'
```

借出状态下关闭损坏，会保留借出状态和借用人：

```json
{
  "ok": true,
  "tool_id": "DRILL-001",
  "new_status": "borrowed",
  "current_borrower": "li_si"
}
```

非借出状态下关闭损坏，会恢复为 `available`：

```json
{
  "ok": true,
  "tool_id": "LADDER-001",
  "new_status": "available",
  "current_borrower": null
}
```

### 12. 检查并标记逾期

```bash
curl -X POST http://localhost:8000/api/overdue/check
```

### 13. 获取逾期规则

```bash
curl http://localhost:8000/api/rules
```

### 14. 修改逾期规则

```bash
curl -X PUT http://localhost:8000/api/rules \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "max_borrow_hours": 48}'
```

### 15. 按工具编号查询历史

```bash
curl http://localhost:8000/api/tools/WRENCH-001/history
```

### 16. 导出审计日志

```bash
# 全部日志
curl http://localhost:8000/api/audit

# 按操作类型筛选
curl "http://localhost:8000/api/audit?action=borrow"

# 按操作人筛选
curl "http://localhost:8000/api/audit?operator=zhang_san"

# 按工具编号筛选
curl "http://localhost:8000/api/audit?tool_id=WRENCH-001"

# 仅失败记录
curl "http://localhost:8000/api/audit?success=0"

# 组合筛选
curl "http://localhost:8000/api/audit?action=borrow&operator=zhang_san&limit=50"
```

### 17. 注册操作员

```bash
curl -X POST http://localhost:8000/api/operators \
  -H "Content-Type: application/json" \
  -d '{"operator_id": "wang_wu", "display_name": "王五", "role": "user", "admin_operator": "admin"}'
```

### 18. 查看所有操作员

```bash
curl http://localhost:8000/api/operators
```

## 主流程示例

```bash
# 1. 初始化
curl -X POST http://localhost:8000/api/init

# 2. 借出扳手
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 2}'

# 3. 归还扳手
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san"}'

# 4. 借出电钻
curl -X POST http://localhost:8000/api/tools/DRILL-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "borrower": "li_si"}'

# 5. 借出期间上报损坏（不改变借出状态，current_borrower 保留）
curl -X POST http://localhost:8000/api/tools/DRILL-001/damage \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "damage_note": "开关失灵"}'

# 6. 查看工具状态（仍是 borrowed，current_borrower 为 li_si，有 damage_note）
curl http://localhost:8000/api/tools/DRILL-001

# 7. 归还损坏的电钻（归还后自动转为 damaged 状态）
curl -X POST http://localhost:8000/api/tools/DRILL-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si"}'

# 8. 导出审计
curl http://localhost:8000/api/audit

# 9. 查询扳手历史
curl http://localhost:8000/api/tools/WRENCH-001/history
```

## 持久化

所有数据存储在 SQLite 文件 `tool_station.db` 中，服务重启后以下内容保持一致：
- 逾期规则配置（`overdue_rules` 表）
- 逾期标记（`tools.is_overdue` + `borrow_records.is_overdue`）
- 损坏备注（`tools.damage_note` / `damage_reporter` / `damage_report_time`）
- 归还时间（`tools.return_time` / `borrow_records.return_time`）
- 审计日志（`audit_log` 表）
