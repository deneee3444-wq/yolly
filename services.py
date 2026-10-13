"""
services.py - Yolly AI Service Provider
═══════════════════════════════════════
All AI service provider logic is contained here.
To switch providers, modify this file only.

Supported:
  - Video: Veo 3.1 Basic, Grok Imagine
  - Image: Nano Banana, Nano Banana Pro, Nano Banana 2, GPT-Image 2
"""

import os
import sys
import random
import time
import requests
import string
import re
import base64
import json
import threading
import queue as _queue
import tempfile
import shutil
import platform as _platform
from concurrent.futures import ThreadPoolExecutor

import atexit
from cloakbrowser import launch_persistent_context

DEFAULT_TARGET_URL = "https://www.yolly.ai/auth/signin?callbackUrl=http%3A%2F%2Fwww.yolly.ai%2Finvite"
DEFAULT_PROXY = "http://tghpxuwn:dxrsnpsyz3df@45.38.107.97:6014"
PROXY = os.environ.get("PROXY", DEFAULT_PROXY)

os.environ["CLOAKBROWSER_SUPPRESS_FONT_WARNING"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

_turnstile_lock = threading.Lock()


def get_turnstile_token(target_url=DEFAULT_TARGET_URL, timeout=300):
    """
    Hedef adrese gidip Cloudflare Turnstile tokenini alip dondurur (varsayilan 300s / 5 dk).
    - Render Linux container uyumlu (--fingerprint-platform=linux ile font ve canvas tutarliligi).
    - Her cozum icin temiz izole gecici profil kullanir (eski cookie/cache reputation engellerini onler).
    - Cozumden sonra Chromium context'ini temiz kapatip 512MB RAM'i serbest birakir.
    - Cloudflare kutucuguna sadece 1 kez tiklar (challenge'i sifirlamaz).
    - Canli periyodik log basar.
    """
    with _turnstile_lock:
        print(f"[*] Turnstile cozumu baslatiliyor (Timeout: {timeout}s)...", flush=True)

        profile_dir = tempfile.mkdtemp(prefix="cb_profile_")

        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1920,1080",
        ]
        if _platform.system() != "Windows":
            args.append("--fingerprint-platform=linux")

        context = None
        token = None
        try:
            print(f"[+] CloakBrowser (headless) baslatiliyor (Proxy: {PROXY})...", flush=True)
            context = launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                proxy=PROXY,
                humanize=True,
                stealth_args=True,
                args=args,
            )
            page = context.pages[0] if context.pages else context.new_page()

            print(f"[+] Hedef adrese gidiliyor: {target_url}", flush=True)
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            print("[+] Sayfa yuklendi. Turnstile kontrol ediliyor...", flush=True)
            time.sleep(2.5)

            token = None
            last_click = 0
            start_time = time.time()

            while time.time() - start_time < timeout:
                for frame in page.frames:
                    try:
                        el = frame.query_selector('[name="cf-turnstile-response"]')
                        if el:
                            val = el.input_value() or el.get_attribute("value")
                            if val and len(val) > 20:
                                token = val
                                break
                    except Exception:
                        pass

                if token:
                    break

                # Cloudflare alani gorunduyse en fazla 5 saniyede bir tikla (spam yapmadan)
                if time.time() - last_click > 5:
                    for frame in page.frames:
                        if "challenges.cloudflare.com" in frame.url:
                            try:
                                frame.locator("body").click(timeout=2000)
                                print(f"[+] Cloudflare kutucuguna tiklandi ({int(time.time()-start_time)}s).", flush=True)
                                last_click = time.time()
                                break
                            except Exception:
                                pass

                time.sleep(1)
                elapsed = int(time.time() - start_time)
                if elapsed > 0 and elapsed % 10 == 0:
                    print(f"[*] Turnstile bekleniyor ({elapsed}/{timeout}s)...", flush=True)

            if token:
                print(f"[+] Turnstile token basariyla alindi: {token[:30]}...", flush=True)
            else:
                print("[-] Belirlenen sure icinde Turnstile token alinamadi.", flush=True)

            return token

        except Exception as e:
            print(f"\n[!] Turnstile cozucu hatasi: {e}\n", flush=True)
            return None
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            shutil.rmtree(profile_dir, ignore_errors=True)


