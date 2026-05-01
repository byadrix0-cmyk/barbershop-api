from fastapi import FastAPI, HTTPException, Request
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

# --- CONFIGURACIÓN DEL NEGOCIO ---
HORA_APERTURA = 9
HORA_CIERRE = 21
DIAS_CERRADO = {5, 6}  # sábado, domingo
MAX_CITAS_SIMULTANEAS = 2

TRANSLATIONS = {
    "corte": "Haircut", "barba": "Beard Trim",
    "tinte": "Hair Color", "tratamiento": "Hair Treatment"
}


# =====================================================
# WRAPPER PARA VAPI
# =====================================================
# Vapi envía tool calls con este formato:
# {
#   "message": {
#     "type": "tool-calls",
#     "toolCallList": [
#       {
#         "id": "toolu_xxx",
#         "name": "check_availability",
#         "arguments": { "date": "mañana", "time": "15:00" }
#       }
#     ]
#   }
# }
#
# Y espera respuesta con este formato:
# {
#   "results": [
#     {
#       "toolCallId": "toolu_xxx",
#       "result": "texto o JSON string"
#     }
#   ]
# }
# =====================================================

@app.post("/vapi/tool-handler")
async def vapi_tool_handler(request: Request):
    """
    Endpoint único que recibe TODOS los tool calls de Vapi,
    los routea a la función correcta, y devuelve el resultado
    en el formato que Vapi espera.
    """
    try:
        body = await request.json()
        message = body.get("message", {})
        tool_call_list = message.get("toolCallList", [])

        if not tool_call_list:
            return {"results": []}

        results = []
        for tool_call in tool_call_list:
            tool_call_id = tool_call.get("id", "")
            tool_name = tool_call.get("name", "")
            args = tool_call.get("arguments", {})

            # Routear al handler correcto
            if tool_name == "check_availability":
                result = _handle_check_availability(args)
            elif tool_name == "book_appointment":
                result = _handle_book_appointment(args)
            elif tool_name == "modify_appointment":
                result = _handle_modify_appointment(args)
            elif tool_name == "cancel_appointment":
                result = _handle_cancel_appointment(args)
            else:
                result = f"Herramienta desconocida: {tool_name}"

            results.append({
                "toolCallId": tool_call_id,
                "result": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            })

        return {"results": results}

    except Exception as e:
        # Si algo falla, devolver error en formato Vapi
        return {"results": [{"toolCallId": "", "result": f"Error del servidor: {str(e)}"}]}


# =====================================================
# FUNCIONES DE APOYO (sin cambios)
# =====================================================

def _validar_fecha_no_pasada(dt: datetime):
    now = datetime.now(TZ_CANARIAS)
    if dt.date() < now.date():
        raise ValueError(f"Esa fecha ya ha pasado. Hoy es {DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month]}.")

def _validar_dia_laborable(dt: datetime):
    if dt.weekday() in DIAS_CERRADO:
        dia_nombre = DAYS_ES[dt.weekday()]
        raise ValueError(f"El {dia_nombre} estamos cerrados. Prueba otro día.")

def resolver_fecha_inteligente(texto_fecha: str) -> tuple[str, str]:
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

    # Intento 2: dateparser
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
    hora, minuto = parsear_hora(time_str)
    if hora < HORA_APERTURA or hora >= HORA_CIERRE:
        raise ValueError(f"A esa hora estamos cerrados. Nuestro horario es de {HORA_APERTURA}:00 a {HORA_CIERRE}:00.")
    dt = datetime.strptime(f"{date_str} {hora:02d}:{minuto:02d}", "%Y-%m-%d %H:%M")
    start_dt = dt.replace(tzinfo=TZ_CANARIAS)
    end_dt = start_dt + timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()

def get_calendar_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise HTTPException(status_code=500, detail="Credenciales no encontradas")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_calendar_id():
    cal_id = os.environ.get("CALENDAR_ID")
    if not cal_id:
        raise HTTPException(status_code=500, detail="CALENDAR_ID no configurado")
    return cal_id

def sanitizar_barbero(barber: Optional[str]) -> str:
    if not barber or str(barber).lower().strip() in ("null", "none", "", "sin preferencia"):
        return "Sin preferencia"
    return barber.strip().capitalize()

