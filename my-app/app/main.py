#!/usr/bin/env python3
"""
Minimal HTTP server for the vacation & attendance request demo.
Serves ui.html at `/` and exposes JSON APIs under `/api/*`.
Frontend (ui.html) and backend (this file) must be evolved together so that UI,
routes, and Excel export columns always stay in sync.
"""

import csv
import datetime as dt
import io
import json
import os
import threading
import traceback
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data.json"
LOCK = threading.Lock()
DEFAULT_PORT = int(os.environ.get("PORT", "8000"))

# Initial seed so the app is usable on first run.
SEED = {
    "users": [
        {
            "id": "e001",
            "name": "Alice Tanaka",
            "role": "employee",
            "department": "Sales",
            "manager_id": "m001",
            "annual_leave_allowance": 20,
        },
        {
            "id": "e002",
            "name": "Bob Suzuki",
            "role": "employee",
            "department": "Engineering",
            "manager_id": "m002",
            "annual_leave_allowance": 18,
        },
        {
            "id": "m001",
            "name": "Mika Yamada",
            "role": "manager",
            "department": "Sales",
            "manager_id": "a001",
            "annual_leave_allowance": 22,
        },
        {
            "id": "m002",
            "name": "Ryo Watanabe",
            "role": "manager",
            "department": "Engineering",
            "manager_id": "a001",
            "annual_leave_allowance": 22,
        },
        {
            "id": "a001",
            "name": "Admin Ito",
            "role": "admin",
            "department": "HQ",
            "manager_id": None,
            "annual_leave_allowance": 25,
        },
    ],
    "leave_types": ["Paid", "Sick", "Half-day", "Special"],
    "work_calendar": {
        "holidays": ["2025-01-01", "2025-02-11", "2025-04-29", "2025-05-03"]
    },
    "approval_routes": [
        {"department": "Sales", "manager_id": "m001"},
        {"department": "Engineering", "manager_id": "m002"},
    ],
    "leave_requests": [],
    "attendance_corrections": [],
}


def _deepcopy_seed():
    return json.loads(json.dumps(SEED))