class TurnstileBrowserWorker:
    """Geriye donuk uyumluluk icin Turnstile helper sarmalayicisi."""
    def get_token(self, target_url=DEFAULT_TARGET_URL, timeout=300):
        return get_turnstile_token(target_url=target_url, timeout=timeout)

    def close(self):
        pass


_turnstile_worker = TurnstileBrowserWorker()

VERIFY_BLACKLIST = set()
blacklist_lock = threading.Lock()

def extract_ip(proxy_url):
    match = re.search(r'(\d+\.\d+\.\d+\.\d+)', proxy_url)
    return match.group(1) if match else proxy_url

# ══════════════════════════════════════════════════════════════════════════════
# MODEL KONFİGÜRASYONLARI
# ══════════════════════════════════════════════════════════════════════════════

# Frontend model name → Yolly API model parameter
VIDEO_MODEL_MAP = {
    "VEO_3":        "veo3.1-basic",
    "SEEDANCE_2_0": "grok-imagine",
    "SORA_2":       "grok-imagine",
    "VIDU_Q3":      "grok-imagine",
    "QUALITY_V2_5": "veo3.1-basic",
}

IMAGE_MODEL_MAP = {
    "NANO_BANANA_PRO": "nano-banana-pro",
    "NANO_BANANA":     "nano-banana",
    "NANO_BANANA_2":   "nano-banana-2",
    "GPT_IMAGE_2":     "gpt-image-2",
}

# Yolly model capabilities & defaults
MODELS = {
    # ── Video ─────────────────────────────────────────────────────────────
    "veo3.1-basic": {
        "label": "Veo 3.1 Basic",
        "type": "video",
        "aspect_ratios": ["16:9", "9:16"],
        "resolutions": ["1080p"],
        "durations": ["5"],
        "supports_start_end_frame": True,
        "extra_params": {
            "negativePrompt": "",
            "audioUrl": "",
            "enablePromptExpansion": False,
            "cameraFixed": False,
            "generateAudio": False,
            "cfgScale": 0.5,
        },
    },
    "grok-imagine": {
        "label": "Grok Imagine",
        "type": "video",
        "aspect_ratios": ["16:9", "9:16", "1:1", "2:3", "3:2"],
        "resolutions": ["480p", "720p"],
        "durations": ["6", "10"],
        "supports_start_end_frame": False,
        "extra_params": {
            "negativePrompt": "",
            "audioUrl": "",
            "enablePromptExpansion": False,
            "cameraFixed": False,
            "cfgScale": 0.5,
        },
    },
    # ── Image ─────────────────────────────────────────────────────────────
    "nano-banana": {
        "label": "Nano Banana",
        "type": "image",
        "aspect_ratios": ["Auto", "1:1", "4:3", "3:4", "16:9", "9:16"],
        "resolutions": [],
    },
    "nano-banana-pro": {
        "label": "Nano Banana Pro",
        "type": "image",
        "aspect_ratios": ["1:1", "3:2", "2:3", "3:4", "4:3", "9:16", "16:9", "21:9"],
        "resolutions": ["1k", "2k", "4k"],
    },
    "nano-banana-2": {
        "label": "Nano Banana 2",
        "type": "image",
        "aspect_ratios": ["Auto", "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1k", "2k", "4k"],
    },
    "gpt-image-2": {
        "label": "GPT-Image 2",
        "type": "image",
        "aspect_ratios": ["Auto", "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1k", "2k", "4k"],
    },
}

