# HROne Employee Attendance & Analytics API

A REST API built with **FastAPI** and **MongoDB** for employee management, attendance tracking, attendance corrections, and department-level analytics.

## Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance record listing and regularization with audit history
- Monthly employee attendance analytics
- Department attendance summaries and daily trends
- Late-arrival leaderboard with ranking and tie handling
- MongoDB aggregation pipelines for analytics
- Index-backed query execution and endpoint explain plans
- Automated regression tests for business rules and edge cases

## Technology Stack

- Python
- FastAPI
- MongoDB
- PyMongo
- Pytest

## Project Structure

- `app/main.py` — API implementation
- `tests/` — automated regression tests
- `requirements.txt` — application dependencies
- `requirements-dev.txt` — development and testing dependencies
- `openapi.yaml` — API contract and business rules
- `DATA_MODEL.md` — MongoDB data model
- `REVIEW.md` — code review findings
- `DECISIONS.md` — implementation and design decisions
- `sample_data/` — sample MongoDB documents
- `sample_seed.py` — sample database seeding script

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

## Setup

Create and activate a virtual environment in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Configure MongoDB using environment variables. For example, for a local MongoDB instance:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "hrone_attendance"
```

Ensure MongoDB is running before starting the application.

## Run the API

```powershell
python -m uvicorn app.main:app --port 8000
```

Check the health endpoint:

```text
http://127.0.0.1:8000/health
```

Interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Run Tests

Run the regression suite:

```powershell
python -m pytest -q
```

## Sample Data

`sample_seed.py` loads the provided sample dataset. Run it only when you intentionally want to initialize or reset the sample database, and verify the configured database before doing so.

## Submission

This repository contains the API implementation, review findings, design decisions, API contract, data model, and automated tests required for the assignment.