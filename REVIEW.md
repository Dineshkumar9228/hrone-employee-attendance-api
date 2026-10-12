# REVIEW.md

## Defect Review — Starter `app/main.py`

List defects identified in the starter implementation, how they were detected, and how they were fixed.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Attendance late-minute calculation | An incorrect shift-date adjustment could produce an incorrect late-minute calculation for overnight attendance. | Compare the calculated attendance date and late minutes against a controlled overnight punch-in timestamp. | Removed the redundant shift-date adjustment and covered early-morning overnight attendance-date handling in tests. |
| 2 | Attendance work-hour calculation | Ordinary floating-point rounding can produce results inconsistent with the required decimal half-up rounding. | Test durations at half-cent rounding boundaries and compare the rounded work hours with the expected result. | Used decimal arithmetic with `ROUND_HALF_UP`; verified the behavior with Sprint 2 tests. |
| 3 | Attendance half-day calculation | Determining half-day status before rounding can disagree with the required threshold based on rounded work hours. | Test a duration whose rounded work hours fall on the 4.50-hour threshold. | Calculate half-day status from the rounded work-hours value and test the threshold. |
| 4 | Attendance punch-out | Updating a record without an atomic eligibility check can allow concurrent requests to close the same open attendance record inconsistently. | Submit concurrent punch-out requests and verify that only one succeeds. | Use an atomic conditional update; a request that loses the race returns HTTP 409. The threaded test uses an in-memory mock, not a live MongoDB integration test. |
| 5 | Sprint 3 correction request and history | The correction request omitted the required `reason` and `regularized_by` fields, and history entries did not match the documented `{at, by, reason, changes}` shape or `{from, to}` values. | Send a schema-valid correction and compare the returned history entry with `openapi.yaml`. | Added the documented request constraints and emit the specified history fields with only actual changes. |
| 6 | Sprint 3 overnight punch-in validation | Validation compared the punch-in's IST calendar date directly with the record date, rejecting early-morning punches that belong to the previous overnight shift date. | Correct an overnight-shift record for 2026-01-05 with a 05:30 IST punch-in on 2026-01-06. | Apply the overnight shift-date rule when validating the corrected punch-in. |
| 7 | Sprint 3 conditional update for seeded records | The concurrency guard required `history: []`, so a seeded record with no `history` field could not be corrected despite the contract allowing omitted history. | Correct a fake-database seed record after removing its `history` field. | Match the history field's absence explicitly; MongoDB's atomic `$push` creates the array while retaining the compare-and-set guard. |

**Review scope note:** The table above records issues identified in the available implementation and test history. It is not a claim that every line of the original starter file has been audited. Add further rows only when the original defect, reproduction case, and fix have been verified.

## Items Reviewed but Not Classified as Defects

| Item | Why it was not classified as a defect |
|---|---|
| A late-minute result of `230` in an earlier attendance response | The exact employee, shift configuration, and input timestamp could not be established from the available context. A controlled 09:30 IST punch-in for a 09:30 shift returned `late_minutes: 0`. The historical result remains unverified rather than being treated as a confirmed defect. |
| Early-morning punches for overnight shifts | These require attendance-date handling based on the IST shift configuration. The behavior was separately covered by tests; it is not inherently a defect. |
| Attendance records on weekends | Weekend records can still contribute to late-minute and overtime calculations. Working-day rules apply separately to monthly working-day and attendance-rate calculations. |
| FastAPI startup-event deprecation warnings | The warnings concern the deprecated `@app.on_event("startup")` pattern. They are cleanup work, not evidence that the tested endpoint behavior is incorrect. A lifespan-handler migration remains optional cleanup. |

## Current Verification Status

- Employee creation was tested successfully and returned HTTP 201.
- Duplicate employee handling was tested and returned HTTP 409 with `emp_code already exists`.
- Invalid employee data was rejected with HTTP 422, including invalid email/employee-code input and identical shift start/end times.
- Employee listing, exact case-sensitive department filtering, and pagination boundaries have been tested.
- Attendance listing was tested for successful responses, pagination, and invalid date ranges.
- MongoDB indexes were created and verified in MongoDB Compass.
- The attendance date-range query was checked using Explain Plan. MongoDB used `IXSCAN` with `idx_attendance_date_code`, returning 9 matching records after examining 9 index keys and 9 documents.
- The health endpoint was tested through Swagger and returned HTTP 200 with `{"status": "ok"}` while MongoDB was running.
- The MongoDB-unavailable health-check path remains unverified. When MongoDB was configured to use an unavailable port, application startup failed during index creation before the health endpoint could be called.
- These results describe the cases tested so far; they do not establish that every edge case or large-dataset performance requirement has been verified.

## Sprint 2: Attendance Punch-In/Punch-Out

### Implemented Behavior