# Frontend AR value → Yolly AR value (pass-through + Deevid legacy compat)
AR_MAP = {
    "16:9": "16:9", "9:16": "9:16", "1:1": "1:1",
    "3:4": "3:4", "4:3": "4:3", "3:2": "3:2", "2:3": "2:3",
    "4:5": "4:5", "5:4": "5:4", "21:9": "21:9",
    "Auto": "Auto", "AUTO": "Auto",
    # Deevid enum format compatibility
    "SIXTEEN_BY_NINE": "16:9", "NINE_BY_SIXTEEN": "9:16",
    "ONE_BY_ONE": "1:1", "THREE_BY_FOUR": "3:4",
    "FOUR_BY_THREE": "4:3", "THREE_BY_TWO": "3:2",
}

# Frontend resolution → Yolly resolution
RESOLUTION_MAP = {
    "1K": "1k", "2K": "2k", "4K": "4k",
    "1k": "1k", "2k": "2k", "4k": "4k",
    "480p": "480p", "720p": "720p", "1080p": "1080p",
}


def get_video_params(frontend_model):
    """Returns (yolly_model, resolution, duration, default_ar) for a frontend video model."""
    yolly_model = VIDEO_MODEL_MAP.get(frontend_model, "grok-imagine")
    cfg = MODELS.get(yolly_model, {})
    return (
        yolly_model,
        cfg.get("resolutions", ["720p"])[0],
        cfg.get("durations", ["6"])[0],
        cfg.get("aspect_ratios", ["16:9"])[0],
    )


# ══════════════════════════════════════════════════════════════════════════════
# OD2.IN TEMP EMAIL
# ══════════════════════════════════════════════════════════════════════════════

class eTemp:
    def random_box(self, length=10):
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    def getEmail(self):
        box = self.random_box(15)
        return f"{box}@tm.od2.in"

    def getVerificationCode(self, mail, timeout=30):
        """od2.in üzerinden gelen 6 haneli doğrulama kodunu çeker."""
        box = mail.split('@')[0]
        
        for _ in range(timeout):
            try:
                url = "https://od2.in/api/get-email"
                params = {"id": box}
                headers = {
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "accept": "*/*",
                    "referer": f"https://od2.in/temp-mail?id={box}",
                }
                
                r = requests.get(url, params=params, headers=headers, timeout=10)
                if r.status_code == 200:
                    inbox = r.json()
                    if inbox and isinstance(inbox, list) and len(inbox) > 0:
                        mail_id = inbox[0]["_id"]
                        
                        r_msg = requests.get(url, params={"emailId": mail_id}, headers=headers, timeout=10)
                        if r_msg.status_code == 200:
                            msg = r_msg.json()
                            
                            subject = (msg.get("subject") or "").lower()
                            from_text = (msg.get("from", {}).get("text") or "").lower()
                            text = (msg.get("text") or "") + "\n" + (msg.get("html") or "")
                            
                            if "yolly" in subject or "yolly" in from_text or "verification" in subject or "code" in subject or "yolly" in text.lower():
                                otp = re.search(r"\b(\d{6})\b", text)
                                if otp:
                                    return otp.group(1)
            except Exception as e:
                print(f"[-] od2.in API hatası: {e}")
                
            time.sleep(2)
        return None



# ══════════════════════════════════════════════════════════════════════════════
# PROXY SİSTEMİ
# ══════════════════════════════════════════════════════════════════════════════

PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies"
    "&proxy_format=protocolipport"
    "&format=text"
)


def fetch_proxies():
    """ProxyScrape'den proxy listesini çeker."""
    print("[*] Proxy listesi çekiliyor...")
    try:
        r = requests.get(PROXYSCRAPE_URL, timeout=10)
        proxies = [line.strip() for line in r.text.splitlines() if line.strip()]
        random.shuffle(proxies)
        print(f"[*] {len(proxies)} proxy bulundu.")
        return proxies
    except Exception as e:
        print(f"[-] Proxy listesi çekilemedi: {e}")
        return []


