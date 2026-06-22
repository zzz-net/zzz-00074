import pytest
import requests
import sqlite3
import os
import time
import subprocess
import signal

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


class TestReserveCreate:
    def test_reserve_available_tool(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["reserve_for"] == "zhang_san"
        assert body["position"] == 1

    def test_reserve_available_tool_triggers_fulfillment(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "zhang_san"
        assert tool["retained_until"] is not None

    def test_reserve_borrowed_tool(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["reserve_for"] == "li_si"
        assert body["position"] == 1

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "zhang_san"

    def test_reserve_overdue_tool(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET status='overdue', is_overdue=1 WHERE tool_id='WRENCH-001'")
        conn.commit()
        conn.close()

        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        assert r.status_code == 200
        assert r.json()["reserve_for"] == "li_si"

    def test_reserve_damaged_tool_rejected(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/damage", json={
            "operator": "li_si", "damage_note": "broken"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "tool_not_reservable"

    def test_reserve_nonexistent_tool(self):
        r = requests.post(f"{BASE}/api/tools/NOPE-999/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 404


class TestReserveDuplicate:
    def test_same_operator_same_tool_waiting_rejected(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "duplicate_reservation"
        assert detail["reserve_for"] == "li_si"

    def test_different_operators_same_tool_ok(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        r1 = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        assert r1.status_code == 200
        assert r1.json()["position"] == 1

        r2 = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })
        assert r2.status_code == 200
        assert r2.json()["position"] == 2


class TestReservePermission:
    def test_user_can_reserve_for_self(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 200

    def test_user_cannot_reserve_for_others(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "li_si"
        })
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error"] == "permission_denied"

    def test_admin_can_reserve_for_others(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["reserve_for"] == "zhang_san"

    def test_nonexistent_operator_rejected(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "ghost", "reserve_for": "ghost"
        })
        assert r.status_code == 403

    def test_reserve_for_nonexistent_operator_rejected(self):
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "ghost"
        })
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_reserve_for"


class TestReserveFifoQueue:
    def test_fifo_order(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })
        res = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        waiting = [r for r in res if r["status"] == "waiting"]
        assert len(waiting) == 2
        assert waiting[0]["reserve_for"] == "li_si"
        assert waiting[1]["reserve_for"] == "admin"


class TestReserveFulfillmentOnReturn:
    def test_return_triggers_fulfillment(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"
        assert tool["retained_until"] is not None

    def test_return_with_no_reservation_goes_available(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["new_status"] == "available"

    def test_return_damaged_tool_does_not_trigger_fulfillment(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/damage", json={
            "operator": "zhang_san", "damage_note": "cracked"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["new_status"] == "damaged"

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "damaged"
        assert tool["reserved_for"] is None


class TestReserveBorrowBlocked:
    def test_reserved_tool_only_reserved_person_can_borrow(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"

        r = requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "tool_reserved"
        assert detail["reserved_for"] == "zhang_san"

    def test_reserved_person_can_borrow(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        assert r.status_code == 200
        assert r.json()["borrower"] == "zhang_san"

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["reserved_for"] is None
        assert tool["retained_until"] is None


class TestReserveCancel:
    def test_cancel_waiting_reservation(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        r1 = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        res_id = r1.json().get("reservation_id") or requests.get(
            f"{BASE}/api/tools/WRENCH-001/reservations"
        ).json()["reservations"][0]["id"]

        r = requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations/{res_id}", json={
            "operator": "li_si"
        })
        assert r.status_code == 200
        assert r.json()["cancelled_for"] == "li_si"
        assert r.json()["previous_status"] == "waiting"

    def test_cancel_fulfilled_triggers_next(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"

        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        fulfilled_res = [r for r in res_list if r["status"] == "fulfilled"][0]

        r = requests.delete(
            f"{BASE}/api/tools/WRENCH-001/reservations/{fulfilled_res['id']}", json={
                "operator": "li_si"
            }
        )
        assert r.status_code == 200

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "admin"

    def test_user_cannot_cancel_others_reservation(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        res_id = [r for r in res_list if r["status"] == "waiting"][0]["id"]

        r = requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations/{res_id}", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 403

    def test_admin_can_cancel_others_reservation(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        res_id = [r for r in res_list if r["status"] == "waiting"][0]["id"]

        r = requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations/{res_id}", json={
            "operator": "admin"
        })
        assert r.status_code == 200


class TestAdminClearQueue:
    def test_admin_clear_all_reservations(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })
        r = requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations", json={
            "operator": "admin"
        })
        assert r.status_code == 200
        assert r.json()["cleared_count"] == 2

        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        active = [r for r in res_list if r["status"] in ("waiting", "fulfilled")]
        assert len(active) == 0

    def test_user_cannot_clear_queue(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        r = requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 403

    def test_clear_queue_returns_reserved_to_available(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"

        requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations", json={
            "operator": "admin"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "available"
        assert tool["reserved_for"] is None


class TestReservationConfig:
    def test_get_default_config(self):
        r = requests.get(f"{BASE}/api/reservation-config")
        assert r.status_code == 200
        config = r.json()["config"]
        assert config["reservation_enabled"] is True
        assert config["retain_minutes"] == 30

    def test_update_config_admin(self):
        r = requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 60
        })
        assert r.status_code == 200
        assert r.json()["config"]["retain_minutes"] == 60

    def test_update_config_user_rejected(self):
        r = requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "zhang_san", "retain_minutes": 60
        })
        assert r.status_code == 403

    def test_disable_reservation_rejects_new_reservations(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "reservation_enabled": False
        })
        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "reservation_disabled"

    def test_disable_reservation_clears_reserved_status(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"

        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "reservation_enabled": False
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "available"
        assert tool["reserved_for"] is None


class TestReservationExpiry:
    def test_expired_retention_triggers_next(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 0
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })

        time.sleep(1)

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"

    def test_expired_retention_no_queue_goes_available(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 0
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })

        time.sleep(1)

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "available"
        assert tool["reserved_for"] is None

    def test_expiry_writes_audit_log(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 0
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })

        time.sleep(1)

        requests.get(f"{BASE}/api/tools/WRENCH-001")

        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reservation_expired", "tool_id": "WRENCH-001"
        })
        assert r.json()["count"] >= 1


