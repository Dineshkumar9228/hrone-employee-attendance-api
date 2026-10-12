import unittest

from test_attendance_sprint2 import AttendanceSprint2Tests, main


class AttendanceSprint4MonthlyTests(AttendanceSprint2Tests):
    def add_record(self, day, status, month="2026-01", **fields):
        record = {
            "emp_code": "EMP0001",
            "date": f"{month}-{day:02d}",
            "status": status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }
        record.update(fields)
        main.db.attendance_logs.insert_one(record)

    def monthly(self, emp_code="EMP0001", month="2026-01"):
        return self.client.get(
            f"/analytics/employees/{emp_code}/monthly",
            params={"month": month},
        )

    def test_monthly_summary_aggregates_business_metrics(self):
        self.add_employee(joined_on="2026-01-05")
        self.add_record(
            5,
            "PRESENT",
            late_minutes=10,
            overtime_minutes=10,
            half_day=False,
        )
        self.add_record(6, "WFH", late_minutes=0, half_day=True)
        self.add_record(7, "ON_DUTY", late_minutes=0, half_day=False)
        self.add_record(8, "ABSENT")
        self.add_record(9, "LEAVE", late_minutes=0)
        self.add_record(
            10,
            "PRESENT",
            late_minutes=20,
            overtime_minutes=30,
            half_day=True,
        )
        self.add_record(11, "WFH", late_minutes=0, overtime_minutes=5)
        self.add_record(
            1,
            "LEAVE",
            month="2026-02",
            late_minutes=0,
            overtime_minutes=0,
        )

        response = self.monthly()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "emp_code": "EMP0001",
                "month": "2026-01",
                "working_days": 20,
                "present_days": 2.5,
                "leave_days": 1,
                "late_count": 2,
                "total_late_minutes": 30,
                "total_overtime_minutes": 45,
                "attendance_pct": 12.5,
            },
        )
        pipeline = main.db.attendance_logs.aggregate_pipelines[-1]
        self.assertEqual(
            pipeline[0]["$match"],
            {
                "emp_code": "EMP0001",
                "date": {"$gte": "2026-01-01", "$lte": "2026-01-31"},
            },
        )

    def test_monthly_working_days_start_on_midmonth_join_date(self):
        self.add_employee(joined_on="2026-01-15")
        self.add_record(15, "PRESENT")

        response = self.monthly()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["working_days"], 12)
        self.assertEqual(body["present_days"], 1)
        self.assertEqual(body["attendance_pct"], 8.33)

    def test_monthly_summary_empty_result_and_zero_working_days(self):
        self.add_employee(joined_on="2026-01-31")

        response = self.monthly()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "emp_code": "EMP0001",
                "month": "2026-01",
                "working_days": 0,
                "present_days": 0,
                "leave_days": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "total_overtime_minutes": 0,
                "attendance_pct": None,
            },
        )

    def test_monthly_summary_returns_404_for_unknown_employee(self):
        response = self.monthly(emp_code="UNKNOWN")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Employee not found")

    def test_monthly_summary_requires_valid_month(self):
        self.add_employee()
        missing = self.client.get("/analytics/employees/EMP0001/monthly")
        self.assertEqual(missing.status_code, 422)
        for month in ("2026-1", "2026-13", "2026-02-30", "0000-01"):
            with self.subTest(month=month):
                response = self.monthly(month=month)
                self.assertEqual(response.status_code, 422)

    def test_monthly_route_exists(self):
        routes = {
            (method, route.path)
            for route in main.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(
            ("GET", "/analytics/employees/{emp_code}/monthly"),
            routes,
        )


class DepartmentSummaryTests(AttendanceSprint2Tests):
    def add_record(self, emp_code, record_date, status, **fields):
        record = {
            "emp_code": emp_code,
            "date": record_date,
            "status": status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }
        record.update(fields)
        main.db.attendance_logs.insert_one(record)

    def summary(self, month="2026-01", department=None):
        params = {"month": month}
        if department is not None:
            params["department"] = department
        return self.client.get("/analytics/departments/summary", params=params)

    def test_department_summary_aggregates_record_metrics_and_headcount(self):
        self.add_employee(joined_on="2026-01-05", department="Engineering")
        self.add_employee(
            emp_code="EMP0002",
            joined_on="2026-01-01",
            department="Engineering",
        )
        self.add_employee(
            emp_code="EMP0006",
            joined_on="2026-01-01",
            department="Engineering",
        )
        self.add_employee(
            emp_code="EMP0003",
            joined_on="2026-01-01",
            department="Sales",
        )
        self.add_employee(
            emp_code="EMP0004",
            joined_on="2026-01-01",
            department="Support",
        )
        self.add_employee(
            emp_code="EMP0005",
            joined_on="2026-02-01",
            department="Future",
        )
        self.add_record(
            "EMP0001",
            "2026-01-05",
            "PRESENT",
            work_hours=8.0,
            late_minutes=10,
            overtime_minutes=30,
        )
        self.add_record(
            "EMP0001",
            "2026-01-06",
            "WFH",
            work_hours=4.0,
            half_day=True,
        )
        self.add_record(
            "EMP0006",
            "2026-01-06",
            "PRESENT",
            work_hours=4.0,
        )
        self.add_record("EMP0001", "2026-01-07", "LEAVE")
        self.add_record(
            "EMP0001",
            "2026-01-10",
            "PRESENT",
            work_hours=12.0,
            late_minutes=25,
            overtime_minutes=45,
        )
        self.add_record(
            "EMP0003",
            "2026-01-05",
            "ON_DUTY",
            work_hours=6.0,
            late_minutes=5,
            overtime_minutes=0,
        )

        response = self.summary()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "month": "2026-01",
                "items": [
                    {
                        "department": "Engineering",
                        "headcount": 3,
                        "present_days": 2.5,
                        "avg_work_hours": 7.0,
                        "late_count": 2,
                        "total_late_minutes": 35,
                        "leave_count": 1,
                        "on_duty_count": 0,
                    },
                    {
                        "department": "Sales",
                        "headcount": 1,
                        "present_days": 1,
                        "avg_work_hours": 6.0,
                        "late_count": 1,
                        "total_late_minutes": 5,
                        "leave_count": 0,
                        "on_duty_count": 1,
                    },
                    {
                        "department": "Support",
                        "headcount": 1,
                        "present_days": 0,
                        "avg_work_hours": None,
                        "late_count": 0,
                        "total_late_minutes": 0,
                        "leave_count": 0,
                        "on_duty_count": 0,
                    },
                ],
            },
        )
        pipeline = main.db.employees.aggregate_pipelines[-1]
        self.assertEqual(
            pipeline[0]["$match"],
            {"joined_on": {"$lte": "2026-01-31"}},
        )
        self.assertEqual(pipeline[1]["$lookup"]["from"], "attendance_logs")
        self.assertEqual(
            pipeline[1]["$lookup"]["pipeline"][0]["$match"]["$expr"]["$and"][1],
            {"$gte": ["$date", "2026-01-01"]},
        )

    def test_department_filter_is_exact_and_applied_in_aggregation(self):
        self.add_employee(department="Engineering")
        self.add_employee(emp_code="EMP0002", department="engineering")

        response = self.summary(department="Engineering")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["department"] for item in response.json()["items"]],
            ["Engineering"],
        )
        pipeline = main.db.employees.aggregate_pipelines[-1]
        self.assertEqual(
            pipeline[0]["$match"],
            {
                "joined_on": {"$lte": "2026-01-31"},
                "department": "Engineering",
            },
        )

    def test_employees_without_logs_are_included_with_null_average(self):
        self.add_employee(department="People")

        response = self.summary(department="People")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["items"],
            [
                {
                    "department": "People",
                    "headcount": 1,
                    "present_days": 0,
                    "avg_work_hours": None,
                    "late_count": 0,
                    "total_late_minutes": 0,
                    "leave_count": 0,
                    "on_duty_count": 0,
                }
            ],
        )

    def test_average_work_hours_rounds_half_up(self):
        self.add_employee(department="People")
        self.add_record(
            "EMP0001",
            "2026-01-05",
            "PRESENT",
            work_hours=1.0,
        )
        self.add_record(
            "EMP0001",
            "2026-01-06",
            "PRESENT",
            work_hours=1.25,
        )

        response = self.summary(department="People")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["items"][0]["avg_work_hours"],
            1.13,
        )

    def test_employees_joining_after_month_are_excluded(self):
        self.add_employee(joined_on="2026-02-01", department="Future")

        response = self.summary(department="Future")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"month": "2026-01", "items": []})

    def test_department_summary_rejects_missing_or_invalid_month(self):
        self.add_employee()
        missing = self.client.get("/analytics/departments/summary")
        self.assertEqual(missing.status_code, 422)
        for month in ("2026-1", "2026-13", "2026-02-30", "0000-01"):
            with self.subTest(month=month):
                response = self.summary(month=month)
                self.assertEqual(response.status_code, 422)

    def test_department_summary_route_exists(self):
        routes = {
            (method, route.path)
            for route in main.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("GET", "/analytics/departments/summary"), routes)


