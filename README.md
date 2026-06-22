# 工具借用亭 JSON API

本地工具借用管理系统，提供工具目录、逾期规则、借出归还、损坏上报、预约排队、权限校验和审计日志，不依赖门禁或库存系统。

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
| POST | `/api/tools/{tool_id}/reserve` | 创建预约 |
| GET | `/api/tools/{tool_id}/reservations` | 查看工具预约队列 |
| DELETE | `/api/tools/{tool_id}/reservations/{reservation_id}` | 取消单条预约 |
| DELETE | `/api/tools/{tool_id}/reservations` | 管理员清队（仅 admin） |
| GET | `/api/reservation-config` | 获取预约配置 |
| PUT | `/api/reservation-config` | 修改预约配置（仅 admin） |

## 工具状态字段

| status 值 | 含义 |
|-----------|------|
| `available` | 可借 |
| `borrowed` | 已借出（可能同时有 `damage_note` 损坏备注） |
| `overdue` | 已逾期（可能同时有 `damage_note` 损坏备注） |
| `damaged` | 已损坏（不在借出中） |
| `overdue_returned` | 逾期已归还 |
| `reserved` | 已预约保留（仅预约人可在保留期内借出） |

**损坏状态说明**：借出状态下上报损坏不会改变 `status`，仅在 `damage_note` 字段记录损坏信息，`current_borrower` 保持不变。归还时若存在损坏备注，工具状态才会转为 `damaged`。

**预约保留状态说明**：当工具归还且有等待中的预约时，工具自动进入 `reserved` 状态，`reserved_for` 记录保留给谁，`retained_until` 记录保留截止时间。在保留期内仅预约人可借出；保留期结束后自动流转到下一位预约人或回到 `available`。

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
| `reserved_for` | string\|null | 预约保留给谁 |
| `retained_until` | string\|null | 保留截止时间（ISO 8601） |

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
| `action` | string | 操作类型（init / import_tool / borrow / return / damage_report / damage_close / overdue_mark / rules_update / register_operator / reserve / reserve_cancel / reserve_clear / reservation_fulfilled / reservation_expired / reservation_config_update） |
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

## 预约记录字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 预约 ID |
| `tool_id` | string | 工具编号 |
| `operator_id` | string | 创建预约的操作员 |
| `reserve_for` | string | 预约目标操作员 |
| `status` | string | 预约状态（waiting / fulfilled / cancelled / expired） |
| `created_at` | string | 创建时间 |
| `fulfilled_at` | string\|null | 兑现时间 |
| `cancelled_at` | string\|null | 取消时间 |
| `expired_at` | string\|null | 过期时间 |

## 预约配置字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `reservation_enabled` | boolean | 是否开启预约功能 |
| `retain_minutes` | integer | 归还后保留给预约人的分钟数 |

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
| 创建预约 | Y（可代他人） | Y（仅为自己） |
| 取消预约 | Y（任意） | Y（仅自己的） |
| 清除预约队列 | Y | N |
| 修改预约配置 | Y | N |

## 预约排队流程

1. **创建预约**：对 `available`、`borrowed`、`overdue`、`reserved` 状态的工具均可创建预约，按先来先到排队
2. **归还触发兑现**：工具归还（含普通归还、逾期归还）后若有等待中的预约，自动将第一位预约人标记为 `fulfilled`，工具进入 `reserved` 状态，设置 `reserved_for` 和 `retained_until`；若无等待预约，普通归还回到 `available`，逾期归还回到 `overdue_returned`。损坏归还进入 `damaged`，不触发兑现
3. **保留期借出**：在 `reserved` 状态下仅 `reserved_for` 指定的操作员可借出；其他人尝试借出会被 `tool_reserved` 错误拦截
4. **保留期超时**：若保留期到期预约人未借出，预约自动标记为 `expired`，系统自动流转到下一位预约人或回到 `available`
5. **取消补位**：取消已兑现的预约后，自动触发下一位预约人进入保留期
6. **管理员清队**：管理员可一键清除所有活跃预约，工具回到 `available`
7. **关闭预约功能**：管理员通过配置关闭预约功能时，所有处于 `reserved` 状态的工具回到 `available`，已兑现的预约被取消

### 同一操作员不可重复占位

同一操作员对同一工具只要存在**未完成预约**（即 `waiting` 或 `fulfilled` 状态），就不能再次创建预约，重复预约返回 `duplicate_reservation` 错误并在响应中给出 `existing_status` 说明已存在的预约处于什么状态。此规则同时覆盖"在等待队列中"和"当前正处于保留期"两种场景。

### 管理员代约

管理员创建预约时可指定 `reserve_for` 为其他操作员；普通用户 `reserve_for` 必须等于 `operator`。

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

### 19. 创建预约

对 `available`、`borrowed`、`overdue`、`reserved` 状态的工具可创建预约。普通用户只能为自己预约，管理员可代他人预约。

```bash
# 普通用户为自己预约
curl -X POST http://localhost:8000/api/tools/WRENCH-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san", "reserve_for": "zhang_san"}'
```

返回：

```json
{
  "ok": true,
  "tool_id": "WRENCH-001",
  "reserve_for": "zhang_san",
  "position": 1,
  "created_at": "2026-06-22T10:00:00+00:00"
}
```

```bash
# 管理员代他人预约
curl -X POST http://localhost:8000/api/tools/WRENCH-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "reserve_for": "li_si"}'
```

普通用户代他人预约会被拒绝（403）：

