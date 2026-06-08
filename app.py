import os
import re
import uuid
import json
import time
import io
import base64
import requests
import threading
from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for

try:
    from yollyAI import eTemp, find_working_proxy, make_proxied_session
except ImportError:
    import random, string
    from concurrent.futures import ThreadPoolExecutor
    import queue as _queue

    class eTemp:
        def random_email(self, length):
            return ''.join(
                random.SystemRandom().choice(string.ascii_lowercase + string.digits)
                for _ in range(length)
            )
        def getEmail(self):
            return self.random_email(15) + '@spamok.com'
        def getVerificationCode(self, mail, timeout=30):
            address = mail.replace('@spamok.com', '')
            for _ in range(timeout):
                try:
                    r = requests.get(f'https://api.spamok.com/v2/EmailBox/{address}', timeout=5)
                    for m in r.json().get('mails', []):
                        if 'Verification Code' in m.get('subject', '') or 'yolly.ai' in m.get('fromDomain', ''):
                            email_r = requests.get(f'https://api.spamok.com/v2/Email/{address}/{m["id"]}', timeout=5)
                            match = re.search(r'\b\d{6}\b', email_r.json().get('messagePlain', ''))
                            if match:
                                return match.group(0)
                except Exception:
                    pass
                time.sleep(2)
            return None

    PROXYSCRAPE_URL = (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&proxy_format=protocolipport&format=text"
    )

    def fetch_proxies():
        try:
            r = requests.get(PROXYSCRAPE_URL, timeout=10)
            proxies = [line.strip() for line in r.text.splitlines() if line.strip()]
            random.shuffle(proxies)
            return proxies
        except Exception:
            return []

    def test_proxy(proxy_url, test_url="https://www.yolly.ai", timeout=5):
        try:
            r = requests.get(test_url, proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout)
            return r.status_code < 500
        except Exception:
            return False

    def find_working_proxy(max_workers=30):
        proxy_list = fetch_proxies()
        if not proxy_list:
            return None
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
                last = (tested_count[0] == total)
            if ok and not found_event.is_set():
                found_event.set()
                result_q.put(proxy)
            elif last:
                result_q.put(None)

        executor = ThreadPoolExecutor(max_workers=max_workers)
        executor.map(probe, proxy_list)
        working = result_q.get()
        found_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
        return working

    def make_proxied_session(proxy_url):
        s = requests.Session()
        if proxy_url:
            s.proxies = {"http": proxy_url, "https": proxy_url}
        s.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "origin": "https://www.yolly.ai",
            "referer": "https://www.yolly.ai/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        })
        return s


app = Flask(__name__)
app.secret_key = "yolly_ai_video_studio_super_secret_key_2025"

# ── In-memory stores ──────────────────────────────────────────────────────────
GENERATION_HISTORY = []
CUSTOM_PROMPTS = []
ACTIVE_JOBS = {}

APP_PASSWORD = "123"

# ── Model Configuration ───────────────────────────────────────────────────────
MODELS_CONFIG = {
    "Grok Imagine": {
        "model_id": "grok-imagine",
        "resolutions": ["480p", "720p"],
        "durations": ["6", "10"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "2:3", "3:2"],
        "supports_image": True
    }
}


# ── Auth ──────────────────────────────────────────────────────────────────────
def is_logged_in():
    return session.get('logged_in') == True

@app.route('/')
def index():
    return render_template('index.html', logged_in=is_logged_in())

@app.route('/login', methods=['POST'])
def login():
    password = request.form.get('password')
    if password == APP_PASSWORD:
        session['logged_in'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Hatalı şifre!'}), 401

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))


# ── Model API ─────────────────────────────────────────────────────────────────
@app.route('/api/models')
def get_models():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(MODELS_CONFIG)


# ── Gallery APIs ──────────────────────────────────────────────────────────────
@app.route('/api/history')
def get_history():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(GENERATION_HISTORY)

@app.route('/api/history/delete', methods=['POST'])
def delete_history_item():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    item_id = request.json.get('id')
    global GENERATION_HISTORY
    GENERATION_HISTORY = [item for item in GENERATION_HISTORY if item.get('id') != item_id]
    return jsonify({'success': True})

@app.route('/api/history/clear-all', methods=['POST'])
def clear_history():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    global GENERATION_HISTORY
    GENERATION_HISTORY = []
    return jsonify({'success': True})


