
import unittest
from datetime import timedelta

from test_attendance_sprint2 import (
    AttendanceSprint2Tests,
    at_ist,
    main,
)


class AttendanceSprint3Tests(AttendanceSprint2Tests):
    def test_correction_route_exists(self):
        routes = {
            (method, route.path)
            for route in main.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(
            ("PATCH", "/attendance/{emp_code}/{attendance_date}"),
            routes,
        )

    def test_missing_employee_returns_404(self):
        response = self.client.patch(
            "/attendance/UNKNOWN/2026-01-05",
            json={"status": "PRESENT"},
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_attendance_record_returns_404(self):
        self.add_employee()
        response = self.client.patch(
            "/attendance/EMP0001/2026-01-05",
            json={"status": "PRESENT"},
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_status_returns_422(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        response = self.client.patch(
            "/attendance/EMP0001/2026-01-05",
            json={"status": "INVALID"},
        )
        self.assertEqual(response.status_code, 422)

    def test_correction_creates_audit_history(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        response = self.client.patch(
            "/attendance/EMP0001/2026-01-05",
            json={"punch_out": int(
                (at_ist(5, 18, 30) + timedelta(minutes=30)).timestamp()
                * 1000
            )},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["overtime_minutes"], 30)
        self.assertTrue(body["history"])
        self.assertIn("punch_out", body["history"][-1]["changes"])


if __name__ == "__main__":
    unittest.main()
