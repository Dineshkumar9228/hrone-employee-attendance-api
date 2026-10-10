# REVIEW.md

This review covers defects found in the original `app/main.py` starter code, how they can be reproduced, and the fixes implemented or still required.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health()` | The original endpoint returned 200 without checking MongoDB connectivity. | Stop MongoDB and request `GET /health`; the original implementation still returns 200. | Added a MongoDB `ping` and return 503 if the ping fails. |
| 2 | `create_indexes()` / database setup | The required unique and query indexes were not created at startup. | Inspect collection indexes in MongoDB Compass; only `_id_` exists initially. | Added startup index creation for employee codes, department queries, attendance natural keys, and attendance sorting. |
| 3 | `EmployeeIn` | Employee fields lacked the contract's format and length validation. | Submit an invalid employee code, malformed email, invalid shift time, or invalid date. | Added Pydantic field constraints and validators. |
| 4 | `EmployeeIn` | Identical shift start and end times were accepted. | Submit an employee with both shift times set to `09:30`. | Added a model validator requiring the times to differ. |
| 5 | `create_employee()` | Checking for duplicates before inserting creates a race condition; simultaneous requests can both pass the check. | Send concurrent requests with the same `emp_code`. | Enforced uniqueness with a MongoDB unique index and handled `DuplicateKeyError` as HTTP 409. |
| 6 | `create_employee()` | `created_at` originally used a naive local datetime and was returned as a datetime instead of epoch milliseconds. | Inspect the stored timestamp and compare the response with the API contract. | Store a UTC-aware datetime and return epoch milliseconds. |
| 7 | `compute_late_minutes()` | The calculation assumes the punch-in and shift start use the same local date and timezone; it does not correctly resolve overnight shifts. | Test a late punch-in around midnight or compare UTC and IST timestamps. | **Pending:** use the resolved IST shift start, including overnight-shift rules, and truncate instants to whole seconds. |
| 8 | `compute_work_hours()` | Python's built-in `round()` uses ties-to-even rounding rather than the required half-up rounding. | Test a duration whose hour value falls exactly on a half-cent rounding boundary. | **Pending:** use decimal half-up rounding to two decimal places. |
| 9 | `compute_overtime()` | Overtime is counted even when it is less than 30 minutes; overnight shift ends are also handled incorrectly. | Test 29 minutes and 30 minutes after shift end, including an overnight shift. | **Pending:** resolve the correct shift-end instant and count overtime only when it reaches 30 minutes. |
| 10 | `punch_in()` | An unknown employee is not handled before accessing the employee document, which can produce a server error. | Punch in using an unknown `emp_code`. | **Pending:** return 404 when the employee does not exist. |
| 11 | `punch_in()` | The original timestamp conversion uses local system time instead of explicitly resolving epoch milliseconds to UTC and IST. It also uses truthiness to decide whether a timestamp was supplied. | Test timestamps near midnight, a zero timestamp, and an instant that crosses an IST calendar boundary. | **Pending:** validate supplied timestamps, truncate to whole seconds, convert explicitly to UTC, and derive the attendance date in IST. |
| 12 | `punch_in()` | The original duplicate check followed by insert is not race-safe, and a duplicate-key exception was not handled. | Send simultaneous punch-in requests for the same employee and attendance date. | **Pending:** rely on the unique (`emp_code`, `date`) index and translate duplicate-key failures to HTTP 409. |
| 13 | `PunchInIn` / `punch_in()` | The original request model did not restrict status to `PRESENT`, `WFH`, or `ON_DUTY`. | Submit `ABSENT` or an arbitrary status to the punch-in endpoint. | **Pending:** validate the allowed presence statuses and reject invalid input with 422. |
| 14 | `list_attendance()` | The original implementation loaded all matching records into Python memory before sorting and pagination. | Exercise the endpoint with a large attendance collection. | Moved filtering, sorting, skipping, and limiting into the MongoDB query. |
| 15 | `list_attendance()` | The original sort used date descending only; it did not use `emp_code` ascending as the tie-breaker. | Create multiple records with the same date and inspect their order. | Sort by date descending, then employee code ascending. |
| 16 | `list_attendance()` | The original endpoint did not validate pagination bounds or reject an invalid date range. | Try `page=0`, `page_size=101`, or `date_from` later than `date_to`. | Added pagination and date-range validation returning 422. |

## Reviewed and retained

- `GET /employees` already filtered by exact department, sorted by `emp_code` ascending, counted filtered records, and applied pagination in MongoDB. Its pagination bounds were also validated.
- The default shift values of `09:30` and `18:30` match the API contract.
- The unique attendance natural key is appropriate because the contract allows at most one record per employee per attendance date.

## Remaining review work

The pending items above must be implemented and tested before this review can be considered complete. Further defects may be found while implementing punch-out, regularization, and analytics endpoints.
