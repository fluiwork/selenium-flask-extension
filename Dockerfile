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

# 🔍 Paso de depuración: mostrar requirements.txt
RUN echo "=== CONTENIDO DE requirements.txt ===" && cat requirements.txt

# 🔍 Instalación con salida detallada
RUN pip install --no-cache-dir -v -r requirements.txt

# Expone el puerto del servidor Flask
EXPOSE 5000

# Comando de inicio
CMD xvfb-run -a python3 selenium_flask_app.py
