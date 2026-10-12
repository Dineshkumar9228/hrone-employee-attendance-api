"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import calendar
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from decimal import Decimal, ROUND_HALF_UP
from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
)
db = client[os.getenv("MONGO_DB", "hrone_attendance")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")
def create_indexes():
    # Employee indexes
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_emp_code",
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="idx_employee_department_code",
    )

    db.employees.create_index(
        [("joined_on", 1)],
        name="idx_employee_joined_on",
    )

    # Attendance indexes
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="idx_attendance_date_code",
    )

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str | None = None,
    attendance_date: str | None = None,
) -> int:
    punch_in_utc = normalize_utc(punch_in)
    punch_in_ist = punch_in_utc.astimezone(IST)

    start_time = datetime.strptime(shift_start, "%H:%M").time()

    if attendance_date is None:
        attendance_day = punch_in_ist.date()

        if shift_end is not None:
            end_time = datetime.strptime(shift_end, "%H:%M").time()

            if end_time <= start_time and punch_in_ist.time() < end_time:
                attendance_day -= timedelta(days=1)
    else:
        attendance_day = date.fromisoformat(attendance_date)

    shift_start_ist = datetime.combine(
        attendance_day,
        start_time,
        tzinfo=IST,
    )

    elapsed_seconds = (
        punch_in_ist - shift_start_ist
    ).total_seconds()

    # Early arrivals and arrivals within the 10-minute grace period
    # are not late.
    if elapsed_seconds <= 600:
        return 0

    # Return whole elapsed minutes since shift start.
    return int(elapsed_seconds // 60)



def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    duration = punch_out - punch_in
    microseconds = (
        (duration.days * 86400 + duration.seconds) * 1_000_000
        + duration.microseconds
    )
    hours = Decimal(microseconds) / Decimal("3600000000")

    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return float(rounded_hours)


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    attendance_date = date.fromisoformat(date_str)
    start_time = datetime.strptime(shift_start, "%H:%M").time()
    end_time = datetime.strptime(shift_end, "%H:%M").time()
    shift_end_day = attendance_date
    if end_time <= start_time:
        shift_end_day += timedelta(days=1)

    shift_end_ist = datetime.combine(shift_end_day, end_time, tzinfo=IST)
    elapsed_seconds = (
        normalize_utc(punch_out) - shift_end_ist.astimezone(timezone.utc)
    ).total_seconds()
    if elapsed_seconds <= 0:
        return 0

    overtime_minutes = int(elapsed_seconds // 60)
    return overtime_minutes if overtime_minutes >= 30 else 0


def utc_now() -> datetime:
    return normalize_utc(datetime.now(timezone.utc))


def epoch_milliseconds_to_utc(value: int) -> datetime:
    return datetime.fromtimestamp(value // 1000, tz=timezone.utc)


def to_epoch_milliseconds(value: datetime) -> int:
    return int(normalize_utc(value).timestamp()) * 1000


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("name", "department")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")

        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1, max_length=20)
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"PRESENT", "WFH", "ON_DUTY"}:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return value

class PunchOutIn(BaseModel):
    emp_code: str = Field(
        min_length=1,
        max_length=20
    )
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )


class AttendanceCorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    punch_out: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value is not None and value not in {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def validate_supplied_punch_timestamp(cls, value):
        if value is None:
            raise ValueError("Punch timestamps must be epoch milliseconds")
        return value

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = int(doc["created_at"].timestamp() * 1000)

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = utc_now()
    else:
        ts = epoch_milliseconds_to_utc(body.punched_at)

    # Determine the local IST attendance date.
    ts_ist = ts.astimezone(IST)
    attendance_date = ts_ist.date()

    # Overnight shift: early-morning punches belong to the previous day.
    shift_start = datetime.strptime(emp["shift_start"], "%H:%M").time()
    shift_end = datetime.strptime(emp["shift_end"], "%H:%M").time()