- Punch-in accepts the three permitted statuses, uses UTC instants truncated to whole seconds, determines the attendance date in IST (including early-morning overnight punches), and maps duplicate-key conflicts to HTTP 409. The existing unique employee/date index remains the concurrency guarantee.
- Late minutes are calculated from the shift-start date associated with the attendance date. The 10-minute allowance is strict, while reported late minutes floor all elapsed minutes since shift start.
- Punch-out validates duration before changing a record, chooses the most recent eligible open punch-in, and atomically writes punch-out and all derived values. A concurrent update that loses the atomic write returns HTTP 409; no rollback is used.
- Work hours use decimal half-up rounding; half-day uses the rounded value. Overtime uses the shift-end instant on the correct IST date and requires at least 30 whole minutes.
- Attendance filtering, inclusive date bounds, count, ordering, and pagination remain MongoDB query operations.

### Tests Actually Passed

- `python -m py_compile app\main.py tests\test_attendance_sprint2.py` completed successfully.
- `python -m unittest discover -s tests -v`: 17 tests passed, 0 failed in the Sprint 2 test run.
- The tests cover fixed-time punch-in/out responses, validation and conflicts, concurrently submitted requests, midnight/overnight calculations, rounding and thresholds, and attendance filtering/sorting/pagination.
- The tests use a thread-safe in-memory collection through FastAPI's test client. Each test replaces `main.db` with a fresh `FakeDatabase` and restores the original reference during teardown. The test records are not written to the development MongoDB database.
- A controlled 09:30 IST punch-in with a 09:30 shift start returns `late_minutes: 0`; the reported `230` was not reproducible from that controlled input. Early-morning overnight attendance-date handling was separately covered and its redundant shift-date adjustment was removed.

### Known Defects or Unresolved Edge Cases

- The reported `230` result cannot be tied to a specific employee or shift configuration from the available context, so that exact historical input remains unverified.
- Threaded requests were exercised against an in-memory mock, not a MongoDB server. MongoDB unique-index and atomic-update race behavior is implemented but not integration-tested here.
- Large-dataset query performance and live MongoDB index usage were not measured in the Sprint 2 test run.

## Sprint 3: Attendance Corrections and Audit History

### Implemented Behavior

- Added `PATCH /attendance/{emp_code}/{attendance_date}` for attendance corrections.
- Enforces the OpenAPI request requirements for `reason` and `regularized_by`, the permitted statuses, and epoch-millisecond punch timestamps. Unknown employees or missing attendance records return 404; invalid dates, invalid combinations, no-op corrections, and invalid punch times return 422.
- Omitted correction fields remain unchanged. `ABSENT` and `LEAVE` clear both punch times and reset derived values; presence statuses require a punch-in on the record's attendance date, including the overnight-shift date rule. Punch-out must be after punch-in and no more than 24 hours later.
- Recomputes derived fields and appends one `{at, by, reason, changes}` entry. `changes` uses `{from, to}` and includes only fields whose stored values changed. Existing history is retained, including records whose seeded history field was absent.
- Uses a compare-and-set update and returns 409 to a concurrent correction that loses the race.

### Tests Actually Passed

- `python -m unittest discover -s tests -p "test_attendance_sprint3.py" -v`: 53 tests passed, 0 failed. This includes inherited Sprint 2 cases.
- `python -m unittest discover -s tests -v`: 70 tests passed, 0 failed.
- `python -m py_compile app\main.py tests\test_attendance_sprint2.py tests\test_attendance_sprint3.py` completed successfully.
- `git diff --check` completed successfully before this documentation update; it was rerun after the update.
- Tests cover omitted fields, no-op corrections, absent/leave behavior and invalid combinations, timestamp type validation, IST and overnight attendance-date validation, punch-out ordering and the 24-hour boundary, exact audit contents and append behavior, missing resources, and concurrent corrections.
- Every API test replaces `main.db` with a fresh in-memory fake database and restores the original reference during teardown. These tests do not write to the development MongoDB database.

## Remaining Unverified

- No live MongoDB integration test has been run for Sprint 2 or Sprint 3 concurrent updates.
- No 100,000-record performance test has been run.
- The MongoDB-unavailable health-check behavior remains unverified.
- Sprint 4 aggregations and execution plans have not been run against live MongoDB; index usage and 100,000-record performance remain unverified.

## Sprint 4: Employee Monthly Analytics

### Implemented Behavior

- Added `GET /analytics/employees/{emp_code}/monthly` with required `month=YYYY-MM` validation, HTTP 404 for an unknown employee, and the `EmployeeMonthly` response fields from `openapi.yaml`.
- Uses an indexed employee/date range `$match` and a MongoDB `$group` aggregation over stored attendance values. The aggregation counts presence only on weekdays, applies the stored half-day flag as 0.5, and returns leave, late, and overtime totals; missing derived values default to zero.
- Counts Monday-Friday working days inclusively from the later of the month's first day and the employee's `joined_on`. `attendance_pct` uses half-up rounding to two decimals and is `null` when working days are zero.