# ── Prompts APIs ──────────────────────────────────────────────────────────────
@app.route('/api/prompts')
def get_prompts():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(CUSTOM_PROMPTS)

@app.route('/api/prompts/add', methods=['POST'])
def add_prompt():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    prompt_text = request.json.get('prompt', '').strip()
    tag = request.json.get('tag', 'Genel').strip()
    if not prompt_text:
        return jsonify({'error': 'Prompt boş olamaz!'}), 400
    new_prompt = {'id': uuid.uuid4().hex, 'prompt': prompt_text, 'tag': tag}
    CUSTOM_PROMPTS.insert(0, new_prompt)
    return jsonify({'success': True, 'prompt': new_prompt})

@app.route('/api/prompts/edit', methods=['POST'])
def edit_prompt():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    prompt_id = request.json.get('id')
    prompt_text = request.json.get('prompt', '').strip()
    tag = request.json.get('tag', 'Genel').strip()
    if not prompt_text:
        return jsonify({'error': 'Prompt boş olamaz!'}), 400
    for p in CUSTOM_PROMPTS:
        if p['id'] == prompt_id:
            p['prompt'] = prompt_text
            p['tag'] = tag
            return jsonify({'success': True, 'prompt': p})
    return jsonify({'error': 'Prompt bulunamadı!'}), 404

@app.route('/api/prompts/delete', methods=['POST'])
def delete_prompt():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    prompt_id = request.json.get('id')
    global CUSTOM_PROMPTS
    CUSTOM_PROMPTS = [p for p in CUSTOM_PROMPTS if p['id'] != prompt_id]
    return jsonify({'success': True})

@app.route('/api/prompts/clear-all', methods=['POST'])
def clear_prompts():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    global CUSTOM_PROMPTS
    CUSTOM_PROMPTS = []
    return jsonify({'success': True})


# ── Start Generation Job ──────────────────────────────────────────────────────
@app.route('/api/generate/start', methods=['POST'])
def start_generation():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401

    prompt = request.form.get('prompt', '').strip()
    model = request.form.get('model', '').strip()
    aspect_ratio = request.form.get('aspect_ratio', '16:9').strip()
    resolution = request.form.get('resolution', '480p').strip()
    duration = request.form.get('duration', '6').strip()
    input_mode = request.form.get('input_mode', 'text').strip()

    if not prompt:
        return jsonify({'error': 'Prompt girmelisiniz!'}), 400
    if model not in MODELS_CONFIG:
        return jsonify({'error': 'Geçersiz model seçimi!'}), 400

    # Clean up stale jobs (older than 1 hour)
    now = time.time()
    for jid in list(ACTIVE_JOBS.keys()):
        if now - ACTIVE_JOBS[jid]['created_at'] > 3600:
            ACTIVE_JOBS.pop(jid, None)

    job_id = uuid.uuid4().hex

    # Store reference image in RAM if provided
    memory_image = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '':
            file_bytes = file.read()
            if len(file_bytes) > 0:
                memory_image = {
                    'filename': file.filename,
                    'content': file_bytes,
                    'content_type': file.content_type or 'image/jpeg'
                }

    ACTIVE_JOBS[job_id] = {
        'prompt': prompt,
        'model': model,
        'aspect_ratio': aspect_ratio,
        'resolution': resolution,
        'duration': duration,
        'input_mode': input_mode,
        'image': memory_image,
        'created_at': time.time(),
        'status': 'finding_proxy',
        'pct': 0,
        'logs': [],
        'outputs': [],
        'thumbnail': '',
        'error': None
    }

    t = threading.Thread(target=run_job_in_background, args=(job_id,))
    t.daemon = True
    t.start()

    return jsonify({'job_id': job_id})


