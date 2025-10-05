# ==============================
# BASE IMAGE
# ==============================
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive

# ==============================
# SISTEMA Y DEPENDENCIAS BÁSICAS
# ==============================
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl unzip xvfb xauth gnupg2 ca-certificates apt-transport-https \
    xdg-utils fonts-liberation libnss3 libxss1 libappindicator3-1 libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 procps \
    && rm -rf /var/lib/apt/lists/*

# ==============================
# AÑADIR REPO DE GOOGLE CHROME (maneja dependencias correctamente)
# ==============================
RUN set -eux; \
    # descargar key y guardarla (signed-by)
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg; \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends google-chrome-stable; \
    rm -rf /var/lib/apt/lists/*

# ==============================
# INSTALAR CHROMEDRIVER (compatible con la versión instalada)
# ==============================
# Intentamos detectar la versión de Chrome y descargar chromedriver correspondiente a "chrome-for-testing"
RUN set -eux; \
    CHROME_VERSION="$(google-chrome --version | awk '{print $3}')" || CHROME_VERSION=""; \
    DRIVER_VERSION="$(echo ${CHROME_VERSION} | cut -d'.' -f1-3)"; \
    if [ -n "${DRIVER_VERSION}" ]; then \
        echo "Detected Chrome: ${CHROME_VERSION} -> driver: ${DRIVER_VERSION}"; \
        # intentar descargar chromedriver para esa versión (chrome-for-testing layout)
        wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip" || \
        wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" || \
        wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$(echo ${DRIVER_VERSION} | cut -d'.' -f1)/linux64/chromedriver-linux64.zip" || true; \
    fi; \
    if [ -f /tmp/chromedriver.zip ]; then \
        unzip -q /tmp/chromedriver.zip -d /tmp/chromedriver-unpack; \
        if [ -f /tmp/chromedriver-unpack/chromedriver ]; then \
            mv /tmp/chromedriver-unpack/chromedriver /usr/local/bin/chromedriver; \
            chmod +x /usr/local/bin/chromedriver; \
        fi; \
        rm -rf /tmp/chromedriver.zip /tmp/chromedriver-unpack; \
    else \
        echo "No chromedriver zip downloaded; attempting apt-get install chromium-driver as fallback"; \
        apt-get update && apt-get install -y --no-install-recommends chromium-driver || true; \
        rm -rf /var/lib/apt/lists/*; \
    fi

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
# MANEJO ROBUSTO DEL PERFIL DE CHROME
# ==============================
COPY chrome_profile_copy/ /tmp/chrome_profile_copy/

RUN mkdir -p /root/.config/google-chrome/Default && \
    set -e; \
    if [ -d "/tmp/chrome_profile_copy/profile_linux" ]; then \
        echo "Usando /tmp/chrome_profile_copy/profile_linux"; \
        cp -a "/tmp/chrome_profile_copy/profile_linux/." /root/.config/google-chrome/Default/; \
    elif [ -d "/tmp/chrome_profile_copy/profile 1" ]; then \
        echo "Usando /tmp/chrome_profile_copy/profile 1"; \
        cp -a "/tmp/chrome_profile_copy/profile 1/." /root/.config/google-chrome/Default/; \
    elif [ -d "/tmp/chrome_profile_copy/Profile2" ]; then \
        echo "Usando /tmp/chrome_profile_copy/Profile2"; \
        cp -a "/tmp/chrome_profile_copy/Profile2/." /root/.config/google-chrome/Default/; \
    elif [ -d "/tmp/chrome_profile_copy/Profile 2" ]; then \
        echo "Usando /tmp/chrome_profile_copy/Profile 2"; \
        cp -a "/tmp/chrome_profile_copy/Profile 2/." /root/.config/google-chrome/Default/; \
    elif [ -d "/tmp/chrome_profile_copy/Profile 1" ]; then \
        echo "Usando /tmp/chrome_profile_copy/Profile 1"; \
        cp -a "/tmp/chrome_profile_copy/Profile 1/." /root/.config/google-chrome/Default/; \
    else \
        echo "No se encontró un subprofile identificado; copiando todo el contenido de chrome_profile_copy"; \
        cp -a /tmp/chrome_profile_copy/. /root/.config/google-chrome/Default/; \
    fi && \
    chmod -R 755 /root/.config/google-chrome/Default && \
    rm -rf /tmp/chrome_profile_copy

# ==============================
# PUERTO Y COMANDO
# ==============================
EXPOSE 5000
CMD ["python", "selenium_flask_app.py"]
