from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os
import json
import dateparser
import re
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

# --- CONFIGURACIÓN DEL NEGOCIO ---
HORA_APERTURA = 9
HORA_CIERRE = 21
DIAS_CERRADO = {5, 6}  # sábado, domingo
MAX_CITAS_SIMULTANEAS = 2

TRANSLATIONS = {
    "corte": "Haircut", "barba": "Beard Trim",
    "tinte": "Hair Color", "tratamiento": "Hair Treatment"
}


# --- FUNCIONES DE APOYO ---

def _validar_fecha_no_pasada(dt: datetime):
    """Rechaza fechas anteriores a hoy."""
    now = datetime.now(TZ_CANARIAS)
    if dt.date() < now.date():
        raise ValueError(f"Esa fecha ya ha pasado. Hoy es {DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month]}.")

def _validar_dia_laborable(dt: datetime):
    """Rechaza días en los que la peluquería está cerrada."""
    if dt.weekday() in DIAS_CERRADO:
        dia_nombre = DAYS_ES[dt.weekday()]
        raise ValueError(f"El {dia_nombre} estamos cerrados. Prueba otro día.")

def resolver_fecha_inteligente(texto_fecha: str) -> tuple[str, str]:
    """Traduce texto de fecha a formato YYYY-MM-DD + versión legible."""
    if not texto_fecha or str(texto_fecha).lower().strip() in ("null", "none", ""):
        raise ValueError("No he entendido bien el día. ¿Me lo puedes repetir?")

    texto_fecha = texto_fecha.strip()

    # Intento 1: formato ISO directo
    try:
        dt = datetime.strptime(texto_fecha, "%Y-%m-%d").replace(tzinfo=TZ_CANARIAS)
        _validar_fecha_no_pasada(dt)
        _validar_dia_laborable(dt)
        dia_legible = f"{DAYS_ES[dt.weekday()]} {dt.day} de {MONTHS_ES[dt.month]}"
        return dt.strftime("%Y-%m-%d"), dia_legible
    except ValueError as e:
        if "pasado" in str(e) or "cerrado" in str(e):
            raise
        pass

    # Intento 2: dateparser para lenguaje natural
    now = datetime.now(TZ_CANARIAS)
    parsed = dateparser.parse(
        texto_fecha,
        languages=['es'],
        settings={
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': now.replace(tzinfo=None),
            'RETURN_AS_TIMEZONE_AWARE': False,
        }
    )

    if parsed:
        parsed = parsed.replace(tzinfo=TZ_CANARIAS)
        if parsed.date() < now.date():
            raise ValueError("Esa fecha ya ha pasado. ¿Para cuándo la querías?")
        _validar_fecha_no_pasada(parsed)
        _validar_dia_laborable(parsed)
        dia_legible = f"{DAYS_ES[parsed.weekday()]} {parsed.day} de {MONTHS_ES[parsed.month]}"
        return parsed.strftime("%Y-%m-%d"), dia_legible
    else:
        raise ValueError("No he entendido la fecha exacta. ¿Podrías decírmela de otra forma?")

def parsear_hora(time_str: str) -> tuple[int, int]:
    """Parsea una hora en múltiples formatos y devuelve (hora_24h, minuto)."""
    text = str(time_str).strip().lower()
    is_pm = "pm" in text or "p.m" in text
    is_am = "am" in text or "a.m" in text
    text = re.sub(r'[apm.\s:h]+$', '', text)
    text = text.replace("h", ":").replace(".", ":")
    parts = text.split(":")
    try:
        hora = int(parts[0])
        minuto = int(parts[1]) if len(parts) > 1 else 0
    except:
        raise ValueError("No he entendido bien la hora. ¿Me la repites?")
    if is_pm and hora < 12:
        hora += 12
    elif is_am and hora == 12:
        hora = 0
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        raise ValueError("Esa hora no es válida.")
    return hora, minuto

def get_time_bounds(date_str: str, time_str: str) -> tuple[str, str]:
    """Calcula inicio y fin del slot (30 min) con validación de horario comercial."""
    hora, minuto = parsear_hora(time_str)
    if hora < HORA_APERTURA or hora >= HORA_CIERRE:
        raise ValueError(f"A esa hora estamos cerrados. Nuestro horario es de {HORA_APERTURA}:00 a {HORA_CIERRE}:00.")
    dt = datetime.strptime(f"{date_str} {hora:02d}:{minuto:02d}", "%Y-%m-%d %H:%M")
    start_dt = dt.replace(tzinfo=TZ_CANARIAS)
    end_dt = start_dt + timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()