```json
{
  "detail": {
    "error": "permission_denied",
    "message": "普通用户 'zhang_san' 只能为自己预约，不能为他人预约",
    "current_operator": "zhang_san",
    "current_role": "user",
    "reserve_for": "li_si"
  }
}
```

同一操作员重复预约会被拒绝（409）：

```json
{
  "detail": {
    "error": "duplicate_reservation",
    "message": "操作员 'zhang_san' 已在工具 'WRENCH-001' 的等待队列中，不能重复占位",
    "tool_id": "WRENCH-001",
    "reserve_for": "zhang_san"
  }
}
```

### 20. 查看工具预约队列

```bash
curl http://localhost:8000/api/tools/WRENCH-001/reservations
```

返回：

```json
{
  "ok": true,
  "tool_id": "WRENCH-001",
  "reservations": [
    {
      "id": 1,
      "tool_id": "WRENCH-001",
      "operator_id": "li_si",
      "reserve_for": "li_si",
      "status": "waiting",
      "created_at": "2026-06-22T10:00:00+00:00",
      "fulfilled_at": null,
      "cancelled_at": null,
      "expired_at": null
    }
  ],
  "count": 1
}
```

### 21. 取消预约

普通用户只能取消自己的预约，管理员可取消任意预约。取消已兑现的预约会自动触发下一位进入保留期。

```bash
curl -X DELETE http://localhost:8000/api/tools/WRENCH-001/reservations/1 \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si"}'
```

返回：

```json
{
  "ok": true,
  "tool_id": "WRENCH-001",
  "reservation_id": 1,
  "cancelled_for": "li_si",
  "previous_status": "waiting"
}
```

### 22. 管理员清队

一键清除工具的所有活跃预约（waiting 和 fulfilled），工具回到 `available`。

```bash
curl -X DELETE http://localhost:8000/api/tools/WRENCH-001/reservations \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin"}'
```

返回：

```json
{
  "ok": true,
  "tool_id": "WRENCH-001",
  "cleared_count": 3
}
```

### 23. 获取预约配置

```bash
curl http://localhost:8000/api/reservation-config
```

返回：

```json
{
  "ok": true,
  "config": {
    "reservation_enabled": true,
    "retain_minutes": 30
  }
}
```

### 24. 修改预约配置

```bash
curl -X PUT http://localhost:8000/api/reservation-config \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "retain_minutes": 60}'
```

关闭预约功能会自动清除所有 `reserved` 状态的工具：

```bash
curl -X PUT http://localhost:8000/api/reservation-config \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "reservation_enabled": false}'
```

### 25. 预约保留期借出拦截

工具处于 `reserved` 状态时，非预约人尝试借出会被拦截：

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "borrower": "li_si"}'
```

返回（409）：

```json
{
  "detail": {
    "error": "tool_reserved",
    "message": "工具 'WRENCH-001' 已保留给 'zhang_san'，其他人不可借出",
    "tool_id": "WRENCH-001",
    "reserved_for": "zhang_san",
    "retained_until": "2026-06-22T10:30:00+00:00"
  }
}
```

预约人可正常借出：

```bash
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san", "borrower": "zhang_san"}'
```

## 主流程示例

```bash
# 1. 初始化
curl -X POST http://localhost:8000/api/init

# 2. 借出扳手
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 2}'

# 3. 李四排队预约
curl -X POST http://localhost:8000/api/tools/WRENCH-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "reserve_for": "li_si"}'

# 4. 管理员也排队
curl -X POST http://localhost:8000/api/tools/WRENCH-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "reserve_for": "admin"}'

# 5. 张三归还扳手 → 李四自动进入保留期
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "zhang_san"}'

# 6. 查看扳手状态（reserved，保留给 li_si）
curl http://localhost:8000/api/tools/WRENCH-001

# 7. 管理员尝试借出被拦截（tool_reserved）
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "borrower": "admin"}'

# 8. 李四借出（保留期内）
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si", "borrower": "li_si"}'

# 9. 李四归还 → 管理员自动进入保留期
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "li_si"}'

# 10. 管理员借出
curl -X POST http://localhost:8000/api/tools/WRENCH-001/borrow \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin", "borrower": "admin"}'

# 11. 管理员归还 → 无预约，回到 available
curl -X POST http://localhost:8000/api/tools/WRENCH-001/return \
  -H "Content-Type: application/json" \
  -d '{"operator": "admin"}'

# 12. 导出审计（含预约相关操作）
curl "http://localhost:8000/api/audit?action=reserve"
curl "http://localhost:8000/api/audit?action=reservation_fulfilled"
curl "http://localhost:8000/api/audit?action=reserve_cancel"
curl "http://localhost:8000/api/audit?action=reserve_clear"
curl "http://localhost:8000/api/audit?action=reservation_expired"
```

## 持久化

所有数据存储在 SQLite 文件 `tool_station.db` 中，服务重启后以下内容保持一致：
- 逾期规则配置（`overdue_rules` 表）
- 逾期标记（`tools.is_overdue` + `borrow_records.is_overdue`）
- 损坏备注（`tools.damage_note` / `damage_reporter` / `damage_report_time`）
- 归还时间（`tools.return_time` / `borrow_records.return_time`）
- 审计日志（`audit_log` 表）
- 预约配置（`reservation_config` 表）
- 预约队列及状态（`reservations` 表）
- 保留期状态（`tools.reserved_for` / `tools.retained_until`）
