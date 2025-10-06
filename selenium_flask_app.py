# selenium_flask_app.py
"""
Selenium + Flask script (modificado):
 - Forzar carga unpacked de la extensión encontrada en Profile 2 con --load-extension.
 - Logs adicionales concisos sobre carga del profile, detección de la extensión y estado de reCAPTCHA.
 - Mantiene el resto de la lógica intacta.
"""

import os
import time
import json
import traceback
import logging
import sys
import re
import random
import shutil
import tempfile
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
# Logging (INFO para estados)
# -------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Silenciar módulos muy verbosos
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
VISIBLE = False   # headless via Xvfb
LOGIN_URL = "https://clientes.celsia.com/clientes/login"
TARGET_URL = "https://clientes.celsia.com/clientes/paga-tus-facturas"

# Selectors (mantén los tuyos)
USERNAME_SELECTOR = '//*[@id="root"]/div/div[2]/div/div/span/div/div/div/form/div[1]/div/input'
PASSWORD_SELECTOR = '//*[@id="root"]/div/div[2]/div/div/span/div/div/div/form/div[2]/div/input'
SUBMIT_SELECTOR = 'button[type="submit"]'
POST_LOGIN_CHECK_SELECTOR = 'app-request-invoice'

INPUT_SELECTOR = '#nicABuscar'
TERMS_CHECKBOX_SELECTOR = '//*[@id="mat-checkbox-1"]/label'
TERMS_CHECKBOX_INPUT = '//*[@id="mat-checkbox-1-input"]'
TERMS_ACCEPT_BUTTON_SELECTOR = '//*[@id="buscarCodigoCuenta"]'
BUTTON_SELECTOR = '//*[@id="buscarCodigoCuenta"]'
RESULT_SELECTOR = '//*[@id="menu-content"]/app-request-invoice/ion-content/ion-grid/ion-row/ion-row[3]/div/div[1]'

COOKIES_FILE = Path("session_cookies.json")

PAGE_LOAD_TIMEOUT = 40
ELEMENT_WAIT_TIMEOUT = 30

# Flags
MANUAL_TERMS = False
MANUAL_INTERACTION = False
MANUAL_WAIT_TIMEOUT = 300

# -------------------------
# UI simple
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
    <p>Ingresa un valor y presiona "Procesar".</p>
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
# Helpers
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
    if not selector:
        raise ValueError("Selector vacío en resolve_locator()")
    s = selector.strip()
    low = s.lower()
    if low.startswith('xpath='):
        return (By.XPATH, s.split('=', 1)[1])
    if s.startswith('/') or s.startswith('//') or s.startswith('.//') or s.startswith('('):
        return (By.XPATH, s)
    return (By.CSS_SELECTOR, s)

# Detect extension 'capsolver' in profile (enhanced heuristic)
def detect_capsolver_in_profile(profile_path: Path):
    try:
        details = []
        keywords = ("capsolver", "captcha solver", "captcha-solver", "captcha")
        candidates = [
            profile_path / "Default" / "Extensions",
            profile_path / "Extensions",
            profile_path
        ]
        for base in candidates:
            try:
                if base and base.exists() and base.is_dir():
                    for ext_id_dir in base.iterdir():
                        if ext_id_dir.is_dir():
                            for ver in ext_id_dir.iterdir():
                                if ver.is_dir():
                                    man = ver / "manifest.json"
                                    if man.exists():
                                        try:
                                            txt = man.read_text(encoding='utf-8', errors='ignore')
                                            j = json.loads(txt)
                                            name = (j.get("name") or "").lower()
                                            desc = (j.get("description") or "").lower()
                                            found_kw = None
                                            for kw in keywords:
                                                if kw in name or kw in desc:
                                                    found_kw = kw
                                                    break
                                            details.append({
                                                "id": ext_id_dir.name,
                                                "version_dir": str(ver.name),
                                                "name": j.get("name"),
                                                "description": j.get("description"),
                                                "match_keyword": found_kw,
                                                "manifest_path": str(man)
                                            })
                                        except Exception:
                                            details.append({
                                                "id": ext_id_dir.name,
                                                "version_dir": str(ver.name),
                                                "name": None,
                                                "description": None,
                                                "match_keyword": None,
                                                "manifest_path": str(man) if man.exists() else None
                                            })
            except Exception:
                continue
        found = [d for d in details if d.get("match_keyword")]
        return (len(found) > 0, found if found else details)
    except Exception:
        logger.exception("[PROFILE] Error detectando extensión en profile")
        return (False, [])

