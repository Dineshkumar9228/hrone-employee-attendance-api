# Benchmark Evidence

This report consolidates measurements recorded in `REVIEW.md`; benchmarks were
not rerun during finalization. Results below are historical evidence, not a
claim that the final combined pipelines have been benchmarked.

## Recorded dataset

- MongoDB 8.2.1
- Database: `hrone_attendance_perf_20261013`
- 5,000 employees and 100,000 attendance records

## Recorded measurements

| Endpoint / probe | Parameters recorded | Recorded timings | Result |
|---|---|---|---|
| Department summary A/B pipeline | Department summary endpoint; exact month and filters were not recorded | Original: 6.539 s, 7.118 s; direct equality/date-range candidate: 5.402 s, 4.977 s | Results were identical; candidate was faster in both paired runs. Preliminary only. |
| Department trend pipeline comparison | `department=Engineering`, `from=2026-01-01`, `to=2026-01-20` | Previous: 29.644 s; revised: 0.979 s, 1.027 s, 0.975 s | Identical 20-row output. Repeat before treating timings as final. |
| Decimal summary probes | Synthetic average `1.00` and `1.01`; full aggregation parameters were not recorded | No timings recorded | Decimal expression returned `1.01`; full aggregation returned five departments and `9.0` average work hours. Final combined summary pipeline was not benchmarked. |

## Query-plan evidence

Recorded `executionStats` explain checks covered `attendance_list`,
`employee_monthly`, `department_summary`, `late_leaderboard`, and
`department_trend`. All reported zero collection scans on the dataset. Recorded
indexes used, as applicable, were `idx_attendance_date_code`,
`uq_attendance_employee_date`, `idx_employee_joined_on`,
`uq_employee_emp_code`, and `idx_employee_department_code`.

The exact parameter values for each explain call and per-endpoint execution
timings were not retained. Raw explain documents were not available for this
report. No plan or timing is inferred for unrecorded parameters.

## Verification limits

The A/B timings and explain observations were not reproduced for this
finalization. They do not establish final end-to-end performance or live
MongoDB behavior after the Decimal128 summary change. No benchmark or explain
query was run as part of this task.
