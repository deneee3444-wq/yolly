import os
import json
import time
import uuid
import math
import threading
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
import requests
import database as db
import service

app = Flask(__name__)
CORS(app)

# Maximum concurrent tasks
MAX_CONCURRENT_TASKS = 10

# --- Helper Functions ---

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

# --- Error Handler ---

@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[ERROR] {request.method} {request.path} → {type(e).__name__}: {e}")
    return jsonify({"error": "Internal server error"}), 500

# --- Page Routes ---

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/api-doc', methods=['GET'])
def api_doc():
    return render_template('apiDocNoTTS.html')

# --- Models Endpoint ---

@app.route('/api/models', methods=['GET'])
def get_models():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(service.get_available_models())

@app.route('/api/models/<mode>', methods=['GET'])
def get_models_by_mode(mode):
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    models = service.get_available_models(mode)
    if not models:
        return jsonify({"error": f"Unknown mode: {mode}"}), 404
    return jsonify(models)

# --- Proxy ---

@app.route('/api/proxy', methods=['GET'])
def proxy():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "url parametresi gerekli"}), 400

    range_header = request.headers.get('Range')
    stream_content, status_code, headers = service.proxy_request(url, range_header)

    return Response(stream_content, status=status_code, headers=headers)


# --- Generation Endpoints ---

