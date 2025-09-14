import os
import logging
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime, timedelta
import openai
import json

# --- Logging setup ---
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- Config ---
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# --- OpenAI client ---
openai.api_key = OPENAI_API_KEY
if OPENAI_API_KEY:
    logging.info(f"🔑 OPENAI_API_KEY trovata (prime 5 cifre: {OPENAI_API_KEY[:10]}...)")
else:
    logging.error("❌ OPENAI_API_KEY mancante!")
logging.info(f"✅ OpenAI client pronto (MODEL={OPENAI_MODEL})")

# --- Google Calendar client ---
try:
    CREDS_PATH = "/secrets/GOOGLE_APPLICATION_CREDENTIALS.json"
    calendar_creds = service_account.Credentials.from_service_account_file(
        CREDS_PATH, scopes=CALENDAR_SCOPES
    )
    calendar_service = build('calendar', 'v3', credentials=calendar_creds)
    logging.info("✅ Google Calendar pronto")
except Exception as e:
    logging.error("❌ Errore inizializzazione Calendar: %s", e)
    calendar_service = None


def parse_event_with_openai(text: str) -> dict:
    """
    Usa ChatGPT per estrarre nome evento, data, ora inizio/fine, descrizione
    Restituisce dizionario con campi: event_name, event_date, start_time, end_time, description
    """
    prompt = f"""
Estrai dal seguente testo le informazioni per creare un evento Google Calendar.
Formato JSON richiesto:
{{
  "event_name": string o null,
  "event_date": string YYYY-MM-DD o null,
  "start_time": string HH:MM o null,
  "end_time": string HH:MM o null,
  "description": string o null
}}
Testo: "{text}"
Se non ci sono informazioni disponibili, usa null.
"""
    try:
        response = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        # Calcola end_time di default se mancante
        if data.get("start_time") and not data.get("end_time"):
            start_dt = datetime.strptime(data["start_time"], "%H:%M")
            end_dt = (start_dt + timedelta(hours=1)).strftime("%H:%M")
            data["end_time"] = end_dt
        return data
    except Exception as e:
        logging.error("❌ Errore OpenAI parsing: %s", e)
        return None


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    incoming_msg = request.form.get('Body')
    sender = request.form.get('From')
    resp = MessagingResponse()
    msg = resp.message()

    logging.info(f"📩 Messaggio da whatsapp:{sender}: {incoming_msg}")

    if not incoming_msg:
        msg.body("❌ Messaggio vuoto ricevuto")
        return str(resp)

    # Messaggio test
    if "test" in incoming_msg.lower():
        status_msg = "🔧 Status:\n"
        status_msg += f"- OpenAI Key: {'✅' if OPENAI_API_KEY else '❌'}\n"
        status_msg += f"- Calendar: {'✅' if calendar_service else '❌'}\n"

        # 🔎 Test connessione OpenAI
        if OPENAI_API_KEY:
            try:
                test_response = openai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": "Rispondi solo con OK"}],
                    max_tokens=5
                )
                test_output = test_response.choices[0].message.content.strip()
                status_msg += f"- OpenAI Test: ✅ ({test_output})"
            except Exception as e:
                status_msg += f"- OpenAI Test: ❌ ({e})"

        msg.body(status_msg)
        return str(resp)

    if calendar_service is None:
        msg.body("❌ Calendar non disponibile")
        return str(resp)

    # Usa OpenAI per estrarre l'evento
    event_data = parse_event_with_openai(incoming_msg)
    if not event_data or not event_data.get("event_name"):
        msg.body("❌ Non sono riuscito a capire il nome dell'evento. Prova a scrivere: 'Aggiungi cena con Marco domani alle 20:00'")
        return str(resp)

    try:
        start_dt = datetime.strptime(f"{event_data['event_date']} {event_data['start_time']}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{event_data['event_date']} {event_data['end_time']}", "%Y-%m-%d %H:%M")

        event_body = {
            "summary": event_data["event_name"],
            "description": event_data.get("description") or f"Creato tramite WhatsApp: {incoming_msg}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Rome"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Rome"},
        }

        result = calendar_service.events().insert(
            calendarId='primary',
            body=event_body
        ).execute()

        formatted_date = start_dt.strftime("%d/%m/%Y alle %H:%M")
        formatted_end = end_dt.strftime("%H:%M")
        msg.body(f"✅ Evento '{event_data['event_name']}' creato!\n📅 {formatted_date} - {formatted_end}")

    except Exception as e:
        logging.error("❌ Errore Calendar insert: %s", e)
        msg.body(f"❌ Errore creazione evento: {e}")

    return str(resp)


@app.route("/health", methods=['GET'])
def health_check():
    status = {
        "openai": "✅" if OPENAI_API_KEY else "❌",
        "calendar": "✅" if calendar_service else "❌"
    }
    return status, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"🚀 Avvio server su porta {port}")
    app.run(host="0.0.0.0", port=port)