def buscar_eventos_dia(service, fecha_str: str) -> list:
    start_search = datetime.strptime(f"{fecha_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
    end_search = datetime.strptime(f"{fecha_str} 23:59", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_CANARIAS).isoformat()
    return service.events().list(
        calendarId=get_calendar_id(),
        timeMin=start_search,
        timeMax=end_search,
        singleEvents=True
    ).execute().get('items', [])

def filtrar_eventos_cliente(events: list, nombre: str) -> list:
    nombre_lower = nombre.lower().strip()
    return [
        e for e in events
        if nombre_lower in e.get('summary', '').split('|')[0].lower()
    ]

def color_barbero(nombre_barbero: str) -> str:
    nombre = nombre_barbero.lower()
    if "kevin" in nombre:
        return "9"
    elif "dani" in nombre:
        return "10"
    return "8"


# =====================================================
# HANDLERS INTERNOS (la lógica de negocio)
# =====================================================

def _handle_check_availability(args: dict) -> str:
    try:
        barbero = sanitizar_barbero(args.get("barber"))
        fecha_exacta, dia_legible = resolver_fecha_inteligente(args.get("date", ""))
        start_time, end_time = get_time_bounds(fecha_exacta, args.get("time", ""))

        service = get_calendar_service()
        events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=start_time,
            timeMax=end_time,
            singleEvents=True
        ).execute().get('items', [])

        hora, minuto = parsear_hora(args.get("time", ""))
        hora_legible = f"{hora:02d}:{minuto:02d}"

        if len(events) >= MAX_CITAS_SIMULTANEAS:
            return json.dumps({
                "status": "success",
                "available": False,
                "message": f"Dile al cliente: 'Lo siento, pero el {dia_legible} a las {hora_legible} estamos completos.'"
            }, ensure_ascii=False)

        if barbero != "Sin preferencia":
            for event in events:
                if barbero.lower() in event.get('summary', '').lower():
                    other = "Dani" if barbero.lower() == "kevin" else "Kevin"
                    return json.dumps({
                        "status": "success",
                        "available": False,
                        "message": f"Dile al cliente: 'Justo a esa hora {barbero} está ocupado, pero el {dia_legible} a las {hora_legible} tengo hueco con {other}, ¿te parece bien?'"
                    }, ensure_ascii=False)

        return json.dumps({
            "status": "success",
            "available": True,
            "message": f"Dile al cliente: 'Perfecto, sí tengo disponibilidad el {dia_legible} a las {hora_legible}. ¿Me indicas tu nombre completo?'"
        }, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Dile al cliente: '{str(e)}'"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def _handle_book_appointment(args: dict) -> str:
    try:
        barbero = sanitizar_barbero(args.get("barber"))
        fecha_exacta, dia_legible = resolver_fecha_inteligente(args.get("date", ""))
        start_time, end_time = get_time_bounds(fecha_exacta, args.get("time", ""))
        hora, minuto = parsear_hora(args.get("time", ""))
        hora_legible = f"{hora:02d}:{minuto:02d}"

        service = get_calendar_service()
        events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=start_time,
            timeMax=end_time,
            singleEvents=True
        ).execute().get('items', [])

        if len(events) >= MAX_CITAS_SIMULTANEAS:
            return json.dumps({
                "status": "error",
                "message": "Dile al cliente: 'Lo siento, ese hueco se acaba de ocupar. ¿Probamos otra hora?'"
            }, ensure_ascii=False)

        if barbero != "Sin preferencia":
            for event in events:
                if barbero.lower() in event.get('summary', '').lower():
                    return json.dumps({
                        "status": "error",
                        "message": f"Dile al cliente: 'Lo siento, {barbero} acaba de ser reservado para esa hora. ¿Quieres probar con otro barbero u otra hora?'"
                    }, ensure_ascii=False)

        nombre = args.get("name", "").strip()
        servicio_raw = args.get("service", "corte").lower().strip()
        servicio_ingles = TRANSLATIONS.get(servicio_raw, servicio_raw.capitalize())

        event = {
            'summary': f"✂️ {nombre} | {servicio_ingles} (con {barbero})",
            'description': f"Cliente: {nombre}\nBarbero: {barbero}\nServicio: {servicio_ingles}\nAgendado por Vapi",
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
            'colorId': color_barbero(barbero)
        }

        service.events().insert(calendarId=get_calendar_id(), body=event).execute()
        return json.dumps({
            "status": "success",
            "message": f"Dile al cliente: 'Perfecto {nombre}, tu cita está confirmada para el {dia_legible} a las {hora_legible} con {barbero}.'"
        }, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Dile al cliente: '{str(e)}'"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def _handle_modify_appointment(args: dict) -> str:
    try:
        barbero = sanitizar_barbero(args.get("barber"))
        curr_fecha_exacta, _ = resolver_fecha_inteligente(args.get("current_date", ""))
        new_fecha_exacta, new_dia_legible = resolver_fecha_inteligente(args.get("new_date", ""))
        new_start, new_end = get_time_bounds(new_fecha_exacta, args.get("new_time", ""))
        hora, minuto = parsear_hora(args.get("new_time", ""))
        hora_legible = f"{hora:02d}:{minuto:02d}"

        nombre = args.get("name", "").strip()
        service = get_calendar_service()

        events = buscar_eventos_dia(service, curr_fecha_exacta)
        eventos_del_cliente = filtrar_eventos_cliente(events, nombre)

        if not eventos_del_cliente:
            return json.dumps({
                "status": "error",
                "message": "Dile al cliente: 'No he podido encontrar ninguna cita a tu nombre para esa fecha.'"
            }, ensure_ascii=False)

        new_events = service.events().list(
            calendarId=get_calendar_id(),
            timeMin=new_start,
            timeMax=new_end,
            singleEvents=True
        ).execute().get('items', [])

        evento_original = eventos_del_cliente[0]
        otros_eventos = [e for e in new_events if e['id'] != evento_original['id']]

        if len(otros_eventos) >= MAX_CITAS_SIMULTANEAS:
            return json.dumps({
                "status": "error",
                "message": f"Dile al cliente: 'Lo siento, el {new_dia_legible} a las {hora_legible} ya está completo. ¿Probamos otra hora?'"
            }, ensure_ascii=False)

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
            evento_original['summary'] = f"✂️ {nombre} | {servicio_original} (con {barbero})"
            evento_original['colorId'] = color_barbero(barbero)

        service.events().update(
            calendarId=get_calendar_id(),
            eventId=evento_original['id'],
            body=evento_original
        ).execute()

        return json.dumps({
            "status": "success",
            "message": f"Dile al cliente: 'Genial, he modificado tu cita. Ha quedado para el {new_dia_legible} a las {hora_legible}.'"
        }, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Dile al cliente: '{str(e)}'"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


def _handle_cancel_appointment(args: dict) -> str:
    try:
        fecha_exacta, dia_legible = resolver_fecha_inteligente(args.get("date", ""))
        nombre = args.get("name", "").strip()
        service = get_calendar_service()

        events = buscar_eventos_dia(service, fecha_exacta)
        eventos_del_cliente = filtrar_eventos_cliente(events, nombre)

        if not eventos_del_cliente:
            return json.dumps({
                "status": "error",
                "message": f"Dile al cliente: 'No encuentro ninguna cita a tu nombre para cancelar el {dia_legible}.'"
            }, ensure_ascii=False)

        for evento in eventos_del_cliente:
            service.events().delete(
                calendarId=get_calendar_id(),
                eventId=evento['id']
            ).execute()

        if len(eventos_del_cliente) > 1:
            return json.dumps({
                "status": "success",
                "message": f"Dile al cliente: 'Tenías {len(eventos_del_cliente)} citas el {dia_legible}. Las he cancelado todas.'"
            }, ensure_ascii=False)

        return json.dumps({
            "status": "success",
            "message": f"Dile al cliente: 'Listo, tu cita del {dia_legible} ha sido cancelada correctamente.'"
        }, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Dile al cliente: '{str(e)}'"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


# =====================================================
# ENDPOINTS ORIGINALES (se mantienen para compatibilidad / testing directo)
# =====================================================

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


@app.get("/")
def root():
    return {"status": "🟢 ONLINE", "message": "API lista para Vapi 🚀"}


@app.post("/check_availability")
def check_availability(req: CheckAvailabilityRequest):
    return json.loads(_handle_check_availability(req.dict()))


@app.post("/book_appointment")
def book_appointment(req: BookAppointmentRequest):
    return json.loads(_handle_book_appointment(req.dict()))


@app.post("/modify_appointment")
def modify_appointment(req: ModifyAppointmentRequest):
    return json.loads(_handle_modify_appointment(req.dict()))


@app.post("/cancel_appointment")
def cancel_appointment(req: CancelAppointmentRequest):
    return json.loads(_handle_cancel_appointment(req.dict()))