@app.route('/api/generate/image', methods=['POST'])
def generate_image():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400

    image_models = service.get_available_models('image')
    default_model = image_models[0]['id'] if image_models else 'default'
    model = data.get('model') or default_model
    model_meta = next((m for m in image_models if m['id'] == model), None)
    
    if not model_meta:
        return jsonify({"error": f"Unknown model: {model}"}), 400

    # 1. Validate Prompt
    max_prompt = model_meta.get('max_prompt_length', 4000)
    if len(data.get('prompt', '')) > max_prompt:
        return jsonify({"error": f"Prompt must be {max_prompt} characters or less"}), 400

    # 2. Validate Reference Images
    images = data.get('reference_images', [])
    supports_ref = model_meta.get('supports_reference_images', False)
    max_ref = model_meta.get('max_reference_images', 5)
    if images:
        if not supports_ref:
            return jsonify({"error": f"{model} model does not support reference_images"}), 400
        if isinstance(images, list) and len(images) > max_ref:
            return jsonify({"error": f"Maximum {max_ref} images allowed"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503
    
    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429

    # 3. Resolve Size
    size = data.get('size')
    supported_sizes = model_meta.get('supported_sizes', [])
    if supported_sizes:
        if size not in supported_sizes:
            size = model_meta.get('default_size') or supported_sizes[0]
    else:
        size = size or '16:9'

    # 4. Resolve Resolution
    resolution = data.get('resolution')
    supported_resolutions = model_meta.get('supported_resolutions', [])
    if supported_resolutions:
        if resolution not in supported_resolutions:
            resolution = model_meta.get('default_resolution') or supported_resolutions[0]
    else:
        resolution = None
    
    task_id = str(uuid.uuid4())
    db.create_task(api_key_id, task_id, 'image',
                   prompt=data.get('prompt'),
                   model=model,
                   size=size,
                   resolution=resolution,
                   duration=None)
    
    threading.Thread(target=service.process_image_task, args=(task_id, data, api_key_id)).start()
    return jsonify({"task_id": task_id})


@app.route('/api/generate/video', methods=['POST'])
def generate_video():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400

    video_models = service.get_available_models('video')
    default_model = video_models[0]['id'] if video_models else 'default'
    model = data.get('model') or default_model
    model_meta = next((m for m in video_models if m['id'] == model), None)
    
    if not model_meta:
        return jsonify({"error": f"Unknown model: {model}"}), 400

    # 1. Validate Prompt
    max_prompt = model_meta.get('max_prompt_length', 2000)
    if len(data.get('prompt', '')) > max_prompt:
        return jsonify({"error": f"Prompt must be {max_prompt} characters or less"}), 400

    # 2. Validate Frame inputs
    # Check start_frame support
    has_start = bool(data.get('start_frame'))
    requires_start = model_meta.get('requires_start_frame', False)
    supports_start = model_meta.get('supports_start_frame', True)
    if requires_start and not has_start:
        return jsonify({"error": f"{model} model requires a start frame (image)"}), 400
    if has_start and not supports_start:
        return jsonify({"error": f"{model} model does not support start_frame"}), 400

    # Check end_frame support
    has_end = bool(data.get('end_frame'))
    supports_end = model_meta.get('supports_end_frame', False)
    if has_end and not supports_end:
        return jsonify({"error": f"{model} model does not support end_frame"}), 400
    if has_end and not has_start:
        return jsonify({"error": "end_frame requires image (start frame) to be provided"}), 400

    # Check reference images support
    ref_images = data.get('reference_images', [])
    supports_ref = model_meta.get('supports_reference_images', False)
    max_ref = model_meta.get('max_reference_images', 0)
    if ref_images:
        if not supports_ref:
            return jsonify({"error": f"{model} model does not support reference_images"}), 400
        if len(ref_images) > max_ref:
            return jsonify({"error": f"Maximum {max_ref} reference images allowed"}), 400
        if has_start or has_end:
            return jsonify({"error": "reference_images cannot be used together with image or end_frame"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503
    
    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    # 3. Resolve Size
    size = data.get('size')
    supported_sizes = model_meta.get('supported_sizes', [])
    if supported_sizes:
        if size not in supported_sizes:
            size = model_meta.get('default_size') or supported_sizes[0]
    else:
        size = size or '16:9'

    # 4. Resolve Duration
    duration = data.get('duration')
    supported_durations = model_meta.get('supported_durations', [])
    if duration is not None:
        try:
            duration = int(duration)
        except ValueError:
            duration = None
            
    if supported_durations:
        if duration not in supported_durations:
            duration = model_meta.get('default_duration') or supported_durations[0]
    else:
        duration = duration or model_meta.get('duration') or 10

    # 5. Resolve Resolution
    resolution = data.get('resolution')
    supported_resolutions = model_meta.get('supported_resolutions', [])
    if supported_resolutions:
        if resolution not in supported_resolutions:
            resolution = model_meta.get('default_resolution') or supported_resolutions[0]
    else:
        resolution = resolution or model_meta.get('resolution') or '720p'

    # 6. Apply constraints if any
    constraints = model_meta.get('constraints', [])
    for c in constraints:
        cond = c.get('if', {})
        then = c.get('then', {})
        if 'resolution' in cond and cond['resolution'] == resolution:
            if 'duration' in then and duration not in then['duration']:
                duration = then['duration'][0]
        if 'duration' in cond and cond['duration'] == duration:
            if 'resolution' in then and resolution != then['resolution']:
                resolution = then['resolution']
                
    task_id = str(uuid.uuid4())
    db.create_task(api_key_id, task_id, 'video',
                   prompt=data.get('prompt'),
                   model=model,
                   size=size,
                   resolution=resolution,
                   duration=duration)
    
    threading.Thread(target=service.process_video_task, args=(task_id, data, api_key_id)).start()
    return jsonify({"task_id": task_id})


@app.route('/api/generate/tts', methods=['POST'])
def generate_tts():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'text' not in data:
        return jsonify({"error": "Text required"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503
    
    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    tts_models = service.get_available_models('tts')
    default_model = tts_models[0]['id'] if tts_models else 'default'
    model = data.get('model') or default_model

    task_id = str(uuid.uuid4())
    db.create_task(api_key_id, task_id, 'tts',
                   prompt=data.get('text'),
                   model=model,
                   voice_id=data.get('voiceId'),
                   speed=data.get('speed'),
                   pitch=data.get('pitch'),
                   volume=data.get('volume'),
                   emotion=data.get('emotion'))
    
    threading.Thread(target=service.process_tts_task, args=(task_id, data, api_key_id)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/music', methods=['POST'])
def generate_music():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400

    if db.get_account_count(api_key_id) == 0:
        return jsonify({"error": "No quota available"}), 503

    running_count = db.get_running_task_count(api_key_id)
    if running_count >= MAX_CONCURRENT_TASKS:
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {running_count}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429

    music_models = service.get_available_models('music')
    default_model = music_models[0]['id'] if music_models else 'default'
    model = data.get('model') or default_model
    task_id = str(uuid.uuid4())

    # Frontend → API mapping
    main_mode = data.get('main_mode', 'Vocal')
    instrumental = 1 if main_mode == 'Instrumental' else 0
    audio_usage = data.get('mode', 'TEXT')

    db.create_task(api_key_id, task_id, 'music',
                   prompt=data.get('prompt'),
                   model=model,
                   style=data.get('style'),
                   lyrics=data.get('lyrics'),
                   instrumental=instrumental,
                   audio_usage=audio_usage)

    # Worker'a Deevid uyumlu parametreler gönder
    worker_data = {
        'prompt': data.get('prompt', ''),
        'model': model,
        'style': data.get('style', ''),
        'lyrics': data.get('lyrics', ''),
        'instrumental': main_mode == 'Instrumental',
        'audioUsage': audio_usage,
        'audio_base64': data.get('audio'),
    }

    threading.Thread(target=service.process_music_task, args=(task_id, worker_data, api_key_id)).start()
    return jsonify({"task_id": task_id})

# --- TTS Voices ---

@app.route('/api/tts/voices', methods=['GET'])
def get_tts_voices():
    api_key_id = verify_api_key()
    if not api_key_id:
        return jsonify({"error": "Unauthorized"}), 401

    voices, error = service.get_tts_voices(api_key_id)
    if error:
        return jsonify({"error": error}), 503 if error == "No quota available" else 500
    return jsonify({"voices": voices})

# --- Status Endpoints ---

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

# --- Quota & Account Endpoints ---

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
