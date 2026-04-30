from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
from typing import Optional

app = FastAPI(title="Peluqueria Kevin API - ElevenLabs Edition")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

TZ_CANARIAS = ZoneInfo("Atlantic/Canary")

DAYS_ES = {
    0: "lunes", 1: "martes", 2: "miércoles",
    3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
}

MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def next_weekday(dt: datetime, weekday: int) -> datetime:
    days_ahead = weekday - dt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return dt + timedelta(days=days_ahead)

@app.get("/")
def root():
    try:
        if not os.environ.get("GOOGLE_CREDENTIALS"):
            return {"status": "🔴 ERROR", "message": "Falta la variable GOOGLE_CREDENTIALS en Render"}
        if not os.environ.get("CALENDAR_ID"):
            return {"status": "🔴 ERROR", "message": "Falta la variable CALENDAR_ID en Render"}
        get_calendar_service()
        return {
            "status": "🟢 ONLINE", 
            "message": "La API está conectada a Google Calendar y lista para ElevenLabs 🚀",
            "timezone": "Atlantic/Canary"
        }
    except Exception as e:
        return {"status": "🔴 OFFLINE", "message": f"Error interno en la conexión: {str(e)}"}

@app.get("/current_datetime")
def current_datetime():
    """
    Devuelve la fecha y hora actual en Atlantic/Canary con fechas relativas
    pre-calculadas en español para inyectar en el prompt de ElevenLabs.
    """
    now = datetime.now(TZ_CANARIAS)
    tomorrow = now + timedelta(days=1)
    day_after = now + timedelta(days=2)
    next_monday = next_weekday(now, 0)
    next_tuesday = next_weekday(now, 1)
    next_wednesday = next_weekday(now, 2)
    next_thursday = next_weekday(now, 3)
    next_friday = next_weekday(now, 4)

    return {
        "today_date": now.strftime("%Y-%m-%d"),
        "today_day_of_week": DAYS_ES[now.weekday()],
        "today_readable": f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month]}",

        "tomorrow_date": tomorrow.strftime("%Y-%m-%d"),
        "tomorrow_day_of_week": DAYS_ES[tomorrow.weekday()],
        "tomorrow_readable": f"{DAYS_ES[tomorrow.weekday()]} {tomorrow.day} de {MONTHS_ES[tomorrow.month]}",

        "day_after_tomorrow_date": day_after.strftime("%Y-%m-%d"),
        "day_after_tomorrow_day_of_week": DAYS_ES[day_after.weekday()],
        "day_after_tomorrow_readable": f"{DAYS_ES[day_after.weekday()]} {day_after.day} de {MONTHS_ES[day_after.month]}",

        "next_monday_date": next_monday.strftime("%Y-%m-%d"),
        "next_tuesday_date": next_tuesday.strftime("%Y-%m-%d"),
        "next_wednesday_date": next_wednesday.strftime("%Y-%m-%d"),
        "next_thursday_date": next_thursday.strftime("%Y-%m-%d"),
        "next_friday_date": next_friday.strftime("%Y-%m-%d"),

        "current_time": now.strftime("%H:%M"),
        "timezone": "Atlantic/Canary"
    }

# --- FUNCIONES DE APOYO ---
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
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    start_dt = dt.replace(tzinfo=TZ_CANARIAS)
    end_dt = start_dt + timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()

TRANSLATIONS = {
    "corte": "Haircut",
    "barba": "Beard Trim",
    "tinte": "Hair Color",
    "tratamiento": "Hair Treatment"
}

# --- PLANTILLAS DE DATOS (ESPECÍFICAS PARA ELEVENLABS) ---
class CheckAvailabilityRequest(BaseModel):
    date: str
    time: str
    barber: Optional[str] = None

class BookAppointmentRequest(BaseModel):
    name: str
    service: str
    date: str
    time: str
    barber: Optional[str] = "Sin preferencia"

class ModifyAppointmentRequest(BaseModel):
    name: str
    current_date: str
    new_date: str
    new_time: str
    barber: Optional[str] = "Sin preferencia"

class CancelAppointmentRequest(BaseModel):
    name: str
    date: str

# --- ENDPOINTS ADAPTADOS PARA ELEVENLABS ---

@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id_google = get_calendar_id()
        
        # EL ERROR ESTABA AQUÍ: Ponía timeMax=end_search en lugar de end_time
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_time, timeMax=end_time, singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        is_available = True
        msg = "Disponible."

        if len(events) >= 2:
            is_available = False
            msg = "Ambos peluqueros están ocupados."
        elif req.barber and req.barber.lower() != "sin preferencia":
            for event in events:
                if req.barber.lower() in event.get('summary', '').lower():
                    other = "Dani" if req.barber.lower() == "kevin" else "Kevin"
                    is_available = False
                    msg = f"{req.barber.capitalize()} está ocupado, pero {other} está libre."

        return {"status": "success", "message": msg if is_available else f"No disponible: {msg}"}
    except Exception as e:
        print(f"Error en check_availability: {str(e)}") # Esto lo imprimirá en los logs de Render
        return {"status": "error", "message": str(e)}


@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        start_time, end_time = get_time_bounds(req.date, req.time)
        service = get_calendar_service()
        cal_id_google = get_calendar_id()
        
        servicio_ingles = TRANSLATIONS.get(req.service.lower(), req.service.capitalize())
        color_evento = "8"
        nombre_barbero = req.barber.capitalize()
        
        if "kevin" in req.barber.lower(): color_evento = "9"
        elif "dani" in req.barber.lower(): color_evento = "10"

        titulo_evento = f"✂️ {req.name} | {servicio_ingles} (con {nombre_barbero})"
        descripcion_evento = f"Cliente: {req.name}\nBarbero: {nombre_barbero}\nAgendado por ElevenLabs"

        event = {
            'summary': titulo_evento,
            'description': descripcion_evento,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
            'colorId': color_evento
        }
        
        service.events().insert(calendarId=cal_id_google, body=event).execute()

        return {"status": "success", "message": f"Cita confirmada para {req.name} con {nombre_barbero}."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    try:
        service = get_calendar_service()
        cal_id_google = get_calendar_id()

        start_search = datetime.strptime(f"{req.current_date} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{req.current_date} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_search, timeMax=end_search, singleEvents=True, q=req.name
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"status": "error", "message": f"No pude encontrar la cita de {req.name} el {req.current_date}."}
            
        event = events[0]
        new_start, new_end = get_time_bounds(req.new_date, req.new_time)
        
        event['start']['dateTime'] = new_start
        event['end']['dateTime'] = new_end
        
        if req.barber and req.barber.lower() != "sin preferencia":
            nombre_barbero = req.barber.capitalize()
            event['summary'] = f"✂️ {req.name} | Modificado ({nombre_barbero})"
            event['colorId'] = "9" if "kevin" in nombre_barbero.lower() else "10" if "dani" in nombre_barbero.lower() else "8"

        service.events().update(calendarId=cal_id_google, eventId=event['id'], body=event).execute()

        return {"status": "success", "message": "Cita modificada con éxito."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    try:
        service = get_calendar_service()
        cal_id_google = get_calendar_id()

        start_search = datetime.strptime(f"{req.date} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{req.date} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_search, timeMax=end_search, singleEvents=True, q=req.name
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"status": "error", "message": f"No encontré ninguna cita a nombre de {req.name} para cancelar."}
            
        service.events().delete(calendarId=cal_id_google, eventId=events[0]['id']).execute()

        return {"status": "success", "message": "La cita ha sido cancelada correctamente."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