def get_calendar_service():
    """Conecta con Google Calendar usando credenciales de servicio."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="Credenciales no encontradas")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    """Obtiene el ID del calendario desde variables de entorno."""
    cal_id = os.environ.get("CALENDAR_ID")
    if not cal_id:
        raise HTTPException(status_code=500, detail="CALENDAR_ID no configurado")
    return cal_id

def sanitizar_barbero(barber: Optional[str]) -> str:
    """Normaliza el campo barbero."""
    if not barber or str(barber).lower().strip() in ("null", "none", "", "sin preferencia"):
        return "Sin preferencia"
    return barber.strip().capitalize()

def buscar_eventos_dia(service, fecha_str: str) -> list:
    """Busca todos los eventos de un día completo."""
    start_search = datetime.strptime(f"{fecha_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
    end_search = datetime.strptime(f"{fecha_str} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
    return service.events().list(
        calendarId=get_calendar_id(),
        timeMin=start_search,
        timeMax=end_search,
        singleEvents=True
    ).execute().get('items', [])

def filtrar_eventos_cliente(events: list, nombre: str) -> list:
    """Filtra eventos que pertenecen al cliente por nombre."""
    nombre_lower = nombre.lower().strip()
    return [
        e for e in events
        if nombre_lower in e.get('summary', '').split('|')[0].lower()
    ]

def color_barbero(nombre_barbero: str) -> str:
    """Devuelve el colorId de Google Calendar según barbero."""
    nombre = nombre_barbero.lower()
    if "kevin" in nombre:
        return "9"
    elif "dani" in nombre:
        return "10"
    return "8"


# --- MODELOS DE DATOS ---

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
    return {"status": "🟢 ONLINE", "message": "API Peluquería Kevin - ElevenLabs 🚀"}


@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    try:
        barbero = sanitizar_barbero(req.barber)
        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        start_time, end_time = get_time_bounds(fecha_exacta, req.time)

        service = get_calendar_service()
        events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=start_time,
            timeMax=end_time,
            singleEvents=True
        ).execute().get('items', [])

        hora, minuto = parsear_hora(req.time)
        hora_legible = f"{hora:02d}:{minuto:02d}"

        if len(events) >= MAX_CITAS_SIMULTANEAS:
            return {
                "status": "success",
                "available": False,
                "message": f"Dile al cliente: 'Lo siento, pero el {dia_legible} a las {hora_legible} estamos completos.'"
            }

        if barbero != "Sin preferencia":
            for event in events:
                if barbero.lower() in event.get('summary', '').lower():
                    other = "Dani" if barbero.lower() == "kevin" else "Kevin"
                    return {
                        "status": "success",
                        "available": False,
                        "message": f"Dile al cliente: 'Justo a esa hora {barbero} está ocupado, pero el {dia_legible} a las {hora_legible} tengo hueco con {other}, ¿te parece bien?'"
                    }

        return {
            "status": "success",
            "available": True,
            "message": f"Dile al cliente: 'Perfecto, sí tengo disponibilidad el {dia_legible} a las {hora_legible}. ¿Me indicas tu nombre completo?'"
        }

    except ValueError as e:
        return {"status": "error", "message": f"Dile al cliente: '{str(e)}'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    try:
        barbero = sanitizar_barbero(req.barber)
        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        start_time, end_time = get_time_bounds(fecha_exacta, req.time)
        hora, minuto = parsear_hora(req.time)
        hora_legible = f"{hora:02d}:{minuto:02d}"

        service = get_calendar_service()
        events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=start_time,
            timeMax=end_time,
            singleEvents=True
        ).execute().get('items', [])

        if len(events) >= MAX_CITAS_SIMULTANEAS:
            return {
                "status": "error",
                "message": "Dile al cliente: 'Lo siento, ese hueco se acaba de ocupar. ¿Probamos otra hora?'"
            }

        if barbero != "Sin preferencia":
            for event in events:
                if barbero.lower() in event.get('summary', '').lower():
                    return {
                        "status": "error",
                        "message": f"Dile al cliente: 'Lo siento, {barbero} acaba de ser reservado para esa hora. ¿Quieres probar con otro barbero u otra hora?'"
                    }

        servicio_ingles = TRANSLATIONS.get(req.service.lower().strip(), req.service.strip().capitalize())

        event = {
            'summary': f"✂️ {req.name.strip()} | {servicio_ingles} (con {barbero})",
            'description': f"Cliente: {req.name.strip()}\nBarbero: {barbero}\nServicio: {servicio_ingles}\nAgendado por ElevenLabs",
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
            'colorId': color_barbero(barbero)
        }

        service.events().insert(calendarId=get_calendar_id(), body=event).execute()
        return {
            "status": "success",
            "message": f"Dile al cliente: 'Perfecto {req.name.strip()}, tu cita está confirmada para el {dia_legible} a las {hora_legible} con {barbero}.'"
        }

    except ValueError as e:
        return {"status": "error", "message": f"Dile al cliente: '{str(e)}'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    try:
        barbero = sanitizar_barbero(req.barber)
        curr_fecha_exacta, _ = resolver_fecha_inteligente(req.current_date)
        new_fecha_exacta, new_dia_legible = resolver_fecha_inteligente(req.new_date)
        new_start, new_end = get_time_bounds(new_fecha_exacta, req.new_time)
        hora, minuto = parsear_hora(req.new_time)
        hora_legible = f"{hora:02d}:{minuto:02d}"

        service = get_calendar_service()

        events = buscar_eventos_dia(service, curr_fecha_exacta)
        eventos_del_cliente = filtrar_eventos_cliente(events, req.name)

        if not eventos_del_cliente:
            return {
                "status": "error",
                "message": "Dile al cliente: 'No he podido encontrar ninguna cita a tu nombre para esa fecha.'"
            }

        new_events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=new_start,
            timeMax=new_end,
            singleEvents=True
        ).execute().get('items', [])

        evento_original = eventos_del_cliente[0]
        otros_eventos = [e for e in new_events if e['id'] != evento_original['id']]

        if len(otros_eventos) >= MAX_CITAS_SIMULTANEAS:
            return {
                "status": "error",
                "message": f"Dile al cliente: 'Lo siento, el {new_dia_legible} a las {hora_legible} ya está completo. ¿Probamos otra hora?'"
            }

        evento_original['start']['dateTime'] = new_start
        evento_original['end']['dateTime'] = new_end

        if barbero != "Sin preferencia":
            summary_original = evento_original.get('summary', '')
            servicio_original = "Corte"
            if '|' in summary_original:
                parte_servicio = summary_original.split('|')[1].strip()
                if '(' in parte_servicio:
                    servicio_original = parte_servicio.split('(')[0].strip()
                else:
                    servicio_original = parte_servicio.strip()
            evento_original['summary'] = f"✂️ {req.name.strip()} | {servicio_original} (con {barbero})"
            evento_original['colorId'] = color_barbero(barbero)

        service.events().update(
            calendarId=get_calendar_id(),
            eventId=evento_original['id'],
            body=evento_original
        ).execute()

        return {
            "status": "success",
            "message": f"Dile al cliente: 'Genial, he modificado tu cita. Ha quedado para el {new_dia_legible} a las {hora_legible}.'"
        }

    except ValueError as e:
        return {"status": "error", "message": f"Dile al cliente: '{str(e)}'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    try:
        fecha_exacta, dia_legible = resolver_fecha_inteligente(req.date)
        service = get_calendar_service()

        events = buscar_eventos_dia(service, fecha_exacta)
        eventos_del_cliente = filtrar_eventos_cliente(events, req.name)

        if not eventos_del_cliente:
            return {
                "status": "error",
                "message": f"Dile al cliente: 'No encuentro ninguna cita a tu nombre para cancelar el {dia_legible}.'"
            }

        for evento in eventos_del_cliente:
            service.events().delete(
                calendarId=get_calendar_id(),
                eventId=evento['id']
            ).execute()

        if len(eventos_del_cliente) > 1:
            return {
                "status": "success",
                "message": f"Dile al cliente: 'Tenías {len(eventos_del_cliente)} citas el {dia_legible}. Las he cancelado todas.'"
            }

        return {
            "status": "success",
            "message": f"Dile al cliente: 'Listo, tu cita del {dia_legible} ha sido cancelada correctamente.'"
        }

    except ValueError as e:
        return {"status": "error", "message": f"Dile al cliente: '{str(e)}'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
