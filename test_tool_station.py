import pytest
import requests
import sqlite3
import os

BASE = "http://localhost:8000"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_station.db")


@pytest.fixture(autouse=True)
def reset_data():
    requests.post(f"{BASE}/api/init")
    yield


def db_query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


class TestImportBatchReject:
    def test_duplicate_id_returns_409_entire_batch(self):
        r = requests.post(f"{BASE}/api/tools/import", json={
            "tools": [
                {"tool_id": "WRENCH-001", "name": "duplicate", "category": "test"},
            ],
            "operator": "admin"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "duplicate_tool_ids"
        assert detail["duplicate_count"] == 1
        assert detail["rejected_count"] == 1

    def test_mixed_duplicate_rejects_all_nothing_in_db(self):
        count_before = requests.get(f"{BASE}/api/tools").json()["count"]

        r = requests.post(f"{BASE}/api/tools/import", json={
            "tools": [
                {"tool_id": "SAW-001", "name": "hand saw", "category": "manual"},
                {"tool_id": "WRENCH-001", "name": "dup wrench", "category": "manual"},
                {"tool_id": "HAMMER-001", "name": "hammer", "category": "manual"},
            ],
            "operator": "admin"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "duplicate_tool_ids"
        assert detail["duplicate_count"] == 1
        assert detail["rejected_count"] == 3
        assert detail["duplicates"][0]["tool_id"] == "WRENCH-001"

        count_after = requests.get(f"{BASE}/api/tools").json()["count"]
        assert count_after == count_before

        rows = db_query("SELECT tool_id FROM tools WHERE tool_id IN (?, ?, ?)",
                        ("SAW-001", "WRENCH-001-dup", "HAMMER-001"))
        assert len(rows) == 0 or all(r["tool_id"] not in ("SAW-001", "HAMMER-001") for r in rows)

        saw_in_db = db_query("SELECT 1 FROM tools WHERE tool_id = ?", ("SAW-001",))
        assert len(saw_in_db) == 0, "SAW-001 should NOT be in DB after batch rejection"

        hammer_in_db = db_query("SELECT 1 FROM tools WHERE tool_id = ?", ("HAMMER-001",))
        assert len(hammer_in_db) == 0, "HAMMER-001 should NOT be in DB after batch rejection"

    def test_no_duplicate_import_succeeds(self):
        r = requests.post(f"{BASE}/api/tools/import", json={
            "tools": [
                {"tool_id": "SAW-001", "name": "hand saw", "category": "manual"},
            ],
            "operator": "admin"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["imported_count"] == 1
        assert "SAW-001" in body["imported"]

    def test_duplicate_import_no_success_audit(self):
        requests.post(f"{BASE}/api/tools/import", json={
            "tools": [{"tool_id": "WRENCH-001", "name": "dup", "category": "test"}],
            "operator": "admin"
        })
        r = requests.get(f"{BASE}/api/audit", params={"action": "import_tool", "success": 1})
        success_count = r.json()["count"]
        assert success_count == 0, "No success audit should exist for rejected batch"

        r2 = requests.get(f"{BASE}/api/audit", params={"action": "import_tool", "success": 0})
        fail_count = r2.json()["count"]
        assert fail_count >= 1, "At least one failure audit should exist"


class TestBorrowDamageReturn:
    def test_borrow_damage_return_full_chain(self):
        r = requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        assert r.status_code == 200
        assert r.json()["borrower"] == "li_si"

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "li_si"
        assert tool["damage_note"] is None

        r = requests.post(f"{BASE}/api/tools/DRILL-001/damage", json={
            "operator": "li_si", "damage_note": "switch broken"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["current_status"] == "borrowed"
        assert body["current_borrower"] == "li_si"
        assert body["damage_note"] == "switch broken"

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "li_si"
        assert tool["damage_note"] == "switch broken"

        r = requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "li_si"
        })
        assert r.status_code == 200
        assert r.json()["new_status"] == "damaged"
        assert r.json()["is_overdue"] is False

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "damaged"
        assert tool["current_borrower"] is None
        assert tool["return_time"] is not None
        assert tool["damage_note"] == "switch broken"

    def test_borrower_still_can_return_after_damage(self):
        requests.post(f"{BASE}/api/tools/METER-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/METER-001/damage", json={
            "operator": "zhang_san", "damage_note": "display cracked"
        })
        r = requests.post(f"{BASE}/api/tools/METER-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["new_status"] == "damaged"


class TestDoubleBorrowPreservesBorrower:
    def test_double_borrow_keeps_original_borrower(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "tool_not_available"
        assert detail["current_status"] == "borrowed"
        assert detail["current_borrower"] == "zhang_san"

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["current_borrower"] == "zhang_san"

    def test_double_borrow_no_success_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "tool_id": "WRENCH-001", "action": "borrow", "success": 1
        })
        assert r.json()["count"] == 1

        r2 = requests.get(f"{BASE}/api/audit", params={
            "tool_id": "WRENCH-001", "action": "borrow", "success": 0
        })
        assert r2.json()["count"] == 1


class TestDamageCloseWhileBorrowed:
    def test_close_damage_while_borrowed_keeps_borrow_state(self):
        requests.post(f"{BASE}/api/tools/HELMET-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/HELMET-001/damage", json={
            "operator": "zhang_san", "damage_note": "cracked"
        })
        r = requests.post(f"{BASE}/api/tools/HELMET-001/damage/close", json={
            "operator": "admin"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["new_status"] == "borrowed"
        assert body["current_borrower"] == "zhang_san"

        tool = requests.get(f"{BASE}/api/tools/HELMET-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "zhang_san"
        assert tool["damage_note"] is None

        r = requests.post(f"{BASE}/api/tools/HELMET-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["new_status"] == "available"

    def test_non_admin_cannot_close_damage_while_borrowed(self):
        requests.post(f"{BASE}/api/tools/LADDER-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        requests.post(f"{BASE}/api/tools/LADDER-001/damage", json={
            "operator": "li_si", "damage_note": "hinge loose"
        })
        r = requests.post(f"{BASE}/api/tools/LADDER-001/damage/close", json={
            "operator": "li_si"
        })
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["current_borrower"] == "li_si"
        assert detail["damage_note"] == "hinge loose"


class TestDamageAvailableTool:
    def test_damage_available_sets_damaged_directly(self):
        r = requests.post(f"{BASE}/api/tools/LADDER-001/damage", json={
            "operator": "li_si", "damage_note": "hinge loose"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["current_status"] == "damaged"
        assert body["current_borrower"] is None

        tool = requests.get(f"{BASE}/api/tools/LADDER-001").json()["tool"]
        assert tool["status"] == "damaged"

    def test_cannot_borrow_damaged_tool(self):
        requests.post(f"{BASE}/api/tools/LADDER-001/damage", json={
            "operator": "li_si", "damage_note": "hinge loose"
        })
        r = requests.post(f"{BASE}/api/tools/LADDER-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        assert r.status_code == 409
