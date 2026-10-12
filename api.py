import os
import json
import time
import uuid
import threading
import atexit
import base64
import requests
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
import database as db
import services

app = Flask(__name__)
CORS(app)

# Graceful shutdown: polling thread'leri temiz kapansın
_shutdown_event = threading.Event()
atexit.register(lambda: _shutdown_event.set())

# --- Configuration & Constants ---
MAX_CONCURRENT_TASKS = 10

# Proxy headers for image/video proxying (generic, provider-agnostic)
PROXY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )
}

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def verify_api_key():
    """Verifies the API key from request headers and returns api_key_id."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None
    
    # Support both "Bearer <key>" and direct key
    if auth_header.startswith('Bearer '):
        provided_key = auth_header[7:]
    else:
        provided_key = auth_header
    
    # Get API key in database - only existing keys are allowed
    api_key_id = db.get_api_key_id(provided_key)
    return api_key_id

def can_start_new_task(api_key_id):
    """Checks if a new task can be started (max concurrent limit per user)."""
    return db.get_running_task_count(api_key_id) < MAX_CONCURRENT_TASKS



# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE GENERATION WORKER
# ═══════════════════════════════════════════════════════════════════════════════

def process_image_task(task_id, params, api_key_id):
    """Background worker for image generation via Yolly."""
    try:
        db.update_task_status(task_id, 'running')
        try:
            session, email = services.create_and_login_new_account()
            if not session:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            # Link account email to task immediately
            db.update_task_account(task_id, email)

            # Save session for crash recovery
            db.update_task_token(task_id, services.serialize_session_data(session))

            # --- Model mapping ---
            frontend_model = params.get('model', 'NANO_BANANA_PRO')
            yolly_model = services.IMAGE_MODEL_MAP.get(frontend_model, 'nano-banana-pro')
            model_config = services.MODELS.get(yolly_model, {})

            # --- Upload reference images ---
            ref_urls = []
            for img_b64 in params.get('reference_images', []):
                img_data = base64.b64decode(img_b64)
                url = services.upload_image(session, img_data)
                if not url:
                    db.update_task_status(task_id, 'failed')
                    db.add_task_log(task_id, "Task ID: failed")
                    return
                ref_urls.append(url)

            if ref_urls:
                db.update_task_reference_urls(task_id, ref_urls)

            # --- AR & Resolution mapping ---
            ar_raw = params.get('size', '1:1')
            aspect_ratio = services.AR_MAP.get(ar_raw, ar_raw)
            # Validate AR against model
            supported_ars = model_config.get("aspect_ratios", [])
            if supported_ars and aspect_ratio not in supported_ars:
                aspect_ratio = supported_ars[0]

            resolution_raw = params.get('resolution', '2K')
            resolution = services.RESOLUTION_MAP.get(resolution_raw, resolution_raw)
            # Only apply resolution for models that support it
            if not model_config.get("resolutions"):
                resolution = None

            # --- Create image task ---
            ext_task_id = services.create_image(
                session, params.get('prompt', ''), yolly_model,
                aspect_ratio, resolution, ref_urls or None
            )

            if ext_task_id == "INSUFFICIENT_CREDITS":
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            if not ext_task_id:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            db.update_task_external_data(
                task_id, ext_task_id,
                services.serialize_session_data(session)
            )

            # --- Poll for completion ---
            status, image_urls = services.poll_image(
                session, ext_task_id, _shutdown_event
            )

            if status == 'completed' and image_urls:
                db.update_task_status(task_id, 'completed', image_urls[0])
                db.add_task_log(task_id, "Task ID: success")
                # Save the new account and consume quota from random unused DB account
                db.consume_quota_and_save_account(api_key_id, email)
            elif status == 'shutdown':
                return  # Let recovery handle it
            else:
                db.update_task_status(task_id, status or 'failed')
                db.add_task_log(task_id, "Task ID: failed")

        except Exception as e:
            db.update_task_status(task_id, 'error')
            db.add_task_log(task_id, "Task ID: failed")
    except Exception:
        db.update_task_status(task_id, 'error')


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO GENERATION WORKER
# ═══════════════════════════════════════════════════════════════════════════════

def process_video_task(task_id, params, api_key_id):
    """Background worker for video generation via Yolly."""
    try:
        db.update_task_status(task_id, 'running')
        try:
            session, email = services.create_and_login_new_account()
            if not session:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            # Link account email to task immediately
            db.update_task_account(task_id, email)

            # Save session for crash recovery
            db.update_task_token(task_id, services.serialize_session_data(session))

            # --- Model mapping ---
            frontend_model = params.get('model', 'SORA_2')
            yolly_model = services.VIDEO_MODEL_MAP.get(frontend_model, 'grok-imagine')
            model_config = services.MODELS.get(yolly_model, {})

            # --- Determine input mode & upload images ---
            images = []
            input_mode = "text"

            start_frame = params.get('start_frame')
            end_frame = params.get('end_frame')
            reference_images = params.get('reference_images', [])

            if start_frame:
                input_mode = "image"
                img_data = base64.b64decode(start_frame)
                start_url = services.upload_image(session, img_data)
                if not start_url:
                    db.update_task_status(task_id, 'failed')
                    db.add_task_log(task_id, "Task ID: failed")
                    return
                images.append(start_url)

                # End frame (only veo3.1-basic supports start+end frame)
                if end_frame and model_config.get("supports_start_end_frame", False):
                    end_data = base64.b64decode(end_frame)
                    end_url = services.upload_image(session, end_data)
                    if not end_url:
                        db.update_task_status(task_id, 'failed')
                        db.add_task_log(task_id, "Task ID: failed")
                        return
                    images.append(end_url)
                    db.update_task_frame_urls(task_id, start_frame_url=start_url, end_frame_url=end_url)
                else:
                    db.update_task_frame_urls(task_id, start_frame_url=start_url, end_frame_url=None)

            elif reference_images:
                # Reference images → use first one as img2video start frame
                input_mode = "image"
                ref_urls = []
                for ref_b64 in reference_images:
                    ref_data = base64.b64decode(ref_b64)
                    ref_url = services.upload_image(session, ref_data)
                    if not ref_url:
                        db.update_task_status(task_id, 'failed')
                        db.add_task_log(task_id, "Task ID: failed")
                        return
                    ref_urls.append(ref_url)
                # Yolly img2video accepts 1 image (or 2 for start/end on veo)
                images = ref_urls[:1]
                db.update_task_reference_urls(task_id, ref_urls)

            # --- AR mapping ---
            size_raw = params.get('size', '16:9')
            ar = services.AR_MAP.get(size_raw, size_raw)
            supported_ars = model_config.get("aspect_ratios", [])
            if supported_ars and ar not in supported_ars:
                ar = supported_ars[0]

            # --- Resolution & Duration from model config ---
            resolution = model_config.get("resolutions", ["720p"])[0]
            duration = model_config.get("durations", ["6"])[0]

            # --- Create video task ---
            ext_task_id, provider = services.create_video(
                session, params.get('prompt', ''), yolly_model,
                input_mode, images, resolution, duration, ar
            )

            if ext_task_id == "INSUFFICIENT_CREDITS":
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            if not ext_task_id:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                return

            # Store session + provider for recovery
            token_data = services.serialize_session_data(session, {"provider": provider})
            db.update_task_external_data(task_id, ext_task_id, token_data)

            # --- Poll for completion ---
            status, video_url = services.poll_video(
                session, ext_task_id, provider, _shutdown_event
            )

            if status == 'completed' and video_url:
                db.update_task_status(task_id, 'completed', video_url)
                db.add_task_log(task_id, "Task ID: success")
                # Save the new account and consume quota from random unused DB account
                db.consume_quota_and_save_account(api_key_id, email)
            elif status == 'shutdown':
                return  # Let recovery handle it
            else:
                db.update_task_status(task_id, status or 'failed')
                db.add_task_log(task_id, "Task ID: failed")

        except Exception as e:
            db.update_task_status(task_id, 'error')
            db.add_task_log(task_id, "Task ID: failed")
    except Exception:
        db.update_task_status(task_id, 'error')


# ═══════════════════════════════════════════════════════════════════════════════
# RECOVERY LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def poll_image_recovery(task_id, ext_task_id, session, account_email=None, api_key_id=None):
    """Recovery polling worker for image tasks."""
    try:
        status, image_urls = services.poll_image(session, ext_task_id, _shutdown_event)
        if status == 'completed' and image_urls:
            db.update_task_status(task_id, 'completed', image_urls[0])
            db.add_task_log(task_id, "Task ID: success")
            if account_email and api_key_id:
                db.consume_quota_and_save_account(api_key_id, account_email)
        elif status != 'shutdown':
            db.update_task_status(task_id, status or 'failed')
            db.add_task_log(task_id, "Task ID: failed")
    except Exception as e:
        db.update_task_status(task_id, 'failed')
        db.add_task_log(task_id, "Task ID: failed")


def poll_video_recovery(task_id, ext_task_id, session, provider, account_email=None, api_key_id=None):
    """Recovery polling worker for video tasks."""
    try:
        status, video_url = services.poll_video(session, ext_task_id, provider, _shutdown_event)
        if status == 'completed' and video_url:
            db.update_task_status(task_id, 'completed', video_url)
            db.add_task_log(task_id, "Task ID: success")
            if account_email and api_key_id:
                db.consume_quota_and_save_account(api_key_id, account_email)
        elif status != 'shutdown':
            db.update_task_status(task_id, status or 'failed')
            db.add_task_log(task_id, "Task ID: failed")
    except Exception as e:
        db.update_task_status(task_id, 'failed')
        db.add_task_log(task_id, "Task ID: failed")


def resume_incomplete_tasks():
    """Recovers stale tasks and resumes polling for submitted ones."""
    print("=" * 50)
    print("[STARTUP] Starting crash recovery...")

    # Phase 1: Clean up truly stale tasks
    try:
        recovery_result = db.recover_stale_tasks()
        if recovery_result['failed_count'] > 0:
            print(f"[STARTUP] Marked {recovery_result['failed_count']} tasks as failed (never logged in)")
    except Exception as e:
        print(f"[STARTUP] Error during stale task recovery: {e}")
        recovery_result = {'needs_check': []}

    # Phase 2: Tasks with token but no external_task_id → never submitted → fail
    needs_check = recovery_result.get('needs_check', [])
    if needs_check:
        print(f"[STARTUP] Marking {len(needs_check)} unsubmitted tasks as failed...")
        for t in needs_check:
            db.update_task_status(t['task_id'], 'failed')
            db.add_task_log(t['task_id'], "Task ID: failed")

    # Phase 3: Resume polling for confirmed submitted tasks
    try:
        tasks = db.get_incomplete_tasks()
        if tasks:
            print(f"[STARTUP] Resuming polling for {len(tasks)} submitted tasks...")
        else:
            print("[STARTUP] No tasks to resume.")

        for t in tasks:
            task_id = t['task_id']
            mode = t['mode']
            ext_id = t['external_task_id']
            token_str = t['token']
            account_email = t.get('account_email')
            api_key_id_val = t.get('api_key_id')

            # TTS/Music: no longer supported → mark failed
            if mode in ('tts', 'music'):
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                continue

            # Deserialize session
            try:
                session, data = services.deserialize_session_data(token_str)
            except Exception:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "Task ID: failed")
                continue

            print(f"  [RESUME] Task {task_id} ({mode}) — External ID: {ext_id}")

            if mode == 'image':
                threading.Thread(
                    target=poll_image_recovery,
                    args=(task_id, ext_id, session, account_email, api_key_id_val)
                ).start()
            elif mode == 'video':
                provider = data.get("provider", "")
                threading.Thread(
                    target=poll_video_recovery,
                    args=(task_id, ext_id, session, provider, account_email, api_key_id_val)
                ).start()

    except Exception as e:
        print(f"[STARTUP] Error during task resume: {e}")

    print("[STARTUP] Crash recovery complete.")
    print("=" * 50)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK FIELD FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

TASK_FIELDS_BY_MODE = {
    'image': ['task_id', 'mode', 'status', 'result_url', 'prompt', 'model', 'size', 'resolution', 'reference_image_urls', 'logs', 'created_at'],
    'video': ['task_id', 'mode', 'status', 'result_url', 'prompt', 'model', 'size', 'resolution', 'duration', 'start_frame_url', 'end_frame_url', 'reference_image_urls', 'logs', 'created_at'],
    'tts':   ['task_id', 'mode', 'status', 'result_url', 'prompt', 'model', 'voice_id', 'speed', 'pitch', 'volume', 'emotion', 'logs', 'created_at'],
    'music': ['task_id', 'mode', 'status', 'result_url', 'prompt', 'model', 'style', 'lyrics', 'instrumental', 'audio_usage', 'reference_audio_url', 'logs', 'created_at'],
}

def filter_task_fields(task):
    """Filters task dict fields based on mode."""
    if not task:
        return task
    mode = task.get('mode')
    fields = TASK_FIELDS_BY_MODE.get(mode, list(task.keys()))
    result = {k: task[k] for k in fields if k in task}
    # Convert instrumental integer to boolean for music tasks
    if mode == 'music' and 'instrumental' in result:
        result['instrumental'] = bool(result.get('instrumental'))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[ERROR] {request.method} {request.path} → {type(e).__name__}: {e}")
    return jsonify({"error": "Internal server error"}), 500

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/api-doc', methods=['GET'])
def api_doc():
    return render_template('apiDocNoTTS.html')

@app.route('/api/image-proxy', methods=['GET'])
def image_proxy():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "url parametresi gerekli"}), 400

    fwd_headers = dict(PROXY_HEADERS)
    range_header = request.headers.get('Range')
    if range_header:
        fwd_headers['Range'] = range_header  # video/mp3 seek desteği

    r = requests.get(url, headers=fwd_headers, stream=True, timeout=(30, 120))

    excluded = {'content-encoding', 'transfer-encoding', 'connection'}
    resp_headers = [(k, v) for k, v in r.headers.items() if k.lower() not in excluded]

    return Response(r.iter_content(chunk_size=8192), status=r.status_code, headers=resp_headers)


@app.route('/screenshot', methods=['GET'])
@app.route('/api/browser/screenshot', methods=['GET'])
def browser_screenshot():
    with services._browser_state_lock:
        img_bytes = services.BROWSER_STATE.get("last_screenshot")
        ss_time = services.BROWSER_STATE.get("last_screenshot_time", 0)
        status_str = services.BROWSER_STATE.get("status", "Boşta")

    if img_bytes:
        resp = Response(img_bytes, mimetype="image/png")
        resp.headers['X-Screenshot-Time'] = str(ss_time)
        resp.headers['X-Browser-Status'] = status_str
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return resp
    return jsonify({"error": "Henüz ekran görüntüsü alınmadı", "status": status_str}), 404


@app.route('/api/browser/capture', methods=['POST', 'GET'])
def browser_capture():
    status_code, message = services.trigger_browser_capture()
    return jsonify({
        "ok": True,
        "status": status_code,
        "message": message,
        "browser_status": services.get_browser_status()
    })


@app.route('/api/browser/status', methods=['GET'])
def browser_status():
    return jsonify(services.get_browser_status())


# ── IMAGE GENERATION ──────────────────────────────────────────────────────────

@app.route('/api/generate/image', methods=['POST'])
def generate_image():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    images = data.get('reference_images', [])
    if isinstance(images, list) and len(images) > 5:
        return jsonify({"error": "Maximum 5 images allowed"}), 400

    if len(data.get('prompt', '')) > 4000:
        return jsonify({"error": "Prompt must be 4000 characters or less"}), 400

    model = data.get('model', 'NANO_BANANA_PRO')
    if model in ('NANO_BANANA_PRO', 'NANO_BANANA_2', 'GPT_IMAGE_2'):
        resolution = data.get('resolution', '2K')
        if resolution not in ['1K', '2K', '4K']:
            return jsonify({"error": "Invalid resolution. Must be one of: 1K, 2K, 4K"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503
    
    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    size = data.get('size', '16:9')
    resolution = data.get('resolution', '2K') if model in ('NANO_BANANA_PRO', 'NANO_BANANA_2', 'GPT_IMAGE_2') else None
    db.create_task(api_key_id, task_id, 'image',
                   prompt=data.get('prompt'),
                   model=model,
                   size=size,
                   resolution=resolution,
                   duration=None)
    
    threading.Thread(target=process_image_task, args=(task_id, data, api_key_id)).start()
    return jsonify({"task_id": task_id})


# ── VIDEO GENERATION ──────────────────────────────────────────────────────────

@app.route('/api/generate/video', methods=['POST'])
def generate_video():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400

    if len(data.get('prompt', '')) > 2000:
        return jsonify({"error": "Prompt must be 2000 characters or less"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503

    model = data.get('model', 'SORA_2')

    # Model-specific validations (kept from original for frontend compatibility)
    if model == 'VIDU_Q3':
        if not data.get('start_frame'):
            return jsonify({"error": "VIDU_Q3 model requires a start frame (image)"}), 400
        if data.get('end_frame'):
            return jsonify({"error": "VIDU_Q3 model does not support end_frame"}), 400
        if data.get('reference_images'):
            return jsonify({"error": "VIDU_Q3 model does not support reference_images"}), 400

    if model == 'SORA_2':
        if data.get('end_frame'):
            return jsonify({"error": "SORA_2 model does not support end_frame"}), 400
        if data.get('reference_images'):
            return jsonify({"error": "SORA_2 model does not support reference_images"}), 400

    if model == 'QUALITY_V2_5':
        if not data.get('start_frame'):
            return jsonify({"error": "QUALITY_V2_5 model requires a start frame (image)"}), 400
        if data.get('end_frame'):
            return jsonify({"error": "QUALITY_V2_5 model does not support end_frame"}), 400
        if data.get('reference_images'):
            return jsonify({"error": "QUALITY_V2_5 model does not support reference_images"}), 400

    if model == 'VEO_3' and data.get('end_frame') and not data.get('start_frame'):
        return jsonify({"error": "end_frame requires image (start frame) to be provided"}), 400

    if model == 'VEO_3':
        reference_images = data.get('reference_images', [])
        if isinstance(reference_images, list) and len(reference_images) > 3:
            return jsonify({"error": "Maximum 3 reference images allowed"}), 400
        if reference_images and (data.get('start_frame') or data.get('end_frame')):
            return jsonify({"error": "reference_images cannot be used together with image or end_frame"}), 400

    if model == 'SEEDANCE_2_0' and data.get('end_frame') and not data.get('start_frame'):
        return jsonify({"error": "end_frame requires image (start frame) to be provided"}), 400

    if model == 'SEEDANCE_2_0':
        reference_images = data.get('reference_images', [])
        if isinstance(reference_images, list) and len(reference_images) > 3:
            return jsonify({"error": "Maximum 3 reference images allowed"}), 400
        if reference_images and (data.get('start_frame') or data.get('end_frame')):
            return jsonify({"error": "reference_images cannot be used together with image or end_frame"}), 400
    
    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429

    # Get Yolly model params for DB storage
    yolly_model, yolly_res, yolly_dur, _ = services.get_video_params(model)

    task_id = str(uuid.uuid4())
    size = data.get('size', '16:9')
    db.create_task(api_key_id, task_id, 'video',
                   prompt=data.get('prompt'),
                   model=model,
                   size=size,
                   resolution=yolly_res,
                   duration=int(yolly_dur))
    
    threading.Thread(target=process_video_task, args=(task_id, data, api_key_id)).start()
    return jsonify({"task_id": task_id})


# ── TTS (DISABLED) ────────────────────────────────────────────────────────────

@app.route('/api/generate/tts', methods=['POST'])
def generate_tts():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "error": "This feature is currently unavailable.",
        "message": "Text-to-Speech generation is temporarily disabled. Please try again later."
    }), 503

@app.route('/api/tts/voices', methods=['GET'])
def get_tts_voices():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "error": "This feature is currently unavailable.",
        "message": "TTS voice listing is temporarily disabled."
    }), 503


# ── MUSIC (DISABLED) ──────────────────────────────────────────────────────────

@app.route('/api/generate/music', methods=['POST'])
def generate_music():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "error": "This feature is currently unavailable.",
        "message": "Music generation is temporarily disabled. Please try again later."
    }), 503


# ── STATUS & TASK MANAGEMENT ─────────────────────────────────────────────────

@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    task = db.get_task(api_key_id, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    
    result = filter_task_fields(task)
    
    return jsonify(result)
    
@app.route('/api/status', methods=['GET'])
def get_all_tasks_status():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    running_count = db.get_running_task_count(api_key_id)

    page_param = request.args.get('page')
    if page_param is not None:
        try:
            page = max(1, int(page_param))
        except ValueError:
            return jsonify({"error": "Invalid page parameter"}), 400

        per_page_param = request.args.get('per_page', 6)
        try:
            per_page = max(1, int(per_page_param))
        except ValueError:
            return jsonify({"error": "Invalid per_page parameter"}), 400

        tasks_raw, total = db.get_tasks_paginated(api_key_id, page, per_page)
        tasks = [filter_task_fields(t) for t in tasks_raw]
        import math
        total_pages = math.ceil(total / per_page) if total > 0 else 1

        return jsonify({
            "tasks": tasks,
            "running_tasks": running_count,
            "max_concurrent": MAX_CONCURRENT_TASKS,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages
        })

    tasks_raw = db.get_all_tasks(api_key_id)
    tasks = [filter_task_fields(t) for t in tasks_raw]
    return jsonify({
        "tasks": tasks,
        "running_tasks": running_count,
        "max_concurrent": MAX_CONCURRENT_TASKS
    })


# ── QUOTA & ACCOUNTS ─────────────────────────────────────────────────────────

@app.route('/api/quota', methods=['GET'])
def get_quota():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    running_count = db.get_running_task_count(api_key_id)
    return jsonify({
        "quota": db.get_account_count(api_key_id),
        "running_tasks": running_count,
        "max_concurrent": MAX_CONCURRENT_TASKS,
        "available_slots": MAX_CONCURRENT_TASKS - running_count
    })

@app.route('/api/accounts/add', methods=['POST'])
def add_accounts():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'accounts' not in data:
        return jsonify({"error": "accounts field required"}), 400
    
    added = 0
    failed = 0
    for acc_str in data['accounts']:
        if ':' in acc_str:
            parts = acc_str.split(':')
            if len(parts) >= 2:
                email = parts[0].strip()
                password = parts[1].strip()
                if db.add_account(api_key_id, email, password):
                    added += 1
                else:
                    failed += 1
    
    return jsonify({
        "message": f"Added {added} accounts, {failed} failed (duplicates)",
        "total_accounts": db.get_account_count(api_key_id)
    })

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    accounts = db.get_all_accounts(api_key_id)
    return jsonify({
        "accounts": accounts,
        "total": len(accounts),
        "available": sum(1 for a in accounts if not a['used'])
    })

@app.route('/api/accounts/<email>', methods=['DELETE'])
def delete_account(email):
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    if db.delete_account(api_key_id, email):
        return jsonify({"message": f"Account {email} deleted"})
    else:
        return jsonify({"error": "Account not found"}), 404
