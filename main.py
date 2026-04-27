from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
from typing import Optional

app = FastAPI(title="Barbershop Appointment API (Canary Islands Edition)")

# ── Google Calendar setup ────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    """Conecta con Google Calendar usando la Service Account."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="GOOGLE_CREDENTIALS no configurada")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    cal_id = os.environ.get("CALENDAR_ID")
    if not cal_id:
        raise HTTPException(status_code=500, detail="CALENDAR_ID no configurada")
    return cal_id

# ── Modelos de Datos ─────────────────────────────────────────────────────────

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

# ── Funciones de Ayuda (Lógica Canaria) ──────────────────────────────────────

def get_time_bounds(date_str: str, time_str: str):
    """Calcula inicio y fin (30 min) con la zona horaria de Canarias."""
    tz_canarias = ZoneInfo("Atlantic/Canary")
    # Convertimos el texto a objeto fecha y le ponemos la zona horaria
    naive_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    start_dt = naive_dt.replace(tzinfo=tz_canarias)
    end_dt = start_dt + timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()

def count_events_in_slot(service, calendar_id: str, start_time: str, end_time: str):
    """Cuenta cuántos barberos están ocupados en ese hueco."""
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_time,
        timeMax=end_time,
        singleEvents=True
    ).execute()
    return len(events_result.get('items', []))

def find_event_by_name(service, calendar_id: str, date_str: str, name: str):
    """Busca una cita por nombre en un día específico."""
    tz_canarias = ZoneInfo("Atlantic/Canary")
    start_of_day = datetime.strptime(f"{date_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=tz_canarias).isoformat()
    end_of_day = datetime.strptime(f"{date_str} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=tz_canarias).isoformat()
    
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_of_day,
        timeMax=end_of_day,
        singleEvents=True,
        q=name
    ).execute()
    
    items = events_result.get('items', [])
    return items[0] if items else None

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Barbershop API (Google Calendar) is running!"}

@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        if count_events_in_slot(service, cal_id, start_time, end_time) >= 2:
            return {"available": False, "message": "Hueco lleno (2 barberos ocupados)."}
        return {"available": True, "message": "Hueco disponible."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        
        if count_events_in_slot(service, cal_id, start_time, end_time) >= 2:
            return {"success": False, "message": "El hueco se acaba de ocupar."}

        event = {
            'summary': f"{req.name} - {req.service}",
            'description': 'Agendado por Marta (ElevenLabs)',
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        service.events().insert(calendarId=cal_id, body=event).execute()
        return {"success": True, "message": "Cita confirmada."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    service = get_calendar_service()
    cal_id = get_calendar_id()
    event = find_event_by_name(service, cal_id, req.current_date, req.name)
    if not event:
        return {"success": False, "message": "No se encontró la cita."}

    new_start, new_end = get_time_bounds(req.new_date, req.new_time)
    if count_events_in_slot(service, cal_id, new_start, new_end) >= 2:
        return {"success": False, "message": "El nuevo horario está lleno."}

    event['start']['dateTime'] = new_start
    event['end']['dateTime'] = new_end
    if req.service: event['summary'] = f"{req.name} - {req.service}"
    service.events().update(calendarId=cal_id, eventId=event['id'], body=event).execute()
    return {"success": True}

@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    service = get_calendar_service()
    cal_id = get_calendar_id()
    event = find_event_by_name(service, cal_id, req.date, req.name)
    if not event: return {"success": False, "message": "Cita no encontrada."}
    service.events().delete(calendarId=cal_id, eventId=event['id']).execute()
    return {"success": True}