    if shift_end <= shift_start and ts_ist.time() < shift_end:
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            emp["shift_start"],
            emp["shift_end"],
            date_str,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)

    # Return timestamps as epoch milliseconds.
    doc["punch_in"] = int(ts.timestamp() * 1000)

    return doc

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = utc_now()
    else:
        ts = epoch_milliseconds_to_utc(body.punched_at)

    ts = normalize_utc(ts)
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        already_closed = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": ts},
                "punch_out": {"$ne": None},
            },
        )

        if already_closed is not None:
            raise HTTPException(
                status_code=409,
                detail="Attendance record already punched out",
            )

        future_punch_in = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$gt": ts},
                "punch_out": None,
            },
            sort=[("punch_in", -1)],
        )
        if future_punch_in is not None:
            raise HTTPException(
                status_code=422,
                detail="Punch-out must be after punch-in and within 24 hours",
            )

        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in record found",
        )

    punch_in_time = normalize_utc(record["punch_in"])
    elapsed = (ts - punch_in_time).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    overtime = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )

    work_hours = compute_work_hours(punch_in_time, ts)
    half_day = work_hours < 4.50

    updated = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_in": {"$lte": ts},
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )
    if updated.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    # Return API timestamps as epoch milliseconds.
    record["punch_in"] = to_epoch_milliseconds(punch_in_time)
    record["punch_out"] = to_epoch_milliseconds(ts)
    record["work_hours"] = work_hours
    record["overtime_minutes"] = overtime
    record["half_day"] = half_day
    record.pop("_id", None)

    return record


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    for field_name, value in (
        ("date_from", date_from),
        ("date_to", date_to),
    ):
        if value is not None:
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field_name} must be a valid YYYY-MM-DD date",
                )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if status:
        query["status"] = status

    if date_from or date_to:
        date_filter = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        query["date"] = date_filter

    skip = (page - 1) * page_size

    total = db.attendance_logs.count_documents(query)

    cursor = (
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    items = list(cursor)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _employee_monthly_pipeline(
    emp_code: str,
    month_start: date,
    month_end: date,
) -> list[dict]:
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$date"
                                                    }
                                                }
                                            },
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": [{"$ifNull": ["$half_day", False]}, True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is None:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )

    year, month_number = (int(part) for part in month.split("-"))
    try:
        month_start = date(year, month_number, 1)
        month_end = date(
            year,
            month_number,
            calendar.monthrange(year, month_number)[1],
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    aggregation = _employee_monthly_pipeline(emp_code, month_start, month_end)
    totals = next(iter(db.attendance_logs.aggregate(aggregation)), {})

    joined_on = date.fromisoformat(employee["joined_on"])
    working_day = max(month_start, joined_on)
    working_days = 0
    while working_day <= month_end:
        if working_day.weekday() < 5:
            working_days += 1
        working_day += timedelta(days=1)

    present_days = totals.get("present_days", 0)
    attendance_pct = None
    if working_days:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal(100) / Decimal(working_days))
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


def _department_summary_pipeline(
    month_start: date,
    month_end: date,
    employee_match: dict,
) -> list[dict]:
    return [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {
                                        "$gte": [
                                            "$date",
                                            month_start.isoformat(),
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$date",
                                            month_end.isoformat(),
                                        ]
                                    },
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code",
                },
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$logs.date"
                                                    }
                                                }
                                            },
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            {
                                                "$ifNull": [
                                                    "$logs.half_day",
                                                    False,
                                                ]
                                            },
                                            True,
                                        ]
                                    },
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$logs.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$logs.late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$logs.late_minutes", 0]},
                                    0,
                                ]
                            },
                            "$logs.late_minutes",
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$work_hours_sum",
                                                            "$work_hours_count",
                                                        ]
                                                    },
                                                    100,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                100,
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is None:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )

    year, month_number = (int(part) for part in month.split("-"))
    try:
        month_start = date(year, month_number, 1)
        month_end = date(
            year,
            month_number,
            calendar.monthrange(year, month_number)[1],
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )

    employee_match = {"joined_on": {"$lte": month_end.isoformat()}}
    if department is not None:
        employee_match["department"] = department

    aggregation = _department_summary_pipeline(
        month_start,
        month_end,
        employee_match,
    )
    items = list(db.employees.aggregate(aggregation))
    return {"month": month, "items": items}


def _month_bounds(month: str) -> tuple[date, date]:
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is None:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )
    year, month_number = (int(part) for part in month.split("-"))
    try:
        return (
            date(year, month_number, 1),
            date(year, month_number, calendar.monthrange(year, month_number)[1]),
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be a valid YYYY-MM month",
        )


