from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import os
import json

app = FastAPI(title="Barbershop Appointment API")

# ── Google Sheets setup ──────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_sheet():
    """Connect to Google Sheets and return the appointments worksheet."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="GOOGLE_CREDENTIALS not set")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        raise HTTPException(status_code=500, detail="SHEET_ID not set")

    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.sheet1


# ── Request models ───────────────────────────────────────────────────────────

class CheckAvailabilityRequest(BaseModel):
    date: str        # YYYY-MM-DD
    time: str        # HH:MM
    service: str

class BookAppointmentRequest(BaseModel):
    name: str
    service: str
    date: str        # YYYY-MM-DD
    time: str        # HH:MM

class ModifyAppointmentRequest(BaseModel):
    name: str
    current_date: str   # YYYY-MM-DD
    new_date: str       # YYYY-MM-DD
    new_time: str       # HH:MM
    service: str = None

class CancelAppointmentRequest(BaseModel):
    name: str
    date: str        # YYYY-MM-DD


# ── Helper functions ─────────────────────────────────────────────────────────

def find_appointment(sheet, name: str, date: str):
    """Find a row index matching name + date. Returns row index (1-based) or None."""
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):  # row 1 is header
        if (
            str(row.get("name", "")).lower() == name.lower()
            and str(row.get("date", "")) == date
            and str(row.get("status", "")).lower() == "confirmed"
        ):
            return i
    return None

def slot_is_taken(sheet, date: str, time: str):
    """Check if a date+time slot already has a confirmed appointment."""
    records = sheet.get_all_records()
    for row in records:
        if (
            str(row.get("date", "")) == date
            and str(row.get("time", "")) == time
            and str(row.get("status", "")).lower() == "confirmed"
        ):
            return True
    return False


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Barbershop API is running"}


@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    """
    Returns whether a given date/time slot is available.
    ElevenLabs calls this before booking or modifying any appointment.
    """
    try:
        # Validate date format
        datetime.strptime(req.date, "%Y-%m-%d")
        # Validate time format
        datetime.strptime(req.time, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    sheet = get_sheet()
    taken = slot_is_taken(sheet, req.date, req.time)

    if taken:
        return {
            "available": False,
            "message": f"The slot on {req.date} at {req.time} is not available."
        }

    return {
        "available": True,
        "message": f"The slot on {req.date} at {req.time} is available."
    }


@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    """
    Books a new appointment and writes it to Google Sheets.
    Only call after check_availability confirms the slot is free.
    """
    try:
        datetime.strptime(req.date, "%Y-%m-%d")
        datetime.strptime(req.time, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    sheet = get_sheet()

    # Double-check availability before booking
    if slot_is_taken(sheet, req.date, req.time):
        return {
            "success": False,
            "message": f"Slot on {req.date} at {req.time} was just taken. Please check availability again."
        }

    # Ensure header row exists
    if not sheet.row_values(1):
        sheet.append_row(["name", "service", "date", "time", "status", "created_at"])

    sheet.append_row([
        req.name,
        req.service,
        req.date,
        req.time,
        "confirmed",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ])

    return {
        "success": True,
        "message": f"Appointment confirmed for {req.name} — {req.service} on {req.date} at {req.time}."
    }


@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    """
    Modifies an existing appointment by updating date, time and optionally service.
    Only call after check_availability confirms the new slot is free.
    """
    try:
        datetime.strptime(req.current_date, "%Y-%m-%d")
        datetime.strptime(req.new_date, "%Y-%m-%d")
        datetime.strptime(req.new_time, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    sheet = get_sheet()

    # Find existing appointment
    row_index = find_appointment(sheet, req.name, req.current_date)
    if not row_index:
        return {
            "success": False,
            "message": f"No confirmed appointment found for {req.name} on {req.current_date}."
        }

    # Check new slot is free
    if slot_is_taken(sheet, req.new_date, req.new_time):
        return {
            "success": False,
            "message": f"The new slot on {req.new_date} at {req.new_time} is not available."
        }

    # Get headers to find column positions
    headers = sheet.row_values(1)
    date_col = headers.index("date") + 1
    time_col = headers.index("time") + 1

    sheet.update_cell(row_index, date_col, req.new_date)
    sheet.update_cell(row_index, time_col, req.new_time)

    if req.service:
        service_col = headers.index("service") + 1
        sheet.update_cell(row_index, service_col, req.service)

    return {
        "success": True,
        "message": f"Appointment for {req.name} updated to {req.new_date} at {req.new_time}."
    }


@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    """
    Cancels an existing appointment by marking it as cancelled in Google Sheets.
    """
    try:
        datetime.strptime(req.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    sheet = get_sheet()

    row_index = find_appointment(sheet, req.name, req.date)
    if not row_index:
        return {
            "success": False,
            "message": f"No confirmed appointment found for {req.name} on {req.date}."
        }

    headers = sheet.row_values(1)
    status_col = headers.index("status") + 1
    sheet.update_cell(row_index, status_col, "cancelled")

    return {
        "success": True,
        "message": f"Appointment for {req.name} on {req.date} has been cancelled."
    }
