# Base image Python 3.13 slim
FROM python:3.13-slim

# ⚡ Instalar dependencias del sistema y xvfb
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    xvfb \
    xauth \
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
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# ⚡ Instalar Google Chrome estable
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
 && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

# ⚡ Crear directorio de la app
WORKDIR /app

# ⚡ Copiar requirements
COPY requirements.txt .

# ⚡ Instalar dependencias Python
RUN pip install --no-cache-dir -r requirements.txt

# ⚡ Copiar el resto de la app
COPY . .

# ⚡ Exponer el puerto (Render asigna PORT automáticamente)
ENV PORT=10000
EXPOSE $PORT

# ⚡ Comando para Render
CMD ["xvfb-run", "-a", "python3", "selenium_flask_app.py"]
