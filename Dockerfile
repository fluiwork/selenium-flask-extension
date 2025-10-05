# =============================
# 🔹 BASE: Python + Chrome + Selenium + Flask
# =============================
FROM python:3.11-slim

# =============================
# 🔹 Configuración inicial
# =============================
ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# =============================
# 🔹 Instalar dependencias del sistema
# =============================
RUN apt-get update && apt-get install -y \
    wget unzip curl gnupg xvfb \
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 libatk1.0-0 \
    libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libnspr4 libnss3 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxrandr2 xdg-utils libu2f-udev \
    libgbm1 libgtk-3-0 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# =============================
# 🔹 Instalar Google Chrome estable
# =============================
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# =============================
# 🔹 Instalar ChromeDriver estable (fix para Chrome 141)
# =============================
RUN DRIVER_VERSION=140.0.7247.0 && \
    echo "Instalando ChromeDriver versión $DRIVER_VERSION" && \
    wget -q -O /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip" && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/ && \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver.zip /usr/local/bin/chromedriver-linux64

# =============================
# 🔹 Instalar dependencias Python
# =============================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =============================
# 🔹 Copiar el código de la app
# =============================
COPY . .

# =============================
# 🔹 Variables y permisos
# =============================
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# =============================
# 🔹 Crear carpeta para perfil de Chrome con extensión
# =============================
# Copia tu perfil exportado desde Linux (con extensión incluida)
# Ejemplo: lo colocas en tu proyecto en /app/config/profile_linux/
COPY config/profile_linux /root/.config/google-chrome/Default/

# Ajustar permisos
RUN chmod -R 777 /root/.config/google-chrome

# =============================
# 🔹 Puerto Flask
# =============================
EXPOSE 5000

# =============================
# 🔹 Comando de ejecución
# =============================
CMD ["python", "selenium_flask_app.py"]
