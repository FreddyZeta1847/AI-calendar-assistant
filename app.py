import os
import logging
import traceback
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.cloud import dialogflow_v2 as dialogflow
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.api_core.client_options import ClientOptions
from datetime import datetime, timedelta
from google.auth import default
from google.protobuf.json_format import MessageToDict

# --- Logging setup ---
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- Config ---
DIALOGFLOW_PROJECT_ID = "calendar-assistantai"
DIALOGFLOW_LANGUAGE_CODE = "it"
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

# Prova prima con le credenziali di default, poi con il file
try:
    # Metodo 1: Credenziali di default (raccomandato su Cloud Run)
    logging.info("🔑 Tentativo con credenziali di default...")
    default_credentials, project_id = default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    
    # Se il project ID è diverso, usa quello rilevato
    if project_id and project_id != DIALOGFLOW_PROJECT_ID:
        logging.info("📋 Project ID rilevato: %s (invece di %s)", project_id, DIALOGFLOW_PROJECT_ID)
        DIALOGFLOW_PROJECT_ID = project_id
    
    # Dialogflow client con credenziali di default
    session_client = dialogflow.SessionsClient(credentials=default_credentials)
    
    # Calendar con credenziali specifiche dal file
    CREDS_PATH = "/secrets/GOOGLE_APPLICATION_CREDENTIALS.json"
    calendar_creds = service_account.Credentials.from_service_account_file(
        CREDS_PATH, scopes=CALENDAR_SCOPES
    )
    calendar_service = build('calendar', 'v3', credentials=calendar_creds)
    
    logging.info("✅ Autenticazione completata - Project: %s", DIALOGFLOW_PROJECT_ID)
    
