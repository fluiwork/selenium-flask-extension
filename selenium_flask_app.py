# selenium_flask_app.py
"""
Selenium + Flask script (modificado):
 - Login usando iframe 'loginunico' (priorizado).
 - Checkbox de términos automático (fast)
 - Debug reducido: solo diagnóstico relativo a la carga del profile/extension
 - Se removió BASE_URL a petición del usuario.
 - Xvfb para headless en producción
 - Logs mejorados (INFO para estados principales)
"""

import os
import time
import json
import traceback
import logging
import sys
import re
import random
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, render_template_string, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains

# Optional: use .env in development
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# -------------------------
# Logging (INFO para estados principales) - MEJORADO
# -------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Silenciar módulos muy verbosos para dejar solo ERROR/CRITICAL
for name in ("selenium", "urllib3", "webdrivermanager", "webdriver_manager", "werkzeug", "http.client", "chardet"):
    try:
        logging.getLogger(name).setLevel(logging.ERROR)
        logging.getLogger(name).propagate = False
    except Exception:
        pass

app = Flask(__name__)

# -------------------------
# CONFIG (ajusta aquí)
# -------------------------
VISIBLE = False   # <-- CAMBIADO A False para headless con Xvfb
LOGIN_URL = "https://clientes.celsia.com/clientes/login"
TARGET_URL = "https://clientes.celsia.com/clientes/paga-tus-facturas"

# ** REEMPLAZA LOS SELECTORES POR LOS CORRECTOS (usa DevTools -> Copy selector / Copy XPath) **
USERNAME_SELECTOR = '//*[@id="root"]/div/div[2]/div/div/span/div/div/div/form/div[1]/div/input'
PASSWORD_SELECTOR = '//*[@id="root"]/div/div[2]/div/div/span/div/div/div/form/div[2]/div/input'
SUBMIT_SELECTOR = 'button[type="submit"]'
POST_LOGIN_CHECK_SELECTOR = 'app-request-invoice'  # Elemento post-login

INPUT_SELECTOR = '#nicABuscar'
TERMS_CHECKBOX_SELECTOR = '//*[@id="mat-checkbox-1"]/label'
TERMS_CHECKBOX_INPUT = '//*[@id="mat-checkbox-1-input"]'
TERMS_ACCEPT_BUTTON_SELECTOR = '//*[@id="buscarCodigoCuenta"]'
BUTTON_SELECTOR = '//*[@id="buscarCodigoCuenta"]'
RESULT_SELECTOR = '//*[@id="menu-content"]/app-request-invoice/ion-content/ion-grid/ion-row/ion-row[3]/div/div[1]'

COOKIES_FILE = Path("session_cookies.json")

PAGE_LOAD_TIMEOUT = 40
ELEMENT_WAIT_TIMEOUT = 30  # Aumentado para dar más tiempo

# ----- BANDERAS -----
MANUAL_TERMS = False  # Checkbox automático
MANUAL_INTERACTION = False  # Botón automático

MANUAL_WAIT_TIMEOUT = 300  # 5 minutos por defecto

# -------------------------
# UI simple para probar
# -------------------------
INDEX_HTML = '''
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prueba Selenium (debug visible)</title>
<style>
  body{font-family:Arial, sans-serif;padding:20px}
  .overlay{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);color:white;font-size:18px;z-index:1000}
  .hidden{display:none}
  .card{max-width:700px;margin:20px auto;background:#f7f7f7;padding:20px;border-radius:8px}
  #resultCard{display:none;margin-top:20px}
</style>
</head>
<body>
  <div class="card">
    <h2>Página 1</h2>
    <p>Ingresa un valor y presiona "Procesar". El navegador Selenium se abrirá visible (para debugging).</p>
    <form id="myForm">
      <label for="valor">Valor:</label><br>
      <input id="valor" name="valor" required style="width:100%;padding:8px;margin-top:8px"><br><br>
      <button type="submit">Procesar</button>
    </form>

    <div id="resultCard">
      <h3>Resultado (Página 2)</h3>
      <div id="resultContent"></div>
    </div>
  </div>

  <div id="overlay" class="overlay hidden">Cargando... por favor espera</div>

  <script>
    const form = document.getElementById('myForm');
    const overlay = document.getElementById('overlay');
    const resultCard = document.getElementById('resultCard');
    const resultContent = document.getElementById('resultContent');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const valor = document.getElementById('valor').value.trim();
      if (!valor) return alert('Ingresa un valor');

      overlay.classList.remove('hidden');
      resultCard.style.display = 'none';

      try {
        const resp = await fetch('/process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ valor })
        });
        const data = await resp.json();
        if (data.success) {
          overlay.classList.add('hidden');
          resultContent.textContent = data.result || '(sin resultado)';
          resultCard.style.display = 'block';
        } else {
          overlay.classList.add('hidden');
          alert('Error: ' + (data.error || 'desconocido'));
        }
      } catch (err) {
        overlay.classList.add('hidden');
        alert('Error en la solicitud: ' + err.message);
      }
    });
  </script>
</body>
</html>
'''

# -------------------------
# Helpers de cookies y login
# -------------------------
def save_cookies_to_file(driver, path=COOKIES_FILE):
    try:
        cookies = driver.get_cookies()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
    except Exception:
        logger.exception("[!] Error guardando cookies:")

def load_cookies_from_file(path=COOKIES_FILE):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("[!] Error cargando cookies:")
        return None


def resolve_locator(selector: str):
    """
    Detecta si 'selector' es XPath o CSS y devuelve (By, selector_limpio).
    """
    if not selector:
        raise ValueError("Selector vacío en resolve_locator()")
    s = selector.strip()
    low = s.lower()
    if low.startswith('xpath='):
        return (By.XPATH, s.split('=', 1)[1])
    if s.startswith('/') or s.startswith('//') or s.startswith('.//') or s.startswith('('):
        return (By.XPATH, s)
    return (By.CSS_SELECTOR, s)

