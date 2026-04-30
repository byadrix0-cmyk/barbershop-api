from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
import dateparser
from typing import Optional

app = FastAPI(title="Peluqueria Kevin API - ElevenLabs Edition (Blindada)")

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

# --- FUNCIONES DE APOYO INTELIGENTES ---

def resolver_fecha_inteligente(texto_fecha: str) -> tuple[str, str]:
    """Traduce la fecha y lanza error si es incomprensible (Fallback eliminado)."""
    if not texto_fecha or str(texto_fecha).lower() == "null":
        raise ValueError("Fecha vacía")

    try:
        dt = datetime.strptime(texto_fecha, "%Y-%m-%d").replace(tzinfo=TZ_CANARIAS)
        dia_legible = f"{DAYS_ES[dt.weekday()]} {dt.day} de {MONTHS_ES[dt.month]}"
        return dt.strftime("%Y-%m-%d"), dia_legible
    except ValueError:
        pass
    
    now = datetime.now(TZ_CANARIAS)
    parsed = dateparser.parse(
        texto_fecha, 
        languages=['es'], 
        settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now.replace(tzinfo=None)}
    )
    
    if parsed:
        parsed = parsed.replace(tzinfo=TZ_CANARIAS)
        dia_legible = f"{DAYS_ES[parsed.weekday()]} {parsed.day} de {MONTHS_ES[parsed.month]}"
        return parsed.strftime("%Y-%m-%d"), dia_legible
    else:
        raise ValueError("No entendí la fecha")

def get_time_bounds(date_str: str, time_str: str):
    """Calcula la hora y limpia basura de texto (am, pm, h)."""
    try:
        time_clean = str(time_str).lower().replace("h", "").replace("am", "").replace("pm", "").strip()
        dt = datetime.strptime(f"{date_str} {time_clean}", "%Y-%m-%d %H:%M")
        start_dt = dt.replace(tzinfo=TZ_CANARIAS)
        end_dt = start_dt + timedelta(minutes=30)
        return start_dt.isoformat(), end_dt.isoformat()
    except ValueError:
        raise ValueError("No entendí la hora")

def next_weekday(dt: datetime, weekday: int) -> datetime:
    days_ahead = weekday - dt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return dt + timedelta(days=days_ahead)

def get_calendar_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="Credenciales no encontradas")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    return os.environ.get("CALENDAR_ID")

TRANSLATIONS = {
    "corte": "Haircut", "barba": "Beard Trim", "tinte": "Hair Color", "tratamiento": "Hair Treatment"
}

# --- PLANTILLAS DE DATOS ---

class CheckAvailabilityRequest(BaseModel):
    date: str
    time: str
    barber: Optional[str] = None

class BookAppointmentRequest(BaseModel):
    name: str
    service: str
    date: str
    time: str
    barber: Optional[str] = None

class ModifyAppointmentRequest(BaseModel):
    name: str
    current_date: str
    new_date: str
    new_time: str
    barber: Optional[str] = None

class CancelAppointmentRequest(BaseModel):
    name: str
    date: str

# --- ENDPOINTS ---

@app.get("/")
def root():
    try:
        get_calendar_service()
        return {"status": "🟢 ONLINE", "message": "API lista para ElevenLabs 🚀"}
    except Exception as e:
        return {"status": "🔴 OFFLINE", "message": str(e)}

@app.get("/current_datetime")
def current_datetime():
    now = datetime.now(TZ_CANARIAS)
    return {
        "today_date": now.strftime("%Y-%m-%d"),
        "today_readable": f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month]}",
        "current_time": now.strftime("%H:%M"),
        "timezone": "Atlantic/Canary"
    }