# -------------------------
# Login functions (kept logic)
# -------------------------
def perform_login(driver,
                  login_url,
                  username_selector,
                  password_selector,
                  submit_selector,
                  post_login_check_selector,
                  wait_timeout=ELEMENT_WAIT_TIMEOUT):
    logger.debug("[*] Realizando login (robusto, flujo único - iframe 'loginunico') ...")
    try:
        try:
            driver.get(login_url)
        except Exception:
            logger.exception("[!] driver.get(login_url) fallo, prosigo con intentos.")

        try:
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            logger.debug("[*] readyState no llegó a 'complete' en 10s (continúa)")

        frames = driver.find_elements(By.TAG_NAME, "iframe")
        logger.debug("[*] Iframes detectados: %d", len(frames))
        chosen_frame = None
        for idx, fr in enumerate(frames):
            try:
                src = fr.get_attribute("src") or ""
                low = src.lower()
                if "loginunico" in low or "azurewebsites" in low or "loginunico-prd" in low:
                    chosen_frame = fr
                    logger.debug("[*] Elegido iframe[%d] para login (src=%s)", idx, src)
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
                        logger.debug("[*] Elegido iframe[%d] por heurística (tiene inputs).", idx)
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
            logger.debug("[*] Dentro del iframe elegido, buscando campos de usuario/clave")

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
                logger.debug("[+] Login confirmado en document principal tras submit en iframe")
                try:
                    driver.get(TARGET_URL)
                    WebDriverWait(driver, 6).until(lambda d: d.execute_script("return document.readyState") == "complete")
                    logger.debug("[*] Redirigido a TARGET_URL inmediatamente tras login")
                except Exception:
                    logger.debug("[*] No se pudo redirigir inmediatamente a TARGET_URL o la carga fue lenta, el flujo continuará normalmente.")
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
    try:
        driver.get(login_url)
    except Exception:
        pass

    cookies = load_cookies_from_file()
    if cookies:
        logger.debug("[*] Cargando cookies desde disco...")
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
            logger.debug("[+] Sesión restaurada con cookies")
            try:
                driver.get(TARGET_URL)
                WebDriverWait(driver, 6).until(lambda d: d.execute_script("return document.readyState") == "complete")
                logger.debug("[*] Redirigido a TARGET_URL tras restaurar cookies")
            except Exception:
                logger.debug("[*] No se pudo redirigir inmediatamente a TARGET_URL tras restaurar cookies.")
            return True
        except Exception:
            logger.debug("[*] Cookies no válidas o sesión expirada, se hará login")

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
        logger.warning("[!] No se pudo iniciar sesión automáticamente (perform_login devolvió False).")
        return False

# Accept terms
def accept_terms_if_present(driver,
                            checkbox_selector=TERMS_CHECKBOX_SELECTOR,
                            checkbox_input_xpath=TERMS_CHECKBOX_INPUT,
                            accept_button_selector=TERMS_ACCEPT_BUTTON_SELECTOR,
                            wait_timeout=2):
    logger.debug("[*] Buscando checkbox de términos y condiciones (fast mode)...")
    try:
        by_input, sel_input = resolve_locator(checkbox_input_xpath)
        try:
            checkbox_input = WebDriverWait(driver, max(1, wait_timeout)).until(
                EC.presence_of_element_located((by_input, sel_input))
            )
            try:
                driver.execute_script("if(!arguments[0].checked){arguments[0].click();}", checkbox_input)
            except Exception:
                try:
                    driver.execute_script("arguments[0].checked = true; arguments[0].dispatchEvent(new Event('change'));", checkbox_input)
                except Exception:
                    pass
            try:
                if checkbox_input.is_selected():
                    logger.debug("[+] Checkbox marcado (fast path)")
                    return True
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[!] No se pudo encontrar/marcar con selector principal (fast): {e}")

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
            logger.debug("[+] Click realizado en el label del checkbox (fast path)")
            return True
        except Exception as e:
            logger.debug(f"[!] No se pudo encontrar/marcar con selector del label (fast): {e}")

        try:
            checkboxes = driver.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
        except Exception:
            checkboxes = []
        logger.debug(f"[*] Se encontraron {len(checkboxes)} checkboxes en total (fast fallback)")
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
                        logger.debug(f"[+] Checkbox alternativo {i+1} marcado (fast fallback)")
                        return True
            except Exception as e:
                logger.debug(f"[!] Error con checkbox alternativo {i+1}: {e}")
                continue

        logger.error("[!] No se pudo encontrar ni marcar ningún checkbox de términos (fast finish)")
        return False

    except Exception as e:
        logger.exception("[!] Error inesperado en accept_terms_if_present (fast mode):")
        return False