# -------------------------
# Funciones login (solo flujo iframe 'loginunico')
# -------------------------
def perform_login(driver,
                  login_url,
                  username_selector,
                  password_selector,
                  submit_selector,
                  post_login_check_selector,
                  wait_timeout=ELEMENT_WAIT_TIMEOUT):
    logger.info("[*] Realizando login (robusto, flujo único - iframe 'loginunico') ...")
    try:
        try:
            driver.get(login_url)
        except Exception:
            logger.exception("[!] driver.get(login_url) fallo, prosigo con intentos.")

        try:
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            logger.debug("[*] Advertencia: readyState no llegó a 'complete' en 10s (continúa)")

        frames = driver.find_elements(By.TAG_NAME, "iframe")
        logger.info("[*] Iframes detectados: %d", len(frames))
        chosen_frame = None
        for idx, fr in enumerate(frames):
            try:
                src = fr.get_attribute("src") or ""
                low = src.lower()
                if "loginunico" in low or "azurewebsites" in low or "loginunico-prd" in low:
                    chosen_frame = fr
                    logger.info("[*] Elegido iframe[%d] para login (src=%s)", idx, src)
                    break
            except Exception:
                pass

        if not chosen_frame:
            for idx, fr in enumerate(frames):
                try:
                    driver.switch_to.frame(fr)
                    inputs_in_frame = driver.find_elements(By.TAG_NAME, "input")
                    if inputs_in_frame and len(inputs_in_frame) >= 1:
                        chosen_frame = fr
                        logger.info("[*] Elegido iframe[%d] por heurística (tiene inputs).", idx)
                    driver.switch_to.default_content()
                    if chosen_frame:
                        break
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass

        if not chosen_frame:
            logger.error("[ERROR] No se encontró iframe de login adecuado.")
            return False

        try:
            driver.switch_to.frame(chosen_frame)
            logger.info("[*] Dentro del iframe elegido, buscando campos de usuario/clave")

            by_u, sel_u = resolve_locator(username_selector)
            by_p, sel_p = resolve_locator(password_selector)
            user_el = None
            pass_el = None
            try:
                els_u = driver.find_elements(by_u, sel_u)
                if els_u:
                    user_el = els_u[0]
            except Exception:
                pass

            try:
                els_p = driver.find_elements(by_p, sel_p)
                if els_p:
                    pass_el = els_p[0]
            except Exception:
                pass

            if not user_el:
                for cand in ['input[type="text"]', 'input[type="email"]', 'input[id*="user"]', 'input[name*="user"]', 'input[id*="username"]']:
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, cand)
                        if els:
                            user_el = els[0]
                            break
                    except Exception:
                        pass

            if not pass_el:
                for cand in ['input[type="password"]', 'input[id*="pass"]', 'input[name*="pass"]', 'input[id*="password"]']:
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, cand)
                        if els:
                            pass_el = els[0]
                            break
                    except Exception:
                        pass

            if not user_el or not pass_el:
                logger.error("[!] No se localizaron campos de login dentro del iframe seleccionado.")
                driver.switch_to.default_content()
                return False

            USER = os.environ.get("MI_SITIO_USER")
            PASS = os.environ.get("MI_SITIO_PASS")
            if not USER or not PASS:
                logger.error("[!] Credenciales no configuradas en variables de entorno.")
                driver.switch_to.default_content()
                return False

            try:
                user_el.clear()
            except Exception:
                pass
            user_el.send_keys(USER)
            try:
                pass_el.clear()
            except Exception:
                pass
            pass_el.send_keys(PASS)

            submitted = False
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
                if btns:
                    for b in btns:
                        try:
                            driver.execute_script("arguments[0].click();", b)
                            submitted = True
                            break
                        except Exception:
                            pass
                if not submitted:
                    bxp = "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'ingresar') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'iniciar') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'entrar')]"
                    els_b = driver.find_elements(By.XPATH, bxp)
                    if els_b:
                        try:
                            driver.execute_script("arguments[0].click();", els_b[0])
                            submitted = True
                        except Exception:
                            pass
            except Exception:
                logger.exception("[!] Error intentando submit dentro del iframe:")

            driver.switch_to.default_content()

            if not submitted:
                logger.warning("[!] No se pudo pulsar submit dentro del iframe.")
                return False

            try:
                by_check, sel_check = resolve_locator(post_login_check_selector)
                WebDriverWait(driver, wait_timeout).until(EC.presence_of_element_located((by_check, sel_check)))
                logger.info("[+] Login confirmado en document principal tras submit en iframe")
                # --- Navegar inmediatamente al TARGET_URL para evitar quedarse en la página intermedia ---
                try:
                    driver.get(TARGET_URL)
                    WebDriverWait(driver, 6).until(lambda d: d.execute_script("return document.readyState") == "complete")
                    logger.info("[*] Redirigido a TARGET_URL inmediatamente tras login")
                except Exception:
                    logger.info("[*] No se pudo redirigir inmediatamente a TARGET_URL o la carga fue lenta, el flujo continuará normalmente.")
                save_cookies_to_file(driver)
                return True
            except Exception:
                logger.warning("[!] No se detectó post-login tras submit en iframe (posible fallo).")
                return False

        except Exception:
            logger.exception("[!] Error dentro del iframe durante login:")
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            return False

    except Exception:
        logger.exception("[!] perform_login fallo inesperado:")
        return False


