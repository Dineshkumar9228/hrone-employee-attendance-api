import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app import main


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def at_ist(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, second, tzinfo=IST)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, keys):
        if isinstance(keys, str):
            keys = [(keys, 1)]
        for field, direction in reversed(keys):
            self.documents.sort(
                key=lambda document: document.get(field),
                reverse=direction < 0,
            )
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.lock = threading.RLock()
        self.next_id = 1
        self.aggregate_pipelines = []

    def create_index(self, *_args, **_kwargs):
        return "fake_index"

    @staticmethod
    def matches(document, query):
        for field, expected in query.items():
            actual = document.get(field)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$exists":
                        if (field in document) != value:
                            return False
                        continue
                    if actual is None and operator != "$ne":
                        return False
                    if operator == "$lte" and not actual <= value:
                        return False
                    if operator == "$lt" and not actual < value:
                        return False
                    if operator == "$gte" and not actual >= value:
                        return False
                    if operator == "$gt" and not actual > value:
                        return False
                    if operator == "$ne" and actual == value:
                        return False
            elif actual != expected:
                return False
        return True

    def insert_one(self, document):
        with self.lock:
            if "date" in document and any(
                row.get("emp_code") == document["emp_code"]
                and row.get("date") == document["date"]
                for row in self.documents
            ):
                raise DuplicateKeyError("duplicate attendance date")
            inserted = copy.deepcopy(document)
            inserted["_id"] = self.next_id
            self.next_id += 1
            self.documents.append(inserted)

            class Result:
                inserted_id = inserted["_id"]

            return Result()

    def find_one(self, query, projection=None, sort=None):
        with self.lock:
            matches = [
                copy.deepcopy(row)
                for row in self.documents
                if self.matches(row, query)
            ]
        cursor = FakeCursor(matches)
        if sort:
            cursor.sort(sort)
        if not cursor.documents:
            return None
        result = cursor.documents[0]
        if projection == {"_id": 0}:
            result.pop("_id", None)
        return result

    def find(self, query, projection=None):
        with self.lock:
            matches = [
                copy.deepcopy(row)
                for row in self.documents
                if self.matches(row, query)
            ]
        if projection == {"_id": 0}:
            for row in matches:
                row.pop("_id", None)
        return FakeCursor(matches)

    def count_documents(self, query):
        with self.lock:
            return sum(self.matches(row, query) for row in self.documents)

    def aggregate(self, pipeline):
        self.aggregate_pipelines.append(copy.deepcopy(pipeline))
        if len(pipeline) > 1 and "$lookup" in pipeline[1]:
            return self.aggregate_department_summary(pipeline)
        if any("$setWindowFields" in stage for stage in pipeline):
            if "$rank" in str(pipeline):
                return self.aggregate_late_leaderboard(pipeline)
            return self.aggregate_department_trend(pipeline)

        match = pipeline[0]["$match"]
        with self.lock:
            records = [
                copy.deepcopy(row)
                for row in self.documents
                if self.matches(row, match)
            ]
        if not records:
            return iter([])

        present_days = 0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0
        for record in records:
            weekday = datetime.fromisoformat(record["date"]).weekday()
            status = record.get("status")
            if status in {"PRESENT", "WFH", "ON_DUTY"} and weekday < 5:
                present_days += 0.5 if record.get("half_day", False) else 1
            leave_days += status == "LEAVE"
            late_minutes = record.get("late_minutes", 0)
            late_count += late_minutes > 0
            total_late_minutes += late_minutes
            total_overtime_minutes += record.get("overtime_minutes", 0)

        return iter([{
            "_id": None,
            "present_days": present_days,
            "leave_days": leave_days,
            "late_count": late_count,
            "total_late_minutes": total_late_minutes,
            "total_overtime_minutes": total_overtime_minutes,
        }])

    def aggregate_late_leaderboard(self, pipeline):
        date_range = pipeline[0]["$match"]["date"]
        month_logs = [
            record
            for record in self.documents
            if date_range["$gte"] <= record.get("date", "") <= date_range["$lte"]
        ]
        totals = {}
        for record in month_logs:
            code = record["emp_code"]
            result = totals.setdefault(code, {"total": 0, "late_count": 0})
            minutes = record.get("late_minutes", 0)
            result["total"] += minutes
            result["late_count"] += minutes > 0

        employee_collection = self.employees
        employee_by_code = {
            employee["emp_code"]: employee
            for employee in employee_collection.documents
        }
        department_filter = None
        lookup = pipeline[3]["$lookup"]["pipeline"][0]["$match"]
        if "$and" in lookup.get("$expr", {}):
            department_filter = lookup["$expr"]["$and"][1]["$eq"][1]

        ranked = []
        for code, result in totals.items():
            employee = employee_by_code.get(code)
            if result["total"] <= 0 or employee is None:
                continue
            if department_filter is not None and employee["department"] != department_filter:
                continue
            ranked.append(
                {
                    "emp_code": code,
                    "name": employee["name"],
                    "department": employee["department"],
                    "total_late_minutes": result["total"],
                    "late_count": result["late_count"],
                }
            )
        ranked.sort(key=lambda row: (-row["total_late_minutes"], row["emp_code"]))

        rank_by_total = {}
        for index, row in enumerate(ranked, start=1):
            total = row["total_late_minutes"]
            rank_by_total.setdefault(total, index)
            row["rank"] = rank_by_total[total]
        rank_limit = pipeline[6]["$match"]["rank"]["$lte"]
        return iter([row for row in ranked if row["rank"] <= rank_limit])

    def aggregate_department_trend(self, pipeline):
        department = pipeline[0]["$match"]["department"]
        day_count = pipeline[2]["$project"]["calendar"]["$map"]["input"]["$range"][1]
        start_date = date.fromisoformat(
            pipeline[2]["$project"]["calendar"]["$map"]["in"]["$dateAdd"]
            ["startDate"]["$dateFromString"]["dateString"]
        )
        if hasattr(self, "employees"):
            employee_collection = self.employees
            attendance_collection = self
        else:
            employee_collection = self
            attendance_collection = self.attendance_logs
        with employee_collection.lock, attendance_collection.lock:
            employees = copy.deepcopy(employee_collection.documents)
            attendance_records = copy.deepcopy(attendance_collection.documents)

        rows = []
        for day_offset in range(day_count):
            current_date = start_date + timedelta(days=day_offset)
            day_string = current_date.isoformat()
            active = [
                employee
                for employee in employees
                if employee["department"] == department
                and employee.get("joined_on", "9999-12-31") <= day_string
            ]
            active_codes = {employee["emp_code"] for employee in active}
            records = [
                record
                for record in attendance_records
                if record.get("emp_code") in active_codes
                and record.get("date") == day_string
            ]
            present_count = sum(
                0.5 if record.get("half_day", False) else 1
                for record in records
                if record.get("status") in {"PRESENT", "WFH", "ON_DUTY"}
            )
            late_count = sum(
                record.get("late_minutes", 0) > 0
                for record in records
            )
            working_day = current_date.weekday() < 5
            headcount = sum(
                employee.get("joined_on", "9999-12-31") <= day_string
                for employee in employees
                if employee["department"] == department
            )
            rate = None
            if working_day and headcount > 0:
                rate = round(present_count / headcount + 1e-12, 4)
            rows.append(
                {
                    "date": day_string,
                    "is_working_day": working_day,
                    "headcount": headcount,
                    "present_count": present_count,
                    "late_count": late_count,
                    "attendance_rate": rate,
                }
            )

        for index, row in enumerate(rows):
            values = [
                item["attendance_rate"]
                for item in rows[max(0, index - 6):index + 1]
                if item["attendance_rate"] is not None
            ]
            row["moving_avg_7d"] = (
                round(sum(values) / len(values) + 1e-12, 4)
                if values
                else None
            )
        return iter(rows)

    def aggregate_department_summary(self, pipeline):
        employee_match = pipeline[0]["$match"]
        lookup_match = pipeline[1]["$lookup"]["pipeline"][0]["$match"]["$expr"]["$and"]
        month_start = next(
            clause["$gte"][1]
            for clause in lookup_match
            if "$gte" in clause
        )
        month_end = next(
            clause["$lte"][1]
            for clause in lookup_match
            if "$lte" in clause
        )
        attendance_logs = self.attendance_logs
        with self.lock, attendance_logs.lock:
            employees = [
                copy.deepcopy(employee)
                for employee in self.documents
                if self.matches(employee, employee_match)
            ]
            attendance_records = copy.deepcopy(attendance_logs.documents)

        departments = {}
        for employee in employees:
            department = employee["department"]
            totals = departments.setdefault(
                department,
                {
                    "headcount": 0,
                    "present_days": 0,
                    "work_hours_sum": 0,
                    "work_hours_count": 0,
                    "late_count": 0,
                    "total_late_minutes": 0,
                    "leave_count": 0,
                    "on_duty_count": 0,
                },
            )
            totals["headcount"] += 1
            records = [
                record
                for record in attendance_records
                if record.get("emp_code") == employee.get("emp_code")
                and month_start <= record.get("date", "") <= month_end
            ]

            for record in records:
                status = record.get("status")
                if (
                    status in {"PRESENT", "WFH", "ON_DUTY"}
                    and datetime.fromisoformat(record["date"]).weekday() < 5
                ):
                    totals["present_days"] += (
                        0.5 if record.get("half_day", False) else 1
                    )
                work_hours = record.get("work_hours")
                if (
                    status in {"PRESENT", "WFH", "ON_DUTY"}
                    and work_hours is not None
                ):
                    totals["work_hours_sum"] += work_hours
                    totals["work_hours_count"] += 1
                late_minutes = record.get("late_minutes", 0)
                totals["late_count"] += late_minutes > 0
                if late_minutes > 0:
                    totals["total_late_minutes"] += late_minutes
                totals["leave_count"] += status == "LEAVE"
                totals["on_duty_count"] += status == "ON_DUTY"

        results = []
        for department, totals in sorted(departments.items()):
            work_hours_count = totals.pop("work_hours_count")
            work_hours_sum = totals.pop("work_hours_sum")
            average = None
            if work_hours_count:
                average = int(
                    work_hours_sum / work_hours_count * 100 + 0.5
                ) / 100
            results.append(
                {
                    "department": department,
                    **totals,
                    "avg_work_hours": average,
                }
            )
        return iter(results)

    def update_one(self, query, update):
        with self.lock:
            for row in self.documents:
                if self.matches(row, query):
                    for field, value in update.get("$set", {}).items():
                        row[field] = copy.deepcopy(value)

                    for field, value in update.get("$push", {}).items():
                        row.setdefault(field, []).append(copy.deepcopy(value))

                    class Result:
                        matched_count = 1

                    return Result()

        class Result:
            matched_count = 0

        return Result()


