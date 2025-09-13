FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app", "--timeout", "300"]

#gcloud builds submit --tag gcr.io/calendar-assistantai/ai-calendar-bot
#gcloud run deploy ai-calendar-bot --image gcr.io/calendar-assistantai/ai-calendar-bot --platform managed --region europe-west1 --allow-unauthenticated --update-secrets /secrets/GOOGLE_APPLICATION_CREDENTIALS.json=calendar-bot-key:latest


#OPENAI_API_KEY=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

'''
sto creando un webhook da pushare su google cloud per poter integrare una chat whatsapp twilio con google calendar, ora come ore sto utilizzando dialog flow per la traduzione tra testo e giorno data ore ed evento.
tuttavia penso sia piu efficace attraverso una comunicazione via api con
'''