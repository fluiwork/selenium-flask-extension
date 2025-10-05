# Imagen base con Python y Chrome
FROM debian:bookworm-slim

# Instala dependencias del sistema
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    xvfb xauth \
    wget unzip gnupg \
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Copia tu aplicación
WORKDIR /app
COPY . /app

# Crea y usa un entorno virtual para instalar dependencias
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN echo "=== CONTENIDO DE requirements.txt ===" && cat requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Expone el puerto del servidor Flask
EXPOSE 5000

# Comando de inicio
CMD xvfb-run -a python3 selenium_flask_app.py