class LateLeaderboardTests(AttendanceSprint2Tests):
    def add_leaderboard_employee(self, emp_code, department="Engineering"):
        self.add_employee(emp_code=emp_code, department=department)

    def add_record(self, emp_code, day, late_minutes, month="2026-01"):
        main.db.attendance_logs.insert_one(
            {
                "emp_code": emp_code,
                "date": f"{month}-{day:02d}",
                "late_minutes": late_minutes,
            }
        )

    def leaderboard(self, month="2026-01", limit=10, department=None):
        params = {"month": month, "limit": limit}
        if department is not None:
            params["department"] = department
        return self.client.get("/analytics/leaderboard/late", params=params)

    def test_leaderboard_uses_competition_ranking_and_aggregates_month(self):
        for code in ("EMP0001", "EMP0002", "EMP0003"):
            self.add_leaderboard_employee(code)
        self.add_record("EMP0001", 5, 20)
        self.add_record("EMP0001", 6, 10)
        self.add_record("EMP0002", 5, 30)
        self.add_record("EMP0003", 5, 15)
        self.add_record("EMP0003", 1, 900, month="2026-02")

        response = self.leaderboard()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "month": "2026-01",
                "items": [
                    {
                        "rank": 1,
                        "emp_code": "EMP0001",
                        "name": "Test Employee",
                        "department": "Engineering",
                        "total_late_minutes": 30,
                        "late_count": 2,
                    },
                    {
                        "rank": 1,
                        "emp_code": "EMP0002",
                        "name": "Test Employee",
                        "department": "Engineering",
                        "total_late_minutes": 30,
                        "late_count": 1,
                    },
                    {
                        "rank": 3,
                        "emp_code": "EMP0003",
                        "name": "Test Employee",
                        "department": "Engineering",
                        "total_late_minutes": 15,
                        "late_count": 1,
                    },
                ],
            },
        )
        pipeline = main.db.attendance_logs.aggregate_pipelines[-1]
        self.assertEqual(
            pipeline[0]["$match"]["date"],
            {"$gte": "2026-01-01", "$lte": "2026-01-31"},
        )
        self.assertEqual(pipeline[5]["$setWindowFields"]["output"]["rank"], {"$rank": {}})
        self.assertEqual(pipeline[6]["$match"], {"rank": {"$lte": 10}})

    def test_leaderboard_keeps_every_tie_at_the_requested_cutoff(self):
        for code in ("EMP0001", "EMP0002", "EMP0003"):
            self.add_leaderboard_employee(code)
        for code in ("EMP0001", "EMP0002", "EMP0003"):
            self.add_record(code, 5, 15)

        response = self.leaderboard(limit=1)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["emp_code"] for item in response.json()["items"]],
            ["EMP0001", "EMP0002", "EMP0003"],
        )
        self.assertEqual(
            [item["rank"] for item in response.json()["items"]],
            [1, 1, 1],
        )

    def test_leaderboard_department_filter_is_exact(self):
        self.add_leaderboard_employee("EMP0001", "Engineering")
        self.add_leaderboard_employee("EMP0002", "engineering")
        self.add_record("EMP0001", 5, 10)
        self.add_record("EMP0002", 5, 20)

        response = self.leaderboard(department="Engineering")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["emp_code"] for item in response.json()["items"]],
            ["EMP0001"],
        )
        self.assertEqual(response.json()["items"][0]["rank"], 1)

    def test_leaderboard_validates_month_and_limit_boundaries(self):
        self.add_employee("EMP0001")
        self.assertEqual(
            self.client.get("/analytics/leaderboard/late").status_code,
            422,
        )
        for month in ("2026-1", "2026-13", "0000-01"):
            with self.subTest(month=month):
                self.assertEqual(self.leaderboard(month=month).status_code, 422)
        for limit in (0, 51):
            with self.subTest(limit=limit):
                self.assertEqual(self.leaderboard(limit=limit).status_code, 422)
        for limit in (1, 50):
            with self.subTest(limit=limit):
                response = self.leaderboard(limit=limit)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["items"], [])


