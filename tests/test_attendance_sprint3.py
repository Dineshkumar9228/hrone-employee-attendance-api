import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from unittest.mock import patch

from test_attendance_sprint2 import (
    AttendanceSprint2Tests,
    at_ist,
    epoch_ms,
    main,
)


UTC = timezone.utc


class AttendanceSprint3Tests(AttendanceSprint2Tests):
    def correction(self, **fields):
        return {
            "reason": "Approved correction",
            "regularized_by": "manager@example.com",
            **fields,
        }

    def correct(self, **fields):
        return self.client.patch(
            "/attendance/EMP0001/2026-01-05",
            json=self.correction(**fields),
        )

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
            json=self.correction(status="PRESENT"),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Employee not found")

    def test_missing_attendance_record_returns_404(self):
        self.add_employee()
        response = self.correct(status="PRESENT")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Attendance record not found")

    def test_correction_requires_contract_metadata(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        for payload in (
            {"status": "WFH", "regularized_by": "manager"},
            {"status": "WFH", "reason": "Approved correction"},
            {"status": "WFH", "reason": "No", "regularized_by": ""},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    "/attendance/EMP0001/2026-01-05",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)

    def test_invalid_status_returns_422(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        response = self.correct(status="INVALID")
        self.assertEqual(response.status_code, 422)

    def test_explicit_null_status_returns_422_without_mutation(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))

        response = self.correct(status=None)

        self.assertEqual(response.status_code, 422)
        stored = main.db.attendance_logs.find_one(
            {"emp_code": "EMP0001", "date": "2026-01-05"}
        )
        self.assertEqual(stored["status"], "PRESENT")
        self.assertEqual(stored["history"], [])

    def test_explicit_null_punch_timestamp_returns_422(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        self.assertEqual(self.correct(punch_out=None).status_code, 422)

    def test_punch_timestamps_reject_strings_and_floats(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        for invalid_timestamp in (
            str(epoch_ms(at_ist(5, 18, 30))),
            float(epoch_ms(at_ist(5, 18, 30))),
        ):
            with self.subTest(timestamp=invalid_timestamp):
                self.assertEqual(
                    self.correct(punch_out=invalid_timestamp).status_code,
                    422,
                )

    def test_omitted_fields_remain_unchanged(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        original = self.punch_out(at_ist(5, 18, 30)).json()

        response = self.correct(status="WFH")

        self.assertEqual(response.status_code, 200, response.text)
        corrected = response.json()
        for field in (
            "punch_in",
            "punch_out",
            "late_minutes",
            "work_hours",
            "overtime_minutes",
            "half_day",
        ):
            self.assertEqual(corrected[field], original[field], field)
        self.assertEqual(
            set(corrected["history"][-1]["changes"]),
            {"status"},
        )

    def test_noop_correction_returns_422_without_history_entry(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))

        response = self.correct()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "No changes supplied")
        stored = main.db.attendance_logs.find_one(
            {"emp_code": "EMP0001", "date": "2026-01-05"}
        )
        self.assertEqual(stored["history"], [])

    def test_absent_and_leave_clear_punches_and_derived_values(self):
        for status, employee_code in (("ABSENT", "EMP0001"), ("LEAVE", "EMP0002")):
            with self.subTest(status=status):
                self.add_employee(emp_code=employee_code)
                self.punch_in(at_ist(5, 9, 30), emp_code=employee_code)
                self.punch_out(at_ist(5, 19, 0), emp_code=employee_code)
                response = self.client.patch(
                    f"/attendance/{employee_code}/2026-01-05",
                    json=self.correction(status=status),
                )
                self.assertEqual(response.status_code, 200, response.text)
                record = response.json()
                self.assertEqual(record["status"], status)
                self.assertIsNone(record["punch_in"])
                self.assertIsNone(record["punch_out"])
                self.assertIsNone(record["work_hours"])
                self.assertEqual(record["late_minutes"], 0)
                self.assertEqual(record["overtime_minutes"], 0)
                self.assertFalse(record["half_day"])

    def test_absent_or_leave_rejects_supplied_punch_fields(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        for punch_field in ("punch_in", "punch_out"):
            with self.subTest(punch_field=punch_field):
                response = self.correct(
                    status="ABSENT",
                    **{punch_field: epoch_ms(at_ist(5, 9, 30))},
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.correct(status="LEAVE", punch_in=None).status_code, 422)

    def test_punch_in_must_belong_to_attendance_date_in_ist(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        wrong_day = self.correct(punch_in=epoch_ms(at_ist(6, 9, 30)))
        self.assertEqual(wrong_day.status_code, 422)

        right_day = self.correct(punch_in=epoch_ms(at_ist(5, 9, 35)))
        self.assertEqual(right_day.status_code, 200, right_day.text)
        self.assertEqual(right_day.json()["punch_in"], epoch_ms(at_ist(5, 9, 35)))

    def test_invalid_attendance_date_returns_422(self):
        self.add_employee()
        response = self.client.patch(
            "/attendance/EMP0001/2026-02-30",
            json=self.correction(status="PRESENT"),
        )
        self.assertEqual(response.status_code, 422)

    def test_overnight_punch_in_uses_shift_attendance_date(self):
        self.add_employee(shift_start="22:00", shift_end="06:00")
        self.punch_in(at_ist(5, 22, 0))
        early_morning = at_ist(6, 5, 30)

        response = self.correct(punch_in=epoch_ms(early_morning))

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["date"], "2026-01-05")
        self.assertEqual(response.json()["punch_in"], epoch_ms(early_morning))

    def test_punch_out_must_be_after_punch_in_and_at_most_24_hours(self):
        self.add_employee()
        punch_in = at_ist(5, 9, 30)
        self.punch_in(punch_in)

        for punch_out in (
            punch_in - timedelta(seconds=1),
            punch_in,
            punch_in + timedelta(hours=24, seconds=1),
        ):
            with self.subTest(punch_out=punch_out):
                self.assertEqual(
                    self.correct(punch_out=epoch_ms(punch_out)).status_code,
                    422,
                )

        at_limit = self.correct(
            punch_out=epoch_ms(punch_in + timedelta(hours=24))
        )
        self.assertEqual(at_limit.status_code, 200, at_limit.text)

    def test_history_matches_contract_and_lists_only_actual_changes(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        expected_at = at_ist(7, 12, 0).astimezone(UTC)
        corrected_out = at_ist(5, 19, 0)
        with patch.object(main, "utc_now", return_value=expected_at):
            response = self.correct(punch_out=epoch_ms(corrected_out))

        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        entry = record["history"][-1]
        self.assertEqual(
            set(entry),
            {"at", "by", "reason", "changes"},
        )
        self.assertEqual(entry["at"], epoch_ms(expected_at))
        self.assertEqual(entry["by"], "manager@example.com")
        self.assertEqual(entry["reason"], "Approved correction")
        self.assertEqual(
            entry["changes"],
            {
                "punch_out": {
                    "from": None,
                    "to": epoch_ms(corrected_out),
                },
                "work_hours": {"from": None, "to": 9.5},
                "overtime_minutes": {"from": 0, "to": 30},
            },
        )

    def test_correction_appends_to_seeded_record_without_history(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        main.db.attendance_logs.documents[0].pop("history")

        response = self.correct(status="WFH")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["history"]), 1)
        self.assertEqual(response.json()["history"][0]["changes"]["status"]["to"], "WFH")

    def test_successive_corrections_append_without_editing_prior_history(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        first = self.correct(status="WFH")
        self.assertEqual(first.status_code, 200, first.text)
        first_entry = first.json()["history"][0]

        second = self.correct(status="ON_DUTY")

        self.assertEqual(second.status_code, 200, second.text)
        history = second.json()["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], first_entry)
        self.assertEqual(history[1]["changes"]["status"]["from"], "WFH")
        self.assertEqual(history[1]["changes"]["status"]["to"], "ON_DUTY")

    def test_concurrent_corrections_do_not_overwrite_or_lose_history(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        collection = main.db.attendance_logs
        original_find_one = collection.find_one
        reads_ready = threading.Barrier(2)

        def synchronized_find_one(query, projection=None, sort=None):
            result = original_find_one(query, projection, sort)
            if "emp_code" in query and "date" in query:
                reads_ready.wait()
            return result

        with patch.object(
            collection,
            "find_one",
            side_effect=synchronized_find_one,
        ):
            def send(status):
                return self.client.patch(
                    "/attendance/EMP0001/2026-01-05",
                    json=self.correction(status=status),
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(send, ("WFH", "ON_DUTY")))

        self.assertCountEqual(
            [response.status_code for response in responses],
            [200, 409],
        )
        stored = original_find_one(
            {"emp_code": "EMP0001", "date": "2026-01-05"}
        )
        self.assertEqual(len(stored["history"]), 1)
        self.assertEqual(stored["status"], stored["history"][0]["changes"]["status"]["to"])
        self.assertEqual(stored["history"][0]["changes"]["status"]["from"], "PRESENT")


if __name__ == "__main__":
    unittest.main()
