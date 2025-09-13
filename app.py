import os
import logging
import traceback
import json
from datetime import datetime, timedelta
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

import openai  # pip install openai

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Config Google ---
DIALOGFLOW_PROJECT_ID = None  # non più usato, lo tieni per compatibilità se vuoi
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

# --- Init Google Calendar (service account JSON montato su /secrets/GOOGLE_APPLICATION_CREDENTIALS.json) ---
try:
    CREDS_PATH = "/secrets/GOOGLE_APPLICATION_CREDENTIALS.json"
    calendar_creds = service_account.Credentials.from_service_account_file(
        CREDS_PATH, scopes=CALENDAR_SCOPES
    )
    calendar_service = build('calendar', 'v3', credentials=calendar_creds)
    logger.info("✅ Google Calendar auth OK")
except Exception as e:
    logger.exception("❌ Errore inizializzazione Calendar: %s", e)
    calendar_service = None

# --- Init OpenAI ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # modifica se vuoi altro modello
if not OPENAI_API_KEY:
    logger.error("❌ OPENAI_API_KEY non impostata")
else:
    openai.api_key = OPENAI_API_KEY
    logger.info("✅ OpenAI client pronto (MODEL=%s)", OPENAI_MODEL)

# -- helper: chiama OpenAI per parsare il testo in JSON strutturato --
def parse_text_with_openai(natural_text: str):
    """
    Invia prompt a OpenAI per ottenere un JSON standardizzato:
    { event_name, event_date (YYYY-MM-DD), start_time (HH:MM), end_time (HH:MM), event_description or null }
    end_time deve essere impostato a +1h se non specificato.
    """
    system_prompt = (
        "Sei un parser che estrae informazioni di calendario da frasi in italiano. "
        "Ricevi un testo (es: 'aggiungi per il 15 la cena con ricky alle 21') e rispondi *solo* con un JSON valido "
        "con i seguenti campi: event_name, event_date (YYYY-MM-DD), start_time (HH:MM 24h), end_time (HH:MM 24h) "
        "e event_description (string o null). Se non è specificata la durata, metti end_time = start_time + 1 ora. "
        "Se la data non è esplicita ma è relativa (es 'domani'), converti in YYYY-MM-DD usando la data corrente del sistema. "
        "Se non è possibile estrarre un campo, usa null per quello campo. Non fornisci testo aggiuntivo, solo JSON."
    )

    user_prompt = f"Testo: \"{natural_text}\""

    try:
        # ChatCompletion (compatibile con openai python lib)
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=300,
            temperature=0.0
        )

        # la risposta testuale
        text = response.choices[0].message['content'].strip()
        logger.info("📤 OpenAI raw response: %s", text[:1000])

        # Se il modello ritorna testo non JSON (a volte succede), tentiamo di "estrarre" il JSON
        # Proviamo a trovare la prima occorrenza di "{" e l'ultima "}"
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            json_text = text[start:end+1]
        else:
            json_text = text  # proviamo così

        parsed = json.loads(json_text)

        # Normalizzazioni: se end_time è null o mancante, impostiamo +1h
        if parsed.get('start_time') and not parsed.get('end_time'):
            try:
                st = datetime.fromisoformat(parsed['event_date'] + "T" + parsed['start_time'])
                et = st + timedelta(hours=1)
                parsed['end_time'] = et.strftime("%H:%M")
            except Exception:
                parsed['end_time'] = None

        return parsed

    except Exception as e:
        logger.exception("❌ Errore chiamata OpenAI: %s", e)
        return None


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    resp = MessagingResponse()
    msg = resp.message()

    incoming_msg = request.form.get('Body')
    from_number = request.form.get('From', 'unknown')
    logger.info("📩 Messaggio da %s: %s", from_number, incoming_msg)

    if not incoming_msg:
        msg.body("❌ Messaggio vuoto")
        return str(resp)

    # Comandi di debug
    if incoming_msg.lower().strip() == "test":
        status_msg = f"🔧 Status:\n- OpenAI: {'✅' if OPENAI_API_KEY else '❌'}\n- Calendar: {'✅' if calendar_service else '❌'}"
        msg.body(status_msg)
        return str(resp)

    if not OPENAI_API_KEY:
        msg.body("❌ Servizio OpenAI non configurato.")
        return str(resp)

    if not calendar_service:
        msg.body("❌ Servizio Google Calendar non configurato.")
        return str(resp)

    # Chiedi a OpenAI di parsare il testo e ottenere il JSON
    parsed = parse_text_with_openai(incoming_msg)
    if not parsed:
        msg.body("❌ Non sono riuscito ad estrarre i parametri dall'input.")
        return str(resp)

    # estraiamo i campi
    event_name = parsed.get('event_name')
    event_date = parsed.get('event_date')  # YYYY-MM-DD
    start_time = parsed.get('start_time')  # HH:MM
    end_time = parsed.get('end_time')      # HH:MM
    event_description = parsed.get('event_description') or ''

    # Validazioni minime
    if not event_name:
        msg.body("❌ Non ho capito il nome dell'evento.")
        return str(resp)
    if not event_date or not start_time:
        msg.body("❌ Non ho capito data o ora di inizio. Prova con: 'domani alle 15:00' o '15/09/2025 alle 21:00'.")
        return str(resp)

    # costruisci datetime oggetti con timezone Europe/Rome
    try:
        start_dt = datetime.fromisoformat(f"{event_date}T{start_time}")
        if end_time:
            end_dt = datetime.fromisoformat(f"{event_date}T{end_time}")
        else:
            end_dt = start_dt + timedelta(hours=1)
    except Exception as e:
        logger.exception("❌ Errore parsing date/time: %s", e)
        msg.body("❌ Errore formato data/ora estratte.")
        return str(resp)

    # corpo evento per Google Calendar
    event_body = {
        'summary': str(event_name),
        'description': str(event_description) if event_description else f"Creato tramite WhatsApp: {incoming_msg}",
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': 'Europe/Rome'
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': 'Europe/Rome'
        },
    }

    try:
        created = calendar_service.events().insert(
            calendarId='primary',  # o l'email del calendario specifico
            body=event_body
        ).execute()
        logger.info("✅ Evento creato: %s", created.get('id'))

        formatted_date = start_dt.strftime('%d/%m/%Y alle %H:%M')
        formatted_end = end_dt.strftime('%H:%M')
        msg.body(f"✅ Evento '{event_name}' creato!\n📅 {formatted_date} - {formatted_end}")
    except Exception as e:
        logger.exception("❌ Errore creazione evento Calendar: %s", e)
        msg.body(f"❌ Errore creazione evento Calendar: {str(e)[:200]}")

    return str(resp)


@app.route("/health", methods=['GET'])
def health_check():
    status = {
        "openai": "✅" if OPENAI_API_KEY else "❌",
        "calendar": "✅" if calendar_service else "❌",
    }
    return f"Health: {status}", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