def _leaderboard_pipeline(
    month_start: date,
    month_end: date,
    limit: int,
    department: Optional[str],
) -> list[dict]:
    employee_match = {"$expr": {"$eq": ["$emp_code", "$$employee_code"]}}
    if department is not None:
        employee_match = {
            "$expr": {
                "$and": [
                    {"$eq": ["$emp_code", "$$employee_code"]},
                    {"$eq": ["$department", department]},
                ]
            }
        }

    return [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "let": {"employee_code": "$_id"},
                "pipeline": [{"$match": employee_match}],
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "employee.emp_code": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$employee.emp_code",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ]


def _department_trend_pipeline(
    department: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    day_count = (to_date - from_date).days + 1
    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "calendar": {
                    "$map": {
                        "input": {"$range": [0, day_count]},
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {
                                        "dateString": from_date.isoformat()
                                    }
                                },
                                "unit": "day",
                                "amount": "$$offset",
                            }
                        },
                    }
                },
            }
        },
        {"$unwind": "$calendar"},
        {
            "$project": {
                "_id": 0,
                "date": "$calendar",
                "date_string": {
                    "$dateToString": {
                        "date": "$calendar",
                        "format": "%Y-%m-%d",
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"report_date": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$report_date"]},
                                ]
                            }
                        }
                    },
                    {"$count": "headcount"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"report_date": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$date", "$$report_date"]}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"employee.department": department}},
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {
                                                    "$eq": [
                                                        {
                                                            "$ifNull": [
                                                                "$half_day",
                                                                False,
                                                            ]
                                                        },
                                                        True,
                                                    ]
                                                },
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$late_minutes", 0]},
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "daily_attendance",
            }
        },
        {
            "$addFields": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.headcount", 0]},
                        0,
                    ]
                },
                "present_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_attendance.present_count", 0]},
                        0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_attendance.late_count", 0]},
                        0,
                    ]
                },
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$addFields": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_raw", None]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_raw",
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    month_start, month_end = _month_bounds(month)
    pipeline = _leaderboard_pipeline(
        month_start,
        month_end,
        limit,
        department,
    )
    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
):
    parsed_dates = []
    for parameter, value in (("from", from_date), ("to", to_date)):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"{parameter} must be a valid YYYY-MM-DD date",
            )
        parsed_dates.append(parsed)

    range_start, range_end = parsed_dates
    if range_end < range_start:
        raise HTTPException(status_code=422, detail="to must be on or after from")
    if (range_end - range_start).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="date range must not exceed 92 days",
        )
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Department not found")

    pipeline = _department_trend_pipeline(
        department,
        range_start,
        range_end,
    )
    items = list(db.employees.aggregate(pipeline))
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
):
    collection = "attendance_logs"
    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if value is not None:
                try:
                    parsed = date.fromisoformat(value)
                    if parsed.isoformat() != value:
                        raise ValueError
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{name} must be a valid YYYY-MM-DD date",
                    )
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )
        if date_from or date_to:
            bounds = {}
            if date_from:
                bounds["$gte"] = date_from
            if date_to:
                bounds["$lte"] = date_to
            query["date"] = bounds
        command = {
            "explain": {
                "find": collection,
                "filter": query,
                "projection": {"_id": 0},
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        }
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required for employee_monthly",
            )
        month_start, month_end = _month_bounds(month)
        command = {
            "explain": {
                "aggregate": collection,
                "pipeline": _employee_monthly_pipeline(
                    emp_code,
                    month_start,
                    month_end,
                ),
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for department_summary",
            )
        month_start, month_end = _month_bounds(month)
        collection = "employees"
        employee_match = {"joined_on": {"$lte": month_end.isoformat()}}
        if department is not None:
            employee_match["department"] = department
        command = {
            "explain": {
                "aggregate": collection,
                "pipeline": _department_summary_pipeline(
                    month_start,
                    month_end,
                    employee_match,
                ),
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required for late_leaderboard",
            )
        month_start, month_end = _month_bounds(month)
        command = {
            "explain": {
                "aggregate": collection,
                "pipeline": _leaderboard_pipeline(
                    month_start,
                    month_end,
                    limit,
                    department,
                ),
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    else:
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from, and to are required for department_trend",
            )
        try:
            range_start = date.fromisoformat(from_date)
            range_end = date.fromisoformat(to_date)
            if (
                range_start.isoformat() != from_date
                or range_end.isoformat() != to_date
            ):
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must be valid YYYY-MM-DD dates",
            )
        if range_end < range_start:
            raise HTTPException(
                status_code=422,
                detail="to must be on or after from",
            )
        if (range_end - range_start).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="date range must not exceed 92 days",
            )
        collection = "employees"
        command = {
            "explain": {
                "aggregate": collection,
                "pipeline": _department_trend_pipeline(
                    department,
                    range_start,
                    range_end,
                ),
                "cursor": {},
            },
            "verbosity": "executionStats",
        }

    explain = db.command(command)
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": json.loads(json_util.dumps(explain)),
    }


