"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

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
    punch_in_ist = punch_in.astimezone(IST)

    if attendance_date is None:
        attendance_day = punch_in_ist.date()
    else:
        attendance_day = date.fromisoformat(attendance_date)

    start_time = datetime.strptime(shift_start, "%H:%M").time()
    shift_start_ist = datetime.combine(
        attendance_day, start_time, tzinfo=IST
    )

    if shift_end is not None:
        end_time = datetime.strptime(shift_end, "%H:%M").time()
        if end_time <= start_time and punch_in_ist.time() < end_time:
            shift_start_ist -= timedelta(days=1)

    elapsed_minutes = int(
        (punch_in_ist - shift_start_ist).total_seconds() // 60
    )

    return elapsed_minutes if elapsed_minutes > 10 else 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )
    hours = seconds / Decimal("3600")

    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return float(rounded_hours)


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
) -> int:
    hours, minutes = map(int, shift_end.split(":"))

    attendance_date = date.fromisoformat(date_str)
    shift_end_ist = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        hours,
        minutes,
        tzinfo=IST,
    )

    punch_out_ist = punch_out.astimezone(IST)

    # If the shift ends at or before its start time, it is overnight.
    # Resolve the shift end on the following calendar day when needed.
    if punch_out_ist < shift_end_ist:
        shift_end_ist -= timedelta(days=1)

    overtime = int(
        (punch_out_ist - shift_end_ist).total_seconds() // 60
    )

    return overtime if overtime >= 30 else 0


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
    emp_code: str
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
    punch_in: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    punch_out: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value not in {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}:
            raise ValueError("Invalid attendance status")
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
        ts = datetime.now(timezone.utc)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        )

    # Truncate the timestamp to whole seconds.
    ts = ts.replace(microsecond=0)

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
        ts = datetime.now(timezone.utc)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        )

    # Store timestamps at whole-second precision.
    ts = ts.replace(microsecond=0)

    # Atomically close the most recent eligible open record.
    record = db.attendance_logs.find_one_and_update(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
            "punch_out": None,
        },
        {"$set": {"punch_out": ts}},
        sort=[("punch_in", -1)],
        return_document=ReturnDocument.BEFORE,
    )

    if record is None:
        existing = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": ts},
            },
            sort=[("punch_in", -1)],
        )

        if existing is not None and existing.get("punch_out") is not None:
            raise HTTPException(
                status_code=409,
                detail="Attendance record already punched out",
            )

        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in record found",
        )

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    # Validate duration after claiming the record.
    elapsed = (ts - punch_in_time).total_seconds()

    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        # Restore the open state if validation fails.
        db.attendance_logs.update_one(
            {"_id": record["_id"], "punch_out": ts},
            {"$set": {"punch_out": None}},
        )
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    # Determine the actual shift-end instant, including overnight shifts.
    attendance_date = date.fromisoformat(record["date"])
    shift_start = datetime.strptime(emp["shift_start"], "%H:%M").time()
    shift_end = datetime.strptime(emp["shift_end"], "%H:%M").time()

    shift_end_day = attendance_date
    if shift_end <= shift_start:
        shift_end_day += timedelta(days=1)

    shift_end_ist = datetime.combine(
        shift_end_day, shift_end, tzinfo=IST
    )
    shift_end_utc = shift_end_ist.astimezone(timezone.utc)

    overtime = int(
        max(0, (ts - shift_end_utc).total_seconds()) // 60
    )
    if overtime < 30:
        overtime = 0

    work_hours = compute_work_hours(punch_in_time, ts)
    half_day = work_hours < 4.50

    db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": ts},
        {
            "$set": {
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )

    # Return API timestamps as epoch milliseconds.
    record["punch_in"] = int(punch_in_time.timestamp() * 1000)
    record["punch_out"] = int(ts.timestamp() * 1000)
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


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