except Exception as e:
    logging.error("❌ Errore nell'autenticazione: %s", str(e))
    # Fallback al metodo precedente
    try:
        logging.info("🔄 Fallback al metodo con file...")
        CREDS_PATH = "/secrets/GOOGLE_APPLICATION_CREDENTIALS.json"
        
        dialogflow_creds = service_account.Credentials.from_service_account_file(
            CREDS_PATH, scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        calendar_creds = service_account.Credentials.from_service_account_file(
            CREDS_PATH, scopes=CALENDAR_SCOPES
        )
        
        session_client = dialogflow.SessionsClient(credentials=dialogflow_creds)
        calendar_service = build('calendar', 'v3', credentials=calendar_creds)
        
        logging.info("✅ Autenticazione fallback completata")
        
    except Exception as e2:
        logging.error("❌ Errore anche nel fallback: %s", str(e2))
        session_client = None
        calendar_service = None


def extract_parameter_value(param):
    """Estrai il valore da un parametro Dialogflow, gestendo diversi tipi"""
    if param is None:
        return None
    
    # Se è una stringa vuota
    if isinstance(param, str) and not param.strip():
        return None
    
    # Se è una lista, prendi il primo elemento
    if isinstance(param, list):
        return param[0] if param else None
    
    # Se è un oggetto protobuf MapComposite, convertilo in dict
    if hasattr(param, 'items') or str(type(param)) == "<class 'proto.marshal.collections.maps.MapComposite'>":
        try:
            # Converte l'oggetto protobuf in dizionario
            param_dict = dict(param) if hasattr(param, 'items') else MessageToDict(param)
            logging.info("🔍 MapComposite convertito: %s", param_dict)
            
            # Cerca campi comuni per data/ora
            for key in ['dateTime', 'date_time', 'startDateTime', 'time']:
                if key in param_dict:
                    return param_dict[key]
            
            # Se non trova campi specifici, prova a restituire il primo valore
            if param_dict:
                return list(param_dict.values())[0]
                
        except Exception as e:
            logging.error("❌ Errore conversione MapComposite: %s", str(e))
            return None
    
    return param


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    try:
        incoming_msg = request.form.get('Body')
        resp = MessagingResponse()
        msg = resp.message()

        logging.info("📩 Messaggio in arrivo: %s", incoming_msg)

        if not incoming_msg:
            msg.body("❌ Messaggio vuoto ricevuto")
            return str(resp)

        # Test di base
        if "ciao" in incoming_msg.lower():
            msg.body("Ciao 👋! Sono attivo su Cloud Run con Dialogflow!")
            return str(resp)

        # Test specifico per debug
        if "test" in incoming_msg.lower():
            status_msg = f"🔧 Status:\n"
            status_msg += f"- Dialogflow: {'✅' if session_client else '❌'}\n"
            status_msg += f"- Calendar: {'✅' if calendar_service else '❌'}\n"
            status_msg += f"- Project: {DIALOGFLOW_PROJECT_ID}"
            msg.body(status_msg)
            return str(resp)

        # Verifica servizi
        if session_client is None:
            msg.body("❌ Dialogflow non disponibile. Scrivi 'test' per debug.")
            return str(resp)

        if calendar_service is None:
            msg.body("❌ Calendar non disponibile.")
            return str(resp)

        # --- Dialogflow ---
        try:
            session_id = request.form.get('From', 'default-session')
            session = session_client.session_path(DIALOGFLOW_PROJECT_ID, session_id)

            text_input = dialogflow.TextInput(
                text=incoming_msg, 
                language_code=DIALOGFLOW_LANGUAGE_CODE
            )
            query_input = dialogflow.QueryInput(text=text_input)
            
            logging.info("🔄 Invio a Dialogflow - Progetto: %s, Sessione: %s", 
                        DIALOGFLOW_PROJECT_ID, session_id[-10:])
            
            response = session_client.detect_intent(session=session, query_input=query_input)

            logging.info("🎯 Intent: %s (confidence: %s)", 
                        response.query_result.intent.display_name,
                        response.query_result.intent_detection_confidence)
            
            fulfillment_text = response.query_result.fulfillment_text
            raw_parameters = dict(response.query_result.parameters)
            
            logging.info("📊 Parametri RAW: %s", raw_parameters)
            logging.info("💬 Risposta Dialogflow: %s", fulfillment_text)
            
        except Exception as e:
            logging.error("❌ Errore Dialogflow: %s", str(e), exc_info=True)
            msg.body(f"❌ Errore Dialogflow: {str(e)[:100]}")
            return str(resp)

        # --- Processamento parametri per eventi ---
        # Estrai parametri usando la nuova funzione
        event_title = extract_parameter_value(raw_parameters.get('event-name')) or \
                      extract_parameter_value(raw_parameters.get('evento')) or \
                      extract_parameter_value(raw_parameters.get('attivita')) or \
                      extract_parameter_value(raw_parameters.get('any'))
        
        # Cerca date-time in diversi campi
        start_time_raw = (raw_parameters.get('date-time') or 
                        raw_parameters.get('event-start-time') or 
                        raw_parameters.get('start-time') or 
                        raw_parameters.get('data-ora'))
        
        end_time_raw = (raw_parameters.get('end-time') or 
                        raw_parameters.get('event-end-time'))
        
        # Estrai i valori
        start_time_str = extract_parameter_value(start_time_raw)
        end_time_str = extract_parameter_value(end_time_raw)
        description = extract_parameter_value(raw_parameters.get('event-description', '')) or ''

        logging.info("🔍 Parametri processati:")
        logging.info("   - Evento: %s", event_title)
        logging.info("   - Inizio: %s", start_time_str)
        logging.info("   - Fine: %s", end_time_str)
        logging.info("   - Descrizione: %s", description)

        # Se non ci sono parametri per eventi, restituisci la risposta di Dialogflow
        if not event_title:
            if fulfillment_text:
                msg.body(fulfillment_text)
            else:
                msg.body("❌ Non ho capito il nome dell'evento. Prova: 'Aggiungi calcetto domani alle 15:00'")
            return str(resp)

        # Se non c'è data/ora, prova parsing manuale del testo
        if not start_time_str:
            logging.info("🔄 Tentativo parsing manuale della data/ora...")
            
            # Parsing semplice per casi comuni
            text_lower = incoming_msg.lower()
            
            if "domani" in text_lower:
                tomorrow = datetime.now() + timedelta(days=1)
                
                # Cerca orari nel formato "alle 15" o "dalle 15"
                import re
                time_match = re.search(r'(?:alle|dalle|da)\s*(\d{1,2})(?::(\d{2}))?', text_lower)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2)) if time_match.group(2) else 0
                    start_dt = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    
                    # Cerca ora di fine
                    end_match = re.search(r'(?:alle|fino alle)\s*(\d{1,2})(?::(\d{2}))?', text_lower.split('alle')[1] if 'alle' in text_lower.split('dalle')[1:] else '')
                    if end_match:
                        end_hour = int(end_match.group(1))
                        end_minute = int(end_match.group(2)) if end_match.group(2) else 0
                        end_dt = tomorrow.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
                    else:
                        end_dt = start_dt + timedelta(hours=1)
                    
                    logging.info("✅ Parsing manuale riuscito - Inizio: %s, Fine: %s", start_dt, end_dt)
                else:
                    msg.body("❌ Non riesco a capire l'orario. Specifica l'ora (es: 'domani alle 15:00')")
                    return str(resp)
            else:
                msg.body("❌ Non riesco a capire quando. Prova: 'domani alle 15:00' o specifica una data")
                return str(resp)
        else:
            # --- Parsing date/ora da Dialogflow ---
            try:
                # Gestione formato ISO o stringa
                if isinstance(start_time_str, str):
                    start_time_str = start_time_str.replace('Z', '+00:00')
                    start_dt = datetime.fromisoformat(start_time_str)
                else:
                    start_dt = datetime.fromisoformat(str(start_time_str))
                
                if end_time_str:
                    if isinstance(end_time_str, str):
                        end_time_str = end_time_str.replace('Z', '+00:00')
                        end_dt = datetime.fromisoformat(end_time_str)
                    else:
                        end_dt = datetime.fromisoformat(str(end_time_str))
                else:
                    end_dt = start_dt + timedelta(hours=1)

            except Exception as e:
                logging.error("❌ Errore parsing data: %s", str(e))
                msg.body("❌ Errore nel formato data/ora. Usa: 'domani alle 15:00'")
                return str(resp)

        # --- Creazione evento Calendar ---
        try:
            event_body = {
                'summary': str(event_title),
                'description': str(description) if description else f"Creato tramite WhatsApp: {incoming_msg}",
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Europe/Rome'
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Europe/Rome'
                },
            }
            
            logging.info("📅 Creazione evento: %s", event_body)
            
            result = calendar_service.events().insert(
                calendarId='santinifederico06@gmail.com', 
                body=event_body
            ).execute()

            logging.info("✅ Evento creato: %s", result.get('id'))
            
            formatted_date = start_dt.strftime('%d/%m/%Y alle %H:%M')
            formatted_end = end_dt.strftime('%H:%M')
            msg.body(f"✅ Evento '{event_title}' creato!\n📅 {formatted_date} - {formatted_end}")
            
        except Exception as e:
            logging.error("❌ Errore Calendar: %s", str(e), exc_info=True)
            msg.body(f"❌ Errore creazione evento: {str(e)[:100]}")

        return str(resp)

    except Exception as e:
        logging.error("❌ Errore generale: %s", str(e), exc_info=True)
        resp = MessagingResponse()
        msg = resp.message()
        msg.body("❌ Errore interno del bot")
        return str(resp)


@app.route("/health", methods=['GET'])
def health_check():
    """Health check con info dettagliate"""
    try:
        status = {
            "dialogflow": "✅" if session_client else "❌",
            "calendar": "✅" if calendar_service else "❌",
            "project_id": DIALOGFLOW_PROJECT_ID
        }
        
        # Test rapido Dialogflow
        if session_client:
            try:
                test_session = session_client.session_path(DIALOGFLOW_PROJECT_ID, "health-check")
                status["dialogflow_test"] = "✅"
            except Exception as e:
                status["dialogflow_test"] = f"❌ {str(e)[:50]}"
        
        # Test rapido Calendar
        if calendar_service:
            try:
                calendar_service.calendarList().list(maxResults=1).execute()
                status["calendar_test"] = "✅"
            except Exception as e:
                status["calendar_test"] = f"❌ {str(e)[:50]}"
        
        return f"Health Check: {status}", 200
        
    except Exception as e:
        logging.error("❌ Health check error: %s", str(e))
        return f"Error: {str(e)}", 503


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logging.info("🚀 Avvio server su porta %s", port)
    app.run(host="0.0.0.0", port=port, debug=False)