class TestDamageCloseTriggersFulfillment:
    def test_damage_close_triggers_reservation(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/damage", json={
            "operator": "zhang_san", "damage_note": "scratched"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "damaged"

        requests.post(f"{BASE}/api/tools/WRENCH-001/damage/close", json={
            "operator": "admin"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"


class TestReservationAuditLog:
    def test_reserve_creates_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reserve", "tool_id": "WRENCH-001", "success": 1
        })
        assert r.json()["count"] >= 1

    def test_cancel_creates_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        res_id = res_list[0]["id"]

        requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations/{res_id}", json={
            "operator": "li_si"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reserve_cancel", "tool_id": "WRENCH-001", "success": 1
        })
        assert r.json()["count"] >= 1

    def test_clear_queue_creates_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations", json={
            "operator": "admin"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reserve_clear", "tool_id": "WRENCH-001", "success": 1
        })
        assert r.json()["count"] >= 1

    def test_fulfillment_creates_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reservation_fulfilled", "tool_id": "WRENCH-001"
        })
        assert r.json()["count"] >= 1


class TestReserveCancelReplenish:
    def test_cancel_first_in_line_second_moves_up(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })

        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        li_si_res = [r for r in res_list if r["reserve_for"] == "li_si"][0]

        requests.delete(
            f"{BASE}/api/tools/WRENCH-001/reservations/{li_si_res['id']}", json={
                "operator": "li_si"
            }
        )

        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "admin"