def ensure_checkbox_checked(driver, checkbox_input_xpath=TERMS_CHECKBOX_INPUT, retries=3, delay=1):
    for attempt in range(retries):
        try:
            by_i, sel_i = resolve_locator(checkbox_input_xpath)
            checkbox = driver.find_element(by_i, sel_i)
            if checkbox.is_selected():
                logger.debug(f"[+] Checkbox verificado como marcado (intento {attempt+1})")
                return True
            else:
                logger.debug(f"[*] Checkbox aún no marcado (intento {attempt+1})")
                time.sleep(delay)
        except Exception as e:
            logger.debug(f"[!] Error verificando checkbox (intento {attempt+1}): {e}")
            time.sleep(delay)
    logger.error("[!] Checkbox no pudo ser verificado como marcado después de intentos")
    return False

def set_input_value(driver, input_el, value):
    try:
        try:
            input_el.clear()
        except Exception:
            pass
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
                                        logger.debug("[+] Valor puesto en input por selector: %s", sel)
                                        return True
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].scrollIntoView(true);", el)
                                    ok = set_input_value(driver, el, valor)
                                    if ok:
                                        logger.debug("[+] Valor puesto en input por selector (after scroll): %s", sel)
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
                                logger.debug("[+] Valor puesto en input por heurística placeholder")
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

