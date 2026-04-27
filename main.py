from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
from typing import Optional

app = FastAPI(title="Barbershop Appointment API (Google Calendar Edition)")

# ── Google Calendar setup ────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    """Connect to Google Calendar and return the service object."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="GOOGLE_CREDENTIALS not set")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    
    # Construimos el servicio de Google Calendar
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    cal_id = os.environ.get("CALENDAR_ID")
    if not cal_id:
        raise HTTPException(status_code=500, detail="CALENDAR_ID not set")
    return cal_id

# ── Request models ───────────────────────────────────────────────────────────

class CheckAvailabilityRequest(BaseModel):
    date: str        # YYYY-MM-DD
    time: str        # HH:MM
    service: Optional[str] = None

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
    service: Optional[str] = None

class CancelAppointmentRequest(BaseModel):
    name: str
    date: str        # YYYY-MM-DD

# ── Helper functions ─────────────────────────────────────────────────────────

def get_time_bounds(date_str: str, time_str: str):
    """Calcula el inicio y fin (30 min) de la cita en formato RFC3339 (el que usa Google)."""
    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=30)
    # Se añade la 'Z' para indicar formato de tiempo UTC estándar
    return start_dt.isoformat() + "Z", end_dt.isoformat() + "Z"

def count_events_in_slot(service, calendar_id: str, start_time: str, end_time: str):
    """Cuenta cuántos eventos (citas) hay en una franja horaria específica."""
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_time,
        timeMax=end_time,
        singleEvents=True
    ).execute()
    return len(events_result.get('items', []))

def find_event_by_name(service, calendar_id: str, date_str: str, name: str):
    """Busca en el día específico un evento que contenga el nombre del cliente."""
    start_of_day = f"{date_str}T00:00:00Z"
    end_of_day = f"{date_str}T23:59:59Z"
    
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_of_day,
        timeMax=end_of_day,
        singleEvents=True,
        q=name  # Esto busca el nombre en el título o descripción del evento
    ).execute()
    
    items = events_result.get('items', [])
    if items:
        return items[0]  # Devuelve la primera coincidencia
    return None

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Barbershop API (Google Calendar) is running!"}


@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    service = get_calendar_service()
    cal_id = get_calendar_id()
    
    event_count = count_events_in_slot(service, cal_id, start_time, end_time)

    # 💈 REGLA DE LOS 2 BARBEROS: Si ya hay 2 o más citas, está lleno
    if event_count >= 2:
        return {
            "available": False,
            "message": f"The slot on {req.date} at {req.time} is fully booked (2 barbers occupied)."
        }

    return {
        "available": True,
        "message": f"The slot on {req.date} at {req.time} is available."
    }


@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    service = get_calendar_service()
    cal_id = get_calendar_id()

    # Doble verificación por si se ocupó mientras hablaban
    if count_events_in_slot(service, cal_id, start_time, end_time) >= 2:
        return {
            "success": False,
            "message": f"Slot on {req.date} at {req.time} was just taken. Please check availability again."
        }

    event_body = {
        'summary': f"{req.name} - {req.service}",
        'description': 'Agendado por Luna (ElevenLabs)',
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
    }

    service.events().insert(calendarId=cal_id, body=event_body).execute()

    return {
        "success": True,
        "message": f"Appointment confirmed for {req.name} — {req.service} on {req.date} at {req.time}."
    }


@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    service = get_calendar_service()
    cal_id = get_calendar_id()

    # 1. Buscar la cita actual
    event = find_event_by_name(service, cal_id, req.current_date, req.name)
    if not event:
        return {
            "success": False,
            "message": f"No confirmed appointment found for {req.name} on {req.current_date}."
        }

    # 2. Comprobar disponibilidad del nuevo turno
    try:
        new_start, new_end = get_time_bounds(req.new_date, req.new_time)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date or time format")

    if count_events_in_slot(service, cal_id, new_start, new_end) >= 2:
        return {
            "success": False,
            "message": f"The new slot on {req.new_date} at {req.new_time} is not available."
        }

    # 3. Actualizar la cita en Calendar
    event['start']['dateTime'] = new_start
    event['end']['dateTime'] = new_end
    if req.service:
        event['summary'] = f"{req.name} - {req.service}"

    service.events().update(calendarId=cal_id, eventId=event['id'], body=event).execute()

    return {
        "success": True,
        "message": f"Appointment for {req.name} updated to {req.new_date} at {req.new_time}."
    }


@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    service = get_calendar_service()
    cal_id = get_calendar_id()

    event = find_event_by_name(service, cal_id, req.date, req.name)
    if not event:
        return {
            "success": False,
            "message": f"No confirmed appointment found for {req.name} on {req.date}."
        }

    # Eliminar el evento del calendario
    service.events().delete(calendarId=cal_id, eventId=event['id']).execute()

    return {
        "success": True,
        "message": f"Appointment for {req.name} on {req.date} has been cancelled."
    }