class TestRestartPersistence:
    def test_reservations_persist_across_restart(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })

        res_before = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()

        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET status='available', current_borrower=NULL, return_time='2099-01-01T00:00:00+00:00' WHERE tool_id='WRENCH-001'")
        conn.commit()
        conn.close()

        res_after_sim = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()
        assert res_after_sim["count"] == res_before["count"]

        waiting = [r for r in res_after_sim["reservations"] if r["status"] == "waiting"]
        assert len(waiting) == 1
        assert waiting[0]["reserve_for"] == "li_si"

    def test_config_persists_in_db(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 45
        })
        rows = db_query("SELECT retain_minutes FROM reservation_config WHERE id = 1")
        assert len(rows) == 1
        assert rows[0]["retain_minutes"] == 45

    def test_reserved_state_persists_in_db(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        rows = db_query("SELECT status, reserved_for, retained_until FROM tools WHERE tool_id = 'WRENCH-001'")
        assert len(rows) == 1
        assert rows[0]["status"] == "reserved"
        assert rows[0]["reserved_for"] == "zhang_san"
        assert rows[0]["retained_until"] is not None


class TestReservationConfigPersistence:
    def test_config_survives_init_reset(self):
        requests.put(f"{BASE}/api/reservation-config", json={
            "operator": "admin", "retain_minutes": 99
        })
        requests.post(f"{BASE}/api/init")
        config = requests.get(f"{BASE}/api/reservation-config").json()["config"]
        assert config["retain_minutes"] == 30
        assert config["reservation_enabled"] is True


class TestFullReservationChain:
    def test_borrow_reserve_return_borrow_chain(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })

        r = requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        assert r.status_code == 200

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"

        r2 = requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        assert r2.status_code == 200
        assert r2.json()["borrower"] == "li_si"

        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "li_si"

    def test_multiple_reserves_chain(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "admin", "reserve_for": "admin"
        })

        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["reserved_for"] == "li_si"

        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "li_si"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["reserved_for"] == "admin"

        requests.post(f"{BASE}/api/tools/WRENCH-001/borrow", json={
            "operator": "admin", "borrower": "admin"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/return", json={
            "operator": "admin"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "available"
        assert tool["reserved_for"] is None


class TestReserveOnAvailableTool:
    def test_reserve_available_immediately_reserved(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "zhang_san"

    def test_reserve_available_then_cancel_goes_back(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        res_list = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        fulfilled = [r for r in res_list if r["status"] == "fulfilled"][0]

        requests.delete(f"{BASE}/api/tools/WRENCH-001/reservations/{fulfilled['id']}", json={
            "operator": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "available"


class TestReserveRegressionNoDuplicateWhileReserved:
    def test_reserved_state_duplicate_rejected_returns_409(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        tool = requests.get(f"{BASE}/api/tools/WRENCH-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "zhang_san"

        r = requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "duplicate_reservation"
        assert detail["existing_status"] == "fulfilled"
        assert detail["reserve_for"] == "zhang_san"

        res = requests.get(f"{BASE}/api/tools/WRENCH-001/reservations").json()["reservations"]
        active = [x for x in res if x["status"] in ("waiting", "fulfilled")]
        assert len(active) == 1
        assert active[0]["status"] == "fulfilled"
        assert active[0]["reserve_for"] == "zhang_san"

    def test_duplicate_rejection_writes_failed_audit(self):
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        requests.post(f"{BASE}/api/tools/WRENCH-001/reserve", json={
            "operator": "zhang_san", "reserve_for": "zhang_san"
        })
        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reserve", "tool_id": "WRENCH-001", "success": 0
        })
        assert r.json()["count"] >= 1
        last_fail = r.json()["audit_log"][0]
        assert last_fail["success"] is False
        assert "未完成预约" in last_fail["detail"]


class TestOverdueReturnFulfillsReservation:
    def test_real_overdue_return_triggers_fulfillment(self):
        requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001'")
        conn.execute("UPDATE borrow_records SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001' AND return_time IS NULL")
        conn.commit()
        conn.close()

        requests.post(f"{BASE}/api/tools/DRILL-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })

        ret = requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "zhang_san"
        })
        assert ret.status_code == 200
        body = ret.json()
        assert body["is_overdue"] is True
        assert body["new_status"] == "reserved"

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "reserved"
        assert tool["reserved_for"] == "li_si"
        assert tool["retained_until"] is not None

        res = requests.get(f"{BASE}/api/tools/DRILL-001/reservations").json()["reservations"]
        li_si_res = [x for x in res if x["reserve_for"] == "li_si"]
        assert len(li_si_res) == 1
        assert li_si_res[0]["status"] == "fulfilled"
        assert li_si_res[0]["fulfilled_at"] is not None

    def test_overdue_return_no_queue_stays_overdue_returned(self):
        requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001'")
        conn.execute("UPDATE borrow_records SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001' AND return_time IS NULL")
        conn.commit()
        conn.close()

        ret = requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "zhang_san"
        })
        assert ret.status_code == 200
        assert ret.json()["new_status"] == "overdue_returned"
        assert ret.json()["is_overdue"] is True

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "overdue_returned"
        assert tool["reserved_for"] is None

    def test_overdue_return_fulfilled_blocks_non_reserver_from_borrowing(self):
        requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001'")
        conn.execute("UPDATE borrow_records SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001' AND return_time IS NULL")
        conn.commit()
        conn.close()

        requests.post(f"{BASE}/api/tools/DRILL-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "zhang_san"
        })

        r = requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "admin", "borrower": "admin"
        })
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "tool_reserved"
        assert detail["reserved_for"] == "li_si"

    def test_overdue_return_fulfilled_audit_log_exists(self):
        requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001'")
        conn.execute("UPDATE borrow_records SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001' AND return_time IS NULL")
        conn.commit()
        conn.close()

        requests.post(f"{BASE}/api/tools/DRILL-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "zhang_san"
        })

        r = requests.get(f"{BASE}/api/audit", params={
            "action": "reservation_fulfilled", "tool_id": "DRILL-001"
        })
        assert r.json()["count"] >= 1
        log = r.json()["audit_log"][0]
        assert "保留给 li_si" in log["detail"]

    def test_overdue_return_fulfilled_reserver_can_borrow(self):
        requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "zhang_san", "borrower": "zhang_san", "borrow_hours": 1
        })
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tools SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001'")
        conn.execute("UPDATE borrow_records SET due_time='2020-01-01T00:00:00+00:00' WHERE tool_id='DRILL-001' AND return_time IS NULL")
        conn.commit()
        conn.close()

        requests.post(f"{BASE}/api/tools/DRILL-001/reserve", json={
            "operator": "li_si", "reserve_for": "li_si"
        })
        requests.post(f"{BASE}/api/tools/DRILL-001/return", json={
            "operator": "zhang_san"
        })

        r = requests.post(f"{BASE}/api/tools/DRILL-001/borrow", json={
            "operator": "li_si", "borrower": "li_si"
        })
        assert r.status_code == 200
        assert r.json()["borrower"] == "li_si"

        tool = requests.get(f"{BASE}/api/tools/DRILL-001").json()["tool"]
        assert tool["status"] == "borrowed"
        assert tool["current_borrower"] == "li_si"
        assert tool["reserved_for"] is None