def ensure_logged_in(driver,
                     base_url,
                     login_url,
                     username_selector,
                     password_selector,
                     submit_selector,
                     post_login_check_selector,
                     wait_timeout=ELEMENT_WAIT_TIMEOUT):
    # Intentamos abrir la página de login directamente (no se usa BASE_URL ya)
    try:
        driver.get(login_url)
    except Exception:
        pass

    cookies = load_cookies_from_file()
    if cookies:
        logger.info("[*] Cargando cookies desde disco...")
        for c in cookies:
            try:
                if 'name' in c and 'value' in c:
                    try:
                        driver.add_cookie(c)
                    except Exception:
                        c_copy = {k: v for k, v in c.items() if k in ('name', 'value', 'domain', 'path', 'expiry', 'secure', 'httpOnly')}
                        try:
                            driver.add_cookie(c_copy)
                        except Exception:
                            pass
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass
        time.sleep(1)
        try:
            by_check, sel_check = resolve_locator(post_login_check_selector)
            WebDriverWait(driver, 3).until(EC.presence_of_element_located((by_check, sel_check)))
            logger.info("[+] Sesión restaurada con cookies")
            # si sesión válida, navegar de inmediato al TARGET_URL
            try:
                driver.get(TARGET_URL)
                WebDriverWait(driver, 6).until(lambda d: d.execute_script("return document.readyState") == "complete")
                logger.info("[*] Redirigido a TARGET_URL tras restaurar cookies")
            except Exception:
                logger.info("[*] No se pudo redirigir inmediatamente a TARGET_URL tras restaurar cookies.")
            return True
        except Exception:
            logger.info("[*] Cookies no válidas o sesión expirada, se hará login")

    ok = perform_login(driver,
                  login_url,
                  username_selector,
                  password_selector,
                  submit_selector,
                  post_login_check_selector,
                  wait_timeout=wait_timeout)
    if ok:
        return True
    else:
        logger.warning("[!] No se pudo iniciar sesión automáticamente (perform_login devolvió False). El flujo continuará intentando acceder a la página objetivo y reintentando login si es necesario.")
        return False

# -------------------------
# Accept terms (automático - fast)
# -------------------------
def accept_terms_if_present(driver,
                            checkbox_selector=TERMS_CHECKBOX_SELECTOR,
                            checkbox_input_xpath=TERMS_CHECKBOX_INPUT,
                            accept_button_selector=TERMS_ACCEPT_BUTTON_SELECTOR,
                            wait_timeout=2):
    """
    Versión rápida para marcar checkbox normales.
    wait_timeout por defecto reducido a 2s para acelerar el intento.
    """
    logger.info("[*] Buscando checkbox de términos y condiciones (fast mode)...")
    
    try:
        # PRIMERO: Intentar con el selector principal del input
        logger.info(f"[*] Intentando con selector principal: {checkbox_input_xpath}")
        by_input, sel_input = resolve_locator(checkbox_input_xpath)
        
        try:
            checkbox_input = WebDriverWait(driver, max(1, wait_timeout)).until(
                EC.presence_of_element_located((by_input, sel_input))
            )
            # intentar marcar inmediatamente por JS
            try:
                driver.execute_script("if(!arguments[0].checked){arguments[0].click();}", checkbox_input)
            except Exception:
                try:
                    driver.execute_script("arguments[0].checked = true; arguments[0].dispatchEvent(new Event('change'));", checkbox_input)
                except Exception:
                    pass

            # pequeña comprobación inmediata
            try:
                if checkbox_input.is_selected():
                    logger.info("[+] Checkbox marcado (fast path)")
                    return True
            except Exception:
                pass
        except Exception as e:
            logger.info(f"[!] No se pudo encontrar/marcar con selector principal (fast): {e}")

        # SEGUNDO: Intentar con el selector del label/container
        logger.info(f"[*] Intentando con selector del label: {checkbox_selector}")
        by_label, sel_label = resolve_locator(checkbox_selector)
        
        try:
            checkbox_label = WebDriverWait(driver, max(1, wait_timeout)).until(
                EC.element_to_be_clickable((by_label, sel_label))
            )
            try:
                driver.execute_script("arguments[0].click();", checkbox_label)
            except Exception:
                try:
                    checkbox_label.click()
                except Exception:
                    pass
            time.sleep(0.15)
            logger.info("[+] Click realizado en el label del checkbox (fast path)")
            return True
            
        except Exception as e:
            logger.info(f"[!] No se pudo encontrar/marcar con selector del label (fast): {e}")

        # TERCERO: búsqueda rápida de cualquier checkbox visible
        logger.info("[*] Buscando cualquier checkbox en la página (fast fallback)...")
        try:
            checkboxes = driver.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
        except Exception:
            checkboxes = []
        logger.info(f"[*] Se encontraron {len(checkboxes)} checkboxes en total (fast fallback)")
        
        for i, checkbox in enumerate(checkboxes):
            try:
                if checkbox.is_displayed() and checkbox.is_enabled():
                    if not checkbox.is_selected():
                        try:
                            driver.execute_script("arguments[0].click();", checkbox)
                        except Exception:
                            try:
                                checkbox.click()
                            except Exception:
                                pass
                        logger.info(f"[+] Checkbox alternativo {i+1} marcado (fast fallback)")
                        return True
            except Exception as e:
                logger.info(f"[!] Error con checkbox alternativo {i+1}: {e}")
                continue

        logger.error("[!] No se pudo encontrar ni marcar ningún checkbox de términos (fast finish)")
        return False

    except Exception as e:
        logger.exception("[!] Error inesperado en accept_terms_if_present (fast mode):")
        return False

def ensure_checkbox_checked(driver, checkbox_input_xpath=TERMS_CHECKBOX_INPUT, retries=3, delay=1):
    """
    Versión simplificada - solo verifica si está marcado
    """
    for attempt in range(retries):
        try:
            by_i, sel_i = resolve_locator(checkbox_input_xpath)
            checkbox = driver.find_element(by_i, sel_i)
            
            if checkbox.is_selected():
                logger.info(f"[+] Checkbox verificado como marcado (intento {attempt+1})")
                return True
            else:
                logger.info(f"[*] Checkbox aún no marcado (intento {attempt+1})")
                time.sleep(delay)
                
        except Exception as e:
            logger.info(f"[!] Error verificando checkbox (intento {attempt+1}): {e}")
            time.sleep(delay)
    
    logger.error("[!] Checkbox no pudo ser verificado como marcado después de intentos")
    return False

