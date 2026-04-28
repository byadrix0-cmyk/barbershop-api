from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
from typing import Optional

app = FastAPI(title="Peluqueria Kevin API - V2 (Con Barberos)")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="Credenciales no encontradas")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    return os.environ.get("CALENDAR_ID")

def get_time_bounds(date_str: str, time_str: str):
    tz_canarias = ZoneInfo("Atlantic/Canary")
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    start_dt = dt.replace(tzinfo=tz_canarias)
    end_dt = start_dt + timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()

# ── NUEVOS MODELOS (Añadido el campo barber) ──
class CheckAvailabilityRequest(BaseModel):
    date: str
    time: str
    service: Optional[str] = None
    barber: Optional[str] = None  # Nuevo

class BookAppointmentRequest(BaseModel):
    name: str
    service: str
    date: str
    time: str
    barber: Optional[str] = "Sin preferencia"  # Nuevo

class ModifyAppointmentRequest(BaseModel):
    name: str
    current_date: str
    new_date: str
    new_time: str
    service: Optional[str] = None
    barber: Optional[str] = None

class CancelAppointmentRequest(BaseModel):
    name: str
    date: str

@app.get("/")
def root():
    return {"status": "Running V2", "timezone": "Atlantic/Canary"}

@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        
        events_result = service.events().list(
            calendarId=cal_id, timeMin=start_time, timeMax=end_time, singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        
        # Regla 1: Si hay 2 eventos, la peluquería entera está llena
        if len(events) >= 2:
            return {"available": False, "message": "Ambos peluqueros están ocupados en ese horario."}
            
        # Regla 2: Si el cliente pidió un barbero específico, miramos si ÉL está ocupado
        if req.barber and req.barber.lower() != "sin preferencia":
            for event in events:
                # Buscamos el nombre del barbero en el título del evento
                if req.barber.lower() in event.get('summary', '').lower():
                    return {"available": False, "message": f"{req.barber} ya tiene una cita a esa hora. Pero el otro peluquero está libre."}

        return {"available": True, "message": "Disponible."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        
        # Guardamos el nombre del barbero en el título para que la API pueda leerlo en el futuro
        titulo_evento = f"{req.name} - {req.service}"
        if req.barber and req.barber.lower() != "sin preferencia":
            titulo_evento += f" (con {req.barber})"
            
        event = {
            'summary': titulo_evento,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
            'description': 'Reserva vía Marta Voice AI'
        }
        service.events().insert(calendarId=cal_id, body=event).execute()
        return {"success": True, "message": "Cita agendada correctamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def find_event_by_name(service, cal_id, date_str, name):
    tz = ZoneInfo("Atlantic/Canary")
    start = datetime.strptime(f"{date_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=tz).isoformat()
    end = datetime.strptime(f"{date_str} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=tz).isoformat()
    events = service.events().list(calendarId=cal_id, timeMin=start, timeMax=end, singleEvents=True, q=name).execute()
    items = events.get('items', [])
    return items[0] if items else None

@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    service = get_calendar_service()
    cal_id = get_calendar_id()
    event = find_event_by_name(service, cal_id, req.current_date, req.name)
    if not event: return {"success": False, "message": "No se encontró la cita."}

    new_start, new_end = get_time_bounds(req.new_date, req.new_time)
    event['start']['dateTime'] = new_start
    event['end']['dateTime'] = new_end
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
