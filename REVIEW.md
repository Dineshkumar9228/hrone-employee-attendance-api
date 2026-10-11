# REVIEW.md

## Defect Review — Starter `app/main.py`

List defects identified in the starter implementation, how they were detected, and how they were fixed.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Attendance late-minute calculation | An incorrect shift-date adjustment could produce an incorrect late-minute calculation for overnight attendance. | Compare the calculated attendance date and late minutes against a controlled overnight punch-in timestamp. | Removed the redundant shift-date adjustment and covered early-morning overnight attendance-date handling in tests. |
| 2 | Attendance work-hour calculation | Ordinary floating-point rounding can produce results inconsistent with the required decimal half-up rounding. | Test durations at half-cent rounding boundaries and compare the rounded work hours with the expected result. | Used decimal arithmetic with `ROUND_HALF_UP`; verified the behavior with Sprint 2 tests. |
| 3 | Attendance half-day calculation | Determining half-day status before rounding can disagree with the required threshold based on rounded work hours. | Test a duration whose rounded work hours fall on the 4.50-hour threshold. | Calculate half-day status from the rounded work-hours value and test the threshold. |
| 4 | Attendance punch-out | Updating a record without an atomic eligibility check can allow concurrent requests to close the same open attendance record inconsistently. | Submit concurrent punch-out requests and verify that only one succeeds. | Use an atomic conditional update; a request that loses the race returns HTTP 409. The threaded test uses an in-memory mock, not a live MongoDB integration test. |

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
- Added request validation for permitted attendance statuses and punch timestamps.
- Correction logic recalculates derived attendance fields when applicable.
- Attendance records initialize an audit-history list, and corrections append history entries containing the correction timestamp and changed values.
- The endpoint includes handling for missing employees and attendance records, and uses a conditional database update to protect against conflicting concurrent corrections.

### Tests Actually Passed

- `python -m unittest discover -s tests -p "test_attendance_sprint3.py" -v`: 39 tests passed, 0 failed.
- `python -m unittest discover -s tests -v`: 56 tests passed, 0 failed in the latest reported full regression run.
- Sprint 3 tests cover route registration, missing employee and attendance responses, invalid status validation, and a correction that recalculates overtime and creates an audit-history entry.
- Sprint 3 tests inherit the Sprint 2 test class, so the targeted Sprint 3 test run also executes the inherited Sprint 2 tests.
- Tests use an in-memory fake database. These results do not establish live MongoDB integration or concurrent correction behavior against a real MongoDB server.

### Remaining Sprint 3 Verification

- Add tests for omitted fields remaining unchanged and no-op corrections.
- Verify `ABSENT` and `LEAVE` corrections clear punch values as required.
- Verify punch-in date restrictions, punch-out ordering, and the 24-hour limit.
- Verify that each correction records only the fields that actually changed.
- Test conflicting concurrent corrections and ensure audit history is not lost.
- Confirm error responses and audit-history output match the assignment contract.

## Remaining Work

- Complete Sprint 3 edge-case and audit-history verification.
- Implement and verify the required analytics endpoints using MongoDB aggregation pipelines.
- Implement and verify the admin explain endpoint and confirm index usage for its supported query patterns.
- Revisit the MongoDB-unavailable health-check behavior and test the expected HTTP 503 response.
- Verify index usage for all required query patterns and evaluate performance against the assignment's large dataset.
- Run final regression tests and distinguish implemented fixes from fixes verified through tests.

## Tests Not Yet Executed

- No live MongoDB integration test has been reported for the Sprint 2 or Sprint 3 concurrent-update paths.
- No 100,000-record performance test has been reported.
- The MongoDB-unavailable health-check behavior remains unverified.
- The full Sprint 3 correction edge-case suite remains incomplete.
- The latest reported full regression run passed 56 tests with 0 failures. This does not establish that all assignment requirements have been implemented or verified.