# -------------------------
# Helpers para encontrar/llenar input y flow de búsqueda
# -------------------------
def set_input_value(driver, input_el, value):
    try:
        try:
            input_el.clear()
        except Exception:
            pass
        # Simular escritura humana
        for char in value:
            input_el.send_keys(char)
            time.sleep(random.uniform(0.05, 0.1))
        return True
    except Exception:
        try:
            driver.execute_script("""
                const el = arguments[0];
                const val = arguments[1];
                el.focus();
                el.value = val;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            """, input_el, value)
            return True
        except Exception:
            return False

def find_and_fill_input_with_candidates(driver, valor, wait_timeout):
    candidate_selectors = [
        INPUT_SELECTOR,
        '#mat-input-0',
        'input[placeholder*="Ingresa un código"]',
        'input[placeholder*="Ingresa un código de cuenta"]',
        'input[placeholder*="código de cuenta"]',
        'input[id*="nic"]',
        'input[name*="nic"]',
        'input[type="text"]',
        '//input[contains(@placeholder, "Ingresa un código")]',
        '//input[contains(@placeholder, "Ingresa un código de cuenta")]',
        '//input[contains(@id, "nic")]',
        '//input[contains(@id, "mat-input")]'
    ]
    end_time = time.time() + wait_timeout
    last_exception = None
    while time.time() < end_time:
        try:
            for sel in candidate_selectors:
                try:
                    by, sel_clean = resolve_locator(sel)
                    els = driver.find_elements(by, sel_clean)
                    if els:
                        for el in els:
                            try:
                                if el.is_displayed():
                                    ok = set_input_value(driver, el, valor)
                                    if ok:
                                        logger.info("[+] Valor puesto en input por selector: %s", sel)
                                        return True
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].scrollIntoView(true);", el)
                                    ok = set_input_value(driver, el, valor)
                                    if ok:
                                        logger.info("[+] Valor puesto en input por selector (after scroll): %s", sel)
                                        return True
                                except Exception:
                                    pass
                except Exception as e:
                    last_exception = e
            try:
                all_inputs = driver.find_elements(By.TAG_NAME, "input")
                for inp in all_inputs:
                    try:
                        ph = (inp.get_attribute("placeholder") or "").lower()
                        if "codigo" in ph or "cuenta" in ph or "ingresa" in ph:
                            ok = set_input_value(driver, inp, valor)
                            if ok:
                                logger.info("[+] Valor puesto en input por heurística placeholder")
                                return True
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            last_exception = e
        time.sleep(0.8)
    if last_exception:
        logger.exception("[!] find_and_fill_input_with_candidates: última excepción:")
    return False

# -------------------------
# NUEVO: validación de estructura deseada en el texto extraído
# -------------------------
def has_desired_structure(text):
    """
    Verifica que el texto contenga una estructura similar a:
      Código de cuenta: <digits>
      ...
      Pago total: $<cantidad>
    """
    if not text or not isinstance(text, str):
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    code_idx = None
    pay_idx = None
    for i, l in enumerate(lines):
        if re.search(r'c[oó]digo\s*de\s*cuenta\s*[:\-]?\s*\d+', l, re.I):
            code_idx = i
            break
    if code_idx is None:
        if re.search(r'c[oó]digo\s*de\s*cuenta\s*[:\-]?\s*\d+', text, re.I):
            pass
        else:
            return False
    for j in range(len(lines)-1, -1, -1):
        if re.search(r'Pago\s*total\s*[:\-]?\s*\$?\s*[\d\.,]+', lines[j], re.I):
            pay_idx = j
            break
    if pay_idx is None:
        if re.search(r'Pago\s*total\s*[:\-]?\s*\$?\s*[\d\.,]+', text, re.I):
            pass
        else:
            return False
    if code_idx is not None and pay_idx is not None:
        if pay_idx <= code_idx:
            return False
        middle = lines[code_idx+1:pay_idx]
        if not middle:
            return False
        for m in middle:
            if len(m) > 2 and not re.search(r'c[oó]digo\s*de\s*cuenta|pago\s*total', m, re.I):
                return True
        return False
    m_code = re.search(r'c[oó]digo\s*de\s*cuenta\s*[:\-]?\s*\d+', text, re.I)
    m_pay = re.search(r'Pago\s*total\s*[:\-]?\s*\$?\s*[\d\.,]+', text, re.I)
    if m_code and m_pay:
        if m_pay.start() > m_code.end() + 3:
            between = text[m_code.end():m_pay.start()].strip()
            if len(between) > 2:
                return True
    return False

