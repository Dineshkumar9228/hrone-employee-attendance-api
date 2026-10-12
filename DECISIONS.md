# DECISIONS.md

### 1. Indexes
I created a unique index on employee code to prevent duplicate employees, and a compound index on department and employee code to support filtered employee listings. The `joined_on` index helps with headcount queries. For attendance, a unique compound index on employee code and date prevents duplicate daily records. A date-descending and employee-code index supports attendance queries sorted by date. I rejected an additional index on `late_minutes` because the leaderboard aggregates late minutes across records, so that index alone would not solve the main aggregation cost.

### 2. Punch-in race
When identical punch-in requests arrive together, both may attempt to insert the same employee's attendance record. The unique compound index allows only one insert. The successful request returns 201; the duplicate-key error must be handled as a 409 conflict.

### 3. Ties
Employees tied on late minutes share a competition rank. If a tie crosses the requested limit, all employees tied at that cutoff are included, ordered by employee code.

### 4. Headcount
The department summary must include employees who joined by the period's end even when they have no attendance logs. It therefore derives eligible headcount from employees, not just attendance records.

### 5. Scaling
For 100 times more data, I would benchmark aggregation pipelines, inspect execution plans, optimise indexes, and consider archiving older records.