def has_desired_structure(text):
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
# Main with forced --load-extension from detected extension folder
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
    logger.info("Inicio de tarea Selenium para valor: %s", valor)
    display = None
    tmp_profile_copy_path = None
    extra_load_extension = None
    try:
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

        repo_root = Path(__file__).parent.resolve()
        desired_profile = repo_root / "chrome_profile_copy" / "Profile 2"
        env_user_data = os.environ.get("EXT_USER_DATA_DIR", "").strip()
        orig_user_data_dir = None

        if env_user_data:
            try:
                cand = Path(env_user_data).expanduser().resolve()
                if cand.exists() and cand.is_dir():
                    if (cand / "Local State").exists() or (cand / "Preferences").exists():
                        orig_user_data_dir = cand
                        logger.info("[PROFILE] EXT_USER_DATA_DIR definida y válida: %s", orig_user_data_dir)
                    else:
                        logger.error("[PROFILE] EXT_USER_DATA_DIR definida pero no parece un profile: %s", cand)
                else:
                    logger.error("[PROFILE] EXT_USER_DATA_DIR definida pero no existe: %s", env_user_data)
            except Exception:
                logger.exception("[PROFILE] Error evaluando EXT_USER_DATA_DIR")

        if not orig_user_data_dir:
            if desired_profile.exists() and desired_profile.is_dir():
                if (desired_profile / "Local State").exists() or (desired_profile / "Preferences").exists() or (desired_profile / "Bookmarks").exists():
                    orig_user_data_dir = desired_profile
                    logger.info("[PROFILE] Usando perfil Linux fijo: %s", orig_user_data_dir)
                else:
                    logger.error("[PROFILE] 'Profile 2' existe pero sin archivos esperados: %s", desired_profile)
            else:
                logger.warning("[PROFILE] No se encontró carpeta 'Profile 2' en chrome_profile_copy: %s", desired_profile)

        if orig_user_data_dir:
            try:
                tmp_dir = Path(tempfile.mkdtemp(prefix="chrome_profile_copy_"))
                tmp_profile_copy_path = tmp_dir
                for item in orig_user_data_dir.iterdir():
                    dest = tmp_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, symlinks=False, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
                logger.info("Se encontró Profile 2 y se creó copia temporal en: %s", tmp_profile_copy_path)
                found_ext, ext_details = detect_capsolver_in_profile(tmp_profile_copy_path)
                if found_ext:
                    logger.info("[PROFILE] Extensión Captcha Solver detectada en la copia del profile. Detalles: %s", ext_details)
                    # Forzar carga unpacked de la extensión encontrada: buscar ruta a la carpeta de la versión encontrada
                    # elegimos la primera coincidencia con match_keyword
                    chosen = ext_details[0]
                    ext_id = chosen.get("id")
                    ver_dir = chosen.get("version_dir")
                    # comprobar la ruta probable: Extensions/<ext_id>/<ver_dir>
                    candidate_ext_path = tmp_profile_copy_path / "Extensions" / ext_id / ver_dir
                    if not candidate_ext_path.exists():
                        # también puede estar en Default/Extensions
                        candidate_ext_path = tmp_profile_copy_path / "Default" / "Extensions" / ext_id / ver_dir
                    if candidate_ext_path.exists() and candidate_ext_path.is_dir():
                        extra_load_extension = str(candidate_ext_path)
                        logger.info("[PROFILE] Forzando --load-extension=%s", extra_load_extension)
                    else:
                        logger.warning("[PROFILE] No se encontró la carpeta unpacked de la extensión en la copia temporal (no se añadirá --load-extension).")
                else:
                    if ext_details:
                        logger.info("[PROFILE] Se inspeccionó la copia del profile; no se detectó Captcha Solver explícitamente. Entradas encontradas: %d", len(ext_details))
                    else:
                        logger.info("[PROFILE] No se detectaron extensiones en la copia del profile")
            except Exception:
                logger.exception("[PROFILE] Error copiando profile a temporal; arrancando sin profile")
                if tmp_profile_copy_path and tmp_profile_copy_path.exists():
                    try:
                        shutil.rmtree(tmp_profile_copy_path, ignore_errors=True)
                    except Exception:
                        pass
                tmp_profile_copy_path = None
        else:
            logger.info("[PROFILE] No se usará profile; Chrome arrancará con perfil limpio (no hay Profile 2)")

        # Añadir user-data-dir a opciones (si existe copia temporal)
        if tmp_profile_copy_path:
            chrome_options.add_argument(f'--user-data-dir={str(tmp_profile_copy_path)}')
            logger.info("[PROFILE] Añadido --user-data-dir=%s", tmp_profile_copy_path)
        # Forzar carga de extension si detectada
        if extra_load_extension:
            # chrome no permite cargar una extensión crx - pero si la carpeta unpacked existe
            try:
                chrome_options.add_argument(f'--load-extension={extra_load_extension}')
                logger.info("[PROFILE] Añadido flag --load-extension apuntando a: %s", extra_load_extension)
            except Exception:
                logger.exception("[PROFILE] Error añadiendo --load-extension:")

        # chrome options baseline
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--disable-gpu')  # Recomendado para headless
        chrome_options.add_experimental_option("detach", True)
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
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
        chrome_options.add_argument('--disable-web-security')

        # WebDriver selection as before
        possible_system_paths = [
            "/usr/local/bin/chromedriver",
            "/usr/bin/chromedriver",
            "/opt/bin/chromedriver",
            "/bin/chromedriver",
            "/usr/local/bin/chromedriver-linux",
        ]
        driver_executable = None
        for p in possible_system_paths:
            try:
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    driver_executable = p
                    logger.info("[WEBDRIVER] Usando chromedriver desde sistema: %s", driver_executable)
                    break
            except Exception:
                pass

        if not driver_executable:
            logger.info("[WEBDRIVER] chromedriver no encontrado en sistema, usando webdriver_manager (puede tardar).")
            os.environ.setdefault("WDM_LOG_LEVEL", "ERROR")
            raw_driver_path = ChromeDriverManager().install()
            driver_executable = raw_driver_path
            if os.path.isdir(raw_driver_path):
                for root, _, files in os.walk(raw_driver_path):
                    for fname in files:
                        if fname == 'chromedriver' or fname.startswith('chromedriver'):
                            candidate = os.path.join(root, fname)
                            if 'license' in fname.lower() or 'third_party' in fname.lower():
                                continue
                            driver_executable = candidate
                            break
                    if driver_executable != raw_driver_path:
                        break
            parent = os.path.dirname(raw_driver_path)
            if driver_executable == raw_driver_path and os.path.isdir(parent):
                for f in os.listdir(parent):
                    if f == 'chromedriver' or f.startswith('chromedriver'):
                        cand = os.path.join(parent, f)
                        if os.path.isfile(cand) and 'third_party' not in f.lower() and 'license' not in f.lower():
                            driver_executable = cand
                            break

        try:
            if driver_executable and os.path.exists(driver_executable):
                os.chmod(driver_executable, 0o755)
        except Exception:
            logger.debug("[!] No se pudo cambiar permisos al driver (continuo de todos modos)")

        service = Service(driver_executable, log_path=str(Path.cwd() / "chromedriver.log"))

        driver = None
        try:
            logger.debug("[*] Lanzando Chrome ...")
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("[WEBDRIVER] Chrome/Chromedriver lanzados.")
        except Exception as e:
            logger.exception("[WEBDRIVER] Error lanzando Chrome/Chromedriver:")
            return {'success': False, 'error': 'Error lanzando Chrome/Chromedriver', 'trace': traceback.format_exc()}

        try:
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        except Exception:
            pass

        # Check chrome://version
        try:
            time.sleep(0.5)
            try:
                driver.get("chrome://version")
                time.sleep(0.6)
                src = driver.page_source or ""
                if tmp_profile_copy_path and str(tmp_profile_copy_path) in src:
                    logger.info("[PROFILE] Chrome arrancó usando la copia temporal del profile: %s", tmp_profile_copy_path)
                elif orig_user_data_dir and str(orig_user_data_dir) in src:
                    logger.info("[PROFILE] Chrome arrancó usando el profile solicitado: %s", orig_user_data_dir)
                else:
                    logger.info("[PROFILE] Chrome NO parece usar el user-data-dir solicitado (validar).")
            except Exception:
                logger.debug("[PROFILE] No se pudo abrir chrome://version (posible bloqueo en headless).")
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
            if logged:
                logger.info("Se hizo login exitosamente.")
        except Exception:
            logger.exception("[!] ensure_logged_in threw:")

        # Ensure on target page
        try:
            driver.get(target_url)
        except Exception:
            pass
        logger.info("Se navegó a la página objetivo (intentando acceder a factura).")

        # Fill input
        found_and_filled = find_and_fill_input_with_candidates(driver, valor, wait_timeout= max(wait_timeout, 25))
        if not found_and_filled:
            logger.error("No se pudo localizar/llenar el input en la página objetivo.")
            return {'success': False, 'error': 'No se pudo localizar/llenar el input en la página objetivo.'}

        # Accept terms
        accepted = accept_terms_if_present(driver,
                                checkbox_selector=TERMS_CHECKBOX_SELECTOR,
                                checkbox_input_xpath=TERMS_CHECKBOX_INPUT,
                                accept_button_selector=TERMS_ACCEPT_BUTTON_SELECTOR,
                                wait_timeout=2)

        if accepted:
            logger.info("Checkbox de términos procesado correctamente (fast).")
            ensure_checkbox_checked(driver, checkbox_input_xpath=TERMS_CHECKBOX_INPUT, retries=2, delay=0.3)
        else:
            logger.warning("[!] No se pudo marcar el checkbox de términos automáticamente (fast)")

        # Robust click: WAIT until button becomes enabled (poll)
        logger.debug("[*] Procediendo con el botón (esperando que deje de ser disabled)...")
        clicked_search = False
        if not MANUAL_INTERACTION:
            by_btn, sel_btn = resolve_locator(button_selector)

            button_wait_deadline = time.time() + 30  # wait up to 30s for button to enable
            btn_elem = None
            while time.time() < button_wait_deadline:
                try:
                    candidates = driver.find_elements(by_btn, sel_btn)
                    if candidates:
                        for c in candidates:
                            try:
                                displayed = c.is_displayed()
                            except Exception:
                                displayed = True
                            try:
                                disabled_attr = c.get_attribute("disabled")
                            except Exception:
                                disabled_attr = None
                            try:
                                aria_disabled = c.get_attribute("aria-disabled")
                            except Exception:
                                aria_disabled = None
                            if displayed and (disabled_attr in (None, "", "false") and (aria_disabled is None or aria_disabled.lower() != "true")):
                                btn_elem = c
                                break
                            else:
                                btn_elem = c
                        if btn_elem:
                            try:
                                disabled_attr = btn_elem.get_attribute("disabled")
                                aria_disabled = btn_elem.get_attribute("aria-disabled")
                            except Exception:
                                disabled_attr = None
                                aria_disabled = None
                            if disabled_attr in (None, "", "false") and (aria_disabled is None or aria_disabled.lower() != "true"):
                                logger.info("Botón detectado y habilitado antes del click.")
                                break
                    time.sleep(0.7)
                except Exception:
                    time.sleep(0.7)

            if not btn_elem:
                try:
                    candidates = driver.find_elements(by_btn, sel_btn)
                    if candidates:
                        btn_elem = candidates[0]
                except Exception:
                    btn_elem = None

            if not btn_elem:
                logger.error("[!] No se pudo localizar el botón con el selector proporcionado.")
            else:
                try:
                    disabled_attr = btn_elem.get_attribute("disabled")
                except Exception:
                    disabled_attr = None
                try:
                    aria_disabled = btn_elem.get_attribute("aria-disabled")
                except Exception:
                    aria_disabled = None

                if (disabled_attr not in (None, "", "false")) or (aria_disabled is not None and aria_disabled.lower() == "true"):
                    logger.info("Botón encontrado pero sigue disabled. Comprobando reCAPTCHA y esperando habilitación (hasta 90s)...")
                    try:
                        recaptcha_iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha'], iframe[src*='google.com/recaptcha'], iframe[src*='gstatic.com/recaptcha']")
                        num_rec = len(recaptcha_iframes)
                    except Exception:
                        num_rec = 0
                    logger.info("[DIAG] Iframes recaptcha detectados: %d", num_rec)

                    rec_max_wait = 90  # más tiempo para que la extensión tenga chance
                    rec_deadline = time.time() + rec_max_wait
                    token = None
                    while time.time() < rec_deadline:
                        try:
                            token = driver.execute_script("var el = document.querySelector('textarea[name=\"g-recaptcha-response\"]'); return el ? el.value : null;")
                        except Exception:
                            token = None
                        if token:
                            logger.info("[DIAG] Se detectó token de reCAPTCHA (len=%d).", len(token))
                            break
                        try:
                            if btn_elem.is_displayed():
                                disabled_attr = btn_elem.get_attribute("disabled")
                                aria_disabled = btn_elem.get_attribute("aria-disabled")
                                if disabled_attr in (None, "", "false") and (aria_disabled is None or aria_disabled.lower() != "true"):
                                    logger.info("[DIAG] Botón se habilitó durante el wait.")
                                    break
                        except Exception:
                            pass
                        time.sleep(1.0)
                    if not token:
                        logger.info("[DIAG] No se detectó token reCAPTCHA dentro del tiempo de espera (o la extensión no resolvió).")

                try:
                    logger.info("[*] Intentando click robusto en el botón (JS -> ActionChains -> element.click).")
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_elem)
                    except Exception:
                        pass
                    clicked = False
                    try:
                        driver.execute_script("arguments[0].click();", btn_elem)
                        clicked = True
                        logger.info("Click ejecutado por JS en el botón (robust-click).")
                    except Exception:
                        try:
                            ActionChains(driver).move_to_element(btn_elem).pause(0.1).click().perform()
                            clicked = True
                            logger.info("Click ejecutado por ActionChains en el botón (robust-click).")
                        except Exception:
                            try:
                                btn_elem.click()
                                clicked = True
                                logger.info("Click ejecutado por element.click() en el botón (robust-click).")
                            except Exception:
                                logger.exception("[!] Intentos de click en botón fallaron (robust-click)")
                    clicked_search = clicked
                except Exception:
                    logger.exception("[!] Error al intentar click robusto en botón")

                try:
                    try:
                        attrs = driver.execute_script("""
                            var el = arguments[0];
                            var a = {};
                            try {
                                for (var i=0;i<el.attributes.length;i++){ a[el.attributes[i].name]=el.attributes[i].value; }
                            } catch(e){}
                            try { a['_displayed'] = el.offsetParent !== null; } catch(e){}
                            try { a['_disabled_attr'] = el.getAttribute('disabled'); } catch(e){}
                            try { a['_aria_disabled'] = el.getAttribute('aria-disabled'); } catch(e){}
                            try { a['_onclick'] = el.getAttribute('onclick') || (el.onclick ? 'has_func' : null); } catch(e){}
                            try { a['_tag'] = el.tagName; } catch(e){}
                            try { a['_text'] = (el.innerText||'').trim().slice(0,200); } catch(e){}
                            return a;
                        """, btn_elem)
                    except Exception:
                        attrs = {'_error': 'no se pudo leer atributos del elemento'}
                    logger.info("[DIAG] Atributos del botón tras click: %s", attrs)
                    try:
                        cur_url = driver.current_url
                    except Exception:
                        cur_url = "(no disponible)"
                    logger.info("[DIAG] URL actual tras click: %s", cur_url)
                    try:
                        initial_len = driver.execute_script("return (document.body && document.body.innerText) ? document.body.innerText.length : 0;")
                    except Exception:
                        initial_len = -1
                    logger.info("[DIAG] Longitud inicial del body (chars): %s", initial_len)
                    dom_changed = False
                    deadline = time.time() + 12
                    while time.time() < deadline:
                        try:
                            cur_len = driver.execute_script("return (document.body && document.body.innerText) ? document.body.innerText.length : 0;")
                            if cur_len != initial_len:
                                dom_changed = True
                                logger.info("[DIAG] Detectado cambio en DOM: %s -> %s chars", initial_len, cur_len)
                                break
                        except Exception:
                            pass
                        time.sleep(0.6)
                    if not dom_changed:
                        logger.info("[DIAG] No se detectó cambio de DOM tras click en ventana principal (12s).")
                        try:
                            frames = driver.find_elements(By.TAG_NAME, "iframe")
                            logger.info("[DIAG] Iframes en la página: %d", len(frames))
                            for i, fr in enumerate(frames[:8]):
                                try:
                                    src = fr.get_attribute('src') or ''
                                    logger.info("[DIAG] iframe[%d] src: %s", i, src[:300])
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    try:
                        token = driver.execute_script("var el = document.querySelector('textarea[name=\"g-recaptcha-response\"]'); return el ? el.value : null;")
                    except Exception:
                        token = None
                    if token:
                        logger.info("[DIAG] Se detectó token de reCAPTCHA (len=%d)", len(token))
                    else:
                        logger.info("[DIAG] No se detectó token de reCAPTCHA en textarea 'g-recaptcha-response'.")
                except Exception:
                    logger.exception("[DIAG] Error durante diagnóstico tras click")

        else:
            logger.info("[MANUAL] Por favor haz click en el botón Consultar (modo manual activo)")

        # Wait for result as before
        result_wait_timeout = max(wait_timeout, 60)
        if MANUAL_INTERACTION:
            result_wait_timeout = MANUAL_WAIT_TIMEOUT

        try:
            WebDriverWait(driver, result_wait_timeout).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "app-request-invoice") or d.find_elements(By.CSS_SELECTOR, "div.payment-card-invoice-info") or d.find_elements(By.TAG_NAME, "ion-grid")
            )
        except Exception:
            logger.debug("[*] No se detectó contenedor 'app-request-invoice' / 'ion-grid' en el tiempo esperado, continuar con búsqueda profunda...")

        # deep search...
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

                    logger.debug("[*] Texto extraído (len): %d", len(result_text) if result_text else 0)

                    try:
                        if has_desired_structure(result_text):
                            logger.info("Estructura deseada encontrada. Retornando resultado.")
                            try:
                                driver.execute_script("arguments[0].style.outline = '4px solid red'; arguments[0].scrollIntoView({block:'center'});", found_el)
                            except Exception:
                                pass
                            logger.info("Resultado extraído (longitud): %d", len(result_text))
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
        # cleanup
        try:
            if 'driver' in locals() and driver:
                try:
                    driver.quit()
                except Exception:
                    pass
        except Exception:
            pass
        if tmp_profile_copy_path:
            try:
                shutil.rmtree(str(tmp_profile_copy_path), ignore_errors=True)
                logger.info("Copia temporal del profile eliminada: %s", tmp_profile_copy_path)
            except Exception:
                logger.debug("[PROFILE] No se pudo eliminar la copia temporal del profile (ignorado).")
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
    try:
        res = run_selenium_task(valor)
    except Exception:
        tb = traceback.format_exc()
        logger.exception("[ERROR] Excepción en /process al ejecutar run_selenium_task")
        res = {'success': False, 'error': 'Error interno ejecutando la tarea', 'trace': tb}
    if not isinstance(res, dict):
        res = {'success': False, 'error': 'Respuesta inesperada del worker'}
    return jsonify(res)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