@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        barbero_seguro = req.barber if req.barber and str(req.barber).lower() != "null" else "Sin preferencia"
        
        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        start_time, end_time = get_time_bounds(fecha_exacta, req.time)
        
        service = get_calendar_service()
        events = service.events().list(calendarId=get_calendar_id(), timeMin=start_time, timeMax=end_time, singleEvents=True).execute().get('items', [])
        
        is_available = True
        msg = f"Hay hueco. Dile al cliente: 'Perfecto, sí tengo disponibilidad el {dia_legible} a las {req.time}. ¿Me indicas tu nombre completo?'"

        if len(events) >= 2:
            is_available = False
            msg = f"No hay hueco. Dile al cliente: 'Lo siento, pero el {dia_legible} a las {req.time} estamos completos.'"
        elif barbero_seguro.lower() != "sin preferencia":
            for event in events:
                if barbero_seguro.lower() in event.get('summary', '').lower():
                    other = "Dani" if barbero_seguro.lower() == "kevin" else "Kevin"
                    is_available = False
                    msg = f"No hay hueco con {barbero_seguro.capitalize()}. Dile al cliente: 'Justo a esa hora {barbero_seguro.capitalize()} está ocupado, pero el {dia_legible} a las {req.time} tengo hueco con {other}, ¿te parece bien?'"

        return {"status": "success", "message": msg}
    except ValueError:
        return {"status": "error", "message": "Dile al cliente: 'Disculpa, no he entendido bien el día o la hora. ¿Me lo podrías repetir, por favor?'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        barbero_seguro = req.barber if req.barber and str(req.barber).lower() != "null" else "Sin preferencia"
        nombre_barbero = barbero_seguro.capitalize()

        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        start_time, end_time = get_time_bounds(fecha_exacta, req.time)
        
        servicio_ingles = TRANSLATIONS.get(req.service.lower(), req.service.capitalize())
        color_evento = "9" if "kevin" in barbero_seguro.lower() else "10" if "dani" in barbero_seguro.lower() else "8"

        event = {
            'summary': f"✂️ {req.name} | {servicio_ingles} (con {nombre_barbero})",
            'description': f"Cliente: {req.name}\nBarbero: {nombre_barbero}\nAgendado por ElevenLabs",
            'start': {'dateTime': start_time}, 'end': {'dateTime': end_time}, 'colorId': color_evento
        }
        
        get_calendar_service().events().insert(calendarId=get_calendar_id(), body=event).execute()
        return {"status": "success", "message": f"Dile al cliente: 'Perfecto {req.name}, tu cita está confirmada para el {dia_legible} a las {req.time} con {nombre_barbero}.'"}
    
    except ValueError:
        return {"status": "error", "message": "Dile al cliente: 'Disculpa, hubo una confusión con el día o la hora. ¿Podrías repetírmelo?'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    try:
        barbero_seguro = req.barber if req.barber and str(req.barber).lower() != "null" else "Sin preferencia"
        
        curr_fecha_exacta, _ = resolver_fecha_inteligente(req.current_date)
        new_fecha_exacta, new_dia_legible = resolver_fecha_inteligente(req.new_date)
        new_start, new_end = get_time_bounds(new_fecha_exacta, req.new_time)
        
        service = get_calendar_service()
        start_search = datetime.strptime(f"{curr_fecha_exacta} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{curr_fecha_exacta} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events = service.events().list(calendarId=get_calendar_id(), timeMin=start_search, timeMax=end_search, singleEvents=True).execute().get('items', [])
        
        # Filtro estricto antimisiles: Comprueba que el nombre esté realmente en el título
        eventos_del_cliente = [e for e in events if req.name.lower() in e.get('summary', '').split('|')[0].lower()]
        
        if not eventos_del_cliente:
            return {"status": "error", "message": "Dile al cliente: 'No he podido encontrar ninguna cita a tu nombre para esa fecha.'"}
            
        event = eventos_del_cliente[0]
        event['start']['dateTime'], event['end']['dateTime'] = new_start, new_end
        
        if barbero_seguro.lower() != "sin preferencia":
            nombre_barbero = barbero_seguro.capitalize()
            event['summary'] = f"✂️ {req.name} | Modificado ({nombre_barbero})"
            event['colorId'] = "9" if "kevin" in nombre_barbero.lower() else "10" if "dani" in nombre_barbero.lower() else "8"

        service.events().update(calendarId=get_calendar_id(), eventId=event['id'], body=event).execute()
        return {"status": "success", "message": f"Dile al cliente: 'Genial, he modificado tu cita. Ha quedado para el {new_dia_legible} a las {req.new_time}.'"}
        
    except ValueError:
        return {"status": "error", "message": "Dile al cliente: 'Disculpa, no entendí bien la nueva fecha. ¿Me la repites?'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    try:
        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        service = get_calendar_service()

        start_search = datetime.strptime(f"{fecha_exacta} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        end_search = datetime.strptime(f"{fecha_exacta} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
        
        events = service.events().list(calendarId=get_calendar_id(), timeMin=start_search, timeMax=end_search, singleEvents=True).execute().get('items', [])
        
        # Filtro estricto antimisiles
        eventos_del_cliente = [e for e in events if req.name.lower() in e.get('summary', '').split('|')[0].lower()]
        
        if not eventos_del_cliente:
            return {"status": "error", "message": f"Dile al cliente: 'No encuentro ninguna cita a tu nombre para cancelar el {dia_legible}.'"}
            
        service.events().delete(calendarId=get_calendar_id(), eventId=eventos_del_cliente[0]['id']).execute()
        return {"status": "success", "message": f"Dile al cliente: 'Listo, tu cita del {dia_legible} ha sido cancelada correctamente.'"}
        
    except ValueError:
        return {"status": "error", "message": "Dile al cliente: 'Disculpa, no he entendido bien la fecha de la cita a cancelar. ¿Me la puedes repetir?'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
