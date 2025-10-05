# Base image
FROM python:3.13-slim

# Variables de entorno
ENV PORT=10000
ENV DISPLAY=:99

# Instalar dependencias de sistema
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

# Instalar Google Chrome estable
RUN wget -O /tmp/google-chrome-stable_current_amd64.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get update \
 && apt-get install -y /tmp/google-chrome-stable_current_amd64.deb \
 && rm /tmp/google-chrome-stable_current_amd64.deb \
 && rm -rf /var/lib/apt/lists/*

# Descargar ChromeDriver que coincide con Chrome
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d '.' -f1) \
 && echo "Chrome major version: $CHROME_VERSION" \
 && wget -O /tmp/chromedriver_linux64.zip https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/$CHROME_VERSION/linux64/chromedriver-linux64.zip \
 && unzip /tmp/chromedriver_linux64.zip -d /usr/local/bin/ \
 && rm /tmp/chromedriver_linux64.zip \
 && chmod +x /usr/local/bin/chromedriver

# Crear directorio de la app
WORKDIR /app

# Copiar requirements y app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Exponer puerto
EXPOSE $PORT

# Comando de inicio
CMD ["xvfb-run", "-a", "python3", "selenium_flask_app.py"]