def load_data():
    """Load persisted JSON; recreate with seed if missing or broken."""
    with LOCK:
        if not DATA_FILE.exists():
            DATA_FILE.write_text(
                json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            DATA_FILE.write_text(
                json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return _deepcopy_seed()


def save_data(data):
    with LOCK:
        DATA_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def generate_id(prefix):
    now = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{now}"


def parse_date(value):
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def user_lookup(data):
    return {u["id"]: u for u in data.get("users", [])}


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "HRDemo/1.0"

    def log_message(self, fmt, *args):
        # Keep stdout logs readable; do not expose stack traces to the browser.
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    # --- Helpers ---------------------------------------------------------
    def respond_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_csv(self, filename, content):
        body = content.encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_html(self, status, content):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON payload.")

    def require_user(self, data):
        user_id = self.headers.get("X-User-Id")
        role = self.headers.get("X-User-Role")
        if not user_id:
            self.respond_json(401, {"ok": False, "message": "Missing X-User-Id"})
            return None
        users = user_lookup(data)
        user = users.get(user_id)
        if not user:
            self.respond_json(403, {"ok": False, "message": "Unknown user"})
            return None
        if role and user.get("role") != role:
            self.respond_json(
                403, {"ok": False, "message": "Role mismatch for this user"}
            )
            return None
        return user

    def in_team(self, manager, target_user):
        if manager["role"] == "admin":
            return True
        return target_user.get("manager_id") == manager.get("id")

    def guard_role(self, user, allowed):
        if user["role"] not in allowed:
            self.respond_json(403, {"ok": False, "message": "Forbidden for this role"})
            return False
        return True

    def handle_error(self, exc):
        self.log_message("Server error: %s", exc)
        traceback.print_exc()
        self.respond_json(
            500, {"ok": False, "message": "Internal server error. Please retry."}
        )

    # --- HTTP verbs ------------------------------------------------------
    def do_OPTIONS(self):
        # Allows future CSRF token headers without blocking browsers.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, X-User-Id, X-User-Role"
        )
        self.end_headers()

    def do_GET(self):
        parsed = up.urlparse(self.path)
        path = parsed.path
        query = up.parse_qs(parsed.query)
        try:
            if path in ("/", "/ui.html"):
                return self.serve_ui()
            if path == "/api/meta":
                return self.api_meta()
            if path == "/api/leave_requests":
                return self.api_list_requests(category="leave", query=query)
            if path == "/api/attendance_corrections":
                return self.api_list_requests(category="correction", query=query)
            if path == "/api/reports":
                return self.api_reports(query)
            if path == "/api/reports/export":
                return self.api_reports_export(query)
            self.respond_json(404, {"ok": False, "message": "Not found"})
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc)

    def do_POST(self):
        parsed = up.urlparse(self.path)
        try:
            if parsed.path == "/api/login":
                return self.api_login()
            if parsed.path == "/api/leave_requests":
                return self.api_create_leave()
            if parsed.path == "/api/attendance_corrections":
                return self.api_create_correction()
            if parsed.path == "/api/approvals":
                return self.api_approve()
            if parsed.path == "/api/settings":
                return self.api_settings_update()
            self.respond_json(404, {"ok": False, "message": "Not found"})
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc)

    # --- Route handlers --------------------------------------------------
    def serve_ui(self):
        html_path = BASE_DIR / "ui.html"
        if not html_path.exists():
            self.respond_html(500, "<h1>ui.html missing</h1>")
            return
        body = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_meta(self):
        data = load_data()
        self.respond_json(
            200,
            {
                "ok": True,
                "users": data["users"],
                "leave_types": data["leave_types"],
                "work_calendar": data["work_calendar"],
                "approval_routes": data["approval_routes"],
            },
        )

    def api_login(self):
        body = self.read_json()
        user_id = body.get("user_id", "")
        data = load_data()
        users = user_lookup(data)
        user = users.get(user_id)
        if not user:
            self.respond_json(401, {"ok": False, "message": "User not found"})
            return
        self.respond_json(200, {"ok": True, "user": user})

    def api_list_requests(self, category, query):
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        users = user_lookup(data)
        scope = query.get("scope", ["mine"])[0]
        bucket = "leave_requests" if category == "leave" else "attendance_corrections"
        items = data.get(bucket, [])
        if user["role"] == "admin":
            filtered = items
        elif user["role"] == "manager":
            if scope == "team":
                filtered = [
                    r
                    for r in items
                    if self.in_team(user, users.get(r["user_id"], {}))
                    or r["user_id"] == user["id"]
                ]
            else:
                filtered = [r for r in items if r["user_id"] == user["id"]]
        else:
            filtered = [r for r in items if r["user_id"] == user["id"]]
        self.respond_json(200, {"ok": True, "items": filtered})

    def api_create_leave(self):
        body = self.read_json()
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        required_fields = ["start_date", "end_date", "leave_type", "reason"]
        missing = [f for f in required_fields if not body.get(f)]
        if missing:
            self.respond_json(
                400, {"ok": False, "message": "Missing fields", "fields": missing}
            )
            return
        start = parse_date(body["start_date"])
        end = parse_date(body["end_date"])
        if end < start:
            self.respond_json(
                400,
                {"ok": False, "message": "End date must be on/after start date"},
            )
            return
        days = (end - start).days + 1
        item = {
            "id": generate_id("lv"),
            "user_id": user["id"],
            "employee_name": user["name"],
            "department": user["department"],
            "leave_type": body["leave_type"],
            "start_date": body["start_date"],
            "end_date": body["end_date"],
            "days": days,
            "reason": body.get("reason", ""),
            "status": "pending",
            "approver_comment": "",
            "approved_by": "",
            "created_at": dt.datetime.utcnow().isoformat(),
        }
        data["leave_requests"].append(item)
        save_data(data)
        self.respond_json(200, {"ok": True, "item": item})

    def api_create_correction(self):
        body = self.read_json()
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        required = ["date", "clock_in", "clock_out", "reason"]
        missing = [f for f in required if not body.get(f)]
        if missing:
            self.respond_json(
                400, {"ok": False, "message": "Missing fields", "fields": missing}
            )
            return
        item = {
            "id": generate_id("ac"),
            "user_id": user["id"],
            "employee_name": user["name"],
            "department": user["department"],
            "date": body["date"],
            "clock_in": body["clock_in"],
            "clock_out": body["clock_out"],
            "break_minutes": body.get("break_minutes", 0),
            "overtime_hours": body.get("overtime_hours", 0),
            "reason": body.get("reason", ""),
            "status": "pending",
            "approver_comment": "",
            "approved_by": "",
            "created_at": dt.datetime.utcnow().isoformat(),
        }
        data["attendance_corrections"].append(item)
        save_data(data)
        self.respond_json(200, {"ok": True, "item": item})

    def api_approve(self):
        body = self.read_json()
        category = body.get("category")
        target_id = body.get("id")
        action = body.get("action")
        comment = body.get("comment", "")
        if category not in {"leave", "correction"}:
            self.respond_json(400, {"ok": False, "message": "Invalid category"})
            return
        if action not in {"approved", "rejected"}:
            self.respond_json(400, {"ok": False, "message": "Invalid action"})
            return
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        if not self.guard_role(user, {"manager", "admin"}):
            return
        bucket = "leave_requests" if category == "leave" else "attendance_corrections"
        items = data.get(bucket, [])
        users = user_lookup(data)
        target = next((r for r in items if r["id"] == target_id), None)
        if not target:
            self.respond_json(404, {"ok": False, "message": "Request not found"})
            return
        owner = users.get(target["user_id"])
        if not self.in_team(user, owner):
            self.respond_json(
                403, {"ok": False, "message": "Cannot approve outside your team"}
            )
            return
        target["status"] = action
        target["approver_comment"] = comment
        target["approved_by"] = user["name"]
        save_data(data)
        self.respond_json(200, {"ok": True, "item": target})

    def api_reports(self, query):
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        if not self.guard_role(user, {"manager", "admin"}):
            return
        start = query.get("start", [""])[0]
        end = query.get("end", [""])[0]
        dept_filter = query.get("department", [""])[0]
        emp_filter = query.get("employee", [""])[0]
        start_date = parse_date(start) if start else None
        end_date = parse_date(end) if end else None
        users = user_lookup(data)
        def match_filters(item):
            owner = users.get(item["user_id"])
            if dept_filter and owner and owner.get("department") != dept_filter:
                return False
            if emp_filter and item["user_id"] != emp_filter:
                return False
            if start_date and parse_date(item.get("start_date", item.get("date"))) < start_date:
                return False
            if end_date and parse_date(item.get("end_date", item.get("date"))) > end_date:
                return False
            return True

        leave_items = [
            r for r in data.get("leave_requests", []) if r["status"] == "approved" and match_filters(r)
        ]
        corr_items = [
            r
            for r in data.get("attendance_corrections", [])
            if r["status"] == "approved" and match_filters(r)
        ]
        leave_summary = {}
        for r in leave_items:
            leave_summary.setdefault(r["user_id"], 0)
            leave_summary[r["user_id"]] += r.get("days", 0)
        report_rows = []
        for uid, days_taken in leave_summary.items():
            u = users.get(uid, {})
            allowance = u.get("annual_leave_allowance", 0)
            report_rows.append(
                {
                    "employee_id": uid,
                    "employee_name": u.get("name", ""),
                    "department": u.get("department", ""),
                    "leave_days_taken": days_taken,
                    "leave_days_remaining": max(allowance - days_taken, 0),
                }
            )
        response = {
            "ok": True,
            "report": {
                "leave_totals": report_rows,
                "correction_count": len(corr_items),
                "filters": {
                    "start": start,
                    "end": end,
                    "department": dept_filter,
                    "employee": emp_filter,
                },
            },
        }
        self.respond_json(200, response)

    def api_reports_export(self, query):
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        if not self.guard_role(user, {"manager", "admin"}):
            return
        # Reuse report filters for consistency.
        start = query.get("start", [""])[0]
        end = query.get("end", [""])[0]
        dept_filter = query.get("department", [""])[0]
        emp_filter = query.get("employee", [""])[0]
        start_date = parse_date(start) if start else None
        end_date = parse_date(end) if end else None
        users = user_lookup(data)

        def match(item):
            owner = users.get(item["user_id"])
            if dept_filter and owner and owner.get("department") != dept_filter:
                return False
            if emp_filter and item["user_id"] != emp_filter:
                return False
            item_start = parse_date(item.get("start_date", item.get("date")))
            item_end = parse_date(item.get("end_date", item.get("date")))
            if start_date and item_end < start_date:
                return False
            if end_date and item_start > end_date:
                return False
            return True

        rows = []
        for r in data.get("leave_requests", []):
            if not match(r):
                continue
            rows.append(
                {
                    "category": "leave",
                    "employee_id": r["user_id"],
                    "employee_name": r["employee_name"],
                    "department": r["department"],
                    "status": r["status"],
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                    "days": r["days"],
                    "leave_type": r["leave_type"],
                    "reason": r["reason"],
                    "approver_comment": r["approver_comment"],
                    "approved_by": r["approved_by"],
                    "created_at": r["created_at"],
                }
            )
        for r in data.get("attendance_corrections", []):
            if not match(r):
                continue
            rows.append(
                {
                    "category": "attendance_correction",
                    "employee_id": r["user_id"],
                    "employee_name": r["employee_name"],
                    "department": r["department"],
                    "status": r["status"],
                    "start_date": r["date"],
                    "end_date": r["date"],
                    "days": 0,
                    "leave_type": "",
                    "reason": r["reason"],
                    "approver_comment": r["approver_comment"],
                    "approved_by": r["approved_by"],
                    "created_at": r["created_at"],
                }
            )
        headers = [
            "category",
            "employee_id",
            "employee_name",
            "department",
            "status",
            "start_date",
            "end_date",
            "days",
            "leave_type",
            "reason",
            "approver_comment",
            "approved_by",
            "created_at",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        filename = "reports.csv"
        self.respond_csv(filename, buf.getvalue())

    def api_settings_update(self):
        body = self.read_json()
        data = load_data()
        user = self.require_user(data)
        if not user:
            return
        if not self.guard_role(user, {"admin"}):
            return
        leave_types = body.get("leave_types")
        holidays = body.get("holidays")
        approval_routes = body.get("approval_routes")
        if leave_types is not None:
            data["leave_types"] = [str(x) for x in leave_types if x]
        if holidays is not None:
            data["work_calendar"]["holidays"] = [str(x) for x in holidays if x]
        if approval_routes is not None:
            data["approval_routes"] = approval_routes
        save_data(data)
        self.respond_json(200, {"ok": True, "settings": data})


def run():
    server = HTTPServer(("", DEFAULT_PORT), RequestHandler)
    print(f"Server running at http://localhost:{DEFAULT_PORT}/")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    run()
