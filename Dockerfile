# ==============================
#  BASE: Python + Chrome + Xvfb
# ==============================
FROM python:3.13-slim

ENV PORT=10000
ENV DISPLAY=:99

# ==============================
#  Dependencias del sistema
# ==============================
RUN apt-get update && apt-get install -y \
    wget curl unzip xvfb xauth gnupg ca-certificates \
    libnss3 libxss1 libappindicator3-1 fonts-liberation libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ==============================
#  Instalar Google Chrome estable
# ==============================
RUN wget -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y /tmp/chrome.deb \
 && rm /tmp/chrome.deb

# ==============================
#  Instalar ChromeDriver compatible
# ==============================
RUN set -eux; \
    CHROME_VERSION=$(google-chrome --version | awk '{print $3}') && \
    DRIVER_VERSION=$(echo "$CHROME_VERSION" | cut -d'.' -f1-3) && \
    echo "Detected Chrome version: $CHROME_VERSION (driver $DRIVER_VERSION)" && \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$DRIVER_VERSION/linux64/chromedriver-linux64.zip" || \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$CHROME_VERSION/linux64/chromedriver-linux64.zip" || \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$(echo $DRIVER_VERSION | cut -d'.' -f1)/linux64/chromedriver-linux64.zip"; \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/; \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
    chmod +x /usr/local/bin/chromedriver; \
    rm -rf /tmp/chromedriver.zip /usr/local/bin/chromedriver-linux64

# ==============================
#  App setup
# ==============================
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos TODO el proyecto, incluyendo el perfil y la extensión
COPY . .

# ==============================
#  Chrome Profile
# ==============================
RUN mkdir -p /root/.config/google-chrome/
COPY chrome_profile_copy/Profile2 /root/.config/google-chrome/Default/
RUN chmod -R 755 /root/.config/google-chrome/Default

# ==============================
#  Permisos y entorno
# ==============================
RUN if [ -d "/app/chrome_profile_copy/Profile2" ]; then chmod -R 755 /app/chrome_profile_copy/Profile2; fi

# Variables opcionales para debug
ENV EXT_USER_DATA_DIR=/app/chrome_profile_copy/Profile2

# ==============================
#  Selenium y WebDriver
# ==============================
RUN pip install --no-cache-dir selenium==4.15.0 webdriver-manager==3.8.6 || true

# ==============================
#  Exponer puerto y ejecutar
# ==============================
EXPOSE 10000

CMD ["sh", "-c", "xvfb-run -a python3 selenium_flask_app.py"]
