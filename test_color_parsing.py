import json
import openai
import os
from datetime import datetime, timedelta

# Set your OpenAI API key
openai.api_key = os.environ.get("OPENAI_API_KEY")

def test_color_parsing(text):
    """Test the color parsing functionality"""
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    prompt = f"""OGGI: {current_date} {current_time}
DOMANI: {tomorrow}

Estrai JSON evento:
{{
  "event_name": "",
  "event_date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "description": "",
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

"oggi"={current_date}, "domani"={tomorrow}, "stasera"=oggi sera
Testo: "{text}"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        return data
    except Exception as e:
        print(f"Error: {e}")
        return None

# Test examples
test_cases = [
    "Aggiungi l'esame domani alle 14:00 in rosso",
    "Crea un appuntamento dal dentista oggi alle 16:30 in blu",
    "Riunione con il team domani alle 10:00 in verde",
    "Cena con amici stasera alle 20:00 in giallo",
    "Meeting importante domani alle 9:00 in viola",
    "Presentazione progetto oggi alle 15:00 in arancione",
    "Appuntamento medico domani alle 11:30 in grigio",
    "Lezione di yoga oggi alle 18:00 in turchese",
    "Call con cliente domani alle 14:30 in rosa",
    "Pranzo di lavoro oggi alle 13:00 in azzurro"
]

print("Testing Color Parsing for Google Calendar Events")
print("=" * 50)

for test in test_cases:
    print(f"\nTest: {test}")
    result = test_color_parsing(test)
    if result:
        print(f"Event: {result.get('event_name')}")
        print(f"Date: {result.get('event_date')} at {result.get('start_time')}")
        print(f"ColorId: {result.get('colorId')}")
        color_map = {
            '1': 'Lavender', '2': 'Sage', '3': 'Grape', '4': 'Flamingo',
            '5': 'Banana', '6': 'Tangerine', '7': 'Peacock', '8': 'Graphite',
            '9': 'Blueberry', '10': 'Basil', '11': 'Tomato'
        }
        if result.get('colorId'):
            print(f"Color Name: {color_map.get(result['colorId'], 'Unknown')}")
    print("-" * 30)