class DepartmentTrendTests(DepartmentSummaryTests):
    def add_record(self, emp_code, record_date, status, **fields):
        record = {
            "emp_code": emp_code,
            "date": record_date,
            "status": status,
            "late_minutes": 0,
            "half_day": False,
        }
        record.update(fields)
        main.db.attendance_logs.insert_one(record)

    def add_attendance(self, emp_code, record_date, status="PRESENT", **fields):
        self.add_record(emp_code, record_date, status, **fields)

    def trend(self, department="Engineering", start="2026-01-05", end="2026-01-12"):
        return self.client.get(
            f"/analytics/departments/{department}/trend",
            params={"from": start, "to": end},
        )

    def test_trend_gap_fills_calendar_and_uses_partial_moving_windows(self):
        self.add_employee(joined_on="2026-01-05", department="Engineering")
        self.add_employee(
            emp_code="EMP0002",
            joined_on="2026-01-07",
            department="Engineering",
        )
        self.add_attendance(
            "EMP0001",
            "2026-01-05",
            "PRESENT",
            half_day=True,
        )
        self.add_attendance("EMP0001", "2026-01-10", "PRESENT")

        response = self.trend()

        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual(len(items), 8)
        self.assertEqual(
            [item["date"] for item in items],
            [
                "2026-01-05",
                "2026-01-06",
                "2026-01-07",
                "2026-01-08",
                "2026-01-09",
                "2026-01-10",
                "2026-01-11",
                "2026-01-12",
            ],
        )
        self.assertEqual(
            [(items[index]["attendance_rate"], items[index]["moving_avg_7d"])
             for index in range(3)],
            [(0.5, 0.5), (0.0, 0.25), (0.0, 0.1667)],
        )
        self.assertEqual(items[5]["present_count"], 1)
        self.assertIsNone(items[5]["attendance_rate"])
        self.assertEqual(items[7]["moving_avg_7d"], 0.0)
        self.assertFalse(items[5]["is_working_day"])
        self.assertEqual(items[2]["headcount"], 2)

    def test_trend_returns_zero_rate_for_empty_workday_and_null_for_zero_headcount(self):
        self.add_employee(joined_on="2026-01-05", department="Engineering")
        self.add_employee(
            emp_code="EMP0002",
            joined_on="2026-01-06",
            department="Future",
        )

        response = self.trend(start="2026-01-05", end="2026-01-06")
        future = self.trend(
            department="Future",
            start="2026-01-05",
            end="2026-01-06",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["attendance_rate"] for item in response.json()["items"]],
            [0.0, 0.0],
        )
        self.assertEqual(future.status_code, 200, future.text)
        self.assertEqual(future.json()["items"][0]["headcount"], 0)
        self.assertIsNone(future.json()["items"][0]["attendance_rate"])
        self.assertIsNone(future.json()["items"][0]["moving_avg_7d"])

    def test_trend_returns_404_for_unknown_department(self):
        response = self.trend(department="Unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Department not found")

    def test_trend_validates_dates_and_inclusive_92_day_boundary(self):
        self.add_employee()
        for start, end in (
            ("2026-02-30", "2026-03-01"),
            ("2026-01-06", "2026-01-05"),
            ("2026-01-01", "2026-04-03"),
        ):
            with self.subTest(start=start, end=end):
                response = self.trend(start=start, end=end)
                self.assertEqual(response.status_code, 422, response.text)
        accepted = self.trend(start="2026-01-01", end="2026-04-02")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(len(accepted.json()["items"]), 92)