def test_proxy(proxy_url, test_url="https://www.yolly.ai", timeout=5):
    """Proxy'nin Yolly'ye ulaşabildiğini test eder."""
    try:
        r = requests.get(test_url, proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def find_working_proxy(max_workers=30, for_verify=False):
    """Tüm proxy listesini paralel tarar, ilk çalışanı döndürür."""
    proxy_list = fetch_proxies()
    if not proxy_list:
        return None

    if for_verify:
        with blacklist_lock:
            proxy_list = [p for p in proxy_list if extract_ip(p) not in VERIFY_BLACKLIST]
        print(f"[*] Verify proxy blacklisted filtered: {len(proxy_list)} proxies left.")

    result_q = _queue.Queue()
    found_event = threading.Event()
    counter_lock = threading.Lock()
    tested_count = [0]
    total = len(proxy_list)

    def probe(proxy):
        if found_event.is_set():
            return
        ok = test_proxy(proxy)
        with counter_lock:
            tested_count[0] += 1
            idx = tested_count[0]
            last = idx == total
        if ok and not found_event.is_set():
            found_event.set()
            result_q.put(proxy)
            print(f"  [+] Çalışan proxy bulundu [{idx}/{total}]: {proxy}")
        elif last:
            result_q.put(None)

    print(f"[*] Paralel tarama başlıyor ({max_workers} thread)...")
    executor = ThreadPoolExecutor(max_workers=max_workers)
    executor.map(probe, proxy_list)
    working = result_q.get()
    found_event.set()
    executor.shutdown(wait=False, cancel_futures=True)

    if working:
        return working
    print("[-] Çalışan proxy bulunamadı.")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# YOLLY SESSION & LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def make_yolly_session():
    """Creates a clean Yolly session with default headers."""
    s = requests.Session()
    s.headers.update({
        "accept": "application/json, text/plain, */*",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": "https://www.yolly.ai",
        "referer": "https://www.yolly.ai/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    })
    return s


def serialize_session_data(session, extra=None):
    """Serialize session cookies + extra metadata (e.g. provider) for DB storage."""
    data = {"cookies": requests.utils.dict_from_cookiejar(session.cookies)}
    if extra:
        data.update(extra)
    return json.dumps(data)


def deserialize_session_data(json_str):
    """Restore session and metadata from stored JSON.
    Returns (session, data_dict).
    """
    data = json.loads(json_str)
    session = make_yolly_session()
    for name, value in data.get("cookies", {}).items():
        session.cookies.set(name, value, domain=".yolly.ai")
    return session, data


def login_yolly(email, password=None):
    """Login to Yolly AI using email verification code.
    Password parameter is ignored (Yolly uses email-only verification).
    Proxy is used ONLY for the send-code step.

    Returns (session, email) on success, (None, None) on failure.
    """
    session = make_yolly_session()

    # 1) Find proxy for send-code
    print(f"[*] Login başlatılıyor: {email}")
    working_proxy = PROXY or find_working_proxy(max_workers=30)
    send_code_proxies = None
    if working_proxy:
        print(f"[*] Send-code proxy'si hazır: {working_proxy}")
        send_code_proxies = {"http": working_proxy, "https": working_proxy}
    else:
        print("[-] Proxy bulunamadı, send-code proxysiz gidecek.")

    # 1.1) Turnstile Token al (5 dakika timeout)
    print(f"[*] Turnstile token alınıyor ({email})...", flush=True)
    turnstile_token = get_turnstile_token(timeout=300)
    if not turnstile_token:
        print(f"[-] Turnstile token alınamadı ({email})", flush=True)
        return None, None

    # 2) send-code — WITH PROXY
    try:
        send_headers = dict(session.headers)
        send_headers.update({
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://www.yolly.ai",
            "referer": "https://www.yolly.ai/auth/signin?callbackUrl=http%3A%2F%2Fwww.yolly.ai%2Finvite",
        })
        res = session.post(
            "https://www.yolly.ai/api/auth/send-code",
            json={"email": email, "turnstileToken": turnstile_token},
            headers=send_headers,
            proxies=send_code_proxies,
            timeout=15,
        )
        if res.status_code != 200:
            print(f"[-] Send code başarısız ({email}): Status {res.status_code}", flush=True)
            return None, None
    except Exception as e:
        print(f"[-] Send code hatası ({email}): {e}", flush=True)
        return None, None

    # 3) Wait and check Spamok — retry up to 3 times with 15s waits
    temp = eTemp()
    code = None
    for attempt in range(1, 4):
        print(f"[*] {attempt}. deneme: 15 saniye bekleniyor (eski kodlar temizlensin)...", flush=True)
        time.sleep(15)
        print(f"[*] Spamok kutusu kontrol ediliyor ({email})...", flush=True)
        code = temp.getVerificationCode(email, timeout=15)
        if code:
            print(f"[+] Doğrulama kodu bulundu: {code}", flush=True)
            break
        if attempt < 3:
            print(f"[-] Kod bulunamadı, tekrar deneniyor...", flush=True)
        else:
            print(f"[-] Doğrulama kodu 3 denemede de alınamadı ({email})", flush=True)
            return None, None

    # 4) CSRF — no proxy
    try:
        csrf_res = session.get("https://www.yolly.ai/api/auth/csrf", timeout=15)
        if csrf_res.status_code != 200:
            print(f"[-] CSRF token alınamadı ({email})")
            return None, None
        csrf_token = csrf_res.json().get("csrfToken")
    except Exception as e:
        print(f"[-] CSRF hatası ({email}): {e}")
        return None, None

    # 5) Verify — NO PROXY
    verify_payload = {
        "email": email,
        "code": code,
        "firstVisitPage": "/",
        "redirect": "false",
        "callbackUrl": "https://www.yolly.ai/",
        "csrfToken": csrf_token,
    }
    verify_headers = dict(session.headers)
    verify_headers.update({
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.yolly.ai",
        "referer": "https://www.yolly.ai/auth/signin?callbackUrl=http%3A%2F%2Fwww.yolly.ai%2Finvite",
    })

    try:
        res = session.post(
            "https://www.yolly.ai/api/auth/callback/verification-code?",
            data=verify_payload,
            headers=verify_headers,
            timeout=15,
        )
        if res.status_code != 200:
            print(f"[-] Doğrulama başarısız ({email}): Status {res.status_code}")
            return None, None
    except Exception as e:
        print(f"[-] Verify hatası ({email}): {e}")
        return None, None

    print(f"[+] Login başarılı: {email}")
    return session, email


def check_credits(session):
    """Check remaining credits for a logged-in session. Returns int."""
    try:
        res = session.get("https://www.yolly.ai/api/user/credits", timeout=15)
        if res.status_code == 200:
            return int(res.json().get("left_credits", 0))
    except Exception:
        pass
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

def upload_image(session, image_bytes, mime_type="image/png"):
    """Upload image bytes to Yolly. Returns URL string or None."""
    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    base64_string = f"data:{mime_type};base64,{b64_data}"
    timestamp = int(time.time() * 1000)
    file_name = f"upload-{timestamp}-0.png"

    payload = {"base64Data": base64_string, "fileName": file_name}

    try:
        res = session.post(
            "https://www.yolly.ai/api/kie/upload", json=payload, timeout=60
        )
        if res.status_code == 200:
            url = res.json().get("data", {}).get("url")
            if url:
                print(f"[+] Resim yüklendi: {url[:80]}...")
                return url
        print(f"[-] Resim yükleme başarısız: {res.text[:200]}")
    except Exception as e:
        print(f"[-] Resim yükleme hatası: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def create_video(session, prompt, yolly_model, input_mode, images,
                 resolution, duration, aspect_ratio):
    """Submit video generation to Yolly.
    images: list of uploaded image URLs.
    Returns (task_id, provider) or ("INSUFFICIENT_CREDITS", None) or (None, None).
    """
    model_config = MODELS.get(yolly_model, {})

    payload = {
        "model": yolly_model,
        "prompt": prompt,
        "images": images or [],
        "inputMode": input_mode,
        "isPublic": True,
        "resolution": resolution,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "locale": "en",
    }

    extra = model_config.get("extra_params", {
        "negativePrompt": "", "audioUrl": "",
        "enablePromptExpansion": False, "cameraFixed": False, "cfgScale": 0.5,
    })
    payload.update(extra)

    session.headers.update({"referer": "https://www.yolly.ai/video"})

    try:
        res = session.post(
            "https://www.yolly.ai/api/video/create", json=payload, timeout=20
        )
        if "Insufficient credits" in res.text:
            return "INSUFFICIENT_CREDITS", None
        if res.status_code != 200:
            print(f"[-] Video create başarısız: {res.text[:300]}")
            return None, None
        data = res.json()
        task_id = data.get("id")
        provider = data.get("provider", yolly_model)
        if not task_id:
            print(f"[-] Task ID alınamadı: {data}")
            return None, None
        return task_id, provider
    except Exception as e:
        print(f"[-] Video create hatası: {e}")
        return None, None


def poll_video(session, task_id, provider, shutdown_event=None,
               max_polls=600, interval=3):
    """Poll Yolly for video completion.
    Returns (status_str, video_url).
    status_str: 'completed' | 'failed' | 'timeout' | 'shutdown'
    """
    params = {"id": task_id, "provider": provider}

    for _ in range(max_polls):
        if shutdown_event and shutdown_event.wait(interval):
            return "shutdown", None
        elif not shutdown_event:
            time.sleep(interval)

        try:
            res = session.get(
                "https://www.yolly.ai/api/video/query", params=params, timeout=15
            )
            if res.status_code != 200:
                continue

            q = res.json()
            # Flat format (Veo 3.1): {status, video_url, ...}
            # Nested format (grok):  {data: {status, videoUrl, ...}}
            nested = q.get("data") if isinstance(q.get("data"), dict) else None

            if nested:
                status = nested.get("status")
                video_url = nested.get("videoUrl")
            else:
                status = q.get("status")
                video_url = (
                    q.get("video_url")
                    or q.get("r2_video_url")
                    or (q.get("video_urls") or [None])[0]
                )

            if status == "completed" and video_url:
                return "completed", video_url
            if status in ("failed", "error"):
                return "failed", None
        except Exception as e:
            print(f"  [!] Video poll hatası: {e}")

    return "timeout", None


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def create_image(session, prompt, yolly_model, aspect_ratio, resolution=None,
                 reference_images=None, number_of_images=1):
    """Submit image generation to Yolly.
    reference_images: list of uploaded image URLs (not base64).
    Returns task_id string, or "INSUFFICIENT_CREDITS", or None.
    """
    model_config = MODELS.get(yolly_model, {})
    active_tab = "image" if reference_images else "text"

    payload = {
        "model": yolly_model,
        "prompt": prompt,
        "referenceImages": reference_images or [],
        "aspectRatio": aspect_ratio,
        "numberOfImages": number_of_images,
        "activeTab": active_tab,
        "isPublic": True,
        "locale": "en",
    }

    # Only add resolution for models that support it
    if resolution and model_config.get("resolutions"):
        payload["resolution"] = resolution

    session.headers.update({"referer": "https://www.yolly.ai/ai-image-generator"})

    try:
        res = session.post(
            "https://www.yolly.ai/api/image/create", json=payload, timeout=20
        )
        if "Insufficient credits" in res.text:
            return "INSUFFICIENT_CREDITS"
        if res.status_code != 200:
            print(f"[-] Image create başarısız: {res.text[:300]}")
            return None
        task_id = res.json().get("id")
        if not task_id:
            print(f"[-] Image task ID alınamadı")
            return None
        return task_id
    except Exception as e:
        print(f"[-] Image create hatası: {e}")
        return None


def poll_image(session, task_id, shutdown_event=None,
               max_polls=600, interval=2):
    """Poll Yolly for image completion.
    Returns (status_str, image_url_list).
    status_str: 'completed' | 'failed' | 'timeout' | 'shutdown'
    """
    for _ in range(max_polls):
        if shutdown_event and shutdown_event.wait(interval):
            return "shutdown", None
        elif not shutdown_event:
            time.sleep(interval)

        try:
            res = session.get(
                "https://www.yolly.ai/api/image/query",
                params={"id": task_id},
                timeout=15,
            )
            if res.status_code != 200:
                continue

            q = res.json()
            status = q.get("status")

            if status == "completed":
                urls = q.get("image_urls", []) or q.get("result", {}).get("imageUrls", [])
                return "completed", urls
            if status in ("failed", "error"):
                return "failed", None
        except Exception as e:
            print(f"  [!] Image poll hatası: {e}")

    return "timeout", None


def create_and_login_new_account(max_attempts=30):
    """
    Creates a new account on the fly like yollyAIProxy.py.
    Retries up to max_attempts until it successfully gets an account with exactly 30 credits.
    Returns (session, email) on success, or (None, None) on failure.
    """
    for attempt_num in range(1, max_attempts + 1):
        print(f"[*] Hesap oluşturma denemesi {attempt_num}/{max_attempts}...", flush=True)
        try:
            temp = eTemp()
            email = temp.getEmail()
            session = make_yolly_session()

            print(f"[*] On-the-fly hesap oluşturuluyor: {email}", flush=True)

            # 1) send-code (with Turnstile token & proxy)
            print(f"[*] Turnstile token alınıyor ({email})...", flush=True)
            turnstile_token = get_turnstile_token(timeout=300)
            if not turnstile_token:
                print("[-] Turnstile token alınamadı!", flush=True)
                continue
            print(f"[+] Turnstile token hazır: {turnstile_token[:30]}...", flush=True)

            sc_proxy = PROXY or find_working_proxy(max_workers=30, for_verify=False)
            if not sc_proxy:
                print("[-] Çalışan proxy bulunamadı (send-code)!", flush=True)
                continue

            send_code_url = "https://www.yolly.ai/api/auth/send-code"
            proxies_dict = {"http": sc_proxy, "https": sc_proxy}
            send_headers = dict(session.headers)
            send_headers.update({
                "accept": "*/*",
                "content-type": "application/json",
                "origin": "https://www.yolly.ai",
                "referer": "https://www.yolly.ai/auth/signin?callbackUrl=http%3A%2F%2Fwww.yolly.ai%2Finvite",
            })

            send_ok = False
            for attempt in range(3):
                try:
                    res = session.post(
                        send_code_url,
                        json={"email": email, "turnstileToken": turnstile_token},
                        headers=send_headers,
                        proxies=proxies_dict,
                        timeout=15
                    )
                    if res.status_code == 200:
                        try:
                            send_data = res.json()
                            if send_data.get("message") == "RATE_LIMIT_IP" or send_data.get("code") == -1:
                                print("[-] Send-code rate limit yedi, yeni proxy aranıyor...")
                                sc_proxy = PROXY or find_working_proxy(max_workers=30, for_verify=False)
                                if sc_proxy:
                                    proxies_dict = {"http": sc_proxy, "https": sc_proxy}
                                continue
                        except Exception:
                            pass
                        send_ok = True
                        break
                    else:
                        print(f"[-] Send-code başarısız ({res.status_code}), proxy değiştiriliyor...")
                        sc_proxy = PROXY or find_working_proxy(max_workers=30, for_verify=False)
                        if sc_proxy:
                            proxies_dict = {"http": sc_proxy, "https": sc_proxy}
                except Exception as e:
                    print(f"[-] Send-code bağlantı hatası: {e}")
                    sc_proxy = PROXY or find_working_proxy(max_workers=30, for_verify=False)
                    if sc_proxy:
                        proxies_dict = {"http": sc_proxy, "https": sc_proxy}

            if not send_ok:
                print("[-] Send-code başarısız oldu.", flush=True)
                continue

            # 2) Doğrulama kodu al (od2.in)
            code = None
            for attempt in range(1, 4):
                print(f"[*] od2.in kutusu kontrol ediliyor ({email})...", flush=True)
                time.sleep(15)
                code = temp.getVerificationCode(email, timeout=15)
                if code:
                    break
                print(f"[-] Kod bulunamadı ({email}), tekrar deneniyor...", flush=True)

            if not code:
                print(f"[-] Doğrulama kodu alınamadı ({email})", flush=True)
                continue

            # 3) CSRF token al (proxy'siz)
            csrf_token = None
            for attempt in range(3):
                try:
                    csrf_res = session.get("https://www.yolly.ai/api/auth/csrf", timeout=15)
                    if csrf_res.status_code == 200:
                        csrf_token = csrf_res.json().get("csrfToken")
                        break
                except Exception:
                    pass
            
            if not csrf_token:
                print(f"[-] CSRF token alınamadı ({email})", flush=True)
                continue

            # 4) Verify Callback (TEMİZ proxy ile)
            verify_proxy = find_working_proxy(max_workers=30, for_verify=True)
            if not verify_proxy:
                print("[-] Temiz verify proxy bulunamadı!", flush=True)
                continue

            verify_url = "https://www.yolly.ai/api/auth/callback/verification-code?"
            verify_payload = {
                "email": email,
                "code": code,
                "firstVisitPage": "/",
                "redirect": "false",
                "callbackUrl": "https://www.yolly.ai/",
                "csrfToken": csrf_token
            }

            verify_headers = dict(session.headers)
            verify_headers.update({
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://www.yolly.ai",
                "referer": "https://www.yolly.ai/auth/signin?callbackUrl=http%3A%2F%2Fwww.yolly.ai%2Finvite",
            })

            verify_success = False
            for attempt in range(3):
                try:
                    v_proxies = {"http": verify_proxy, "https": verify_proxy}
                    res = session.post(
                        verify_url,
                        data=verify_payload,
                        headers=verify_headers,
                        proxies=v_proxies,
                        timeout=15
                    )
                    if res.status_code == 200:
                        verify_success = True
                        with blacklist_lock:
                            VERIFY_BLACKLIST.add(extract_ip(verify_proxy))
                        break
                    else:
                        print(f"[-] Verify başarısız ({res.status_code}), proxy değiştiriliyor...", flush=True)
                        verify_proxy = find_working_proxy(max_workers=30, for_verify=True)
                except Exception as e:
                    print(f"[-] Verify bağlantı hatası: {e}", flush=True)
                    verify_proxy = find_working_proxy(max_workers=30, for_verify=True)

            if not verify_success:
                print("[-] Verify başarısız.", flush=True)
                continue

            # 5) Kredi kontrolü
            credits = 0
            for attempt in range(3):
                try:
                    credits = check_credits(session)
                    if credits > 0:
                        break
                except Exception:
                    pass

            # 30 kredi kontrolü: yollyAIProxy.py'deki gibi 30 kredi olmalı!
            if credits != 30:
                print(f"[-] Kredi 30 değil ({credits}), yeni hesap denenecek.", flush=True)
                continue

            print(f"[+] Başarılı hesap oluşturuldu: {email} (Kredi: {credits})", flush=True)
            return session, email

        except Exception as e:
            print(f"[-] Hesap oluşturulurken beklenmeyen hata oluştu: {e}", flush=True)
            continue

    print(f"[-] {max_attempts} denemede 30 kredilik hesap oluşturulamadı!", flush=True)
    return None, None

