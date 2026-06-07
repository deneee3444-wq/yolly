import random
import time
import requests
import string
import re
import os
import base64
import threading
import queue as _queue
from concurrent.futures import ThreadPoolExecutor

# ── SPAMOK EMAIL ─────────────────────────────────────────────────────────────
class eTemp:
    def random_email(self, length):
        return ''.join(
            random.SystemRandom().choice(string.ascii_lowercase + string.digits)
            for _ in range(length)
        )

    def getEmail(self):
        return self.random_email(15) + '@spamok.com'

    def getVerificationCode(self, mail, timeout=30):
        """Spamok üzerinden gelen 6 haneli doğrulama kodunu çeker."""
        address = mail.replace('@spamok.com', '')

        for _ in range(timeout):
            r = requests.get(f'https://api.spamok.com/v2/EmailBox/{address}')
            if r.status_code == 200:
                data = r.json()

                for m in data.get('mails', []):
                    if 'Verification Code' in m.get('subject', '') or 'yolly.ai' in m.get('fromDomain', ''):
                        mail_id = m['id']

                        email_r = requests.get(f'https://api.spamok.com/v2/Email/{address}/{mail_id}')
                        if email_r.status_code == 200:
                            plain_text = email_r.json().get('messagePlain', '')
                            match = re.search(r'\b\d{6}\b', plain_text)
                            
                            if match:
                                return match.group(0)

            time.sleep(2) 
        return None

# ── PROXY SİSTEMİ ────────────────────────────────────────────────────────────
PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies"
    "&proxy_format=protocolipport"
    "&format=text"
)

def fetch_proxies() -> list:
    """ProxyScrape'den proxy listesinin TAMAMINI çeker."""
    print("[*] Proxy listesi çekiliyor...")
    try:
        r = requests.get(PROXYSCRAPE_URL, timeout=10)
        proxies = [line.strip() for line in r.text.splitlines() if line.strip()]
        random.shuffle(proxies)
        print(f"[*] {len(proxies)} proxy bulundu, tümü taranacak.")
        return proxies
    except Exception as e:
        print(f"[-] Proxy listesi çekilemedi: {e}")
        return []

