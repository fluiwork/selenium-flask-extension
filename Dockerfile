# ==============================
# BASE IMAGE
# ==============================
FROM python:3.13-slim

# ==============================
# SISTEMA Y DEPENDENCIAS
# ==============================
RUN apt-get update && apt-get install -y \
    wget curl unzip xvfb xauth gnupg ca-certificates \
    libnss3 libxss1 libappindicator3-1 fonts-liberation libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ==============================
# INSTALAR GOOGLE CHROME
# ==============================
RUN wget -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb

# ==============================
# INSTALAR CHROMEDRIVER COMPATIBLE
# ==============================
RUN set -eux; \
    CHROME_VERSION=$(google-chrome --version | awk '{print $3}'); \
    DRIVER_VERSION=$(echo "$CHROME_VERSION" | cut -d'.' -f1-3); \
    echo "Detected Chrome version: $CHROME_VERSION (driver $DRIVER_VERSION)"; \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$DRIVER_VERSION/linux64/chromedriver-linux64.zip" || \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$CHROME_VERSION/linux64/chromedriver-linux64.zip" || \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$(echo $DRIVER_VERSION | cut -d'.' -f1)/linux64/chromedriver-linux64.zip"; \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/; \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
    chmod +x /usr/local/bin/chromedriver; \
    rm -rf /tmp/chromedriver.zip /usr/local/bin/chromedriver-linux64

# ==============================
# TRABAJO Y REQUERIMIENTOS
# ==============================
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ==============================
# COPIAR CÓDIGO FUENTE
# ==============================
COPY . .

# ==============================
# COPIAR PERFIL DE CHROME
# ==============================
RUN mkdir -p /root/.config/google-chrome/
COPY chrome_profile_copy/Profile2 /root/.config/google-chrome/Default/
RUN chmod -R 755 /root/.config/google-chrome/Default

# ==============================
# PUERTO Y COMANDO
# ==============================
EXPOSE 5000
CMD ["python", "selenium_flask_app.py"]
