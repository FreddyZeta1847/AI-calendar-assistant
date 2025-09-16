import os
import logging
import openai
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime, timedelta
import json

# --- Logging setup ---
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- Config ---
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']
# Clean the API key from any whitespace or newline characters
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if OPENAI_API_KEY:
    OPENAI_API_KEY = OPENAI_API_KEY.strip()  # Remove any whitespace, \r, \n
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")  # Use gpt-3.5-turbo for old API

# --- OpenAI setup (old API) ---
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
    logging.info(f"🔑 OPENAI_API_KEY trovata (prime 10 cifre: {OPENAI_API_KEY[:10]}...)")
    logging.info(f"✅ OpenAI configurato (MODEL={OPENAI_MODEL})")
else:
    logging.error("❌ OPENAI_API_KEY mancante!")

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
    Usa ChatGPT per estrarre nome evento, data, ora inizio/fine, descrizione e colore
    Restituisce dizionario con campi: event_name, event_date, start_time, end_time, description, colorId
    """
    # Ottieni data e ora correnti
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    current_weekday = now.strftime("%A")

    # Shorter, more efficient prompt
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    prompt = f"""OGGI: {current_date} {current_time}
DOMANI: {tomorrow}

Estrai JSON evento:
{{
  "event_name": "",
  "event_date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "description": "(opzionale: dettagli aggiuntivi sull'evento)",
  "colorId": ""
}}

COLORI GOOGLE CALENDAR DISPONIBILI:
1: Lavender (Lavanda)
2: Sage (Salvia/Verde chiaro)
3: Grape (Uva/Viola)
4: Flamingo (Rosa)
5: Banana (Giallo)
6: Tangerine (Arancione)
7: Peacock (Turchese/Azzurro)
8: Graphite (Grigio)
9: Blueberry (Blu)
10: Basil (Verde scuro)
11: Tomato (Rosso)

Se l'utente specifica un colore, scegli il colorId più simile tra quelli disponibili.
Esempio: "rosso" -> colorId: "11", "blu" -> colorId: "9", "rosa" -> colorId: "4"

Se l'utente fornisce dettagli aggiuntivi sull'evento (luogo, partecipanti, note), includili nella description.
Esempio: "cena con Marco al ristorante Rossi" -> description: "Luogo: ristorante Rossi"

"oggi"={current_date}, "domani"={tomorrow}, "stasera"=oggi sera
Testo: "{text}"""
    try:
        logging.info(f"Chiamando OpenAI per parsing evento...")
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,  # Reduced tokens
            timeout=15  # 15 second timeout
        )
        content = response.choices[0].message.content
        logging.info(f"✅ Risposta OpenAI ricevuta: {content}")
        data = json.loads(content)
        logging.info(f"📋 Parsed JSON: {data}")

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
    start_time = datetime.now()
    # Log all form data for debugging
    logging.info(f"🔍 Webhook received - Form data: {dict(request.form)}")
    logging.info(f"🔍 Headers: {dict(request.headers)}")

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
                logging.info("Testing OpenAI connection...")
                test_response = openai.ChatCompletion.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": "Rispondi solo con OK"}],
                    max_tokens=5,
                    timeout=30
                )
                test_output = test_response.choices[0].message.content.strip()
                status_msg += f"- OpenAI Test: ✅ ({test_output})"
                logging.info(f"OpenAI test successful: {test_output}")
            except Exception as e:
                error_msg = str(e)
                logging.error(f"OpenAI test failed: {e}")
                if "Connection" in error_msg or "connection" in error_msg:
                    error_detail = "Connection error"
                elif "401" in error_msg or "Unauthorized" in error_msg:
                    error_detail = "API key invalid"
                elif "429" in error_msg:
                    error_detail = "Rate limit"
                else:
                    error_detail = error_msg[:30] if len(error_msg) > 30 else error_msg
                status_msg += f"- OpenAI Test: ❌ ({error_detail})"

        msg.body(status_msg)
        logging.info(f"📤 Invio risposta a WhatsApp: {status_msg}")
        response_str = str(resp)
        logging.info(f"📋 TwiML response: {response_str}")
        return response_str

    if calendar_service is None:
        msg.body("❌ Calendar non disponibile")
        return str(resp)

    # Check elapsed time before heavy operations
    elapsed = (datetime.now() - start_time).total_seconds()
    if elapsed > 25:  # Close to 30s timeout
        msg.body("⏱️ Operazione in corso, riprova tra poco...")
        return str(resp)

    try:
        # Usa OpenAI per estrarre l'evento
        event_data = parse_event_with_openai(incoming_msg)
        if not event_data or not event_data.get("event_name"):
            msg.body("❌ Non sono riuscito a capire il nome dell'evento. Prova a scrivere: 'Aggiungi cena con Marco domani alle 20:00'")
            return str(resp)
    except Exception as e:
        logging.error(f"❌ Error parsing with OpenAI: {e}")
        msg.body("❌ Errore nell'elaborazione. Riprova.")
        return str(resp)

    try:
        logging.info(f"🔍 Parsing date/time from event_data: {event_data}")

        # Parse dates with detailed logging
        try:
            start_dt = datetime.strptime(f"{event_data['event_date']} {event_data['start_time']}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{event_data['event_date']} {event_data['end_time']}", "%Y-%m-%d %H:%M")
            logging.info(f"✅ Date parsing successful: {start_dt} - {end_dt}")
        except Exception as date_error:
            logging.error(f"❌ Date parsing error: {date_error}")
            msg.body(f"❌ Errore nel formato data/ora: {date_error}")
            return str(resp)

        # Prepare event description
        description = event_data.get("description", "").strip()
        if not description:
            description = "Evento creato tramite WhatsApp"

        event_body = {
            "summary": event_data["event_name"],
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Rome"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Rome"},
        }

        # Aggiungi colore: usa quello specificato o azzurro come default
        if event_data.get("colorId"):
            event_body["colorId"] = event_data["colorId"]
            logging.info(f"🎨 Color ID set: {event_data['colorId']}")
        else:
            # Default: azzurro/turchese (colorId 7)
            event_body["colorId"] = "7"
            logging.info(f"🎨 Default color set: 7 (Azzurro/Turchese)")

        logging.info(f"🔍 Event body created: {event_body}")

        # Check timeout before calendar operation
        elapsed = (datetime.now() - start_time).total_seconds()
        logging.info(f"⏱️ Elapsed time before calendar operation: {elapsed}s")
        if elapsed > 25:
            msg.body("⏱️ Timeout, evento in elaborazione...")
            return str(resp)

        # Debug: Lista calendari disponibili
        logging.info("🔍 Listing available calendars...")
        try:
            calendars_result = calendar_service.calendarList().list().execute()
            calendars = calendars_result.get('items', [])
            for cal in calendars:
                logging.info(f"📅 Calendar: {cal.get('id')} - {cal.get('summary')} - primary: {cal.get('primary', False)}")
        except Exception as cal_error:
            logging.error(f"❌ Error listing calendars: {cal_error}")

        # Use your specific calendar ID
        your_calendar_id = 'santinifederico06@gmail.com'

        logging.info(f"📅 Starting calendar insertion on: {your_calendar_id}")
        result = calendar_service.events().insert(
            calendarId=your_calendar_id,
            body=event_body
        ).execute()

        logging.info(f"✅ Calendar insert successful!")
        logging.info(f"📋 Full result: {result}")
        logging.info(f"🔗 Event ID: {result.get('id')}")
        logging.info(f"🔗 Event URL: {result.get('htmlLink')}")
        logging.info(f"📅 Event status: {result.get('status')}")

        formatted_date = start_dt.strftime("%d/%m/%Y alle %H:%M")
        formatted_end = end_dt.strftime("%H:%M")

        # Aggiungi info colore nella risposta
        color_names = {
            '1': 'Lavanda', '2': 'Salvia', '3': 'Viola', '4': 'Rosa',
            '5': 'Giallo', '6': 'Arancione', '7': 'Turchese', '8': 'Grigio',
            '9': 'Blu', '10': 'Verde', '11': 'Rosso'
        }
        # Usa il colore specificato o il default (7 - Turchese)
        color_id = event_data.get("colorId", "7")
        color_name = color_names.get(color_id, 'Turchese')
        color_info = f"\n🎨 Colore: {color_name}"

        # Build response without ID
        response_text = f"✅ Evento '{event_data['event_name']}' creato!\n📅 {formatted_date} - {formatted_end}{color_info}"

        # Add description if present
        if description and description != "Evento creato tramite WhatsApp":
            response_text += f"\n📝 {description}"
        logging.info(f"📤 Sending response: {response_text}")
        msg.body(response_text)

    except Exception as e:
        logging.error("❌ Errore Calendar insert: %s", e)
        logging.error(f"❌ Calendar error type: {type(e).__name__}")
        logging.error(f"❌ Calendar error details: {str(e)}")

        # More specific error messages
        if "403" in str(e):
            error_msg = "Permessi calendario insufficienti"
        elif "404" in str(e):
            error_msg = "Calendario non trovato"
        elif "timeout" in str(e).lower():
            error_msg = "Timeout connessione calendario"
        else:
            error_msg = f"Errore: {str(e)[:50]}"

        logging.error(f"📤 Sending error response: {error_msg}")
        msg.body(f"❌ {error_msg}")

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
    logging.info(f"Environment variables:")
    logging.info(f"  - PORT: {port}")
    logging.info(f"  - OPENAI_MODEL: {OPENAI_MODEL}")
    logging.info(f"  - OPENAI_API_KEY present: {bool(OPENAI_API_KEY)}")
    app.run(host="0.0.0.0", port=port)