import sqlite3
import json
import os
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_station.db")

app = FastAPI(title="工具借用亭 JSON API", version="1.0.0")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tools (
                tool_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                status TEXT DEFAULT 'available',
                current_borrower TEXT,
                borrow_time TEXT,
                due_time TEXT,
                return_time TEXT,
                damage_note TEXT,
                damage_reporter TEXT,
                damage_report_time TEXT,
                is_overdue INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS overdue_rules (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                max_borrow_hours INTEGER DEFAULT 24,
                overdue_check_enabled INTEGER DEFAULT 1,
                auto_mark_overdue INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                tool_id TEXT,
                operator TEXT NOT NULL,
                detail TEXT,
                success INTEGER DEFAULT 1,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS borrow_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL,
                borrower TEXT NOT NULL,
                borrow_time TEXT NOT NULL,
                due_time TEXT NOT NULL,
                return_time TEXT,
                is_overdue INTEGER DEFAULT 0,
                damage_note TEXT,
                damage_reporter TEXT,
                damage_report_time TEXT
            );

            CREATE TABLE IF NOT EXISTS operators (
                operator_id TEXT PRIMARY KEY,
                display_name TEXT,
                role TEXT DEFAULT 'user'
            );
        """)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def write_audit(conn, action: str, operator: str, tool_id: str = None,
                detail: str = None, success: int = 1):
    conn.execute(
        "INSERT INTO audit_log (action, tool_id, operator, detail, success, timestamp) VALUES (?,?,?,?,?,?)",
        (action, tool_id, operator, detail, success, now_iso()),
    )


def get_operator_role(conn, operator_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT role FROM operators WHERE operator_id = ?", (operator_id,)
    ).fetchone()
    return row["role"] if row else None


def get_rules(conn):
    row = conn.execute("SELECT * FROM overdue_rules WHERE id = 1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO overdue_rules (id, max_borrow_hours, overdue_check_enabled, auto_mark_overdue) VALUES (1,24,1,1)"
        )
        row = conn.execute("SELECT * FROM overdue_rules WHERE id = 1").fetchone()
    return dict(row)


def tool_row_to_dict(row) -> dict:
    d = dict(row)
    d["is_overdue"] = bool(d["is_overdue"])
    return d


def record_row_to_dict(row) -> dict:
    d = dict(row)
    d["is_overdue"] = bool(d["is_overdue"])
    return d


class ToolImportItem(BaseModel):
    tool_id: str
    name: str
    category: str = ""

class ToolsImportRequest(BaseModel):
    tools: list[ToolImportItem]
    operator: str

class BorrowRequest(BaseModel):
    operator: str
    borrower: str
    borrow_hours: Optional[int] = None

class ReturnRequest(BaseModel):
    operator: str

class DamageReportRequest(BaseModel):
    operator: str
    damage_note: str

class DamageCloseRequest(BaseModel):
    operator: str

class RulesUpdateRequest(BaseModel):
    operator: str
    max_borrow_hours: Optional[int] = None
    overdue_check_enabled: Optional[bool] = None
    auto_mark_overdue: Optional[bool] = None

class OperatorRegisterRequest(BaseModel):
    operator_id: str
    display_name: str = ""
    role: str = "user"
    admin_operator: str


@app.on_event("startup")
def startup():
    init_db()


@app.post("/api/init")
def api_init():
    with get_db() as conn:
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM borrow_records")
        conn.execute("DELETE FROM tools")
        conn.execute("DELETE FROM operators")
        conn.execute(
            "DELETE FROM overdue_rules WHERE id = 1"
        )
        conn.execute(
            "INSERT INTO overdue_rules (id, max_borrow_hours, overdue_check_enabled, auto_mark_overdue) VALUES (1,24,1,1)"
        )
        sample_tools = [
            ("WRENCH-001", "10mm 扳手", "手动工具"),
            ("DRILL-001", "电钻", "电动工具"),
            ("HELMET-001", "安全帽", "防护用品"),
            ("METER-001", "万用表", "测量仪器"),
            ("LADDER-001", "折叠梯", "辅助工具"),
        ]
        for tid, name, cat in sample_tools:
            conn.execute(
                "INSERT INTO tools (tool_id, name, category, status, is_overdue, created_at) VALUES (?,?,?,'available',0,?)",
                (tid, name, cat, now_iso()),
            )
        sample_operators = [
            ("admin", "管理员", "admin"),
            ("zhang_san", "张三", "user"),
            ("li_si", "李四", "user"),
        ]
        for oid, dname, role in sample_operators:
            conn.execute(
                "INSERT INTO operators (operator_id, display_name, role) VALUES (?,?,?)",
                (oid, dname, role),
            )
        write_audit(conn, "init", "system", detail="初始化样例数据", success=1)
    return {"ok": True, "message": "初始化完成，已导入5件工具和3名操作员"}


@app.post("/api/tools/import")
def api_tools_import(req: ToolsImportRequest):
    with get_db() as conn:
        role = get_operator_role(conn, req.operator)
        if role != "admin":
            raise HTTPException(status_code=403, detail={
                "error": "permission_denied",
                "message": f"操作员 '{req.operator}' 无导入权限，需要 admin 角色",
                "current_operator": req.operator,
                "current_role": role,
            })
        duplicates = []
        for t in req.tools:
            existing = conn.execute(
                "SELECT tool_id, name, status, current_borrower FROM tools WHERE tool_id = ?",
                (t.tool_id,),
            ).fetchone()
            if existing:
                duplicates.append({
                    "tool_id": t.tool_id,
                    "existing_name": existing["name"],
                    "existing_status": existing["status"],
                    "current_borrower": existing["current_borrower"],
                })
        if duplicates:
            for t in req.tools:
                write_audit(
                    conn, "import_tool", req.operator, t.tool_id,
                    detail=f"整批拒绝：工具编号 {t.tool_id} 因批量中存在重复编号（{', '.join(d['tool_id'] for d in duplicates)}）被拒绝", success=0,
                )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "duplicate_tool_ids",
                "message": f"整批导入被拒绝：检测到 {len(duplicates)} 个重复工具编号（{', '.join(d['tool_id'] for d in duplicates)}），全部工具均未导入",
                "duplicates": duplicates,
                "rejected_count": len(req.tools),
                "duplicate_count": len(duplicates),
            })
        imported = []
        for t in req.tools:
            conn.execute(
                "INSERT INTO tools (tool_id, name, category, status, is_overdue, created_at) VALUES (?,?,?,'available',0,?)",
                (t.tool_id, t.name, t.category, now_iso()),
            )
            imported.append(t.tool_id)
            write_audit(
                conn, "import_tool", req.operator, t.tool_id,
                detail=f"导入工具 {t.name}", success=1,
            )
    return {
        "ok": True,
        "imported": imported,
        "imported_count": len(imported),
    }


@app.get("/api/tools")
def api_tools_list(status: Optional[str] = None):
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tools WHERE status = ? ORDER BY tool_id", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tools ORDER BY tool_id").fetchall()
    return {"ok": True, "tools": [tool_row_to_dict(r) for r in rows], "count": len(rows)}


@app.get("/api/tools/{tool_id}")
def api_tool_detail(tool_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"工具 '{tool_id}' 不存在",
            "tool_id": tool_id,
        })
    return {"ok": True, "tool": tool_row_to_dict(row)}


@app.post("/api/tools/{tool_id}/borrow")
def api_tool_borrow(tool_id: str, req: BorrowRequest):
    with get_db() as conn:
        tool = conn.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
        if not tool:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"工具 '{tool_id}' 不存在",
                "tool_id": tool_id,
            })
        if tool["status"] != "available":
            write_audit(
                conn, "borrow", req.operator, tool_id,
                detail=f"借出失败：工具状态为 {tool['status']}", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "tool_not_available",
                "message": f"工具 '{tool_id}' 当前不可借出",
                "tool_id": tool_id,
                "current_status": tool["status"],
                "current_borrower": tool["current_borrower"],
            })
        rules = get_rules(conn)
        borrow_hours = req.borrow_hours or rules["max_borrow_hours"]
        borrow_time = now_iso()
        due_time = (datetime.now(timezone.utc) + timedelta(hours=borrow_hours)).isoformat()
        conn.execute(
            "UPDATE tools SET status='borrowed', current_borrower=?, borrow_time=?, due_time=?, return_time=NULL, is_overdue=0 WHERE tool_id=?",
            (req.borrower, borrow_time, due_time, tool_id),
        )
        conn.execute(
            "INSERT INTO borrow_records (tool_id, borrower, borrow_time, due_time, is_overdue) VALUES (?,?,?,?,0)",
            (tool_id, req.borrower, borrow_time, due_time),
        )
        write_audit(
            conn, "borrow", req.operator, tool_id,
            detail=f"借出给 {req.borrower}，应还时间 {due_time}", success=1,
        )
    return {
        "ok": True,
        "tool_id": tool_id,
        "borrower": req.borrower,
        "borrow_time": borrow_time,
        "due_time": due_time,
        "borrow_hours": borrow_hours,
    }


@app.post("/api/tools/{tool_id}/return")
def api_tool_return(tool_id: str, req: ReturnRequest):
    with get_db() as conn:
        tool = conn.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
        if not tool:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"工具 '{tool_id}' 不存在",
                "tool_id": tool_id,
            })
        if tool["status"] not in ("borrowed", "overdue"):
            write_audit(
                conn, "return", req.operator, tool_id,
                detail=f"归还失败：工具状态为 {tool['status']}，非借出状态", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "tool_not_borrowed",
                "message": f"工具 '{tool_id}' 当前未被借出，无法归还",
                "tool_id": tool_id,
                "current_status": tool["status"],
                "current_borrower": tool["current_borrower"],
            })
        role = get_operator_role(conn, req.operator)
        if role != "admin" and tool["current_borrower"] != req.operator:
            write_audit(
                conn, "return", req.operator, tool_id,
                detail=f"归还失败：操作员非借用人（借用人: {tool['current_borrower']}）", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=403, detail={
                "error": "permission_denied",
                "message": f"操作员 '{req.operator}' 不是借用人，无法归还",
                "tool_id": tool_id,
                "current_borrower": tool["current_borrower"],
                "current_status": tool["status"],
            })
        return_time = now_iso()
        is_overdue_now = False
        if tool["due_time"]:
            due_dt = datetime.fromisoformat(tool["due_time"])
            is_overdue_now = datetime.now(timezone.utc) > due_dt
        new_status = "overdue_returned" if is_overdue_now else "available"
        if tool["damage_note"]:
            new_status = "damaged"
        conn.execute(
            "UPDATE tools SET status=?, current_borrower=NULL, return_time=?, is_overdue=? WHERE tool_id=?",
            (new_status, return_time, int(is_overdue_now), tool_id),
        )
        conn.execute(
            "UPDATE borrow_records SET return_time=?, is_overdue=? WHERE tool_id=? AND return_time IS NULL AND borrower=?",
            (return_time, int(is_overdue_now), tool_id, tool["current_borrower"]),
        )
        overdue_flag = "（逾期归还）" if is_overdue_now else ""
        write_audit(
            conn, "return", req.operator, tool_id,
            detail=f"归还工具{overdue_flag}，原借用人 {tool['current_borrower']}", success=1,
        )
    return {
        "ok": True,
        "tool_id": tool_id,
        "return_time": return_time,
        "is_overdue": is_overdue_now,
        "new_status": new_status,
    }


@app.post("/api/tools/{tool_id}/damage")
def api_tool_damage(tool_id: str, req: DamageReportRequest):
    with get_db() as conn:
        tool = conn.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
        if not tool:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"工具 '{tool_id}' 不存在",
                "tool_id": tool_id,
            })
        if tool["damage_note"]:
            write_audit(
                conn, "damage_report", req.operator, tool_id,
                detail=f"损坏上报失败：工具已有损坏记录", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "already_damaged",
                "message": f"工具 '{tool_id}' 已有损坏记录",
                "tool_id": tool_id,
                "current_status": tool["status"],
                "current_borrower": tool["current_borrower"],
                "damage_note": tool["damage_note"],
                "damage_reporter": tool["damage_reporter"],
            })
        report_time = now_iso()
        is_borrowed = tool["status"] in ("borrowed", "overdue")
        if is_borrowed:
            conn.execute(
                "UPDATE tools SET damage_note=?, damage_reporter=?, damage_report_time=? WHERE tool_id=?",
                (req.damage_note, req.operator, report_time, tool_id),
            )
            new_status = tool["status"]
        else:
            conn.execute(
                "UPDATE tools SET damage_note=?, damage_reporter=?, damage_report_time=?, status='damaged' WHERE tool_id=?",
                (req.damage_note, req.operator, report_time, tool_id),
            )
            new_status = "damaged"
        write_audit(
            conn, "damage_report", req.operator, tool_id,
            detail=f"上报损坏：{req.damage_note}（借出状态: {is_borrowed}）", success=1,
        )
    return {
        "ok": True,
        "tool_id": tool_id,
        "damage_note": req.damage_note,
        "damage_reporter": req.operator,
        "damage_report_time": report_time,
        "current_status": new_status,
        "current_borrower": tool["current_borrower"] if is_borrowed else None,
    }


@app.post("/api/tools/{tool_id}/damage/close")
def api_tool_damage_close(tool_id: str, req: DamageCloseRequest):
    with get_db() as conn:
        tool = conn.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
        if not tool:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"工具 '{tool_id}' 不存在",
                "tool_id": tool_id,
            })
        if not tool["damage_note"]:
            write_audit(
                conn, "damage_close", req.operator, tool_id,
                detail=f"关闭损坏失败：工具无损坏记录", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "not_damaged",
                "message": f"工具 '{tool_id}' 无损坏记录可关闭",
                "tool_id": tool_id,
                "current_status": tool["status"],
                "current_borrower": tool["current_borrower"],
            })
        role = get_operator_role(conn, req.operator)
        if role != "admin":
            write_audit(
                conn, "damage_close", req.operator, tool_id,
                detail=f"关闭损坏失败：操作员 '{req.operator}' 角色 '{role}' 无权限（需 admin）", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=403, detail={
                "error": "permission_denied",
                "message": f"操作员 '{req.operator}' 无权关闭损坏报告，需要 admin 角色",
                "tool_id": tool_id,
                "current_status": tool["status"],
                "current_borrower": tool["current_borrower"],
                "damage_note": tool["damage_note"],
                "damage_reporter": tool["damage_reporter"],
            })
        is_borrowed = tool["status"] in ("borrowed", "overdue")
        if is_borrowed:
            conn.execute(
                "UPDATE tools SET damage_note=NULL, damage_reporter=NULL, damage_report_time=NULL WHERE tool_id=?",
                (tool_id,),
            )
            new_status = tool["status"]
            borrower_kept = tool["current_borrower"]
        else:
            conn.execute(
                "UPDATE tools SET damage_note=NULL, damage_reporter=NULL, damage_report_time=NULL, status='available' WHERE tool_id=?",
                (tool_id,),
            )
            new_status = "available"
            borrower_kept = None
        write_audit(
            conn, "damage_close", req.operator, tool_id,
            detail=f"关闭损坏报告（借出状态: {is_borrowed}），工具状态: {new_status}", success=1,
        )
    return {
        "ok": True,
        "tool_id": tool_id,
        "new_status": new_status,
        "current_borrower": borrower_kept,
    }


@app.post("/api/overdue/check")
def api_overdue_check():
    marked = []
    with get_db() as conn:
        rules = get_rules(conn)
        if not rules["auto_mark_overdue"]:
            return {"ok": True, "message": "自动逾期标记已关闭", "marked": [], "marked_count": 0}
        rows = conn.execute(
            "SELECT * FROM tools WHERE status = 'borrowed' AND is_overdue = 0 AND due_time IS NOT NULL"
        ).fetchall()
        now = datetime.now(timezone.utc)
        for row in rows:
            due_dt = datetime.fromisoformat(row["due_time"])
            if now > due_dt:
                conn.execute(
                    "UPDATE tools SET status='overdue', is_overdue=1 WHERE tool_id=?",
                    (row["tool_id"],),
                )
                conn.execute(
                    "UPDATE borrow_records SET is_overdue=1 WHERE tool_id=? AND return_time IS NULL AND borrower=?",
                    (row["tool_id"], row["current_borrower"]),
                )
                write_audit(
                    conn, "overdue_mark", "system", row["tool_id"],
                    detail=f"工具逾期标记，借用人 {row['current_borrower']}，应还时间 {row['due_time']}", success=1,
                )
                marked.append({
                    "tool_id": row["tool_id"],
                    "borrower": row["current_borrower"],
                    "due_time": row["due_time"],
                })
    return {"ok": True, "marked": marked, "marked_count": len(marked)}


@app.get("/api/rules")
def api_rules_get():
    with get_db() as conn:
        rules = get_rules(conn)
    return {"ok": True, "rules": {
        "max_borrow_hours": rules["max_borrow_hours"],
        "overdue_check_enabled": bool(rules["overdue_check_enabled"]),
        "auto_mark_overdue": bool(rules["auto_mark_overdue"]),
    }}


@app.put("/api/rules")
def api_rules_update(req: RulesUpdateRequest):
    with get_db() as conn:
        role = get_operator_role(conn, req.operator)
        if role != "admin":
            raise HTTPException(status_code=403, detail={
                "error": "permission_denied",
                "message": f"操作员 '{req.operator}' 无修改规则权限，需要 admin 角色",
                "current_operator": req.operator,
                "current_role": role,
            })
        rules = get_rules(conn)
        if req.max_borrow_hours is not None:
            rules["max_borrow_hours"] = req.max_borrow_hours
        if req.overdue_check_enabled is not None:
            rules["overdue_check_enabled"] = int(req.overdue_check_enabled)
        if req.auto_mark_overdue is not None:
            rules["auto_mark_overdue"] = int(req.auto_mark_overdue)
        conn.execute(
            "UPDATE overdue_rules SET max_borrow_hours=?, overdue_check_enabled=?, auto_mark_overdue=? WHERE id=1",
            (rules["max_borrow_hours"], rules["overdue_check_enabled"], rules["auto_mark_overdue"]),
        )
        write_audit(
            conn, "rules_update", req.operator,
            detail=f"更新规则: max_borrow_hours={rules['max_borrow_hours']}, overdue_check_enabled={bool(rules['overdue_check_enabled'])}, auto_mark_overdue={bool(rules['auto_mark_overdue'])}",
            success=1,
        )
    return {"ok": True, "rules": {
        "max_borrow_hours": rules["max_borrow_hours"],
        "overdue_check_enabled": bool(rules["overdue_check_enabled"]),
        "auto_mark_overdue": bool(rules["auto_mark_overdue"]),
    }}


@app.get("/api/tools/{tool_id}/history")
def api_tool_history(tool_id: str):
    with get_db() as conn:
        tool = conn.execute("SELECT tool_id FROM tools WHERE tool_id = ?", (tool_id,)).fetchone()
        if not tool:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"工具 '{tool_id}' 不存在",
                "tool_id": tool_id,
            })
        rows = conn.execute(
            "SELECT * FROM borrow_records WHERE tool_id = ? ORDER BY borrow_time DESC",
            (tool_id,),
        ).fetchall()
        audits = conn.execute(
            "SELECT * FROM audit_log WHERE tool_id = ? ORDER BY timestamp DESC",
            (tool_id,),
        ).fetchall()
    return {
        "ok": True,
        "tool_id": tool_id,
        "borrow_records": [record_row_to_dict(r) for r in rows],
        "audit_records": [dict(a) for a in audits],
        "borrow_count": len(rows),
        "audit_count": len(audits),
    }


@app.get("/api/audit")
def api_audit_export(
    action: Optional[str] = None,
    operator: Optional[str] = None,
    tool_id: Optional[str] = None,
    success: Optional[int] = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    with get_db() as conn:
        conditions = []
        params = []
        if action:
            conditions.append("action = ?")
            params.append(action)
        if operator:
            conditions.append("operator = ?")
            params.append(operator)
        if tool_id:
            conditions.append("tool_id = ?")
            params.append(tool_id)
        if success is not None:
            conditions.append("success = ?")
            params.append(success)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM audit_log{where} ORDER BY timestamp DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    records = []
    for r in rows:
        d = dict(r)
        d["success"] = bool(d["success"])
        records.append(d)
    return {"ok": True, "audit_log": records, "count": len(records)}


@app.post("/api/operators")
def api_operator_register(req: OperatorRegisterRequest):
    with get_db() as conn:
        role = get_operator_role(conn, req.admin_operator)
        if role != "admin":
            raise HTTPException(status_code=403, detail={
                "error": "permission_denied",
                "message": f"操作员 '{req.admin_operator}' 无注册权限，需要 admin 角色",
                "current_operator": req.admin_operator,
                "current_role": role,
            })
        existing = conn.execute(
            "SELECT operator_id FROM operators WHERE operator_id = ?",
            (req.operator_id,),
        ).fetchone()
        if existing:
            write_audit(
                conn, "register_operator", req.admin_operator,
                detail=f"注册失败：操作员 '{req.operator_id}' 已存在", success=0,
            )
            conn.commit()
            raise HTTPException(status_code=409, detail={
                "error": "operator_exists",
                "message": f"操作员 '{req.operator_id}' 已存在",
                "operator_id": req.operator_id,
            })
        conn.execute(
            "INSERT INTO operators (operator_id, display_name, role) VALUES (?,?,?)",
            (req.operator_id, req.display_name, req.role),
        )
        write_audit(
            conn, "register_operator", req.admin_operator,
            detail=f"注册操作员 {req.operator_id}（{req.display_name}），角色 {req.role}", success=1,
        )
    return {"ok": True, "operator_id": req.operator_id, "role": req.role}


@app.get("/api/operators")
def api_operators_list():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM operators ORDER BY operator_id").fetchall()
    return {"ok": True, "operators": [dict(r) for r in rows], "count": len(rows)}
