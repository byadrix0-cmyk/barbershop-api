from fastapi import FastAPI, HTTPException, Request # Añadido Request
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

# --- FUNCIONES DE APOYO (SE MANTIENEN IGUAL) ---
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

# --- TRADUCCIONES ---
TRANSLATIONS = {
    "corte": "Haircut",
    "barba": "Beard Trim",
    "tinte": "Hair Color",
    "tratamiento": "Hair Treatment"
}

# --- LÓGICA DE EXTRACCIÓN PARA VAPI (NUEVO) ---
# Esta función extrae los argumentos y el ID de llamada que envía Vapi
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

# --- ENDPOINTS ADAPTADOS A VAPI ---

@app.post("/check_availability")
async def check_availability(request: Request):
    call_id, _, args = await get_vapi_payload(request)
    
    date = args.get("date")
    time = args.get("time")
    barber = args.get("barber")

    try:
        start_time, end_time = get_time_bounds(date, time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        
        events_result = service.events().list(
            calendarId=cal_id, timeMin=start_time, timeMax=end_time, singleEvents=True
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
                    is_available = False
                    msg = f"{barber} está ocupado, pero el otro peluquero está libre."

        # RESPUESTA FORMATO VAPI
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
    
    name = args.get("name")
    service_type = args.get("service")
    date = args.get("date")
    time = args.get("time")
    barber = args.get("barber", "Sin preferencia")

    try:
        start_time, end_time = get_time_bounds(date, time)
        service = get_calendar_service()
        cal_id = get_calendar_id()
        
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
        
        service.events().insert(calendarId=cal_id, body=event).execute()

        return {
            "results": [{
                "toolCallId": call_id,
                "result": f"Cita confirmada para {name} con {nombre_barbero}."
            }]
        }
    except Exception as e:
        return {"results": [{"toolCallId": call_id, "error": str(e)}]}

# NOTA: Modify y Cancel seguirían el mismo patrón de extraer call_id y devolver "results"
