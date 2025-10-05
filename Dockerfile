# Base image
FROM python:3.13-slim

# Variables de entorno para headless y puerto
ENV PORT=10000
ENV DISPLAY=:99

# Instalar dependencias del sistema y Xvfb
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    xvfb \
    xauth \
    gnupg \
    ca-certificates \
    libnss3 \
    libxss1 \
    libappindicator3-1 \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Instalar Google Chrome estable sin apt-key
RUN wget -O /tmp/google-chrome-stable_current_amd64.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get update \
 && apt-get install -y /tmp/google-chrome-stable_current_amd64.deb \
 && rm /tmp/google-chrome-stable_current_amd64.deb \
 && rm -rf /var/lib/apt/lists/*

# Crear directorio de la app
WORKDIR /app

# Copiar requirements y instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar la app
COPY . .

# Exponer puerto
EXPOSE $PORT

# Comando para Render
CMD ["xvfb-run", "-a", "python3", "selenium_flask_app.py"]