### Tests Actually Passed

- Tests use the existing in-memory fake database and verify route registration, response fields, the month-filtered aggregation query, weekday and weekend handling, half-day weighting, missing derived values, leave/late/overtime totals, mid-month join dates, zero-working-day behavior, invalid/missing months, and the unknown-employee 404 response.
- `python -m unittest discover -s tests -p "test_attendance_sprint4.py" -v`: 40 tests passed, 0 failed; this includes the inherited Sprint 2 tests.
- `python -m unittest discover -s tests -v`: 110 tests passed, 0 failed.
- `python -m py_compile app\main.py tests\test_attendance_sprint2.py tests\test_attendance_sprint3.py tests\test_attendance_sprint4.py` and `git diff --check` completed successfully.
- The in-memory fake validates the endpoint's aggregation behavior; this does not establish execution of the pipeline against a live MongoDB server or measured index use at scale.

## Sprint 4: Department Summary

### Implemented Behavior

- Added `GET /analytics/departments/summary` with required `month=YYYY-MM`, optional exact-match `department`, and the documented `{month, items}` response shape.
- Uses an `employees` aggregation with a month-bounded `$lookup` into `attendance_logs`, then groups employees before departments. The employee `$match` uses `joined_on <= month_end`; `$unwind` preserves employees with no logs, and future joiners are excluded.
- Aggregates weekday presence with half-day weighting; averages work hours over present-status records that have stored work hours (across records, not per-employee averages); and computes late, leave, and on-duty counts as documented. The average uses half-up rounding to two decimals. Items are sorted by department.

### Tests Actually Passed

- In-memory tests cover normal results and response shape, exact case-sensitive department filtering, no-log employees and null averages, future join-date exclusion, invalid/missing month input, and the constructed `$lookup` date range.
- The normal case distinguishes record-based averaging from per-employee averaging and confirms weekend records contribute to late totals but not weekday present days.
- A dedicated case verifies the average work-hours half-up rounding boundary.
- `python -m unittest discover -s tests -p "test_attendance_sprint4.py" -v`: 64 tests passed, 0 failed.
- `python -m unittest discover -s tests -v`: 134 tests passed, 0 failed.
- `python -m py_compile app\main.py tests\test_attendance_sprint2.py tests\test_attendance_sprint4.py` and `git diff --check` completed successfully.
- The tests run against the in-memory fake; execution against live MongoDB and index behavior at scale remain unverified.

## Sprint 4: Late Leaderboard, Department Trend, and Admin Explain

### Implemented Behavior

- Added `GET /analytics/leaderboard/late`. It validates `month=YYYY-MM`, applies the optional exact-match department filter and `limit` from 1 to 50 (default 10), aggregates monthly late totals/counts in MongoDB, joins employee metadata, and uses `$rank` competition ranking. The cutoff is applied after ranking, preserving every employee tied at an included rank.
- Added `GET /analytics/departments/{department}/trend`. It validates ISO calendar dates, rejects reversed or over-92-day inclusive ranges with 422, returns 404 when no employee belongs to the department, and builds a continuous daily series in the MongoDB aggregation. Headcount uses `joined_on`; non-working days and zero-headcount rates are `null`; working days without records have rate `0.0`. The moving average uses the current row plus up to six preceding rows in the requested range and averages non-null rates, including partial windows at the range start.
- Added `GET /admin/explain/{endpoint}` for the five endpoint identifiers in `openapi.yaml`. It validates required parameters and runs the matching MongoDB `find` or aggregation command with `executionStats`, preserving endpoint filters, sorting, pagination, and projection. Employee monthly, department summary, leaderboard, and trend use the same aggregation builders as their production routes. The response is `{endpoint, collection, explain}` with MongoDB BSON values serialized as JSON.
- Added an idempotent `joined_on` employee index to support the department-summary join-date match in the explained aggregation.
- Extended the in-memory fake aggregation and command support for isolated tests; tests do not connect to or modify development MongoDB data.

### Tests Actually Passed

- In-memory tests cover competition ranks, multi-record monthly totals, tied cutoff inclusion, exact department filtering, month/limit validation, daily gap filling, partial moving-average windows, zero-attendance and zero-headcount behavior, unknown departments, date ordering and inclusive 92-day limits, supported explain endpoints, execution-stats verbosity, required/invalid explain parameters, and matching full monthly/department-summary pipelines.
- `python -m unittest discover -s tests -p "test_attendance_sprint4.py" -v`: 134 tests passed, 0 failed.
- `python -m unittest discover -s tests -v`: 204 tests passed, 0 failed.
- `python -m py_compile app\main.py tests\test_attendance_sprint2.py tests\test_attendance_sprint3.py tests\test_attendance_sprint4.py` and `git diff --check` completed successfully.
- Live MongoDB execution and measured explain plans/index usage remain unverified; the unit tests assert the explain command and use an in-memory command stub.