from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
from typing import Optional

app = FastAPI(title="Peluqueria Kevin API - Vapi Edition")

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
            "message": "La API está conectada a Google Calendar y lista para Vapi 🚀",
            "timezone": "Atlantic/Canary"
        }
    except Exception as e:
        return {"status": "🔴 OFFLINE", "message": f"Error interno en la conexión: {str(e)}"}


@app.get("/current_datetime")
def current_datetime():
    """
    Devuelve la fecha y hora actual en Atlantic/Canary con fechas relativas
    pre-calculadas en español para inyectar en el prompt de Vapi.
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

async def get_vapi_payload(request: Request):
    data = await request.json()
    message = data.get("message", {})
    tool_calls = message.get("toolCalls", [])
    if not tool_calls:
        return None, None, None
    call_id = tool_calls[0].get("id")
    function_name = tool_calls[0].get("function", {}).get("name")
    arguments = tool_calls[0].get("function", {}).get("arguments", {})
    return call_id, function_name, arguments


# --- ENDPOINTS ---

@app.post("/check_availability")
async def check_availability(request: Request):
    call_id, _, args = await get_vapi_payload(request)
    if not call_id: return {"error": "Petición inválida"}
    
    date = args.get("date")
    time = args.get("time")
    barber = args.get("barber")

    try:
        start_time, end_time = get_time_bounds(date, time)
        service = get_calendar_service()
        cal_id_google = get_calendar_id()
        
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_time, timeMax=end_time, singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        is_available = True
        msg = "Disponible."

        if len(events) >= 2:
            is_available = False
            msg = "Ambos peluqueros están ocupados."
        elif barber and barber.lower() != "sin preferencia":
            for event in events:
                if barber.lower() in event.get('summary', '').lower():
                    other = "Dani" if barber.lower() == "kevin" else "Kevin"
                    is_available = False
                    msg = f"{barber.capitalize()} está ocupado, pero {other} está libre."

        return {
            "results": [{
                "toolCallId": call_id,
                "result": msg if is_available else f"No disponible: {msg}"
            }]
        }
    except Exception as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}


@app.post("/book_appointment")
async def book_appointment(request: Request):
    call_id, _, args = await get_vapi_payload(request)
    if not call_id: return {"error": "Petición inválida"}
    
    name = args.get("name")
    service_type = args.get("service")
    date = args.get("date")
    time = args.get("time")
    barber = args.get("barber", "Sin preferencia")

    try:
        start_time, end_time = get_time_bounds(date, time)
        service = get_calendar_service()
        cal_id_google = get_calendar_id()
        
        servicio_ingles = TRANSLATIONS.get(service_type.lower(), service_type.capitalize())
        color_evento = "8"
        nombre_barbero = barber.capitalize()
        
        if "kevin" in barber.lower(): color_evento = "9"
        elif "dani" in barber.lower(): color_evento = "10"

        titulo_evento = f"✂️ {name} | {servicio_ingles} (con {nombre_barbero})"
        descripcion_evento = f"Cliente: {name}\nBarbero: {nombre_barbero}\nAgendado por Marta/Vapi"

        event = {
            'summary': titulo_evento,
            'description': descripcion_evento,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
            'colorId': color_evento
        }
        
        service.events().insert(calendarId=cal_id_google, body=event).execute()

        return {
            "results": [{
                "toolCallId": call_id,
                "result": f"Cita confirmada para {name} con {nombre_barbero}."
            }]
        }
    except Exception as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}


@app.post("/modify_appointment")
async def modify_appointment(request: Request):
    call_id, _, args = await get_vapi_payload(request)
    if not call_id: return {"error": "Petición inválida"}
    
    name = args.get("name")
    current_date = args.get("current_date")
    new_date = args.get("new_date")
    new_time = args.get("new_time")
    barber = args.get("barber", "Sin preferencia")

    try:
        service = get_calendar_service()
        cal_id_google = get_calendar_id()

        start_search = datetime.strptime(f"{current_date} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{current_date} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_search, timeMax=end_search, singleEvents=True, q=name
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"results": [{"toolCallId": call_id, "result": f"No pude encontrar la cita de {name} el {current_date}."}]}
            
        event = events[0]
        new_start, new_end = get_time_bounds(new_date, new_time)
        
        event['start']['dateTime'] = new_start
        event['end']['dateTime'] = new_end
        
        if barber and barber.lower() != "sin preferencia":
            nombre_barbero = barber.capitalize()
            event['summary'] = f"✂️ {name} | Modificado ({nombre_barbero})"
            event['colorId'] = "9" if "kevin" in nombre_barbero.lower() else "10" if "dani" in nombre_barbero.lower() else "8"

        service.events().update(calendarId=cal_id_google, eventId=event['id'], body=event).execute()

        return {"results": [{"toolCallId": call_id, "result": "Cita modificada con éxito."}]}
    except Exception as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}


@app.post("/cancel_appointment")
async def cancel_appointment(request: Request):
    call_id, _, args = await get_vapi_payload(request)
    if not call_id: return {"error": "Petición inválida"}
    
    name = args.get("name")
    date_str = args.get("date")

    try:
        service = get_calendar_service()
        cal_id_google = get_calendar_id()

        start_search = datetime.strptime(f"{date_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{date_str} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events_result = service.events().list(
            calendarId=cal_id_google, timeMin=start_search, timeMax=end_search, singleEvents=True, q=name
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"results": [{"toolCallId": call_id, "result": f"No encontré ninguna cita a nombre de {name} para cancelar."}]}
            
        service.events().delete(calendarId=cal_id_google, eventId=events[0]['id']).execute()

        return {"results": [{"toolCallId": call_id, "result": "La cita ha sido cancelada correctamente."}]}
    except Exception as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}