def test_proxy(proxy_url: str, test_url: str = "https://www.yolly.ai", timeout: int = 5) -> bool:
    """Proxy'nin yolly.ai'ye ulaşabildiğini test eder."""
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        r = requests.get(test_url, proxies=proxies, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False

def find_working_proxy(max_workers: int = 30):
    """Tüm proxy listesini çok thread'li olarak tarar. İlk çalışanı döndürür."""
    proxy_list = fetch_proxies()
    if not proxy_list:
        return None

    result_q    = _queue.Queue()
    found_event = threading.Event()
    counter_lock = threading.Lock()
    tested_count = [0]
    total        = len(proxy_list)

    def probe(proxy: str):
        if found_event.is_set():
            return

        ok = test_proxy(proxy)

        with counter_lock:
            tested_count[0] += 1
            idx = tested_count[0]
            last = (idx == total)

        if ok and not found_event.is_set():
            found_event.set()
            result_q.put(proxy)
            print(f"  [+] Çalışan proxy bulundu [{idx}/{total}]: {proxy}")
        else:
            if last:
                result_q.put(None)

    print(f"[*] Paralel tarama başlıyor ({max_workers} thread)...")
    executor = ThreadPoolExecutor(max_workers=max_workers)
    executor.map(lambda p: probe(p), proxy_list)

    working = result_q.get()

    found_event.set()
    executor.shutdown(wait=False, cancel_futures=True)

    if working:
        return working

    print("[-] Çalışan proxy bulunamadı.")
    return None

def make_proxied_session(proxy_url):
    """Proxy ayarlı (ya da ayarsız) bir Session döner."""
    s = requests.Session()
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    
    # Standart header'ları session'a gömüyoruz
    s.headers.update({
        "accept": "application/json, text/plain, */*",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": "https://www.yolly.ai",
        "referer": "https://www.yolly.ai/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    })
    return s

# ── YOLLY.AI KAYIT VE ÜRETİM ─────────────────────────────────────────────────

def register_yolly(session):
    """Verilen session (proxy'li) üzerinden kayıt işlemini dener."""
    temp = eTemp()
    email = temp.getEmail()
    
    print(f"[*] Email    : {email}")

    send_code_url = "https://www.yolly.ai/api/auth/send-code"
    try:
        send_code_res = session.post(send_code_url, json={"email": email}, timeout=15)
        if send_code_res.status_code != 200:
            print(f"[-] Kod gönderme başarısız (Status {send_code_res.status_code})")
            return None, None
    except Exception as e:
        print(f"[-] Kod gönderme isteği proxy hatası verdi: {e}")
        return None, None

    print("[*] Spamok kutusu kontrol ediliyor (timeout 30s)...")
    code = temp.getVerificationCode(email, timeout=30)
    
    if not code:
        print("[-] Doğrulama kodu e-postaya ulaşmadı.")
        return None, None
        
    print(f"[+] Doğrulama Kodu Bulundu: {code}")

    try:
        csrf_url = "https://www.yolly.ai/api/auth/csrf"
        csrf_res = session.get(csrf_url, timeout=15)
        if csrf_res.status_code != 200:
            print("[-] CSRF Token alınamadı.")
            return None, None
        csrf_token = csrf_res.json().get("csrfToken")
    except Exception as e:
        print(f"[-] CSRF isteği proxy hatası verdi: {e}")
        return None, None

    verify_url = "https://www.yolly.ai/api/auth/callback/verification-code?"
    verify_payload = {
        "email": email,
        "code": code,
        "firstVisitPage": "/",
        "redirect": "false",
        "callbackUrl": "https://www.yolly.ai/",
        "csrfToken": csrf_token
    }
    
    verify_headers = session.headers.copy()
    verify_headers["content-type"] = "application/x-www-form-urlencoded"
    
    try:
        verify_res = session.post(verify_url, data=verify_payload, headers=verify_headers, timeout=15)
        if verify_res.status_code != 200:
            print(f"[-] Doğrulama başarısız: {verify_res.status_code}")
            return None, None
    except Exception as e:
        print(f"[-] Verify isteği proxy hatası verdi: {e}")
        return None, None
        
    print("[+] Kod doğrulandı, session çerezleri alındı!")
    return session, email

def upload_image_to_yolly(session, image_path):
    if not os.path.exists(image_path):
        print(f"[-] Hata: Yüklenecek resim bulunamadı ({image_path})!")
        return None

    ext = os.path.splitext(image_path)[1].lower()
    mime_type = "image/png" if ext == ".png" else "image/jpeg"

    with open(image_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode('utf-8')

    base64_string = f"data:{mime_type};base64,{b64_data}"
    timestamp = int(time.time() * 1000)
    file_name = f"video-input-{timestamp}-0{ext}"

    upload_url = "https://www.yolly.ai/api/kie/upload"
    payload = {"base64Data": base64_string, "fileName": file_name}

    session.headers.update({"referer": "https://www.yolly.ai/video"})

    print(f"[*] Resim yükleniyor ({file_name})...")
    try:
        res = session.post(upload_url, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            image_url = data.get("data", {}).get("url")
            if image_url:
                print(f"[+] Resim başarıyla yüklendi: {image_url}")
                return image_url
        print(f"[-] Resim yükleme başarısız! Yanıt: {res.text}")
    except Exception as e:
        print(f"[-] Resim yükleme ağ hatası: {e}")
    return None

def generate_yolly_video(session, prompt, image_path=None, model="grok-imagine", 
                         input_mode="text", resolution="480p", duration="6", aspect_ratio="16:9"):
    
    print(f"\n[*] --- 2. AŞAMA: VİDEO ÜRETİMİ ({input_mode.upper()} TO VIDEO) ---")
    
    images_payload = []
    if input_mode == "image":
        if not image_path:
            print("[-] Hata: Image to Video modu için bir 'image_path' belirtmelisiniz!")
            return "ERROR"
        uploaded_image_url = upload_image_to_yolly(session, image_path)
        if not uploaded_image_url:
            print("[-] Resim yüklenemediği için işlem iptal edildi.")
            return "ERROR"
        images_payload = [uploaded_image_url]

    create_url = "https://www.yolly.ai/api/video/create"
    payload = {
        "model": model,
        "prompt": prompt,
        "images": images_payload,
        "inputMode": input_mode,
        "isPublic": True,
        "resolution": resolution,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "negativePrompt": "",
        "audioUrl": "",
        "enablePromptExpansion": False,
        "cameraFixed": False,
        "cfgScale": 0.5,
        "locale": "en"
    }

    print(f"[*] '{model}' modeli ile üretim tetikleniyor...")
    try:
        res = session.post(create_url, json=payload, timeout=20)
        
        # Bakiye hatasını status code'dan bağımsız yakalıyoruz
        if "Insufficient credits" in res.text:
            print(f"[-] Video üretim isteği başarısız (Kredi Bitti): {res.text}")
            return "INSUFFICIENT_CREDITS"
            
        if res.status_code != 200:
            print(f"[-] Video üretim isteği başarısız: {res.text}")
            return "ERROR"

        data = res.json()
        task_id = data.get("id")
        provider = data.get("provider", model)

        if not task_id:
            print(f"[-] Task ID alınamadı: {data}")
            return "ERROR"
            
    except Exception as e:
        print(f"[-] Üretim tetikleme ağ hatası: {e}")
        return "ERROR"

    print(f"[+] Üretim başladı! Task ID: {task_id}")
    print("[*] Video bekleniyor (2 saniyede bir kontrol edilecek)...")
    
    query_url = "https://www.yolly.ai/api/video/query"
    params = {"id": task_id, "provider": provider}

    while True:
        try:
            q_res = session.get(query_url, params=params, timeout=15)
            if q_res.status_code == 200:
                q_data = q_res.json()
                task_info = q_data.get("data", {})
                status = task_info.get("status")

                if status == "completed":
                    video_url = task_info.get("videoUrl")
                    print("\n" + "="*50)
                    print(f"[+] İŞLEM BAŞARILI! VİDEO HAZIR:")
                    print(f"    Link: {video_url}")
                    print("="*50 + "\n")
                    return "SUCCESS"
                elif status in ["failed", "error"]:
                    print(f"\n[-] Video üretimi başarısız oldu! Hata: {task_info.get('error')}")
                    return "ERROR"
        except Exception as e:
            print(f"  [!] Polling bağlantı sorunu, tekrar deneniyor... ({e})")
            
        time.sleep(2)


if __name__ == "__main__":
    
    # =========================================================================
    # DEĞİŞTİRİLEBİLİR ÖZELLİKLER REHBERİ
    # =========================================================================
    # input_mode  : "text" (Text to Video)  VEYA  "image" (Image to Video)
    # ar          : "16:9", "9:16", "1:1", "2:3", "3:2"
    # resolution  : "480p", "720p"
    # duration    : "6", "10" 
    # =========================================================================
    SECILEN_MOD = "text"             
    SECILEN_AR = "16:9"              
    SECILEN_RES = "480p"             
    SECILEN_DURATION = "6"           
    
    KULLANILACAK_PROMPT = "hello"
    SECILEN_MODEL = "grok-imagine"
    KULLANILACAK_RESIM = "test.jpg"  
    # =========================================================================

    attempt = 0
    
    # ANA DÖNGÜ (Bakiye biterse en başa sarabilmek için)
    while True:
        attempt += 1
        print(f"\n{'='*55}")
        print(f"[*] DENEME #{attempt} — Yeni hesap açılışı başlatılıyor...")
        
        yolly_session = None
        
        # PROXY İLE KAYIT AŞAMASI
        while True:
            working_proxy = find_working_proxy(max_workers=30)
            
            if not working_proxy:
                print("[-] Hiçbir proxy bulunamadı. Proxysiz deneniyor...")
                proxied_session = make_proxied_session(None)
            else:
                proxied_session = make_proxied_session(working_proxy)
                print(f"[*] Kullanılan Proxy: {working_proxy}")

            yolly_session, reg_email = register_yolly(proxied_session)
            
            if yolly_session:
                break # Kayıt başarılı oldu, proxy arama döngüsünden çık.
                
            print("[-] Kayıt aşamasında hata oluştu. Yeni proxy ile tekrar deneniyor...\n")
            time.sleep(1)


        # --- KAYIT BAŞARILI, PROXY'Yİ DEVREDEN ÇIKAR ---
        print("[*] Kayıt tamam! Generate/Polling işlemlerinin hızlı olması için Proxy temizleniyor...")
        yolly_session.proxies = {}  # Bundan sonraki istekler (Kredi kontrolü dahil) kendi bağlantınızla (proxysiz) yapılacak


        # --- HESAP AÇILDIKTAN SONRA KREDİ KONTROLÜ (PROXYSIZ) ---
        print("[*] Hesap kredisi kontrol ediliyor (Proxysiz)...")
        try:
            credits_res = yolly_session.get("https://www.yolly.ai/api/user/credits", timeout=15)
            if credits_res.status_code == 200:
                credits_data = credits_res.json()
                left_credits = str(credits_data.get("left_credits", "0"))
                print(f"[+] Mevcut Kredi: {left_credits}")
                
                if left_credits == "0":
                    print("[!] Bakiye 0! Diğer adımlara geçilmeden yeni proxy/hesap aranıyor...")
                    time.sleep(1)
                    continue  # Ana döngüyü başa sarar (Yeni proxy -> Yeni hesap)
            else:
                print(f"[-] Kredi kontrolü başarısız (Status {credits_res.status_code}), döngü başa sarılıyor.")
                time.sleep(1)
                continue
        except Exception as e:
            print(f"[-] Kredi kontrolü ağ hatası: {e}, döngü başa sarılıyor.")
            time.sleep(1)
            continue


        # === VİDEO ÜRETİM AŞAMASI ===
        status = generate_yolly_video(
            session=yolly_session, 
            prompt=KULLANILACAK_PROMPT, 
            image_path=KULLANILACAK_RESIM,
            model=SECILEN_MODEL,
            input_mode=SECILEN_MOD,
            resolution=SECILEN_RES,
            duration=SECILEN_DURATION,
            aspect_ratio=SECILEN_AR
        )

        if status == "INSUFFICIENT_CREDITS":
            print("[!] Bakiye yetersiz hatası alındı! Yeni bir hesap açılışına geçiliyor...")
            time.sleep(1)
            continue # Ana döngüyü başa sarar (Yeni proxy -> Yeni hesap)
            
        elif status == "SUCCESS":
            print("[+] İşlem başarıyla sonuçlandı. Döngü sonlandırılıyor.")
            break # İşlem başarıyla bitti, tamamen çık.
            
        else:
            print("[-] Resim yükleme veya video üretme aşamasında kritik bir hata oluştu.")
            # İstersen burada da "continue" diyerek hatalarda başa sarmasını sağlayabilirsin,
            # ancak sonsuz hata döngüsüne girmemesi için şu an programı sonlandırıyorum.
            break