class AdminExplainTests(AttendanceSprint2Tests):
    def explain(self, endpoint, **params):
        return self.client.get(f"/admin/explain/{endpoint}", params=params)

    def test_explain_runs_execution_stats_for_supported_endpoints(self):
        self.add_employee()
        requests = (
            (
                "attendance_list",
                {"emp_code": "EMP0001", "status": "PRESENT", "date_from": "2026-01-05", "page": 2, "page_size": 5},
                "attendance_logs",
            ),
            (
                "employee_monthly",
                {"emp_code": "EMP0001", "month": "2026-01"},
                "attendance_logs",
            ),
            (
                "department_summary",
                {"month": "2026-01", "department": "Engineering"},
                "employees",
            ),
            (
                "late_leaderboard",
                {"month": "2026-01", "limit": 1},
                "attendance_logs",
            ),
            (
                "department_trend",
                {"department": "Engineering", "from": "2026-01-05", "to": "2026-01-06"},
                "employees",
            ),
        )
        for endpoint, params, collection in requests:
            with self.subTest(endpoint=endpoint):
                response = self.explain(endpoint, **params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    set(response.json()),
                    {"endpoint", "collection", "explain"},
                )
                self.assertEqual(response.json()["endpoint"], endpoint)
                self.assertEqual(response.json()["collection"], collection)
                self.assertIn("executionStats", response.json()["explain"])
        self.assertEqual(len(main.db.commands), len(requests))
        for command in main.db.commands:
            self.assertEqual(command["verbosity"], "executionStats")

    def test_explain_uses_full_endpoint_aggregation_pipelines(self):
        self.add_employee()
        main.employee_monthly("EMP0001", "2026-01")
        monthly_pipeline = main.db.attendance_logs.aggregate_pipelines[-1]
        monthly_response = self.explain(
            "employee_monthly",
            emp_code="EMP0001",
            month="2026-01",
        )
        self.assertEqual(monthly_response.status_code, 200)
        self.assertEqual(
            main.db.commands[-1]["explain"]["pipeline"],
            monthly_pipeline,
        )

        main.department_summary("2026-01", "Engineering")
        summary_pipeline = main.db.employees.aggregate_pipelines[-1]
        summary_response = self.explain(
            "department_summary",
            month="2026-01",
            department="Engineering",
        )
        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(
            main.db.commands[-1]["explain"]["pipeline"],
            summary_pipeline,
        )
        self.assertEqual(len(main.db.commands[-1]["explain"]["pipeline"]), 7)

    def test_explain_rejects_unsupported_endpoints_and_missing_parameters(self):
        unsupported = self.explain("not_supported")
        self.assertEqual(unsupported.status_code, 422)
        for endpoint in (
            "employee_monthly",
            "department_summary",
            "late_leaderboard",
            "department_trend",
        ):
            with self.subTest(endpoint=endpoint):
                response = self.explain(endpoint)
                self.assertEqual(response.status_code, 422)
        invalid_month = self.explain(
            "late_leaderboard",
            month="2026-13",
        )
        self.assertEqual(invalid_month.status_code, 422)
        invalid_range = self.explain(
            "department_trend",
            department="Engineering",
            **{"from": "2026-01-01", "to": "2026-04-03"},
        )
        self.assertEqual(invalid_range.status_code, 422)

    def test_attendance_list_explain_preserves_filters_and_pagination(self):
        response = self.explain(
            "attendance_list",
            emp_code="EMP0001",
            status="LEAVE",
            date_from="2026-01-01",
            date_to="2026-01-31",
            page=2,
            page_size=10,
        )
        self.assertEqual(response.status_code, 200, response.text)
        command = main.db.commands[-1]["explain"]
        self.assertEqual(command["find"], "attendance_logs")
        self.assertEqual(
            command["filter"],
            {
                "emp_code": "EMP0001",
                "status": "LEAVE",
                "date": {"$gte": "2026-01-01", "$lte": "2026-01-31"},
            },
        )
        self.assertEqual(command["projection"], {"_id": 0})
        self.assertEqual(command["sort"], {"date": -1, "emp_code": 1})
        self.assertEqual((command["skip"], command["limit"]), (10, 10))


if __name__ == "__main__":
    unittest.main()