def run_job_in_background(job_id):
    job = ACTIVE_JOBS.get(job_id)
    if not job:
        return

    def add_log(msg, status="info", pct=None):
        if job_id not in ACTIVE_JOBS:
            return
        if pct is not None:
            job['pct'] = pct
        lt = time.localtime()
        time_str = f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
        job['logs'].append({'message': msg, 'status': status, 'pct': job['pct'], 'time': time_str})

    def fail(msg, pct=None):
        add_log(msg, "error", pct)
        if job_id in ACTIVE_JOBS:
            ACTIVE_JOBS[job_id]['status'] = 'failed'
            ACTIVE_JOBS[job_id]['error'] = msg

    prompt = job['prompt']
    model_name = job['model']
    aspect_ratio = job['aspect_ratio']
    resolution = job['resolution']
    duration = job['duration']
    input_mode = job['input_mode']
    ref_image = job['image']

    try:
        add_log("İşlem başlatıldı...", "info", 3)

        # ── Step 1: Proxy Bulma ───────────────────────────────────────────────
        if job_id not in ACTIVE_JOBS: return
        add_log("ProxyScrape'den çalışan proxy aranıyor (paralel tarama)...", "finding_proxy", 5)

        working_proxy = find_working_proxy(max_workers=30)

        if job_id not in ACTIVE_JOBS: return

        if working_proxy:
            add_log(f"Çalışan proxy bulundu: {working_proxy}", "finding_proxy", 15)
            sess = make_proxied_session(working_proxy)
        else:
            add_log("Çalışan proxy bulunamadı. Proxysiz devam ediliyor...", "warning", 15)
            sess = make_proxied_session(None)

        # ── Step 2: Geçici E-posta + Kod Gönderme ───────────────────────────
        if job_id not in ACTIVE_JOBS: return
        add_log("Spamok üzerinden geçici e-posta oluşturuluyor...", "registering", 18)

        temp = eTemp()
        email = temp.getEmail()
        add_log(f"E-posta oluşturuldu: {email}", "registering", 20)

        if job_id not in ACTIVE_JOBS: return
        add_log("Yolly.AI'ye doğrulama kodu isteği gönderiliyor...", "registering", 22)

        try:
            send_res = sess.post("https://www.yolly.ai/api/auth/send-code", json={"email": email}, timeout=15)
            if send_res.status_code != 200:
                return fail(f"Kod gönderme başarısız (Status {send_res.status_code}): {send_res.text[:100]}", 22)
        except Exception as e:
            return fail(f"Kod gönderme ağ hatası: {e}", 22)

        # ── Step 3: E-posta Kutusunu Tara ────────────────────────────────────
        if job_id not in ACTIVE_JOBS: return
        add_log("Spamok kutusu kontrol ediliyor (30 saniye limit)...", "registering", 28)

        code = temp.getVerificationCode(email, timeout=30)

        if job_id not in ACTIVE_JOBS: return

        if not code:
            return fail("Doğrulama kodu e-postaya ulaşmadı (30s timeout).", 28)

        add_log(f"6 haneli doğrulama kodu yakalandı: {code}", "registering", 35)

        # ── Step 4: CSRF Token ────────────────────────────────────────────────
        if job_id not in ACTIVE_JOBS: return
        try:
            csrf_res = sess.get("https://www.yolly.ai/api/auth/csrf", timeout=15)
            if csrf_res.status_code != 200:
                return fail("CSRF Token alınamadı.", 36)
            csrf_token = csrf_res.json().get("csrfToken")
        except Exception as e:
            return fail(f"CSRF isteği hatası: {e}", 36)

        # ── Step 5: Kodu Doğrula ──────────────────────────────────────────────
        if job_id not in ACTIVE_JOBS: return
        add_log("Doğrulama kodu Yolly.AI'ye gönderiliyor...", "registering", 40)

        verify_headers = dict(sess.headers)
        verify_headers["content-type"] = "application/x-www-form-urlencoded"
        verify_payload = {
            "email": email,
            "code": code,
            "firstVisitPage": "/",
            "redirect": "false",
            "callbackUrl": "https://www.yolly.ai/",
            "csrfToken": csrf_token
        }
        try:
            verify_res = sess.post(
                "https://www.yolly.ai/api/auth/callback/verification-code?",
                data=verify_payload,
                headers=verify_headers,
                timeout=15
            )
            if verify_res.status_code != 200:
                return fail(f"Doğrulama başarısız (Status {verify_res.status_code}).", 40)
        except Exception as e:
            return fail(f"Doğrulama ağ hatası: {e}", 40)

        add_log("Hesap doğrulandı! Session çerezleri alındı.", "registering", 46)

        if job_id not in ACTIVE_JOBS: return

        # Proxy'yi kaldır — üretim/polling kendi bağlantıyla
        add_log("Kayıt tamam! Üretim için proxy kaldırılıyor...", "registering", 48)
        sess.proxies = {}

        # ── Step 6: Kredi Kontrolü ────────────────────────────────────────────
        if job_id not in ACTIVE_JOBS: return
        add_log("Hesap kredisi kontrol ediliyor...", "checking_credits", 50)
        try:
            credits_res = sess.get("https://www.yolly.ai/api/user/credits", timeout=15)
            if credits_res.status_code == 200:
                left = str(credits_res.json().get("left_credits", "0"))
                add_log(f"Mevcut kredi: {left}", "checking_credits", 52)
                if left == "0":
                    return fail("Bakiye 0! Bu hesapta yeterli kredi yok.", 52)
            else:
                add_log(f"Kredi kontrolü başarısız ({credits_res.status_code}), devam ediliyor...", "warning", 52)
        except Exception as e:
            add_log(f"Kredi kontrolü ağ hatası, devam ediliyor: {e}", "warning", 52)

        # ── Step 7: Görsel Yükleme (Image to Video modu) ─────────────────────
        images_payload = []
        if input_mode == "image" and ref_image:
            if job_id not in ACTIVE_JOBS: return
            add_log(f"Referans görsel Yolly.AI'ye yükleniyor...", "uploading", 55)

            ext = os.path.splitext(ref_image['filename'])[1].lower()
            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            b64_data = base64.b64encode(ref_image['content']).decode('utf-8')
            base64_string = f"data:{mime_type};base64,{b64_data}"
            timestamp = int(time.time() * 1000)
            file_name = f"video-input-{timestamp}-0{ext}"

            sess.headers.update({"referer": "https://www.yolly.ai/video"})
            try:
                up_res = sess.post(
                    "https://www.yolly.ai/api/kie/upload",
                    json={"base64Data": base64_string, "fileName": file_name},
                    timeout=30
                )
                if up_res.status_code == 200:
                    image_url = up_res.json().get("data", {}).get("url")
                    if image_url:
                        images_payload = [image_url]
                        add_log(f"Görsel başarıyla yüklendi.", "uploading", 62)
                    else:
                        add_log("Görsel URL alınamadı, text moda geçiliyor...", "warning", 62)
                        input_mode = "text"
                else:
                    add_log(f"Görsel yükleme başarısız (Status {up_res.status_code}), text moda geçiliyor...", "warning", 62)
                    input_mode = "text"
            except Exception as e:
                add_log(f"Görsel yükleme ağ hatası: {e}, text moda geçiliyor...", "warning", 62)
                input_mode = "text"

        job['image'] = None  # RAM'i serbest bırak

        if job_id not in ACTIVE_JOBS: return

        # ── Step 8: Video Üretimini Tetikle ───────────────────────────────────
        config = MODELS_CONFIG[model_name]
        add_log(f"Video üretimi tetikleniyor ({model_name} • {resolution} • {duration}s • {input_mode.upper()})...", "generating", 65)

        gen_payload = {
            "model": config['model_id'],
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

        try:
            gen_res = sess.post("https://www.yolly.ai/api/video/create", json=gen_payload, timeout=20)

            if "Insufficient credits" in gen_res.text:
                return fail("Yetersiz kredi (Insufficient credits).", 65)

            if gen_res.status_code != 200:
                return fail(f"Video üretim isteği başarısız ({gen_res.status_code}): {gen_res.text[:150]}", 65)

            gen_data = gen_res.json()
            task_id = gen_data.get("id")
            provider = gen_data.get("provider", config['model_id'])

            if not task_id:
                return fail(f"Task ID alınamadı: {gen_data}", 65)

        except Exception as e:
            return fail(f"Üretim tetikleme ağ hatası: {e}", 65)

        add_log(f"Üretim kuyruğa alındı! Task ID: {task_id}", "generating", 70)
        add_log("Video işleniyor, 2 saniyede bir kontrol ediliyor...", "generating", 72)

        if job_id not in ACTIVE_JOBS: return

        # ── Step 9: Polling ────────────────────────────────────────────────────
        query_url = "https://www.yolly.ai/api/video/query"
        params = {"id": task_id, "provider": provider}
        start_time = time.time()
        max_duration = 600  # 10 dakika

        while True:
            if job_id not in ACTIVE_JOBS:
                break

            if time.time() - start_time > max_duration:
                fail("Video üretimi zaman aşımına uğradı (10 dakika limiti doldu).", 85)
                break

            try:
                q_res = sess.get(query_url, params=params, timeout=15)
                if q_res.status_code == 200:
                    task_info = q_res.json().get("data", {})
                    status = task_info.get("status")
                    elapsed = int(time.time() - start_time)

                    if status == "completed":
                        video_url = task_info.get("videoUrl", "")
                        thumbnail_url = task_info.get("coverUrl", "")

                        add_log(f"Video üretimi başarıyla tamamlandı! ({elapsed}s)", "completed", 100)

                        item = {
                            'id': uuid.uuid4().hex,
                            'video_url': video_url,
                            'thumbnail_url': thumbnail_url,
                            'prompt': prompt,
                            'model': model_name,
                            'aspect_ratio': aspect_ratio,
                            'resolution': resolution,
                            'duration': duration,
                            'input_mode': input_mode,
                            'created_at': time.strftime('%d.%m.%Y %H:%M:%S')
                        }
                        GENERATION_HISTORY.insert(0, item)

                        if job_id in ACTIVE_JOBS:
                            ACTIVE_JOBS[job_id]['outputs'] = [video_url]
                            ACTIVE_JOBS[job_id]['thumbnail'] = thumbnail_url
                            ACTIVE_JOBS[job_id]['status'] = 'completed'
                        break

                    elif status in ["failed", "error"]:
                        err = task_info.get("error") or "Bilinmeyen sunucu hatası"
                        fail(f"Video üretimi sunucu tarafında başarısız: {err}", 90)
                        break

                    else:
                        pct = min(72 + int((time.time() - start_time) / max_duration * 25), 97)
                        add_log(f"Video işleniyor... (Durum: {status} • {elapsed}s geçti)", "generating", pct)
                else:
                    add_log(f"Sorgu hatası (Status {q_res.status_code}), tekrar deneniyor...", "warning")

            except Exception as e:
                add_log(f"Polling bağlantı hatası, tekrar deneniyor: {e}", "warning")

            time.sleep(2)

    except Exception as e:
        fail(f"Beklenmedik hata: {str(e)}", 90)


# ── SSE Stream ────────────────────────────────────────────────────────────────
@app.route('/api/generate/stream/<job_id>')
def stream_job(job_id):
    if not is_logged_in():
        return Response(f'data: {{"type":"error","message":"Unauthorized"}}\n\n', mimetype='text/event-stream')

    job = ACTIVE_JOBS.get(job_id)
    if not job:
        return Response(f'data: {json.dumps({"type":"error","message":"İşlem bulunamadı"})}\n\n', mimetype='text/event-stream')

    def generate():
        last_sent_idx = 0
        while True:
            current_job = ACTIVE_JOBS.get(job_id)
            if not current_job:
                yield f"data: {json.dumps({'type': 'failed', 'error': 'İşlem iptal edildi.'})}\n\n"
                break

            logs = current_job.get('logs', [])
            while last_sent_idx < len(logs):
                log_entry = logs[last_sent_idx]
                yield f"data: {json.dumps({'type': 'log', 'message': log_entry['message'], 'status': log_entry['status'], 'pct': log_entry['pct']})}\n\n"
                last_sent_idx += 1

            if current_job['status'] == 'completed':
                yield f"data: {json.dumps({'type': 'completed', 'outputs': current_job['outputs'], 'thumbnail': current_job.get('thumbnail', '')})}\n\n"
                break
            elif current_job['status'] == 'failed':
                yield f"data: {json.dumps({'type': 'failed', 'error': current_job['error']})}\n\n"
                break

            time.sleep(1)

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/generate/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401
    if job_id in ACTIVE_JOBS:
        ACTIVE_JOBS.pop(job_id, None)
        return jsonify({'success': True})
    return jsonify({'error': 'İşlem bulunamadı'}), 404


@app.route('/api/generate/active')
def get_active_jobs():
    if not is_logged_in():
        return jsonify({'error': 'Unauthorized'}), 401

    active_list = {}
    for jid, job in ACTIVE_JOBS.items():
        active_list[jid] = {
            'prompt': job['prompt'],
            'model': job['model'],
            'aspect_ratio': job['aspect_ratio'],
            'resolution': job['resolution'],
            'duration': job['duration'],
            'input_mode': job['input_mode'],
            'status': job['status'],
            'pct': job['pct'],
            'logs': job['logs'],
            'outputs': job.get('outputs', []),
            'thumbnail': job.get('thumbnail', ''),
            'error': job.get('error')
        }
    return jsonify(active_list)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