class FakeDatabase:
    def __init__(self):
        self.employees = FakeCollection()
        self.attendance_logs = FakeCollection()
        self.employees.attendance_logs = self.attendance_logs
        self.attendance_logs.employees = self.employees
        self.commands = []

    def command(self, command):
        self.commands.append(copy.deepcopy(command))
        return {
            "ok": 1,
            "queryPlanner": {"winningPlan": {"stage": "IXSCAN"}},
            "executionStats": {"executionSuccess": True},
        }


class AttendanceSprint2Tests(unittest.TestCase):
    def setUp(self):
        self.original_db = main.db
        main.db = FakeDatabase()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        main.db = self.original_db

    def add_employee(
        self,
        emp_code="EMP0001",
        shift_start="09:30",
        shift_end="18:30",
        joined_on="2026-01-05",
        department="Engineering",
    ):
        main.db.employees.insert_one(
            {
                "emp_code": emp_code,
                "name": "Test Employee",
                "shift_start": shift_start,
                "shift_end": shift_end,
                "joined_on": joined_on,
                "department": department,
            }
        )

    def punch_in(self, when, emp_code="EMP0001", status="PRESENT"):
        return self.client.post(
            "/attendance/punch-in",
            json={
                "emp_code": emp_code,
                "status": status,
                "punched_at": epoch_ms(when),
            },
        )

    def punch_out(self, when, emp_code="EMP0001"):
        return self.client.post(
            "/attendance/punch-out",
            json={"emp_code": emp_code, "punched_at": epoch_ms(when)},
        )

    def test_punch_in_success_uses_epoch_ms_and_truncates_seconds(self):
        self.add_employee()
        response = self.punch_in(at_ist(5, 9, 30, 0).replace(microsecond=987000), status="WFH")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["date"], "2026-01-05")
        self.assertEqual(body["status"], "WFH")
        self.assertEqual(body["punch_in"], epoch_ms(at_ist(5, 9, 30)))
        self.assertEqual(body["late_minutes"], 0)
        self.assertIsNone(body["punch_out"])

    def test_punch_in_uses_current_utc_when_timestamp_is_omitted(self):
        self.add_employee()
        fixed_now = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
        with patch.object(main, "utc_now", return_value=fixed_now):
            response = self.client.post(
                "/attendance/punch-in",
                json={"emp_code": "EMP0001", "status": "PRESENT"},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["punch_in"], epoch_ms(fixed_now))

    def test_punch_in_unknown_employee_and_invalid_status(self):
        unknown = self.punch_in(at_ist(5, 9, 30), emp_code="EMP9999")
        self.assertEqual(unknown.status_code, 404)
        self.add_employee()
        invalid = self.client.post(
            "/attendance/punch-in",
            json={
                "emp_code": "EMP0001",
                "status": "ABSENT",
                "punched_at": epoch_ms(at_ist(5, 9, 30)),
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_duplicate_and_concurrent_punch_in_are_conflicts(self):
        self.add_employee()
        moment = at_ist(5, 9, 30)
        self.assertEqual(self.punch_in(moment).status_code, 201)
        self.assertEqual(self.punch_in(moment).status_code, 409)

        main.db.attendance_logs.documents.clear()
        barrier = threading.Barrier(2)

        def send():
            barrier.wait()
            return self.punch_in(moment).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: send(), range(2)))
        self.assertCountEqual(results, [201, 409])

    def test_late_minutes_at_start_grace_boundary_and_eleven_minutes(self):
        for minute, second, expected in (
            (30, 0, 0),
            (40, 0, 0),
            (41, 0, 11),
        ):
            with self.subTest(minute=minute, second=second):
                punch = datetime(2026, 1, 5, 9, minute, second, tzinfo=IST)
                self.assertEqual(
                    main.compute_late_minutes(
                        punch, "09:30", "18:30", "2026-01-05"
                    ),
                    expected,
                )

    def test_late_minutes_across_midnight_and_overnight_attendance_date(self):
        midnight_shift_punch = at_ist(6, 0, 16)
        self.assertEqual(
            main.compute_late_minutes(
                midnight_shift_punch, "00:05", "08:00", "2026-01-06"
            ),
            11,
        )
        overnight_punch = at_ist(6, 0, 6)
        attendance_date = "2026-01-05"
        self.assertEqual(
            main.compute_late_minutes(
                overnight_punch, "23:55", "07:00", attendance_date
            ),
            11,
        )
        self.assertEqual(
            main.compute_late_minutes(
                at_ist(5, 9, 30), "09:30", "18:30", "2026-01-05"
            ),
            0,
        )

    def test_punch_out_unknown_employee_and_no_eligible_record(self):
        self.assertEqual(self.punch_out(at_ist(5, 18, 30)).status_code, 404)
        self.add_employee()
        self.assertEqual(self.punch_out(at_ist(5, 18, 30)).status_code, 404)

    def test_successful_and_duplicate_punch_out(self):
        self.add_employee()
        self.assertEqual(self.punch_in(at_ist(5, 9, 30)).status_code, 201)
        out_time = at_ist(5, 18, 30)
        response = self.punch_out(out_time)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["punch_out"], epoch_ms(out_time))
        self.assertEqual(response.json()["work_hours"], 9.0)
        self.assertEqual(response.json()["overtime_minutes"], 0)
        self.assertFalse(response.json()["half_day"])
        self.assertEqual(self.punch_out(out_time).status_code, 409)

    def test_punch_out_uses_current_utc_when_timestamp_is_omitted(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        fixed_now = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        with patch.object(main, "utc_now", return_value=fixed_now):
            response = self.client.post(
                "/attendance/punch-out",
                json={"emp_code": "EMP0001"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["punch_out"], epoch_ms(fixed_now))

    def test_punch_out_before_at_and_more_than_24_hours(self):
        self.add_employee()
        punch_in = at_ist(5, 9, 30)
        self.punch_in(punch_in)
        self.assertEqual(self.punch_out(punch_in).status_code, 422)
        self.assertEqual(
            self.punch_out(punch_in - timedelta(seconds=1)).status_code, 422
        )
        at_limit = self.punch_out(punch_in + timedelta(hours=24))
        self.assertEqual(at_limit.status_code, 200)

        self.add_employee(emp_code="EMP0002")
        self.punch_in(punch_in, emp_code="EMP0002")
        self.assertEqual(
            self.punch_out(
                punch_in + timedelta(hours=24, seconds=1), emp_code="EMP0002"
            ).status_code,
            422,
        )
        stored = main.db.attendance_logs.find_one({"emp_code": "EMP0002"})
        self.assertIsNone(stored["punch_out"])

    def test_concurrent_punch_out_only_closes_record_once(self):
        self.add_employee()
        self.punch_in(at_ist(5, 9, 30))
        barrier = threading.Barrier(2)

        def send():
            barrier.wait()
            return self.punch_out(at_ist(5, 18, 30)).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: send(), range(2)))
        self.assertCountEqual(results, [200, 409])

    def test_work_hours_use_half_up_rounding(self):
        start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
        self.assertEqual(
            main.compute_work_hours(start, start + timedelta(seconds=3617)),
            1.00,
        )
        self.assertEqual(
            main.compute_work_hours(start, start + timedelta(seconds=3618)),
            1.01,
        )

    def test_half_day_threshold_uses_rounded_work_hours(self):
        self.add_employee()
        start = at_ist(5, 9, 30)
        self.punch_in(start)
        below_threshold = self.punch_out(start + timedelta(seconds=4 * 3600 + 29 * 60 + 24))
        self.assertEqual(below_threshold.status_code, 200)
        self.assertEqual(below_threshold.json()["work_hours"], 4.49)
        self.assertTrue(below_threshold.json()["half_day"])

        self.add_employee(emp_code="EMP0002")
        self.punch_in(start, emp_code="EMP0002")
        at_threshold = self.punch_out(
            start + timedelta(seconds=4 * 3600 + 29 * 60 + 48),
            emp_code="EMP0002",
        )
        self.assertEqual(at_threshold.status_code, 200)
        self.assertEqual(at_threshold.json()["work_hours"], 4.5)
        self.assertFalse(at_threshold.json()["half_day"])

    def test_overtime_requires_at_least_30_whole_minutes(self):
        end = at_ist(5, 18, 30)
        self.assertEqual(
            main.compute_overtime(
                end + timedelta(minutes=29), "09:30", "18:30", "2026-01-05"
            ),
            0,
        )
        self.assertEqual(
            main.compute_overtime(
                end + timedelta(minutes=30), "09:30", "18:30", "2026-01-05"
            ),
            30,
        )

    def test_overnight_punch_out_overtime_boundary(self):
        self.add_employee(shift_start="22:00", shift_end="06:00")
        punch_in = at_ist(5, 22, 0)
        self.assertEqual(self.punch_in(punch_in).status_code, 201)
        below = self.punch_out(at_ist(6, 6, 29))
        self.assertEqual(below.status_code, 200)
        self.assertEqual(below.json()["overtime_minutes"], 0)

        self.add_employee(emp_code="EMP0002", shift_start="22:00", shift_end="06:00")
        self.assertEqual(self.punch_in(punch_in, emp_code="EMP0002").status_code, 201)
        threshold = self.punch_out(at_ist(6, 6, 30), emp_code="EMP0002")
        self.assertEqual(threshold.status_code, 200)
        self.assertEqual(threshold.json()["overtime_minutes"], 30)

    def test_attendance_date_filters_are_inclusive_and_total_is_filtered(self):
        self.add_employee()
        for day, status in ((5, "PRESENT"), (6, "WFH"), (7, "ON_DUTY")):
            self.punch_in(at_ist(day, 9, 30), status=status)
        inclusive = self.client.get(
            "/attendance",
            params={"date_from": "2026-01-05", "date_to": "2026-01-06"},
        )
        self.assertEqual(inclusive.status_code, 200)
        self.assertEqual(inclusive.json()["total"], 2)
        self.assertEqual(
            {row["date"] for row in inclusive.json()["items"]},
            {"2026-01-05", "2026-01-06"},
        )
        response = self.client.get(
            "/attendance",
            params={
                "emp_code": "EMP0001",
                "date_from": "2026-01-05",
                "date_to": "2026-01-06",
                "status": "WFH",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual([row["date"] for row in body["items"]], ["2026-01-06"])
        reversed_range = self.client.get(
            "/attendance",
            params={"date_from": "2026-01-07", "date_to": "2026-01-05"},
        )
        self.assertEqual(reversed_range.status_code, 422)

    def test_attendance_pagination_and_sorting(self):
        for code in ("EMP0002", "EMP0001", "EMP0003"):
            self.add_employee(emp_code=code)
        for code, day in (("EMP0002", 5), ("EMP0001", 6), ("EMP0003", 6)):
            self.punch_in(at_ist(day, 9, 30), emp_code=code)
        response = self.client.get("/attendance", params={"page": 1, "page_size": 2})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["page"], 1)
        self.assertEqual(
            [(row["date"], row["emp_code"]) for row in body["items"]],
            [("2026-01-06", "EMP0001"), ("2026-01-06", "EMP0003")],
        )
        self.assertEqual(
            self.client.get("/attendance", params={"page": 0}).status_code, 422
        )
        self.assertEqual(
            self.client.get("/attendance", params={"page_size": 101}).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/attendance", params={"page_size": 0}).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