# -------------------------
# Funcion principal Selenium (integrada)
# -------------------------
def run_selenium_task(valor,
                      target_url=TARGET_URL,
                      input_selector=INPUT_SELECTOR,
                      button_selector=BUTTON_SELECTOR,
                      result_selector=RESULT_SELECTOR,
                      base_url=LOGIN_URL,
                      login_url=LOGIN_URL,
                      username_selector=USERNAME_SELECTOR,
                      password_selector=PASSWORD_SELECTOR,
                      submit_selector=SUBMIT_SELECTOR,
                      post_login_check_selector=POST_LOGIN_CHECK_SELECTOR,
                      wait_timeout=ELEMENT_WAIT_TIMEOUT):
    
    # Iniciar Xvfb para entorno headless - CONFIGURACIÓN MEJORADA
    display = None
    display_var = os.environ.get('DISPLAY')
    if not VISIBLE and not display_var:
        logger.info("Iniciando Xvfb para entorno headless...")
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1920, 1080))
            display.start()
            logger.info("Xvfb iniciado correctamente")
        except Exception as e:
            logger.error(f"Error iniciando Xvfb: {e}")

    chrome_options = Options()

    # --- Use an existing Chrome user-data-dir and Profile 2 ---
    try:
        # -------------------------
        # Configuración de profile / extensión (compatible Windows local + contenedor)
        # -------------------------
        repo_root = Path(__file__).parent.resolve()
        default_profile_rel = repo_root / "chrome_profile_copy"

        # Preferir variable de entorno si está definida
        env_user_data = os.environ.get("EXT_USER_DATA_DIR", "").strip()
        if env_user_data:
            try:
                user_data_dir = Path(env_user_data).expanduser().resolve()
            except Exception:
                user_data_dir = Path(env_user_data)
        else:
            user_data_dir = default_profile_rel

        # Helper: detectar si el contenido del profile parece haber sido creado en Windows
        def is_profile_windows(p: Path) -> bool:
            try:
                if not p or not p.exists():
                    return False
                check_files = []
                for fname in ("Local State", "Preferences", "Bookmarks"):
                    f = p / fname
                    if f.exists():
                        check_files.append(f)
                for sub in ("Default", "Profile 2", "Profile 1"):
                    ss = p / sub
                    if ss.exists():
                        for fname in ("Preferences", "Local State", "Bookmarks"):
                            f = ss / fname
                            if f.exists():
                                check_files.append(f)
                pattern = re.compile(r'[A-Za-z]:\\\\|[A-Za-z]:\\|\\\\\\\\')  # C:\ or \\server
                for cf in check_files:
                    try:
                        text = cf.read_text(encoding='utf-8', errors='ignore')
                        if pattern.search(text):
                            return True
                    except Exception:
                        try:
                            with open(cf, 'rb') as fh:
                                chunk = fh.read(8192)
                                if re.search(b'[A-Za-z]:\\\\|\\\\\\\\', chunk):
                                    return True
                        except Exception:
                            continue
            except Exception:
                pass
            return False

        # Helper: detectar si profile parece Linux (heurística simple)
        def is_profile_linux(p: Path) -> bool:
            try:
                if not p or not p.exists():
                    return False
                # si contiene '/home/' referencias o no contiene patrones Windows
                check_files = []
                for fname in ("Local State", "Preferences", "Bookmarks"):
                    f = p / fname
                    if f.exists():
                        check_files.append(f)
                for cf in check_files:
                    try:
                        text = cf.read_text(encoding='utf-8', errors='ignore')
                        if '/home/' in text or '/.config' in text:
                            return True
                        # no encontrar patrones windows sugiere linux
                        if not re.search(r'[A-Za-z]:\\\\|[A-Za-z]:\\|\\\\\\\\', text):
                            return True
                    except Exception:
                        continue
                # fallback: si hay subfolder Default y Preferences dentro, considerarlo linux-compatible
                if (p / "Default").exists() and (p / "Default" / "Preferences").exists():
                    return True
            except Exception:
                pass
            return False

        # Resolver existencia y permisos
        if not user_data_dir or not Path(user_data_dir).exists():
            logger.warning("[PROFILE] user-data-dir no existe: %s", user_data_dir)
            user_data_dir = None
        else:
            try:
                _ = list(user_data_dir.iterdir())[:1]
            except PermissionError:
                logger.error("[PROFILE] Permisos insuficientes para leer user-data-dir: %s", user_data_dir)
                user_data_dir = None
            except Exception:
                logger.info("[PROFILE] user-data-dir accesible: %s", user_data_dir)

        running_on_windows = (os.name == 'nt')

        # Si existe profile, detectar si fue creado en Windows
        profile_is_windows = False
        if user_data_dir:
            try:
                profile_is_windows = is_profile_windows(Path(user_data_dir))
                if profile_is_windows:
                    logger.warning("[PROFILE] El perfil en %s parece haber sido creado en Windows (heurística detectó rutas Windows).", user_data_dir)
                else:
                    logger.info("[PROFILE] El perfil en %s parece compatible con OS actual.", user_data_dir)
            except Exception:
                logger.exception("[PROFILE] Error inspeccionando el profile for Windows-compatibility:")

        # Si estamos en Linux (Render) y el profile parece Windows, buscar un perfil Linux alternativo en repo
        ext_path = os.environ.get("EXT_PATH", str(repo_root / "extensions" / "myext")).strip()
        if profile_is_windows and not running_on_windows:
            logger.error("[PROFILE] Perfil detectado como Windows pero el host es Linux. Buscando perfil Linux alternativo en repo...")
            linux_candidate = None
            # 1) buscar carpeta chrome_profile_copy_linux
            for cand_name in ("chrome_profile_copy_linux", "chrome_profile_copy_linux2", "chrome_profile_copy"):
                cand = repo_root / cand_name
                if cand.exists() and cand.is_dir() and is_profile_linux(cand):
                    linux_candidate = cand
                    break
            # 2) buscar cualquier carpeta en repo root que parezca profile linux
            if not linux_candidate:
                for entry in repo_root.iterdir():
                    if entry.is_dir() and entry.name.lower().startswith("chrome") and is_profile_linux(entry):
                        linux_candidate = entry
                        break
            # 3) buscar en subcarpeta chrome_profile_copy/* subfolders compatibles
            if not linux_candidate:
                base = repo_root / "chrome_profile_copy"
                if base.exists() and base.is_dir():
                    for sub in base.iterdir():
                        if sub.is_dir() and is_profile_linux(sub):
                            linux_candidate = base
                            break

            if linux_candidate:
                logger.warning("[PROFILE] Encontrado perfil Linux alternativo: %s. Usando ese.", linux_candidate)
                user_data_dir = linux_candidate
            else:
                logger.error("[PROFILE] No se encontró perfil Linux alternativo válido en repo. Haré fallback a cargar extensión unpacked (si existe).")
                user_data_dir = None
                if ext_path and Path(ext_path).is_dir():
                    try:
                        chrome_options.add_argument(f'--load-extension={ext_path}')
                        logger.warning("[PROFILE] Fallback: cargando extension unpacked desde: %s", ext_path)
                    except Exception:
                        logger.exception("[PROFILE] Error añadiendo --load-extension:")
                else:
                    logger.warning("[PROFILE] EXT_PATH no existe (%s). Chrome arrancará con perfil limpio en Render.", ext_path)

        # Si no hicimos fallback y el profile existe, usarlo (primero sin profile-directory)
        if user_data_dir:
            try:
                user_data_dir = Path(user_data_dir).resolve()
                chrome_options.add_argument(f'--user-data-dir={str(user_data_dir)}')
                logger.warning("[PROFILE] Añadido --user-data-dir=%s", user_data_dir)
                # intentar detectar sub-profile (Profile 2, Default, Profile 1)
                chosen_profile = None
                for cand in ("Profile 2", "Profile 1", "Default"):
                    if (user_data_dir / cand).exists():
                        chosen_profile = cand
                        break
                if not chosen_profile:
                    # elegir primer folder de profile plausible
                    for entry in user_data_dir.iterdir():
                        if entry.is_dir() and entry.name.lower() not in ("local state", "system volume information"):
                            chosen_profile = entry.name
                            break
                if chosen_profile:
                    chrome_options.add_argument(f'--profile-directory={chosen_profile}')
                    logger.warning("[PROFILE] Añadido --profile-directory=%s", chosen_profile)
                else:
                    logger.info("[PROFILE] No se encontró subcarpeta de profile; arrancando con user-data-dir sin profile-directory")
            except Exception:
                logger.exception("[PROFILE] Error añadiendo user-data-dir/profile-directory:")
        else:
            # no hay profile válido: si no se añadió ext_path antes, intentar ahora
            if ext_path and Path(ext_path).is_dir():
                try:
                    chrome_options.add_argument(f'--load-extension={ext_path}')
                    logger.warning("[PROFILE] No hay profile; cargando extension unpacked desde: %s", ext_path)
                except Exception:
                    logger.exception("[PROFILE] No se pudo añadir --load-extension:")
            else:
                logger.warning("[PROFILE] No se usará profile ni extension; Chrome arrancará con perfil limpio.")

    except Exception:
        logger.exception("[PROFILE] Error inesperado configurando profile/extension:")

    # Añadido: detach para mantener navegador si se quiere (como en tu snippet)
    chrome_options.add_experimental_option("detach", True)

    # Eliminado: --headless=new ya que usamos Xvfb
    # if not VISIBLE:
    #    chrome_options.add_argument('--headless=new')
    
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # Configuraciones avanzadas para evitar detección
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # NOTE: no se añade '--disable-extensions' para permitir que la extensión instalada en el perfil funcione
    chrome_options.add_argument('--no-first-run')
    chrome_options.add_argument('--no-default-browser-check')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('--allow-running-insecure-content')
    chrome_options.add_argument('--disable-notifications')
    chrome_options.add_argument('--disable-popup-blocking')
    chrome_options.add_argument('--disable-background-timer-throttling')
    chrome_options.add_argument('--disable-renderer-backgrounding')
    chrome_options.add_argument('--disable-backgrounding-occluded-windows')
    chrome_options.add_argument('--disable-features=TranslateUI')
    chrome_options.add_argument('--disable-ipc-flooding-protection')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    chrome_options.add_argument('--window-size=1920,1080')

    # descargar/obtener ruta inicial (puede devolver carpeta o fichero)
    raw_driver_path = ChromeDriverManager().install()
    driver_executable = raw_driver_path

    # si es una carpeta, buscar el ejecutable dentro (nombre exacto 'chromedriver' preferido)
    if os.path.isdir(raw_driver_path):
        for root, _, files in os.walk(raw_driver_path):
            for fname in files:
                if fname == 'chromedriver' or fname.startswith('chromedriver'):
                    candidate = os.path.join(root, fname)
                    # ignorar archivos claramente no binarios (ej: LICENSE, THIRD_PARTY...)
                    if 'license' in fname.lower() or 'third_party' in fname.lower():
                        continue
                    driver_executable = candidate
                    break
            if driver_executable != raw_driver_path:
                break

    # si por alguna razón raw_driver_path no era carpeta pero apunta a un archivo raro,
    # intentar buscar junto al archivo
    if driver_executable == raw_driver_path:
        parent = os.path.dirname(raw_driver_path)
        if os.path.isdir(parent):
            for f in os.listdir(parent):
                if f == 'chromedriver' or f.startswith('chromedriver'):
                    cand = os.path.join(parent, f)
                    if os.path.isfile(cand) and 'third_party' not in f.lower() and 'license' not in f.lower():
                        driver_executable = cand
                        break

    # forzar permisos ejecutables si es posible
    try:
        if os.path.exists(driver_executable):
            os.chmod(driver_executable, 0o755)
    except Exception:
        logger.info("[!] No se pudo cambiar permisos al driver (continuo de todos modos)")

    # Usar Service con log_path para diagnosticar (chromedriver.log)
    logger.info(f"[*] Usando chromedriver en: {driver_executable}")
    service = Service(driver_executable, log_path=str(Path.cwd() / "chromedriver.log"))

    driver = None
    try:
        logger.info("[*] Lanzando Chrome (visible=" + str(VISIBLE) + ") ...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Ejecutar script para eliminar webdriver property (no fatal si falla)
        try:
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception:
            pass
        
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

        # Comprobar qué profile está usando realmente Chrome (intentar leer chrome://version)
        try:
            time.sleep(0.5)
            try:
                driver.get("chrome://version")
                time.sleep(0.7)
                src = driver.page_source or ""
                # comprobación directa contra user_data_dir
                try:
                    if 'user_data_dir' in locals() and user_data_dir and str(user_data_dir) in src:
                        logger.warning("[PROFILE] Chrome arrancó usando el user-data-dir solicitado: %s", user_data_dir)
                    else:
                        logger.warning("[PROFILE] Chrome NO parece usar el user-data-dir solicitado (validar).")
                except Exception:
                    logger.info("[PROFILE] No se pudo confirmar chrome://version content")
            except Exception:
                logger.info("[PROFILE] No se pudo abrir chrome://version (posible bloqueo en entorno headless).")
        except Exception:
            logger.exception("[PROFILE] Error comprobando chrome://version:")

        USER = os.environ.get("MI_SITIO_USER")
        PASS = os.environ.get("MI_SITIO_PASS")
        if not USER or not PASS:
            error_msg = 'No hay credenciales en variables de entorno. Configura MI_SITIO_USER y MI_SITIO_PASS.'
            logger.error("[ERROR] " + error_msg)
            return {'success': False, 'error': error_msg}

        logged = False
        try:
            logged = ensure_logged_in(driver,
                             base_url=base_url,
                             login_url=login_url,
                             username_selector=username_selector,
                             password_selector=password_selector,
                             submit_selector=submit_selector,
                             post_login_check_selector=post_login_check_selector,
                             wait_timeout=wait_timeout)
        except Exception:
            logger.exception("[!] ensure_logged_in threw:")

        # Force navigation to target_url again if not already there (defensive)
        try:
            driver.get(target_url)
        except Exception:
            pass

        logger.info("[*] Navegando a target_url (asegurado tras login intento): %s", target_url)
        try:
            driver.get(target_url)
        except Exception:
            pass

        time.sleep(1.0)
        try:
            current = driver.current_url or ""
        except Exception:
            current = ""
        logger.info("[*] URL actual tras navegación: %s", current)

        if 'login' in current.lower() or 'auth' in current.lower() or (not logged and ('clientes' in current and 'paga-tus-facturas' not in current)):
            logger.info("[*] Detectada redirección a login o no estamos logueados; reintentando login y luego navegando al target_url.")
            try:
                perform_login(driver,
                              login_url,
                              username_selector,
                              password_selector,
                              submit_selector,
                              post_login_check_selector,
                              wait_timeout=wait_timeout)
            except Exception:
                logger.exception("[!] perform_login lanzó excepción en reintento:")
            try:
                driver.get(target_url)
            except Exception:
                pass
            time.sleep(1.0)
            try:
                logger.info("[*] URL actual tras reintento: %s", driver.current_url)
            except Exception:
                pass

        # 3) Esperar y localizar el input objetivo usando selectores alternativos/heurística
        logger.info("[*] Buscando/llenando input objetivo con reintentos (candidatos)...")
        found_and_filled = find_and_fill_input_with_candidates(driver, valor, wait_timeout= max(wait_timeout, 25))
        if not found_and_filled:
            logger.error("No se pudo localizar/llenar el input en la página objetivo. Revisa logs.")
            return {'success': False, 'error': 'No se pudo localizar/llenar el input en la página objetivo.'}

        # -------------------------
        # PRIMERO: CHECKBOX DE TÉRMINOS (fast)
        # -------------------------
        logger.info("[*] Iniciando proceso automático de aceptación de términos (fast)...")

        # Pasamos wait_timeout reducido al accept_terms_if_present para hacerlo más rápido
        accepted = accept_terms_if_present(driver,
                                checkbox_selector=TERMS_CHECKBOX_SELECTOR,
                                checkbox_input_xpath=TERMS_CHECKBOX_INPUT,
                                accept_button_selector=TERMS_ACCEPT_BUTTON_SELECTOR,
                                wait_timeout=2)

        if accepted:
            logger.info("[+] Checkbox de términos procesado correctamente (fast)")
            # Verificar que quedó marcado con menos reintentos
            ensure_checkbox_checked(driver, checkbox_input_xpath=TERMS_CHECKBOX_INPUT, retries=2, delay=0.3)
        else:
            logger.warning("[!] No se pudo marcar el checkbox de términos automáticamente (fast)")

        # 4) PULSADO DEL BOTÓN después de aceptar términos — detectar activación rápidamente
        logger.info("[*] Procediendo con el botón después de aceptar términos (fast-click)...")
        clicked_search = False

        if not MANUAL_INTERACTION:
            # Esperar un poco después de aceptar términos
            time.sleep(0.4)
            
            logger.info("[*] Buscando botón a clickear: %s", button_selector)
            by_btn, sel_btn = resolve_locator(button_selector)

            try:
                # Poll rápido hasta que el botón esté habilitado / clickable
                def find_active_button(drv):
                    try:
                        el = drv.find_element(by_btn, sel_btn)
                        try:
                            enabled = el.is_enabled()
                        except Exception:
                            enabled = True
                        if enabled and el.is_displayed():
                            return el
                        return False
                    except Exception:
                        return False

                # Timeout corto y poll frecuente para ser reactivo
                wait_btn = WebDriverWait(driver, 12, poll_frequency=0.2)
                btn = wait_btn.until(find_active_button)

                # Resaltar el botón si posible
                try:
                    driver.execute_script("arguments[0].style.outline = '3px solid lime';", btn)
                except Exception:
                    pass

                # Click inmediato con JS (más rápido y confiable)
                try:
                    driver.execute_script("arguments[0].click();", btn)
                except Exception:
                    try:
                        action = ActionChains(driver)
                        action.move_to_element(btn).pause(0.1).click().perform()
                    except Exception:
                        try:
                            btn.click()
                        except Exception:
                            logger.exception("[!] Intentos de click en botón fallaron")

                clicked_search = True
                logger.info("[+] Click en botón de búsqueda realizado (fast-click).")
                
            except Exception:
                logger.exception("[!] No se pudo hacer click automático en el botón (fast-click)")
        else:
            logger.info("[MANUAL] Por favor haz click en el botón Consultar")

        # ----------------- esperar resultado específico (xpath que proporcionaste) -----------------
        result_wait_timeout = max(wait_timeout, 45)
        if MANUAL_INTERACTION:
            result_wait_timeout = MANUAL_WAIT_TIMEOUT

        logger.info("[*] Esperando selector de resultado exacto (robusto): %s", result_selector)

        try:
            WebDriverWait(driver, result_wait_timeout).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "app-request-invoice") or d.find_elements(By.CSS_SELECTOR, "div.payment-card-invoice-info") or d.find_elements(By.TAG_NAME, "ion-grid")
            )
            logger.info("[*] Se detectó contenedor de resultado (app-request-invoice / ion-grid / payment-card-invoice-info).")
        except Exception:
            logger.info("[*] No se detectó contenedor 'app-request-invoice' / 'ion-grid' en el tiempo esperado, continuar con búsqueda profunda...")

        _find_deep_js = """
function findDeep(selector){
    function walk(root){
        try{
            var el = root.querySelector(selector);
            if (el) return el;
        }catch(e){}
        var nodes = root.querySelectorAll('*');
        for(var i=0;i<nodes.length;i++){
            var n = nodes[i];
            try{
                if(n.shadowRoot){
                    var f = walk(n.shadowRoot);
                    if(f) return f;
                }
            }catch(e){}
        }
        return null;
    }
    return walk(document);
}
return findDeep(arguments[0]);
"""

        def find_element_deep(driver, css_selector):
            try:
                el = driver.execute_script(_find_deep_js, css_selector)
                return el
            except Exception:
                return None

        deadline = time.time() + result_wait_timeout
        found_el = None
        while time.time() < deadline:
            try:
                try:
                    by_res, sel_res = resolve_locator(result_selector)
                    if by_res == By.XPATH:
                        els = driver.find_elements(by_res, sel_res)
                        if els:
                            for e in els:
                                try:
                                    if e.is_displayed():
                                        found_el = e
                                        break
                                except Exception:
                                    found_el = e
                                    break
                    else:
                        found_el = find_element_deep(driver, result_selector)
                except Exception:
                    pass

                try:
                    if not found_el:
                        found_el = find_element_deep(driver, "div.payment-card-invoice-info")
                except Exception:
                    pass

                try:
                    if not found_el:
                        h2s = driver.find_elements(By.TAG_NAME, "h2")
                        for h in h2s:
                            try:
                                txt = h.text.strip()
                                if txt and len(txt) > 3:
                                    try:
                                        anc = h.find_element(By.XPATH, "ancestor::app-request-invoice")
                                        found_el = anc
                                        break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                except Exception:
                    pass

                if found_el:
                    try:
                        text_parts = []
                        try:
                            h2 = found_el.find_element(By.TAG_NAME, "h2")
                            text_parts.append(h2.text.strip())
                        except Exception:
                            pass
                        try:
                            spans = found_el.find_elements(By.TAG_NAME, "span")
                            for sp in spans:
                                try:
                                    v = sp.text.strip()
                                    if v:
                                        text_parts.append(v)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            ps = found_el.find_elements(By.TAG_NAME, "p")
                            for p in ps:
                                try:
                                    v = p.text.strip()
                                    if v:
                                        text_parts.append(v)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        result_text = "\n".join([s for s in text_parts if s])
                        if not result_text:
                            try:
                                result_text = driver.execute_script("return (arguments[0] && (arguments[0].innerText || arguments[0].textContent)) || '';", found_el).strip()
                            except Exception:
                                try:
                                    result_text = found_el.text.strip()
                                except Exception:
                                    result_text = ""
                    except Exception:
                        try:
                            result_text = found_el.text.strip()
                        except Exception:
                            result_text = ""

                    logger.info("[*] Texto extraído (len): %d", len(result_text) if result_text else 0)

                    try:
                        if has_desired_structure(result_text):
                            logger.info("[+] Estructura deseada encontrada. Retornando resultado.")
                            try:
                                driver.execute_script("arguments[0].style.outline = '4px solid red'; arguments[0].scrollIntoView({block:'center'});", found_el)
                            except Exception:
                                pass
                            logger.info("[+] Resultado extraído (longitud): %d", len(result_text))
                            return {'success': True, 'result': result_text}
                        else:
                            found_el = None
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(0.6)

        logger.error("No se encontró el resultado con la estructura esperada dentro del tiempo")
        return {'success': False, 'error': 'No se encontró el resultado con la estructura esperada dentro del tiempo'}

    except TimeoutException as e:
        tb = traceback.format_exc()
        logger.exception("[ERROR] TimeoutException: %s", e)
        return {'success': False, 'error': f'Tiempo de espera excedido: {e}', 'trace': tb}
    except WebDriverException as e:
        tb = traceback.format_exc()
        logger.exception("[ERROR] WebDriverException: %s", e)
        return {'success': False, 'error': f'Error de WebDriver: {e}', 'trace': tb}
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception("[ERROR] Exception: %s", e)
        return {'success': False, 'error': str(e), 'trace': tb}
    finally:
        if driver:
            if VISIBLE:
                logger.info("[*] Depuración visible: el navegador permanecerá 1 segundo antes de cerrar.")
                time.sleep(1)
            try:
                driver.quit()
            except Exception:
                pass
        # Detener Xvfb si se inició
        if display:
            try:
                display.stop()
                logger.info("Xvfb detenido")
            except Exception as e:
                logger.error(f"Error deteniendo Xvfb: {e}")

# -------------------------
# Flask routes
# -------------------------
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/process', methods=['POST'])
def process():
    data = request.get_json(force=True)
    valor = data.get('valor', '').strip()
    if not valor:
        return jsonify({'success': False, 'error': 'Valor vacío'})

    res = run_selenium_task(valor)
    return jsonify(res)

if __name__ == '__main__':
    # Ejecutar Flask con debug=False para evitar logs de desarrollo
    app.run(host='127.0.0.1', port=5000, debug=False)