@app.patch("/attendance/{emp_code}/{attendance_date}")
def correct_attendance(
    emp_code: str,
    attendance_date: str,
    body: AttendanceCorrectionIn,
):
    # Validate the attendance date.
    try:
        parsed_date = date.fromisoformat(attendance_date)
        if parsed_date.isoformat() != attendance_date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="attendance_date must be a valid YYYY-MM-DD date",
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": attendance_date}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set
    old_status = record.get("status")
    new_status = body.status if "status" in supplied else old_status

    if new_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch timestamps",
            )
        new_punch_in = None
        new_punch_out = None
    else:
        if "punch_in" in supplied:
            new_punch_in = (
                epoch_milliseconds_to_utc(body.punch_in)
                if body.punch_in is not None
                else None
            )
        else:
            new_punch_in = record.get("punch_in")

        if "punch_out" in supplied:
            new_punch_out = (
                epoch_milliseconds_to_utc(body.punch_out)
                if body.punch_out is not None
                else None
            )
        else:
            new_punch_out = record.get("punch_out")

        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence statuses require punch_in",
            )

        punch_in_ist = new_punch_in.astimezone(IST)
        punch_in_date = punch_in_ist.date()
        shift_start = datetime.strptime(
            employee["shift_start"], "%H:%M"
        ).time()
        shift_end = datetime.strptime(employee["shift_end"], "%H:%M").time()
        if shift_end <= shift_start and punch_in_ist.time() < shift_end:
            punch_in_date -= timedelta(days=1)
        if punch_in_date.isoformat() != attendance_date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date in IST",
            )

        if new_punch_out is not None:
            duration = (
                normalize_utc(new_punch_out) - normalize_utc(new_punch_in)
            ).total_seconds()
            if duration <= 0 or duration > 86400:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in and within 24 hours",
                )

    new_values = {"status": new_status}

    if new_status in {"ABSENT", "LEAVE"}:
        new_values.update({
            "punch_in": None,
            "punch_out": None,
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        })
    else:
        new_values["punch_in"] = normalize_utc(new_punch_in)
        new_values["punch_out"] = (
            normalize_utc(new_punch_out)
            if new_punch_out is not None
            else None
        )
        new_values["late_minutes"] = compute_late_minutes(
            new_values["punch_in"],
            employee["shift_start"],
            employee["shift_end"],
            attendance_date,
        )

        if new_values["punch_out"] is not None:
            new_values["work_hours"] = compute_work_hours(
                new_values["punch_in"],
                new_values["punch_out"],
            )
            new_values["overtime_minutes"] = compute_overtime(
                new_values["punch_out"],
                employee["shift_start"],
                employee["shift_end"],
                attendance_date,
            )
            new_values["half_day"] = new_values["work_hours"] < 4.50
        else:
            new_values["work_hours"] = None
            new_values["overtime_minutes"] = 0
            new_values["half_day"] = False

    changed = {}
    for field, new_value in new_values.items():
        old_value = record.get(field)
        if field in {"punch_in", "punch_out"} and old_value is not None:
            old_value = normalize_utc(old_value)
        if old_value != new_value:
            changed[field] = {
                "from": (
                    to_epoch_milliseconds(old_value)
                    if field in {"punch_in", "punch_out"} and old_value is not None
                    else old_value
                ),
                "to": (
                    to_epoch_milliseconds(new_value)
                    if field in {"punch_in", "punch_out"} and new_value is not None
                    else new_value
                ),
            }

    if not changed:
        raise HTTPException(status_code=422, detail="No changes supplied")

    history_entry = {
        "at": utc_now(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changed,
    }

    # Compare-and-set protects against overwriting a concurrent correction.
    condition = {
        "_id": record["_id"],
        "status": old_status,
    }
    if "history" in record:
        condition["history"] = record["history"]
    else:
        condition["history"] = {"$exists": False}
    for field in ("punch_in", "punch_out", "late_minutes", "work_hours",
                  "overtime_minutes", "half_day"):
        condition[field] = record.get(field)

    result = db.attendance_logs.update_one(
        condition,
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
    )

    if result.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction",
        )

    updated_record = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        if updated_record.get(field) is not None:
            updated_record[field] = to_epoch_milliseconds(
                updated_record[field]
            )

    for entry in updated_record.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = to_epoch_milliseconds(entry["at"])

    return updated_record


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
