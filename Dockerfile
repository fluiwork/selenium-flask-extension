# Base image
FROM python:3.13-slim

# Variables de entorno
ENV PORT=10000
ENV DISPLAY=:99

# Dependencias del sistema + Xvfb
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
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar Google Chrome estable (última versión estable)
RUN wget -O /tmp/google-chrome-stable_current_amd64.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get update \
 && apt-get install -y /tmp/google-chrome-stable_current_amd64.deb \
 && rm /tmp/google-chrome-stable_current_amd64.deb \
 && rm -rf /var/lib/apt/lists/*

# Instalar ChromeDriver compatible con la versión instalada de Chrome
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}') && \
    DRIVER_VERSION=$(echo $CHROME_VERSION | cut -d'.' -f1-3) && \
    echo "Detected Chrome version: $CHROME_VERSION (driver $DRIVER_VERSION)" && \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip" && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/ && \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver.zip /usr/local/bin/chromedriver-linux64

# Crear directorio de la app
WORKDIR /app

# Copiar requirements (si lo usas) e instalar pip deps
COPY requirements.txt . 
RUN pip install --no-cache-dir -r requirements.txt

# Copiar la aplicación al contenedor (incluye ./extensions/ y ./chrome_profile_copy si las añadiste)
COPY . .

# Variables por defecto para extensión / profile dentro del contenedor
ENV EXT_PATH=/app/extensions/myext
ENV EXT_USER_DATA_DIR=/app/chrome_profile_copy

# Ajustar permisos de assets si existen (evita problemas de lectura)
RUN if [ -d "$EXT_PATH" ]; then chmod -R 755 "$EXT_PATH"; fi
RUN if [ -d "$EXT_USER_DATA_DIR" ]; then chmod -R 755 "$EXT_USER_DATA_DIR"; fi

# Instalar Selenium + webdriver-manager actualizado (4.x compatible con Chrome 141+)
RUN pip install --no-cache-dir selenium==4.21.0 webdriver-manager==4.0.1

# Exponer puerto para Render
EXPOSE 10000

# Comando de inicio usando Xvfb
CMD ["sh", "-c", "xvfb-run -a python3 selenium_flask_app.py"]
