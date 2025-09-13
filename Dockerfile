FROM python:3.11-slim

WORKDIR /app

# Copia requirements
COPY requirements.txt .

# Installa le dipendenze
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice
COPY . .

# Porta di default
ENV PORT=8080

# Avvio con gunicorn (produzione)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app", "--workers", "2", "--timeout", "120"]
