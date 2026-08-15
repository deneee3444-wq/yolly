"""
Service Module - Self-Contained MyEdit Online Integration
Integrates with MyEdit Online services.
Uses SpamOK / Temp-Mail for on-the-fly account registration and verification.
Saves created accounts directly to the database.

Supported Models:
- Image:
  - NANO_BANANA     -> Google Gemini 2.5 Flash
  - NANO_BANANA_2   -> Google Gemini 3.1 Flash
  - NANO_BANANA_PRO -> Google Gemini 3 Pro
  - GPT_IMAGE_2     -> OpenAI GPT-Image-2
- Video:
  - VEO_3_1         -> Google Veo 3.1 Lite
  - GROK_VIDEO      -> Google Veo 3.1 Lite
"""
import os
import json
import time
import random
import string
import struct
import re
import base64
import threading
import atexit
from urllib.parse import quote
import requests
from typing import Union
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import database as db

# Graceful shutdown event
_shutdown_event = threading.Event()
atexit.register(lambda: _shutdown_event.set())

# ==============================================================================
# MYEDIT API ENDPOINTS & CONSTANTS
# ==============================================================================
INIT_URL = "https://cse.cyberlink.com/cse/v2/init"
SIGNUP_URL = "https://mauth.cyberlink.com/member-auth/public/sign-up"
LOGIN_URL = "https://mauth.cyberlink.com/member-auth/public/sign-in"
TOKEN_EXCHANGE_URL = "https://cse.cyberlink.com/cse/v2/getCseTokenByMember"
DAILY_BONUS_URL = "https://credit.cyberlink.com/v1/member/daily-bonus/get"
CREDIT_KEY_URL = "https://credit.cyberlink.com/v1/app/key"
CREDIT_TASK_BONUS_GET_URL = "https://credit.cyberlink.com/v1/app/task-bonus/get"
CREDIT_MEMBER_REMAIN_URL = "https://credit.cyberlink.com/v2/member/remain"
SUB_AUTH_URL = "https://myedit.online/api/cloud/subscriptions/auth"

MYEDIT_TTI_URL = "https://myedit.online/tti/effect"
MYEDIT_VGEN_URL = "https://myedit.online/vgen/effect"
MYEDIT_VGEN_BUSY_URL = "https://myedit.online/vgen/effect/busy"

AES_IV = b"CyberLinkCSE"  # CSE module 12 byte constant IV
CREDIT_IV = b"CyberLinkCredit"  # Credit module 16 byte IV and AAD
SID_AOL_POL = "ae44600d"  # MyEdit Service ID

MYEDIT_HARDCODED_RSA_PUB = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtvujIyahk6iftVcwDe/N2IN6f6YDebIE/"
    "y9HlOe78HjywtLB4f39MkBJyQItum8IaoPn+cS2JHTDG9oGgSuE47kLqVQH51rZ7Aw+L19Sv8B+8p7CsDDt"
    "OM2QjR7ypa/cAugt0ao0t9eH+vMkiMhsYkZ6uvUDgod+KskwjyDTaGXlAlSc8Orztn44xsGSCxUz86lgsuzRE0"
    "VHPYdVHYYMV8xT3qhExvtNobu2z2wxRUM4TLN397CkANGQLScnQlbG92MMVsFoSBrycSCjv6zUlBFMmnVR4l5"
    "m5CHGbe/2/iNXjhf1aA7XpJVZ/2HybuDUUDSjnsTNDqXU/p2++GT8WQIDAQAB"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://myedit.online",
    "Referer": "https://myedit.online/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

MEMBER_AUTH_PUB_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAs+UBoDNErzXk+r2WdgWegRnXe"
    "DdI9oMbT1x69OPiPIonPHjcHdd0X9t3BWDuhs7VIXOGYnd7ZiacG7461673sDTJ2Ue98C"
    "M1XyzawyQ+8HxzW1BQw+L/3tYWhhMVhdo3sGdftCuY9SCgbvj6EksI4I2SaViv3/pIejK"
    "PxfDtesK5h2TgDmKerjmcvVO6IqPgbVPB0zMCL7hkhODhPb+CpTyh/8h7v4892tCyuC83"
    "LChioIPE7fZ531ERlWK9r1ggwgMuuDSE/uuv6bWp9XpN7zwKU95KAxyBTlDE0AtTLrXC/"
    "w5yCQjaZZE09wW7tM68PUQAIcARO4E9WaNoiEzDQQIDAQAB"
)
MEMBER_AUTH_IV = b"CLMemberAuth"
MEMBER_AUTH_KEY_ID = 1

MEMBER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://mauth.cyberlink.com",
    "Referer": "https://mauth.cyberlink.com/auth/myedit/signup?mode=myedit&isBusiness=false&lang=ENU",
    "User-Agent": HEADERS["User-Agent"],
}

_TEMP_MAIL_BASE = "https://smtp-backend.abhi.at/api"
_TEMP_MAIL_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://temp-mail.abhi.at",
    "referer": "https://temp-mail.abhi.at/",
    "user-agent": HEADERS["User-Agent"],
}

# ==============================================================================
# MODEL CONFIGURATIONS & MAPPINGS
# ==============================================================================

# Image Models Backend Config
IMAGE_MODELS_CONFIG = {
    "gemini_2_5_flash": {
        "name": "Nano Banana",
        "vendor": "Google",
        "actionId_prefix": "genimage_1_img_google_gemini2.5flash",
        "promptLength": 2500,
        "ref_img_limit": 3,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 2},
            "enable": {"1K": 2}
        },
        "default_style": "Style_3003_Custom_Gemini"
    },
    "gemini_3_1_flash": {
        "name": "Nano Banana 2",
        "vendor": "Google",
        "actionId_prefix": "genimage_1_img_google_gemini3.1flash",
        "promptLength": 2500,
        "ref_img_limit": 14,
        "supported_resolutions": ["1K", "2K", "4K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3, "2K": 5, "4K": 7},
            "enable": {"1K": 3, "2K": 5, "4K": 7}
        },
        "default_style": "Style_3002_Custom_Gemini"
    },
    "gemini_3_pro": {
        "name": "Nano Banana Pro",
        "vendor": "Google",
        "actionId_prefix": "genimage_1_img_google_gemini3pro",
        "promptLength": 2500,
        "ref_img_limit": 14,
        "supported_resolutions": ["1K", "2K", "4K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 6, "2K": 6, "4K": 12},
            "enable": {"1K": 6, "2K": 6, "4K": 12}
        },
        "default_style": "Style_3001_Custom_Gemini"
    },
    "gpt_image_2": {
        "name": "GPT-Image-2",
        "vendor": "OpenAI",
        "actionId_prefix": "genimage_1_img_openai_gptimage2",
        "promptLength": 8000,
        "ref_img_limit": 16,
        "supported_resolutions": ["1K", "2K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3, "2K": 6},
            "enable": {"1K": 3, "2K": 6}
        },
        "default_style": "Style_1002_Custom_ChatGPT"
    }
}

# Video Models Backend Config
VIDEO_MODELS_CONFIG = {
    "veo_3_1_lite": {
        "name": "Veo 3.1 Lite",
        "model": "veo-3.1-lite-generate-001",
        "vendor": "Google",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p", "1080p"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 6, 8],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p", "1080p"],
            "TextToVideo": ["720p", "1080p"],
        },
        "supported_durations_by_mode": {
            "ImageToVideo": [4, 6, 8],
            "TextToVideo": [4, 6, 8],
        },
        "action_id": "genvideo_1_sec_google_veo3.1lite_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_google_custom_veo3.1lite_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("ImageToVideo", "none", "720p"): 3,
            ("ImageToVideo", "none", "1080p"): 5,
            ("ImageToVideo", "vendor", "720p"): 3,
            ("ImageToVideo", "vendor", "1080p"): 5,
            ("TextToVideo", "none", "720p"): 2,
            ("TextToVideo", "none", "1080p"): 3,
            ("TextToVideo", "vendor", "720p"): 3,
            ("TextToVideo", "vendor", "1080p"): 5,
            ("none", "720p"): 2,
            ("none", "1080p"): 3,
            ("vendor", "720p"): 3,
            ("vendor", "1080p"): 5,
        },
        "credit": 3,
        "mode": "std",
    }
}

# Mapping dictionaries (Frontend/API Model ID -> Backend Config Key)
IMAGE_MODEL_MAPPING = {
    "NANO_BANANA": "gemini_2_5_flash",
    "NANO_BANANA_2": "gemini_3_1_flash",
    "NANO_BANANA_PRO": "gemini_3_pro",
    "GPT_IMAGE_2": "gpt_image_2",
    # Direct / lower-case keys for compatibility
    "nano_banana": "gemini_2_5_flash",
    "nano_banana_2": "gemini_3_1_flash",
    "nano_banana_pro": "gemini_3_pro",
    "gpt_image_2": "gpt_image_2",
    "gemini_2_5_flash": "gemini_2_5_flash",
    "gemini_3_1_flash": "gemini_3_1_flash",
    "gemini_3_pro": "gemini_3_pro",
}

VIDEO_MODEL_MAPPING = {
    "VEO_3_1": "veo_3_1_lite",
    "GROK_VIDEO": "veo_3_1_lite",
    # Direct / lower-case keys for compatibility
    "veo_3_1": "veo_3_1_lite",
    "grok_video": "veo_3_1_lite",
    "veo_3_1_lite": "veo_3_1_lite",
}

# Available Models for Frontend Listing & API Validation
AVAILABLE_MODELS = {
    "image": [
        {
            "id": "NANO_BANANA",
            "name": "Nano Banana",
            "description": "Nano Banana (Gemini 2.5 Flash) by Google - Supports up to 3 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 3,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "NANO_BANANA_2",
            "name": "Nano Banana 2",
            "description": "Nano Banana 2 (Gemini 3.1 Flash) by Google - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K", "4K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "NANO_BANANA_PRO",
            "name": "Nano Banana Pro",
            "description": "Nano Banana Pro (Gemini 3 Pro) by Google - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K", "4K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "GPT_IMAGE_2",
            "name": "GPT-Image-2",
            "description": "GPT-Image-2 by OpenAI - Supports up to 16 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 16,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 8000
        }
    ],
    "video": [
        {
            "id": "VEO_3_1",
            "name": "Veo 3.1",
            "description": "Veo 3.1 Lite by Google - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 6, 8],
            "supported_resolutions": ["720p", "1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 4,
            "max_prompt_length": 4000
        },
        {
            "id": "GROK_VIDEO",
            "name": "Grok Video",
            "description": "Grok Video (Veo 3.1 Lite) - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 6, 8],
            "supported_resolutions": ["720p", "1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 4,
            "max_prompt_length": 4000
        }
    ],
    "tts": [],
    "music": []
}

def get_available_models(mode=None):
    """Returns available models with real metadata for frontend and API usage."""
    import copy
    models = copy.deepcopy(AVAILABLE_MODELS)
    for model in models.get('video', []):
        real_key = VIDEO_MODEL_MAPPING.get(model['id'], model['id'])
        config = VIDEO_MODELS_CONFIG.get(real_key, {})
        if 'supported_modes' in config:
            model['supported_modes'] = config['supported_modes']
        if 'reference_media_limit' in config:
            model['reference_media_limit'] = config['reference_media_limit']
        if 'supported_frame_modes' in config:
            model['supported_frame_modes'] = config['supported_frame_modes']
        if config.get('supported_modes') == ['ImageToVideo']:
            model['requires_start_frame'] = True

        by_mode_res = config.get('supported_resolutions_by_mode')
        by_mode_dur = config.get('supported_durations_by_mode')
        if by_mode_res:
            model['supported_resolutions_by_mode'] = by_mode_res
        if by_mode_dur:
            model['supported_durations_by_mode'] = by_mode_dur

    if mode:
        return models.get(mode, [])
    return models

# ==============================================================================
# TEMP MAIL CLIENT
# ==============================================================================

class TempMailClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_TEMP_MAIL_HEADERS)
        self.email = None
        self._seen_ids = set()

    def get_email(self) -> str:
        username = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        r = self.session.post(
            f"{_TEMP_MAIL_BASE}/mailboxes/custom",
            json={"username": username},
            timeout=15,
        )
        r.raise_for_status()
        self.email = r.json()["address"]
        print(f"[Temp Mail] Created: {self.email}")
        return self.email

    def wait_for_activation_link(self, timeout: int = 60) -> str:
        print("[Temp Mail] Gelen kutusu sorgulaniyor...")
        deadline = time.time() + timeout
        url = f"{_TEMP_MAIL_BASE}/mailboxes/{quote(self.email, safe='')}/messages"

        while time.time() < deadline:
            try:
                r = self.session.post(url, params={"_": int(time.time() * 1000)}, timeout=15)
                r.raise_for_status()
                inbox = r.json()
            except requests.RequestException as e:
                print(f"[!] Inbox hatasi: {e}")
                time.sleep(2)
                continue

            for msg in inbox.get("messages", []):
                mid = msg["id"]
                if mid in self._seen_ids:
                    continue
                self._seen_ids.add(mid)

                print(f"[+] Yeni mail: {msg['subject']}  (from: {msg['from']})")
                full_r = self.session.get(f"{_TEMP_MAIL_BASE}/messages/{mid}", timeout=15)
                full_r.raise_for_status()
                full = full_r.json()

                parsed = full.get("parsedData") or {}
                html = parsed.get("html", "") or ""
                text = parsed.get("text", "") or ""
                combined = f"{text} {html}"

                # Trace linklerinden aktivasyon linkini bul
                trace_links = re.findall(r'https://membership\.cyberlink\.com/prog/event/autoedm/trace_mem\.jsp\?[^\s"\'<>]+', combined)
                for link in trace_links:
                    link = link.replace("&amp;", "&")
                    if "account-activate" in link or "Activate" in link or "active-member" in link:
                        print(f"  -> Aktivasyon linki bulundu: {link[:80]}...")
                        return link

                # Fallback: Herhangi bir trace linki
                if trace_links:
                    link = trace_links[0].replace("&amp;", "&")
                    print(f"  -> Link ayiklandi (fallback): {link[:80]}...")
                    return link

            print(f"[.] Inbox bos, 2s sonra tekrar...")
            time.sleep(2)

        raise TimeoutError("Aktivasyon maili gelmedi!")

# ==============================================================================
# MEMBER AUTH & CSE ENCRYPTION HELPERS
# ==============================================================================

def create_member_auth_payload(user_data: dict):
    """MemberAuth API icin RSA-OAEP + AES-256-GCM sifreli payload uretir."""
    der_bytes = base64.b64decode(MEMBER_AUTH_PUB_KEY)
    public_key = serialization.load_der_public_key(der_bytes)

    aes_key = AESGCM.generate_key(bit_length=256)
    rsa_encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    a_param = base64.b64encode(rsa_encrypted_aes_key).decode("utf-8")

    json_bytes = json.dumps(user_data, separators=(",", ":")).encode("utf-8")
    aesgcm = AESGCM(aes_key)
    cipher_bytes = aesgcm.encrypt(MEMBER_AUTH_IV, json_bytes, None)
    data_param = base64.b64encode(cipher_bytes).decode("utf-8")

    return {
        "a": a_param,
        "data": data_param,
        "k": MEMBER_AUTH_KEY_ID,
    }, aes_key

def decrypt_member_auth_response(response_b64: str, aes_key: bytes):
    """MemberAuth API'den donen Base64 sifreli yaniti cozer."""
    enc_bytes = base64.b64decode(response_b64)
    aesgcm = AESGCM(aes_key)
    decrypted_bytes = aesgcm.decrypt(MEMBER_AUTH_IV, enc_bytes, None)
    return json.loads(decrypted_bytes.decode("utf-8"))

def get_server_public_key():
    resp = requests.post(INIT_URL, json={"p": "myedit"}, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["public_key"], data["id"]

def create_payload(user_data: dict):
    pub_key_b64, key_id = get_server_public_key()
    der_bytes = base64.b64decode(pub_key_b64)
    public_key = serialization.load_der_public_key(der_bytes)
    aes_key = AESGCM.generate_key(bit_length=256)

    rsa_encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    a_param = base64.b64encode(rsa_encrypted_aes_key).decode("utf-8")

    json_bytes = json.dumps(user_data, separators=(",", ":")).encode("utf-8")
    aesgcm = AESGCM(aes_key)
    cipher_bytes = aesgcm.encrypt(AES_IV, json_bytes, None)
    data_param = base64.b64encode(cipher_bytes).decode("utf-8")

    return {
        "a": a_param,
        "data": data_param,
        "k": str(key_id),
    }, aes_key

def decrypt_response(response_b64: str, aes_key: bytes):
    enc_bytes = base64.b64decode(response_b64)
    aesgcm = AESGCM(aes_key)
    decrypted_bytes = aesgcm.decrypt(AES_IV, enc_bytes, None)
    return json.loads(decrypted_bytes.decode("utf-8"))

def signup(email: str, password: str, lang: str = "enu", country: str = "US"):
    """Kullanici kaydi olusturur (MemberAuth API)."""
    user_data = {
        "email": email,
        "password": password,
        "language": lang.upper(),
        "rec_upgrade": 0,
        "sid": "myedit",
        "nJoint": 62
    }
    payload, aes_key = create_member_auth_payload(user_data)
    res = requests.post(SIGNUP_URL, json=payload, headers=MEMBER_HEADERS, timeout=30)
    res.raise_for_status()
    body = res.json()
    if body.get("status") == "SUCCESS" and "info" in body:
        decrypted = decrypt_member_auth_response(body["info"], aes_key)
        return {"status": "SUCCESS", "info": decrypted}
    return body

def activate_account(activation_url: str):
    """Mail gelen aktivasyon linkini takip eder ve hesabi aktiflestirir."""
    print("Aktivasyon istegi gonderiliyor...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    resp = session.get(activation_url, allow_redirects=True, timeout=30)
    if resp.status_code == 200:
        print("[+] HESAP BASARIYLA AKTIFLESTIRILDI!")
        return True
    else:
        print("[-] Aktivasyon uyarisi: Beklenenden farkli bir sayfaya yonlendi.")
        return False

def login(email: str, password: str, lang: str = "enu", country: str = "US"):
    """Giris yapip cltoken ve memberToken alir (MemberAuth API)."""
    user_data = {
        "email": email,
        "password": password,
        "recaptcha": None,
        "sid": "myedit",
    }
    payload, aes_key = create_member_auth_payload(user_data)
    res = requests.post(LOGIN_URL, json=payload, headers=MEMBER_HEADERS, timeout=30)
    res.raise_for_status()
    body = res.json()
    if body.get("status") == "SUCCESS" and "info" in body:
        decrypted = decrypt_member_auth_response(body["info"], aes_key)
        return {"status": "SUCCESS", "info": decrypted}
    return body

def get_cse_token_by_member(member_token: str):
    """memberToken kullanarak cltoken (CSE Token) takasi yapar."""
    user_data = {"memberToken": member_token}
    payload, aes_key = create_payload(user_data)
    res = requests.post(TOKEN_EXCHANGE_URL, json=payload, headers=HEADERS, timeout=30)
    res.raise_for_status()
    body = res.json()
    if "response" in body:
        decrypted = decrypt_response(body["response"], aes_key)
        return decrypted
    return body

def get_daily_bonus(member_token: str):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {member_token}",
        "Origin": "https://myedit.online",
        "Referer": "https://myedit.online/",
        "User-Agent": HEADERS["User-Agent"],
    }
    payload = {"sid": SID_AOL_POL}
    res = requests.post(DAILY_BONUS_URL, json=payload, headers=headers, timeout=30)
    res.raise_for_status()
    return res.json()

def get_member_remaining_credits(member_token: str):
    try:
        url = f"{CREDIT_MEMBER_REMAIN_URL}?detail=true&lang=ENU&sid={SID_AOL_POL}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": HEADERS["User-Agent"],
            "Authorization": f"Bearer {member_token}",
            "Origin": "https://myedit.online",
            "Referer": "https://myedit.online/",
        }
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        credits_json = res.json()
        return credits_json
    except Exception:
        return None

def get_credit_server_public_key():
    resp = requests.get(CREDIT_KEY_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()["result"]
    return data["key"], data["id"]

def create_credit_payload(data_dict: dict):
    pub_key_b64, key_id = get_credit_server_public_key()
    der_bytes = base64.b64decode(pub_key_b64)
    public_key = serialization.load_der_public_key(der_bytes)
    aes_key = AESGCM.generate_key(bit_length=256)

    rsa_encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    a_param = base64.b64encode(rsa_encrypted_aes_key).decode("utf-8")

    json_bytes = json.dumps(data_dict, separators=(",", ":")).encode("utf-8")
    aesgcm = AESGCM(aes_key)
    cipher_bytes = aesgcm.encrypt(CREDIT_IV, json_bytes, CREDIT_IV)
    data_param = base64.b64encode(cipher_bytes).decode("utf-8")

    return {
        "a": a_param,
        "data": data_param,
        "id": str(key_id),
    }

def claim_task_bonus(member_token: str, feature_id: str = "TextToImage", claim_credit: int = None):
    try:
        data_obj = {
            "sid": SID_AOL_POL,
            "unique_id": "",
            "version": "temp_one_time_free",
            "event_id": feature_id,
            "member_token": member_token,
        }
        if claim_credit is not None:
            data_obj["claim_credit"] = claim_credit
        payload = create_credit_payload(data_obj)
        res = requests.post(CREDIT_TASK_BONUS_GET_URL, json=payload, headers=HEADERS, timeout=30)
        res.raise_for_status()
        return res.json()
    except Exception:
        return None

def check_task_bonus(member_token: str, feature_id: str = "TextToImage"):
    try:
        check_url = "https://credit.cyberlink.com/v1/app/task-bonus/check"
        data_obj = {
            "sid": SID_AOL_POL,
            "unique_id": "",
            "version": "temp_one_time_free",
            "event_id": feature_id,
            "member_token": member_token,
        }
        payload = create_credit_payload(data_obj)
        res = requests.post(check_url, json=payload, headers=HEADERS, timeout=30)
        res.raise_for_status()
        return res.json()
    except Exception:
        return None

def collect_all_bonuses(member_token: str):
    """Kullanicinin tum aktif task bonuslarini ve gunluk bonusunu toplar."""
    print("\n[Bonuses] Tum gunluk ve gorev bonuslari toplaniyor...")
    
    # 1. Gunluk Bonus
    try:
        daily_res = get_daily_bonus(member_token)
        print(f"  -> Gunluk Bonus Toplama Sonucu: {daily_res.get('result', daily_res)}")
    except Exception as e:
        print(f"  [!] Gunluk bonus toplama hatasi: {e}")
        
    # 2. Aktif Gorev Bonuslari
    active_tasks = [
        "TextToImage",      # 14 Kredi
        "AICollage",        # 6 Kredi
        "AIReplacement",    # 6 Kredi
        "TextToVideo",      # 50 Kredi
        "ImageToVideo",     # 50 Kredi
        "Storytelling",     # 40 Kredi
        "LyricsToSong"      # 5 Kredi
    ]
    
    for task_id in active_tasks:
        try:
            check_task_bonus(member_token, feature_id=task_id)
            claim_task_bonus(member_token, feature_id=task_id)
        except Exception as e:
            print(f"  [!] Gorev bonusu ({task_id}) toplanirken hata: {e}")
            
    try:
        credits_json = get_member_remaining_credits(member_token)
        if credits_json:
            total_remain = credits_json.get("total_remain", 0)
            print(f"  -> Kalan Kredi Detayi: {total_remain} Kredi")
    except Exception:
        pass

def sync_feature_credit(feature_id: str = "TextToImage", action_id: str = "genimage_1_img_openai_gptimage2_none_1K", credit: int = 3):
    try:
        url = f"https://credit.cyberlink.com/v1/featurelist/feature?feature_id={feature_id}&action_id={action_id}&credit={credit}&min_unit=1&is_main=false&discount=0&sid={SID_AOL_POL}"
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        return True
    except Exception:
        return False

def build_myedit_iv(timestamp_ms: int, sid: int = 0) -> bytes:
    return struct.pack('>Q', timestamp_ms) + struct.pack('>I', sid)

def get_myedit_rsa_public_key():
    der_bytes = base64.b64decode(MYEDIT_HARDCODED_RSA_PUB)
    return serialization.load_der_public_key(der_bytes)

def rsa_encrypt_aes_key(public_key, aes_key: bytes) -> str:
    encrypted = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return base64.b64encode(encrypted).decode('utf-8')

def encrypt_myedit_aes_gcm(aes_key: bytes, plaintext: Union[str, bytes], timestamp_ms: int, sid: int = 0) -> str:
    iv = build_myedit_iv(timestamp_ms, sid)
    if isinstance(plaintext, str):
        plaintext = plaintext.encode('utf-8')
    aesgcm = AESGCM(aes_key)
    cipher_bytes = aesgcm.encrypt(iv, plaintext, None)
    return base64.b64encode(cipher_bytes).decode('utf-8')

def decrypt_myedit_aes_gcm(aes_key: bytes, ciphertext_b64: str, timestamp_ms: int, sid: int = 0) -> bytes:
    iv = build_myedit_iv(timestamp_ms, sid)
    enc_bytes = base64.b64decode(ciphertext_b64)
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(iv, enc_bytes, None)

def encrypt_myedit_aes_gcm_hex(aes_key: bytes, raw_bytes: bytes, timestamp_ms: int, sid: int = 0) -> str:
    iv = build_myedit_iv(timestamp_ms, sid)
    aesgcm = AESGCM(aes_key)
    cipher_bytes = aesgcm.encrypt(iv, raw_bytes, None)
    return cipher_bytes.hex()

def get_subscription_token(member_token: str) -> str:
    rsa_pub_key = get_myedit_rsa_public_key()
    aes_key = AESGCM.generate_key(bit_length=256)
    ts_ms = int(time.time() * 1000)

    key_param = rsa_encrypt_aes_key(rsa_pub_key, aes_key)
    cl_sid_json = json.dumps({"cl_sid": [SID_AOL_POL]}, separators=(',', ':'))
    receipt_param = encrypt_myedit_aes_gcm(aes_key, cl_sid_json, ts_ms, 0)

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Authorization": f"Bearer {member_token}",
        "Content-Type": "application/json",
        "Origin": "https://myedit.online",
        "Referer": "https://myedit.online/",
    }
    body = {
        "product": "myedit",
        "version": "3.9.0",
        "versiontype": "3.9.0",
        "platform": "web",
        "receipt": receipt_param,
        "key": key_param,
        "timestamp": ts_ms,
    }
    resp = requests.post(SUB_AUTH_URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["subscription_token"]

def format_error_code(error) -> str:
    """Returns strictly the HTTP status code (e.g. '429', '500', '503', '504') for client/frontend log visibility."""
    if not error:
        return "500"
    if isinstance(error, requests.exceptions.RequestException):
        if hasattr(error, 'response') and error.response is not None:
            return str(error.response.status_code)
    
    err_str = str(error)
    match = re.search(r'\b(4\d{2}|5\d{2})\b', err_str)
    if match:
        return match.group(1)
    
    if "timeout" in err_str.lower():
        return "504"
    return "500"

def decrypt_downloaded_media(url: str, aes_key: bytes, enc_key_b64: str, enc_iv_b64: str, init_ts_ms: int, output_path: str = "output.bin"):
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        enc_bytes = resp.content
        raw_key = decrypt_myedit_aes_gcm(aes_key, enc_key_b64, init_ts_ms, 0)
        raw_iv = decrypt_myedit_aes_gcm(aes_key, enc_iv_b64, init_ts_ms, 0)
        aesgcm = AESGCM(raw_key)
        decrypted_bytes = aesgcm.decrypt(raw_iv, enc_bytes, None)
        with open(output_path, "wb") as f:
            f.write(decrypted_bytes)
        return os.path.abspath(output_path)
    except Exception:
        return None

# ==============================================================================
# VIDEO MODEL SPECIFIC FUNCTIONS
# ==============================================================================

def get_image_dimensions(file_path):
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            return img.width, img.height
    except ImportError:
        try:
            with open(file_path, 'rb') as f:
                head = f.read(24)
                if len(head) == 24 and head.startswith(b'\x89PNG\r\n\x1a\n'):
                    check = struct.unpack('>I', head[16:20])[0]
                    if check != 0x0d0a1a0a:
                        w, h = struct.unpack('>II', head[16:24])
                        return w, h
                elif head.startswith(b'\xff\xd8'):
                    f.seek(0)
                    size = 2
                    ftype = 0
                    while not 0xc0 <= ftype <= 0xcf or ftype in (0xc4, 0xc8, 0xcc):
                        f.seek(size, 1)
                        byte = f.read(1)
                        while ord(byte) == 0xff:
                            byte = f.read(1)
                        ftype = ord(byte)
                        size = struct.unpack('>H', f.read(2))[0] - 2
                    f.seek(1, 1)
                    h, w = struct.unpack('>HH', f.read(4))
                    return w, h
        except Exception:
            pass
        return 1280, 720

def check_vgen_busy(referer="https://myedit.online/en/video-editor/text-to-video/edit"):
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://myedit.online",
        "Referer": referer,
    }
    try:
        resp = requests.get(MYEDIT_VGEN_BUSY_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("busy", False)
    except Exception:
        return False

def prepare_image_for_vgen(image_path: str, aspect_ratio: str, target_w: int = None, target_h: int = None, suffix: str = "") -> str:
    try:
        from PIL import Image
        try:
            ar_w, ar_h = map(int, aspect_ratio.split(":"))
            target_ar = ar_w / ar_h
        except Exception:
            target_ar = 16 / 9
            ar_w, ar_h = 16, 9
            
        img = Image.open(image_path)
        orig_w, orig_h = img.size
        orig_ar = orig_w / orig_h
        
        if abs(orig_ar - target_ar) > 0.01:
            if orig_ar > target_ar:
                new_w = int(orig_h * target_ar)
                left = (orig_w - new_w) // 2
                img = img.crop((left, 0, left + new_w, orig_h))
            else:
                new_h = int(orig_w / target_ar)
                top = (orig_h - new_h) // 2
                img = img.crop((0, top, orig_w, top + new_h))
            
        if not target_w or not target_h:
            if ar_w == 16 and ar_h == 9:
                target_w, target_h = 1280, 720
            elif ar_w == 9 and ar_h == 16:
                target_w, target_h = 720, 1280
            elif ar_w == 1 and ar_h == 1:
                target_w, target_h = 1024, 1024
            else:
                target_w, target_h = 1280, 720
                
        img_resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        temp_file = os.path.join(os.path.dirname(image_path), f"temp_vgen_input{suffix}.jpg")
        img_resized.save(temp_file, "JPEG", quality=95)
        return temp_file
    except Exception:
        return image_path

# ==============================================================================
# MAIN EXECUTION FUNCTIONS & CUSTOM ERRORS
# ==============================================================================

class CreditExhaustedError(Exception):
    """Raised when an account runs out of credits (Forbidden / Fail to verify credit)."""
    pass

class AuthExpiredError(Exception):
    """Raised when a member token is invalid or expired (Unauthorized)."""
    pass

def generate_ai_image_service(
    member_token: str,
    user_prompt: str = "a majestic fantasy landscape, digital art, highly detailed 8k",
    image_paths: list = None,
    model_key: str = "gemini_2_5_flash",
    style_id: str = None,
    aspect_ratio: str = "1:1",
    resolution: str = "1K",
    batch_size: str = "1",
    output_format: str = "jpeg",
    filename_prefix: str = "",
    task_id: str = None,
):
    # Resolve through mapping if frontend name passed
    resolved_model = IMAGE_MODEL_MAPPING.get(model_key, model_key)
    if resolved_model not in IMAGE_MODELS_CONFIG:
        resolved_model = "gemini_2_5_flash"

    model_data = IMAGE_MODELS_CONFIG[resolved_model]
    if not style_id:
        style_id = model_data.get("default_style", "Style_Default")

    if len(user_prompt) > model_data["promptLength"]:
        user_prompt = user_prompt[:model_data["promptLength"]]

    has_reference = False
    ref_limit = model_data.get("ref_img_limit", 0)
    if image_paths and len(image_paths) > 0:
        if ref_limit == 0:
            raise ValueError("Reference image not supported by model.")
        if len(image_paths) > ref_limit:
            image_paths = image_paths[:ref_limit]
        has_reference = True

    try:
        b_size = int(batch_size)
    except ValueError:
        b_size = 1

    feature_id_val = "TextToImage"
    mode_key = "enable" if has_reference else "none"
    credit_res_map = model_data["credits"].get(mode_key, model_data["credits"].get("none", {}))
    credit_cost = credit_res_map.get(resolution, list(credit_res_map.values())[0] if credit_res_map else 2)
    total_credit_cost = credit_cost * b_size
    action_id_val = f"{model_data['actionId_prefix']}_{mode_key}_{resolution}"

    sync_feature_credit(feature_id=feature_id_val, action_id=action_id_val, credit=total_credit_cost)
    rsa_pub_key = get_myedit_rsa_public_key()
    sub_token = get_subscription_token(member_token)

    loaded_images_bytes = []
    if has_reference and image_paths:
        for p in image_paths:
            with open(p, "rb") as f:
                loaded_images_bytes.append(f.read())

    # For Google & OpenAI models (version 5), MyEdit expects a source placeholder image if none provided
    if not loaded_images_bytes:
        from PIL import Image
        import io
        img = Image.new('RGB', (512, 512), color=(120, 160, 220))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        loaded_images_bytes.append(buf.getvalue())

    aes_key = AESGCM.generate_key(bit_length=256)
    ts_ms = int(time.time() * 1000)
    key_param = rsa_encrypt_aes_key(rsa_pub_key, aes_key)
    receipt_json = json.dumps({"product": "myedit", "version": "3.9.0", "versiontype": "3.9.0", "platform": "web"}, separators=(',', ':'))
    receipt_param = encrypt_myedit_aes_gcm(aes_key, receipt_json, ts_ms, 0)
    enc_member_token = encrypt_myedit_aes_gcm(aes_key, member_token, ts_ms, 0)

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Origin": "https://myedit.online",
        "Referer": "https://myedit.online/en/photo-editor/ai-image-generator/edit",
    }
    
    form_data_init = {
        "receipt": receipt_param,
        "member_token": enc_member_token,
        "key": key_param,
        "timestamp": str(ts_ms),
    }

    if loaded_images_bytes:
        if image_paths and len(image_paths) > 0:
            ext = os.path.splitext(image_paths[0])[1].replace(".", "").lower() or "jpg"
            if ext == "jpeg":
                ext = "jpg"
        else:
            ext = "jpg"
        filename_val = f"TEXT_TO_IMAGE_source_0.{ext}"
        form_data_init["filename"] = filename_val
        form_data_init["filesize"] = str(len(loaded_images_bytes[0]))

    resp_init = requests.post(MYEDIT_TTI_URL, data=form_data_init, headers=headers, timeout=30)
    if resp_init.status_code == 403 or "Fail to verify credit" in resp_init.text or ("Forbidden" in resp_init.text and "credit" in resp_init.text.lower()):
        raise CreditExhaustedError(f"Credit verification failed: {resp_init.text}")
    if resp_init.status_code == 401:
        raise AuthExpiredError(f"Auth token expired: {resp_init.text}")
    resp_init.raise_for_status()
    init_json = resp_init.json()
    
    s_id_str = str(init_json["s_id"])
    s_id_int = int(s_id_str)
    s_token_b64 = init_json["s_token"]
    raw_session_token = decrypt_myedit_aes_gcm(aes_key, s_token_b64, ts_ms, 0)

    uploaded_sources_list = []
    uploaded_reference_urls = []
    if loaded_images_bytes:
        put_headers = {
            "User-Agent": HEADERS["User-Agent"],
            "Content-Type": "image/jpeg",
            "Origin": "https://myedit.online",
            "Referer": "https://myedit.online/",
        }

        for idx, img_bytes in enumerate(loaded_images_bytes):
            if image_paths and idx < len(image_paths):
                ext = os.path.splitext(image_paths[idx])[1].replace(".", "").lower() or "jpg"
                if ext == "jpeg":
                    ext = "jpg"
            else:
                ext = "jpg"
            fname = f"TEXT_TO_IMAGE_source_{idx}.{ext}"
            fsize = str(len(img_bytes))

            req_ts_ms = int(time.time() * 1000)
            enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
            get_link_url = f"{MYEDIT_TTI_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}"

            resp_link = requests.post(get_link_url, data={"filename": fname, "filesize": fsize}, headers=headers, timeout=30)
            resp_link.raise_for_status()
            storage_url = resp_link.json()["storage"]
            
            clean_url = storage_url.split("?")[0]
            uploaded_reference_urls.append(clean_url)

            resp_upload = requests.put(storage_url, data=img_bytes, headers=put_headers, timeout=30)
            resp_upload.raise_for_status()
            uploaded_sources_list.append(idx + 1)

        if task_id and uploaded_reference_urls:
            db.update_task_reference_urls(task_id, uploaded_reference_urls)

    sources_str = json.dumps(uploaded_sources_list)

    req_ts_ms = int(time.time() * 1000)
    enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
    apply_url = f"{MYEDIT_TTI_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}"

    alias_str = time.strftime("%y%m%d_%H%M")
    apply_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Authorization": f"Bearer {member_token}",
        "x-subscription-token": f"Bearer {sub_token}",
        "Origin": "https://myedit.online",
        "Referer": "https://myedit.online/en/photo-editor/ai-image-generator/edit",
    }
    
    consumption_data = {
        "member_token": member_token,
        "cl_sid": SID_AOL_POL,
        "feature_id": feature_id_val,
        "action_id": action_id_val,
        "unit": int(batch_size),
        "total_credit": total_credit_cost
    }
    consumption_param = encrypt_myedit_aes_gcm(
        aes_key,
        json.dumps(consumption_data, separators=(',', ':')),
        req_ts_ms,
        s_id_int
    )

    form_data_apply = {
        "style_id": style_id,
        "style_prompt": "",
        "version": "5",
        "aspect_ratio": aspect_ratio,
        "output_format": output_format,
        "user_prompt": user_prompt,
        "batch_size": str(batch_size),
        "consumption": consumption_param,
        "cloud_sync": "true",
        "alias": alias_str,
        "effect": "TextToImage",
        "resolution": resolution,
        "sources": sources_str
    }

    files_apply = {k: (None, str(v)) for k, v in form_data_apply.items()}

    resp_apply = requests.patch(apply_url, files=files_apply, headers=apply_headers, timeout=30)
    if task_id:
        db.add_task_log(task_id, str(resp_apply.status_code))
    if resp_apply.status_code == 403 or "Fail to verify credit" in resp_apply.text or ("Forbidden" in resp_apply.text and "credit" in resp_apply.text.lower()):
        raise CreditExhaustedError(f"Credit verification failed: {resp_apply.text}")
    if resp_apply.status_code == 401:
        raise AuthExpiredError(f"Auth token expired: {resp_apply.text}")
    resp_apply.raise_for_status()

    apply_json = resp_apply.json()
    cl_task_id = apply_json.get("task_id")
    polling = apply_json.get("polling", {})
    delay = polling.get("delay", 5)

    max_attempts = 120
    for i in range(max_attempts):
        time.sleep(delay)
        req_ts_ms = int(time.time() * 1000)
        enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
        poll_url = f"{MYEDIT_TTI_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}/{cl_task_id}"

        resp_poll = requests.get(poll_url, headers=headers, timeout=30)
        resp_poll.raise_for_status()
        poll_json = resp_poll.json()
        status = poll_json.get("status")

        if status == "Done":
            files = poll_json.get("files", [])
            s3_files = []
            dec_metadata = None
            for idx, f in enumerate(files):
                furl = f.get("url", "")
                enc_key = init_json.get("p_key")
                enc_iv = init_json.get("p_iv")

                if furl:
                    s3_files.append(furl)
                    if enc_key and enc_iv and not dec_metadata:
                        dec_metadata = {
                            "aes_key": aes_key.hex(),
                            "enc_key": enc_key,
                            "enc_iv": enc_iv,
                            "ts_ms": ts_ms
                        }
            return {
                "status": "Done",
                "files": s3_files,
                "reference_urls": uploaded_reference_urls,
                "decryption_metadata": dec_metadata
            }

        if status in ("Error", "Failed"):
            poll_err_str = json.dumps(poll_json)
            if "credit" in poll_err_str.lower() or "Forbidden" in poll_err_str:
                raise CreditExhaustedError(f"Task failed due to credit limit: {poll_err_str}")
            return {"status": "Failed", "error": poll_json, "reference_urls": uploaded_reference_urls}

    return {"status": "Timeout", "reference_urls": uploaded_reference_urls}

def generate_ai_video_service(
    member_token: str,
    user_prompt: str = "a cute astronaut cat floating in space station, cinematic lighting",
    model_key: str = "veo_3_1_lite",
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
    processing_duration: int = 4,
    sound: str = "none",
    effect_mode: str = "TextToVideo",
    source_image_path: str = None,
    last_image_path: str = None,
    ref_images: list = None,
    ref_videos: list = None,
    frame_mode: str = "single",
    filename_prefix: str = "",
    task_id: str = None,
):
    # Resolve through mapping
    resolved_model = VIDEO_MODEL_MAPPING.get(model_key, model_key)
    model_data = VIDEO_MODELS_CONFIG.get(resolved_model, VIDEO_MODELS_CONFIG["veo_3_1_lite"])
    
    if isinstance(model_data["model"], dict):
        model_name_str = model_data["model"].get(effect_mode, list(model_data["model"].values())[0])
    else:
        model_name_str = model_data["model"]
    vendor_str = model_data["vendor"]

    if effect_mode == "ImageToVideo" and last_image_path:
        frame_mode = "startend"

    prepared_media = []
    if effect_mode == "ImageToVideo" and source_image_path and os.path.exists(source_image_path):
        try:
            ar_w, ar_h = map(int, aspect_ratio.split(":"))
        except Exception:
            ar_w, ar_h = 16, 9
        
        h_val = 1080 if resolution == "1080p" else 720
        if ar_w == 16 and ar_h == 9:
            target_w, target_h = int(h_val * 16 / 9), h_val
        elif ar_w == 9 and ar_h == 16:
            target_w, target_h = h_val, int(h_val * 16 / 9)
        elif ar_w == 1 and ar_h == 1:
            target_w, target_h = (1024, 1024) if h_val >= 720 else (720, 720)
        else:
            target_w, target_h = int(h_val * ar_w / ar_h), h_val
    
        temp1 = prepare_image_for_vgen(source_image_path, aspect_ratio, target_w, target_h, suffix="_first")
        prepared_media.append({
            "path": temp1,
            "tag": "first_frame",
            "type": "image",
            "is_temp": temp1 != source_image_path
        })
        
        if frame_mode == "startend" and last_image_path and os.path.exists(last_image_path):
            temp2 = prepare_image_for_vgen(last_image_path, aspect_ratio, target_w, target_h, suffix="_end")
            prepared_media.append({
                "path": temp2,
                "tag": "end_frame",
                "type": "image",
                "is_temp": temp2 != last_image_path
            })

    def cleanup_temp_images():
        for media in prepared_media:
            if media["is_temp"] and os.path.exists(media["path"]):
                try:
                    os.remove(media["path"])
                except Exception:
                    pass

    action_id_sound = {"vendor": "enable", "none": "none", "auto": "enable"}.get(sound, sound)
    if effect_mode == "ImageToVideo":
        overrides = model_data.get("action_id_i2v_overrides", {})
        if resolution in overrides:
            action_id_str = overrides[resolution].format(sound=action_id_sound, resolution=resolution, frame_mode=frame_mode)
        elif "action_id_i2v" in model_data:
            action_id_str = model_data["action_id_i2v"].format(sound=action_id_sound, resolution=resolution, frame_mode=frame_mode)
        else:
            action_id_str = model_data["action_id"].format(sound=action_id_sound, resolution=resolution)
    else:
        overrides = model_data.get("action_id_overrides", {})
        if resolution in overrides:
            action_id_str = overrides[resolution].format(sound=action_id_sound, resolution=resolution)
        else:
            action_id_str = model_data["action_id"].format(sound=action_id_sound, resolution=resolution)
            
    credit_map = model_data.get("credit_map", {})
    credit_cost = credit_map.get((effect_mode, sound, frame_mode, resolution))
    if credit_cost is None:
        credit_cost = credit_map.get((effect_mode, sound, resolution))
    if credit_cost is None:
        credit_cost = credit_map.get((sound, resolution))
    if credit_cost is None:
        credit_cost = model_data.get("credit", 3)

    get_member_remaining_credits(member_token)
    sync_feature_credit(feature_id=effect_mode, action_id=action_id_str, credit=credit_cost)

    rsa_pub_key = get_myedit_rsa_public_key()
    sub_token = get_subscription_token(member_token)
    referer_url = "https://myedit.online/en/video-editor/image-to-video/edit" if effect_mode == "ImageToVideo" else "https://myedit.online/en/video-editor/text-to-video/edit"

    aes_key = AESGCM.generate_key(bit_length=256)
    ts_ms = int(time.time() * 1000)
    key_param = rsa_encrypt_aes_key(rsa_pub_key, aes_key)

    receipt_obj = {
        "product": "MyEdit",
        "version": "3.9.0",
        "versiontype": "3.9.0",
        "platform": "web",
        "consumption": {
            "member_token": member_token,
            "cl_sid": SID_AOL_POL,
            "feature_id": effect_mode,
            "action_id": action_id_str,
            "unit": processing_duration,
            "total_credit": credit_cost * processing_duration,
        },
    }
    receipt_json = json.dumps(receipt_obj, separators=(",", ":"))
    receipt_param = encrypt_myedit_aes_gcm(aes_key, receipt_json, ts_ms, 0)

    headers_init = {
        "User-Agent": HEADERS["User-Agent"],
        "Origin": "https://myedit.online",
        "Referer": referer_url,
    }

    files_init = {
        "key": (None, key_param),
        "timestamp": (None, str(ts_ms)),
        "receipt": (None, receipt_param),
    }

    if prepared_media:
        sources_list = []
        for i, item in enumerate(prepared_media):
            file_size = os.path.getsize(item["path"])
            filename = os.path.basename(item["path"])
            sources_list.append({"filename": filename, "filesize": file_size})
        files_init["sources"] = (None, json.dumps(sources_list, separators=(",", ":")))

    resp_init = requests.post(MYEDIT_VGEN_URL, files=files_init, headers=headers_init, timeout=30)
    if resp_init.status_code == 403 or "Fail to verify credit" in resp_init.text or ("Forbidden" in resp_init.text and "credit" in resp_init.text.lower()):
        raise CreditExhaustedError(f"Credit verification failed: {resp_init.text}")
    if resp_init.status_code == 401:
        raise AuthExpiredError(f"Auth token expired: {resp_init.text}")
    resp_init.raise_for_status()
    init_json = resp_init.json()
    
    s_id_str = str(init_json["s_id"])
    s_id_int = int(s_id_str)
    s_token_b64 = init_json["s_token"]
    raw_session_token = decrypt_myedit_aes_gcm(aes_key, s_token_b64, ts_ms, 0)

    uploaded_reference_urls = []
    if prepared_media:
        media_info_list = init_json.get("media_info", [])
        for i, item in enumerate(prepared_media):
            if i < len(media_info_list) and "url" in media_info_list[i]:
                upload_url = media_info_list[i]["url"]
                clean_url = upload_url.split("?")[0]
                uploaded_reference_urls.append(clean_url)

                with open(item["path"], 'rb') as f:
                    file_data = f.read()
                content_type = 'video/mp4' if item["type"] == "video" else 'image/jpeg'
                resp_upload = requests.put(upload_url, data=file_data, headers={'Content-Type': content_type})

        if task_id and uploaded_reference_urls:
            db.update_task_reference_urls(task_id, uploaded_reference_urls)

    req_ts_ms = int(time.time() * 1000)
    enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
    apply_url = f"{MYEDIT_VGEN_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}"

    alias_str = time.strftime("%y%m%d_%H%M")
    apply_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Authorization": f"Bearer {member_token}",
        "x-subscription-token": f"Bearer {sub_token}",
        "Origin": "https://myedit.online",
        "Referer": referer_url,
    }

    sound_form_val = "vendor" if sound in ["vendor", "auto", "on", "On"] else "none"
    form_data_apply = {
        "vendor": vendor_str,
        "processing_duration": str(processing_duration),
        "user_prompt": user_prompt,
        "need_bad_word_check": "false",
        "sound": sound_form_val,
        "create_thumbnail": "true",
        "cloud_sync": "true",
        "alias": alias_str,
        "model": model_name_str,
        "is_custom": "true",
        "is_fixed_model": "true",
        "effect": effect_mode,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    model_mode = model_data.get("mode", "std")
    form_data_apply["mode"] = model_mode

    if prepared_media:
        media_info_list = init_json.get("media_info", [])
        media_ids = [m["id"] for m in media_info_list if "id" in m]
        first_frame_id = media_ids[0] if len(media_ids) > 0 else 1
        end_frame_id = media_ids[1] if len(media_ids) > 1 else None

        if effect_mode == "ImageToVideo":
            img_w, img_h = get_image_dimensions(prepared_media[0]["path"])
            form_data_apply["width"] = str(img_w)
            form_data_apply["height"] = str(img_h)
            
            if end_frame_id is not None:
                form_data_apply["sources"] = json.dumps([first_frame_id, -1, end_frame_id])
                last_image_file_size = os.path.getsize(prepared_media[1]["path"])
                last_image_obj = {"media_id": end_frame_id, "filesize": last_image_file_size, "width": img_w, "height": img_h}
                form_data_apply["last_image"] = json.dumps(last_image_obj, separators=(",", ":"))
            else:
                form_data_apply["sources"] = json.dumps([first_frame_id])

    files_apply = {k: (None, str(v)) for k, v in form_data_apply.items()}
    resp_apply = requests.patch(apply_url, files=files_apply, headers=apply_headers, timeout=60)
    if task_id:
        db.add_task_log(task_id, str(resp_apply.status_code))
    if resp_apply.status_code == 403 or "Fail to verify credit" in resp_apply.text or ("Forbidden" in resp_apply.text and "credit" in resp_apply.text.lower()):
        raise CreditExhaustedError(f"Credit verification failed: {resp_apply.text}")
    if resp_apply.status_code == 401:
        raise AuthExpiredError(f"Auth token expired: {resp_apply.text}")
    resp_apply.raise_for_status()

    apply_json = resp_apply.json()
    cl_task_id = apply_json.get("task_id")
    polling = apply_json.get("polling", {})
    delay = polling.get("delay", 5)

    max_attempts = 120
    for i in range(max_attempts):
        time.sleep(delay)
        req_ts_ms = int(time.time() * 1000)
        enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
        poll_url = f"{MYEDIT_VGEN_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}/{cl_task_id}"

        resp_poll = requests.get(poll_url, headers=headers_init, timeout=30)
        resp_poll.raise_for_status()
        poll_json = resp_poll.json()
        status = poll_json.get("status")

        if status == "Done":
            files = poll_json.get("files", [])
            s3_files = []
            dec_metadata = None
            for idx, f in enumerate(files):
                furl = f.get("url", "")
                enc_key = init_json.get("p_key")
                enc_iv = init_json.get("p_iv")

                if furl:
                    # If this is a thumbnail URL returned instead of the video, fetch the real mp4 URL
                    if "thumbnail" in furl.lower() and idx == 0:
                        try:
                            match = re.search(r'/Credit/(\d+)/', furl)
                            if match:
                                consume_task_id = match.group(1)
                                req_ts_ms_consume = int(time.time() * 1000)
                                enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms_consume, s_id_int)
                                consume_url = f"https://myedit.online/info/consume/{enc_token_hex}-{s_id_str}-{req_ts_ms_consume}/tasks/files"
                                
                                files_form = {
                                    "consume_task_id": (None, str(consume_task_id)),
                                    "sync_status": (None, "1,2"),
                                    "sort_by": (None, "created_time asc"),
                                }
                                
                                resp_consume = requests.post(consume_url, files=files_form, headers=headers_init, timeout=30)
                                resp_consume.raise_for_status()
                                consume_json = resp_consume.json()
                                
                                result_files = consume_json.get("files", [])
                                if result_files:
                                    mp4_file = None
                                    for rf in result_files:
                                        rf_url = rf.get("url", "")
                                        rf_name = rf.get("name", "")
                                        if rf_name.lower().endswith(".mp4") or (".mp4" in rf_url.lower() and "thumbnail" not in rf_url.lower()):
                                            mp4_file = rf
                                            break
                                    
                                    if not mp4_file:
                                        mp4_file = result_files[0]
                                    
                                    furl = mp4_file.get("url", furl)
                        except Exception as e:
                            print(f"[THUMBNAIL-FIX] Failed to fetch real video URL: {e}")

                    s3_files.append(furl)
                    if enc_key and enc_iv and not dec_metadata:
                        dec_metadata = {
                            "aes_key": aes_key.hex(),
                            "enc_key": enc_key,
                            "enc_iv": enc_iv,
                            "ts_ms": ts_ms
                        }
            cleanup_temp_images()
            return {
                "status": "Done",
                "files": s3_files,
                "reference_urls": uploaded_reference_urls,
                "decryption_metadata": dec_metadata
            }

        if status in ("Error", "Failed"):
            cleanup_temp_images()
            poll_err_str = json.dumps(poll_json)
            if "credit" in poll_err_str.lower() or "Forbidden" in poll_err_str:
                raise CreditExhaustedError(f"Task failed due to credit limit: {poll_err_str}")
            return {"status": "Failed", "error": poll_json, "reference_urls": uploaded_reference_urls}

    cleanup_temp_images()
    return {"status": "Timeout", "reference_urls": uploaded_reference_urls}

# ==============================================================================
# ACCOUNT CACHE & REUSE SYSTEM
# ==============================================================================

ACTIVE_ACCOUNTS = {} # {api_key_id: {"email": email, "password": password, "member_token": token, "timestamp": time.time()}}
ACCOUNT_LOCK = threading.Lock()

def mark_account_exhausted(api_key_id, email):
    """Marks an account as used=1 (credits exhausted) in the database and removes it from active cache."""
    with ACCOUNT_LOCK:
        if api_key_id in ACTIVE_ACCOUNTS and ACTIVE_ACCOUNTS[api_key_id].get("email") == email:
            del ACTIVE_ACCOUNTS[api_key_id]
    try:
        if db.DB_TYPE == 'postgresql':
            db._execute_query('UPDATE accounts SET used = 1 WHERE api_key_id = %s AND email = %s', (api_key_id, email))
        else:
            db._execute_query('UPDATE accounts SET used = 1 WHERE api_key_id = ? AND email = ?', (api_key_id, email))
        print(f"[ACCOUNT] Account {email} marked as used=1 (credits exhausted).")
    except Exception as e:
        print(f"[ACCOUNT] Error marking account exhausted: {e}")

def create_myedit_account(api_key_id):
    """Creates a new MyEdit account dynamically on-the-fly."""
    try:
        temp_mail = TempMailClient()
        email = temp_mail.get_email()
        password = "CyberLink123!"

        # 1. Signup
        signup_res = signup(email, password)
        if signup_res.get("status") != "SUCCESS":
            print(f"[-] Signup failed: {signup_res}")
            return None, None

        # 2. Get activation link
        try:
            activation_url = temp_mail.wait_for_activation_link(timeout=60)
        except Exception as e:
            print(f"[-] Activation link not received: {e}")
            return None, None

        # 3. Activate
        if not activate_account(activation_url):
            print("[-] Activation not verified, trying login anyway...")

        time.sleep(3)

        # 4. Login
        login_res = login(email, password)
        if login_res.get("status") != "SUCCESS":
            print(f"[-] Login failed: {login_res}")
            return None, None

        info = login_res.get("info", {})
        member_token = info.get("memberToken")
        if not member_token:
            print("[-] Login failed, no memberToken.")
            return None, None
        
        # 5. Collect all daily and task bonuses
        try:
            collect_all_bonuses(member_token)
        except Exception as e:
            print(f"[!] Bonus collection failed: {e}")

        # Add account to database (used = 0)
        db.add_account(api_key_id, email, password)
        print(f"[+] Successfully registered and saved MyEdit account: {email}")

        return member_token, email
    except Exception as e:
        print(f"[-] Account creation exception: {e}")
        return None, None

def create_myedit_account_wrapper(api_key_id):
    """Wrapper function matching naming structure."""
    return create_myedit_account(api_key_id)

def get_or_create_active_account(api_key_id, task_id=None, force_new=False):
    """Gets an active account from memory cache, existing DB account, or creates a new one."""
    # 1. Check memory cache if not force_new
    if not force_new:
        with ACCOUNT_LOCK:
            if api_key_id in ACTIVE_ACCOUNTS:
                acc = ACTIVE_ACCOUNTS[api_key_id]
                email = acc.get("email")
                member_token = acc.get("member_token")
                if member_token and email:
                    if task_id:
                        try:
                            if db.DB_TYPE == 'postgresql':
                                db._execute_query('UPDATE tasks SET account_email = %s WHERE task_id = %s', (email, task_id))
                            else:
                                db._execute_query('UPDATE tasks SET account_email = ? WHERE task_id = ?', (email, task_id))
                        except Exception:
                            pass
                    return member_token, acc

    # 2. Check if DB has the most recently created valid account (ORDER BY id DESC)
    if not force_new:
        try:
            query = 'SELECT email, password FROM accounts WHERE api_key_id = %s ORDER BY id DESC LIMIT 1' if db.DB_TYPE == 'postgresql' else 'SELECT email, password FROM accounts WHERE api_key_id = ? ORDER BY id DESC LIMIT 1'
            db_acc = db._execute_query(query, (api_key_id,), fetch_one=True)
            if db_acc and db_acc.get("email") and db_acc.get("password"):
                email = db_acc["email"]
                password = db_acc["password"]
                print(f"[ACCOUNT] Found latest account in DB: {email}. Attempting login...")
                login_res = login(email, password)
                if login_res.get("status") == "SUCCESS" and "info" in login_res:
                    member_token = login_res["info"].get("memberToken")
                    if member_token:
                        acc_data = {
                            "email": email,
                            "password": password,
                            "member_token": member_token,
                            "timestamp": time.time()
                        }
                        with ACCOUNT_LOCK:
                            ACTIVE_ACCOUNTS[api_key_id] = acc_data
                        if task_id:
                            try:
                                if db.DB_TYPE == 'postgresql':
                                    db._execute_query('UPDATE tasks SET account_email = %s WHERE task_id = %s', (email, task_id))
                                else:
                                    db._execute_query('UPDATE tasks SET account_email = ? WHERE task_id = ?', (email, task_id))
                            except Exception:
                                pass
                        return member_token, acc_data
                else:
                    print(f"[ACCOUNT] Login failed for DB account ({email}). Registering a fresh new account...")
        except Exception as e:
            print(f"[ACCOUNT] Error retrieving DB account: {e}")

    # 3. Create a fresh account via temp-mail
    for attempt in range(5):
        print(f"[ACCOUNT] Registering new account for api_key_id={api_key_id} (attempt {attempt+1}/5)...")
        member_token, email = create_myedit_account_wrapper(api_key_id)
        if member_token and email:
            password = "CyberLink123!"
            acc_data = {
                "email": email,
                "password": password,
                "member_token": member_token,
                "timestamp": time.time()
            }
            with ACCOUNT_LOCK:
                ACTIVE_ACCOUNTS[api_key_id] = acc_data
            if task_id:
                try:
                    if db.DB_TYPE == 'postgresql':
                        db._execute_query('UPDATE tasks SET account_email = %s WHERE task_id = %s', (email, task_id))
                    else:
                        db._execute_query('UPDATE tasks SET account_email = ? WHERE task_id = ?', (email, task_id))
                except Exception:
                    pass
            return member_token, acc_data
        time.sleep(2)

    return None, None

def deduct_api_key_quota(api_key_id, task_id=None):
    """Deducts 1 account/quota from the API key's available accounts upon successful task completion."""
    conn = None
    try:
        active_acc = ACTIVE_ACCOUNTS.get(api_key_id)
        active_email = active_acc.get("email") if active_acc else None

        conn = db.get_connection()
        cursor = conn.cursor()
        consumed_email = None

        if db.DB_TYPE == 'postgresql':
            if active_email:
                cursor.execute(
                    'SELECT email FROM accounts WHERE api_key_id = %s AND used = 0 AND email != %s LIMIT 1',
                    (api_key_id, active_email)
                )
                row = cursor.fetchone()
                if row:
                    consumed_email = row['email'] if isinstance(row, dict) else row[0]
            
            if not consumed_email:
                cursor.execute(
                    'SELECT email FROM accounts WHERE api_key_id = %s AND used = 0 LIMIT 1',
                    (api_key_id,)
                )
                row = cursor.fetchone()
                if row:
                    consumed_email = row['email'] if isinstance(row, dict) else row[0]

            if consumed_email:
                cursor.execute(
                    'UPDATE accounts SET used = 1 WHERE api_key_id = %s AND email = %s',
                    (api_key_id, consumed_email)
                )
                if task_id:
                    cursor.execute(
                        'UPDATE tasks SET account_email = %s WHERE task_id = %s',
                        (consumed_email, task_id)
                    )
                conn.commit()
                print(f"[QUOTA] Successfully deducted 1 quota ({consumed_email}) for task {task_id}.")
        else:
            if active_email:
                cursor.execute(
                    'SELECT email FROM accounts WHERE api_key_id = ? AND used = 0 AND email != ? LIMIT 1',
                    (api_key_id, active_email)
                )
                row = cursor.fetchone()
                if row:
                    consumed_email = row['email'] if isinstance(row, dict) else row[0]

            if not consumed_email:
                cursor.execute(
                    'SELECT email FROM accounts WHERE api_key_id = ? AND used = 0 LIMIT 1',
                    (api_key_id,)
                )
                row = cursor.fetchone()
                if row:
                    consumed_email = row['email'] if isinstance(row, dict) else row[0]

            if consumed_email:
                cursor.execute(
                    'UPDATE accounts SET used = 1 WHERE api_key_id = ? AND email = ?',
                    (api_key_id, consumed_email)
                )
                if task_id:
                    cursor.execute(
                        'UPDATE tasks SET account_email = ? WHERE task_id = ?',
                        (consumed_email, task_id)
                    )
                conn.commit()
                print(f"[QUOTA] Successfully deducted 1 quota ({consumed_email}) for task {task_id}.")
        return consumed_email
    except Exception as e:
        print(f"[QUOTA] Error deducting quota: {e}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

def login_with_retry_and_link(api_key_id, task_id=None):
    """Compatibility wrapper for obtaining active account."""
    return get_or_create_active_account(api_key_id, task_id)

def save_b64_to_temp_file(b64_data, suffix=".jpg"):
    """Saves base64 data to a local temporary file."""
    if "," in b64_data:
        b64_data = b64_data.split(",")[1]
    data = base64.b64decode(b64_data)
    os.makedirs("temp", exist_ok=True)
    temp_path = f"temp/ref_{int(time.time() * 1000)}_{random.randint(1000, 9999)}{suffix}"
    with open(temp_path, "wb") as f:
        f.write(data)
    return os.path.abspath(temp_path)

# ==============================================================================
# TASK WORKER PROCESSORS
# ==============================================================================

def process_image_task(task_id, params, api_key_id):
    """Processes an image generation task in background thread."""
    temp_files = []
    try:
        db.update_task_status(task_id, 'running')

        prompt = params.get('prompt', '')
        model_req = params.get('model', 'NANO_BANANA')
        # Map frontend model ID to backend model key
        model = IMAGE_MODEL_MAPPING.get(model_req, model_req)
        aspect_ratio = params.get('size', '1:1')
        resolution = params.get('resolution', '1K')
        batch_size = int(params.get('batch_size', 1))

        # Handle reference images (Image-to-Image)
        reference_images = []
        images = params.get('reference_images', [])
        if images:
            for img_b64 in images:
                temp_path = save_b64_to_temp_file(img_b64)
                temp_files.append(temp_path)
                reference_images.append(temp_path)

        model_data = IMAGE_MODELS_CONFIG.get(model)
        if not model_data:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, "400")
            return

        feature_id = "TextToImage"
        max_account_retries = 3
        last_error = None
        result = None
        current_token = None

        for attempt in range(max_account_retries):
            force_new = (attempt > 0)
            member_token, account = get_or_create_active_account(api_key_id, task_id=task_id, force_new=force_new)
            if not member_token:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "503")
                return

            current_token = member_token
            try:
                credit_info = get_member_remaining_credits(member_token)
                total_credits = credit_info.get("total_remain", "?") if credit_info else "?"
                print(f"\n[RENDER LOG] [IMAGE TASK: {task_id}] -> Aktif Hesap: {account['email']} | Mevcut Kalan Kredi: {total_credits}")

                claim_task_bonus(member_token, feature_id)
                check_task_bonus(member_token, feature_id)

                token_data = json.dumps({
                    "member_token": member_token,
                    "reference_images": params.get('reference_images', []),
                    "reference_videos": params.get('reference_videos', []),
                    "start_frame": params.get('start_frame'),
                    "end_frame": params.get('end_frame')
                })
                db.update_task_token(task_id, token_data)

                result = generate_ai_image_service(
                    member_token=member_token,
                    user_prompt=prompt,
                    image_paths=reference_images if reference_images else None,
                    model_key=model,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    batch_size=str(batch_size),
                    filename_prefix=f"task_{task_id}",
                    task_id=task_id
                )
                break

            except CreditExhaustedError as e:
                print(f"[RETRY] Credit exhausted for account {account['email']} on image task {task_id}: {e}. Rotating to new account...")
                mark_account_exhausted(api_key_id, account['email'])
                last_error = e
                continue

            except AuthExpiredError as e:
                print(f"[RETRY] Auth token expired for account {account['email']}. Re-logging in...")
                login_res = login(account['email'], account['password'])
                if login_res.get("status") == "SUCCESS" and "info" in login_res:
                    refreshed_token = login_res["info"].get("memberToken")
                    if refreshed_token:
                        with ACCOUNT_LOCK:
                            if api_key_id in ACTIVE_ACCOUNTS:
                                ACTIVE_ACCOUNTS[api_key_id]["member_token"] = refreshed_token
                        last_error = e
                        continue
                mark_account_exhausted(api_key_id, account['email'])
                last_error = e
                continue

        if not result:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, format_error_code(last_error))
            return

        if result.get("status") == "Done":
            completed_files = result.get("files", [])
            dec_metadata = result.get("decryption_metadata")
            token_data_dict = {
                "member_token": current_token,
                "reference_images": params.get('reference_images', []),
                "reference_videos": params.get('reference_videos', []),
                "start_frame": params.get('start_frame'),
                "end_frame": params.get('end_frame')
            }
            if dec_metadata:
                token_data_dict.update({
                    "dec_aes_key": dec_metadata.get("aes_key"),
                    "dec_enc_key": dec_metadata.get("enc_key"),
                    "dec_enc_iv": dec_metadata.get("enc_iv"),
                    "dec_ts_ms": dec_metadata.get("ts_ms")
                })
            db.update_task_token(task_id, json.dumps(token_data_dict))

            if completed_files:
                db.update_task_status(task_id, 'completed', completed_files[0])
                deduct_api_key_quota(api_key_id, task_id)
                post_credits_info = get_member_remaining_credits(current_token)
                post_credits = post_credits_info.get("total_remain", "?") if post_credits_info else "?"
                print(f"[RENDER LOG] [IMAGE TASK: {task_id}] [TAMAMLANDI] -> Hesap: {account['email']} | Kalan Kredi: {post_credits}\n")
            else:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "500")
        elif result.get("status") == "Timeout":
            db.update_task_status(task_id, 'timeout')
            db.add_task_log(task_id, "504")
        else:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, format_error_code(result.get('error')))

    except Exception as e:
        print(f"[ERROR] Image task {task_id} error: {e}")
        db.update_task_status(task_id, 'error')
        db.add_task_log(task_id, format_error_code(e))
    finally:
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

def process_video_task(task_id, params, api_key_id):
    """Processes a video generation task in background thread."""
    temp_files = []
    try:
        db.update_task_status(task_id, 'running')

        prompt = params.get('prompt', '')
        model_req = params.get('model', 'VEO_3_1')
        # Map frontend model ID to backend model key
        model = VIDEO_MODEL_MAPPING.get(model_req, model_req)
        aspect_ratio = params.get('size', '16:9')
        resolution = params.get('resolution', '1080p')
        duration = int(params.get('duration', 4))
        sound = params.get('sound', 'none')

        input_mode = "TextToVideo"
        source_image_path = None
        last_image_path = None
        frame_mode = "single"

        # Handle start frame
        start_frame_b64 = params.get('start_frame')
        if start_frame_b64:
            input_mode = "ImageToVideo"
            temp_start = save_b64_to_temp_file(start_frame_b64)
            temp_files.append(temp_start)
            source_image_path = temp_start

        # Handle end frame
        end_frame_b64 = params.get('end_frame')
        if end_frame_b64 and start_frame_b64:
            frame_mode = "startend"
            temp_end = save_b64_to_temp_file(end_frame_b64)
            temp_files.append(temp_end)
            last_image_path = temp_end

        model_data = VIDEO_MODELS_CONFIG.get(model)
        if not model_data:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, "400")
            return

        max_account_retries = 3
        last_error = None
        result = None
        current_token = None

        for attempt in range(max_account_retries):
            force_new = (attempt > 0)
            member_token, account = get_or_create_active_account(api_key_id, task_id=task_id, force_new=force_new)
            if not member_token:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "503")
                return

            current_token = member_token
            try:
                credit_info = get_member_remaining_credits(member_token)
                total_credits = credit_info.get("total_remain", "?") if credit_info else "?"
                print(f"\n[RENDER LOG] [VIDEO TASK: {task_id}] -> Aktif Hesap: {account['email']} | Mevcut Kalan Kredi: {total_credits}")

                claim_task_bonus(member_token, feature_id=input_mode)
                check_task_bonus(member_token, feature_id=input_mode)

                token_data = json.dumps({
                    "member_token": member_token,
                    "reference_images": params.get('reference_images', []),
                    "reference_videos": params.get('reference_videos', []),
                    "start_frame": params.get('start_frame'),
                    "end_frame": params.get('end_frame')
                })
                db.update_task_token(task_id, token_data)

                result = generate_ai_video_service(
                    member_token=member_token,
                    user_prompt=prompt,
                    model_key=model,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    processing_duration=duration,
                    sound=sound,
                    effect_mode=input_mode,
                    source_image_path=source_image_path,
                    last_image_path=last_image_path,
                    frame_mode=frame_mode,
                    filename_prefix=f"task_{task_id}",
                    task_id=task_id
                )
                break

            except CreditExhaustedError as e:
                print(f"[RETRY] Credit exhausted for account {account['email']} on video task {task_id}: {e}. Rotating to new account...")
                mark_account_exhausted(api_key_id, account['email'])
                last_error = e
                continue

            except AuthExpiredError as e:
                print(f"[RETRY] Auth token expired for account {account['email']}. Re-logging in...")
                login_res = login(account['email'], account['password'])
                if login_res.get("status") == "SUCCESS" and "info" in login_res:
                    refreshed_token = login_res["info"].get("memberToken")
                    if refreshed_token:
                        with ACCOUNT_LOCK:
                            if api_key_id in ACTIVE_ACCOUNTS:
                                ACTIVE_ACCOUNTS[api_key_id]["member_token"] = refreshed_token
                        last_error = e
                        continue
                mark_account_exhausted(api_key_id, account['email'])
                last_error = e
                continue

        if not result:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, format_error_code(last_error))
            return

        if result.get("status") == "Done":
            completed_files = result.get("files", [])
            video_file = next((f for f in completed_files if ".mp4" in f.lower() and "thumbnail" not in f.lower()), None)
            dec_metadata = result.get("decryption_metadata")
            token_data_dict = {
                "member_token": current_token,
                "reference_images": params.get('reference_images', []),
                "reference_videos": params.get('reference_videos', []),
                "start_frame": params.get('start_frame'),
                "end_frame": params.get('end_frame')
            }
            if dec_metadata:
                token_data_dict.update({
                    "dec_aes_key": dec_metadata.get("aes_key"),
                    "dec_enc_key": dec_metadata.get("enc_key"),
                    "dec_enc_iv": dec_metadata.get("enc_iv"),
                    "dec_ts_ms": dec_metadata.get("ts_ms")
                })
            db.update_task_token(task_id, json.dumps(token_data_dict))

            if video_file:
                db.update_task_status(task_id, 'completed', video_file)
                deduct_api_key_quota(api_key_id, task_id)
            else:
                db.update_task_status(task_id, 'completed', completed_files[0] if completed_files else "")
                deduct_api_key_quota(api_key_id, task_id)
            post_credits_info = get_member_remaining_credits(current_token)
            post_credits = post_credits_info.get("total_remain", "?") if post_credits_info else "?"
            print(f"[RENDER LOG] [VIDEO TASK: {task_id}] [TAMAMLANDI] -> Hesap: {account['email']} | Kalan Kredi: {post_credits}\n")
        elif result.get("status") == "Timeout":
            db.update_task_status(task_id, 'timeout')
            db.add_task_log(task_id, "504")
        else:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, format_error_code(result.get('error')))

    except Exception as e:
        print(f"[ERROR] Video task {task_id} error: {e}")
        db.update_task_status(task_id, 'error')
        db.add_task_log(task_id, format_error_code(e))
    finally:
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

def process_tts_task(task_id, params, api_key_id):
    db.update_task_status(task_id, 'failed')
    db.add_task_log(task_id, "501")

def process_music_task(task_id, params, api_key_id):
    db.update_task_status(task_id, 'failed')
    db.add_task_log(task_id, "501")

def get_tts_voices(api_key_id):
    return [], "TTS not supported by this service"

# ==============================================================================
# PROXY & RECOVERY LOGIC
# ==============================================================================

def proxy_request(url, range_header=None):
    """Local or HTTP Proxy implementation for serving media files."""
    import urllib.parse as urlparse

    # 1. Clean double proxy url prefix
    while True:
        if "/proxy?url=" in url:
            parsed_temp = urlparse.urlparse(url)
            params_temp = urlparse.parse_qs(parsed_temp.query)
            nested_url = params_temp.get("url", [None])[0]
            if nested_url:
                url = nested_url
                continue
        break

    if not url.startswith("http://") and not url.startswith("https://"):
        if os.path.exists(url):
            file_size = os.path.getsize(url)
            def iter_file():
                with open(url, "rb") as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            mime_type = "video/mp4" if url.endswith(".mp4") else "image/jpeg"
            headers = [
                ("Content-Type", mime_type),
                ("Content-Length", str(file_size)),
                ("Accept-Ranges", "bytes")
            ]
            return iter_file(), 200, headers
        else:
            return iter([]), 404, []

    # 2. Private MyEdit S3 URL Handling
    is_myedit_s3 = "cl-aol-media" in url or "cyberlink" in url
    
    if is_myedit_s3:
        import hashlib
        parsed_url = urlparse.urlparse(url)
        url_path = parsed_url.path
        
        url_path_hash = hashlib.md5(url_path.encode('utf-8')).hexdigest()
        ext = ".mp4" if url_path.lower().endswith(".mp4") else ".jpg"
        
        cache_dir = "cache"
        if not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir)
            except Exception:
                pass
        
        local_cache_path = os.path.join(cache_dir, f"{url_path_hash}{ext}")

        # A. If cached locally, stream from disk
        if os.path.exists(local_cache_path):
            file_size = os.path.getsize(local_cache_path)
            mime_type = "video/mp4" if ext == ".mp4" else "image/jpeg"
            headers = [
                ("Content-Type", mime_type),
                ("Accept-Ranges", "bytes")
            ]
            
            start = 0
            end = file_size - 1
            status_code = 200

            if range_header and range_header.startswith("bytes="):
                try:
                    ranges = range_header.replace("bytes=", "").split("-")
                    if ranges[0]:
                        start = int(ranges[0])
                    if len(ranges) > 1 and ranges[1]:
                        end = int(ranges[1])
                    status_code = 206
                    headers.append(("Content-Range", f"bytes {start}-{end}/{file_size}"))
                except Exception:
                    pass

            headers.append(("Content-Length", str(end - start + 1)))

            def iter_cached_file():
                with open(local_cache_path, "rb") as f:
                    f.seek(start)
                    offset = start
                    while offset <= end:
                        chunk_end = min(offset + 8192, end + 1)
                        chunk = f.read(chunk_end - offset)
                        if not chunk:
                            break
                        yield chunk
                        offset += len(chunk)

            return iter_cached_file(), status_code, headers

        # B. Lookup task in database to fetch decryption keys / base64
        conn = db.get_connection()
        cursor = conn.cursor()
        task_row = None
        try:
            query_val = f"%{url_path}%"
            if db.DB_TYPE == 'postgresql':
                cursor.execute('SELECT token FROM tasks WHERE result_url LIKE %s OR reference_image_urls LIKE %s', (query_val, query_val))
            else:
                cursor.execute('SELECT token FROM tasks WHERE result_url LIKE ? OR reference_image_urls LIKE ?', (query_val, query_val))
            row = cursor.fetchone()
            if row:
                if isinstance(row, dict):
                    task_row = row.get("token")
                elif isinstance(row, tuple) or isinstance(row, list):
                    task_row = row[0]
                else:
                    task_row = getattr(row, "token", None)
        except Exception as e:
            print(f"Proxy db query error: {e}")
        finally:
            cursor.close()
            conn.close()

        if task_row:
            try:
                task_data = json.loads(task_row)
            except Exception:
                task_data = {}

            # Case B1: Requesting reference image/video from database
            if "source" in url_path or "input." in url_path:
                filename = os.path.basename(url_path)
                match = re.search(r'(?:input\.|source_)(\d+)', filename)
                idx = 0
                if match:
                    if "input." in filename:
                        idx = int(match.group(1)) - 1
                    else:
                        idx = int(match.group(1))
                    if idx < 0:
                        idx = 0
                elif "first" in filename:
                    idx = 0
                elif "end" in filename:
                    idx = 1

                images = task_data.get("reference_images", [])
                videos = task_data.get("reference_videos", [])
                start_frame = task_data.get("start_frame")
                end_frame = task_data.get("end_frame")

                b64_data = None
                mime_type = "image/jpeg"

                is_first = ("first" in filename) or (idx == 0 and ("input.1" in filename or "source_0" in filename))
                is_end = ("end" in filename) or (idx == 1 and ("input.2" in filename or "source_1" in filename))

                if is_first and start_frame:
                    b64_data = start_frame
                elif is_end and end_frame:
                    b64_data = end_frame
                elif "video" in filename or filename.endswith(".mp4"):
                    if idx < len(videos):
                        b64_data = videos[idx]
                        mime_type = "video/mp4"
                else:
                    if idx < len(images):
                        b64_data = images[idx]
                    elif idx == 0 and start_frame:
                        b64_data = start_frame
                    elif idx == 1 and end_frame:
                        b64_data = end_frame

                if b64_data:
                    if "," in b64_data:
                        b64_data = b64_data.split(",")[1]
                    media_bytes = base64.b64decode(b64_data)
                    
                    try:
                        with open(local_cache_path, "wb") as f:
                            f.write(media_bytes)
                    except Exception:
                        pass

                    file_size = len(media_bytes)
                    headers = [
                        ("Content-Type", mime_type),
                        ("Content-Length", str(file_size)),
                        ("Accept-Ranges", "bytes")
                    ]
                    def iter_bytes():
                        for offset in range(0, file_size, 8192):
                            yield media_bytes[offset:offset+8192]
                    return iter_bytes(), 200, headers
                else:
                    return iter([]), 404, []

            # Case B2: Requesting VGEN result video/image (requires decryption if keys are present)
            dec_aes_key = task_data.get("dec_aes_key")
            dec_enc_key = task_data.get("dec_enc_key")
            dec_enc_iv = task_data.get("dec_enc_iv")
            dec_ts_ms = task_data.get("dec_ts_ms")

            try:
                fwd_headers = {
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
                }
                resp = requests.get(url, headers=fwd_headers, timeout=60)
                resp.raise_for_status()
                raw_bytes = resp.content

                if dec_aes_key and dec_enc_key and dec_enc_iv and dec_ts_ms:
                    try:
                        aes_key = bytes.fromhex(dec_aes_key)
                        dec_enc_key = dec_enc_key.replace(" ", "+")
                        dec_enc_iv = dec_enc_iv.replace(" ", "+")

                        raw_key = decrypt_myedit_aes_gcm(aes_key, dec_enc_key, int(dec_ts_ms), 0)
                        raw_iv = decrypt_myedit_aes_gcm(aes_key, dec_enc_iv, int(dec_ts_ms), 0)
                        aesgcm = AESGCM(raw_key)
                        decrypted_bytes = aesgcm.decrypt(raw_iv, raw_bytes, None)
                    except Exception as dec_err:
                        print(f"Decryption failed: {dec_err}")
                        decrypted_bytes = raw_bytes
                else:
                    decrypted_bytes = raw_bytes

                try:
                    with open(local_cache_path, "wb") as f:
                        f.write(decrypted_bytes)
                except Exception:
                    pass

                file_size = len(decrypted_bytes)
                mime_type = "video/mp4" if url_path.lower().endswith(".mp4") else "image/jpeg"

                headers = [
                    ("Content-Type", mime_type),
                    ("Accept-Ranges", "bytes")
                ]

                start = 0
                end = file_size - 1
                status_code = 200

                if range_header and range_header.startswith("bytes="):
                    try:
                        ranges = range_header.replace("bytes=", "").split("-")
                        if ranges[0]:
                            start = int(ranges[0])
                        if len(ranges) > 1 and ranges[1]:
                            end = int(ranges[1])
                        status_code = 206
                        headers.append(("Content-Range", f"bytes {start}-{end}/{file_size}"))
                    except Exception:
                        pass

                headers.append(("Content-Length", str(end - start + 1)))

                def iter_bytes():
                    offset = start
                    while offset <= end:
                        chunk_end = min(offset + 8192, end + 1)
                        yield decrypted_bytes[offset:chunk_end]
                        offset = chunk_end

                return iter_bytes(), status_code, headers
            except Exception as e:
                print(f"Proxy fetch/decryption error: {e}")
                return iter([]), 500, []

        return iter([]), 404, []

    # 3. Standard HTTP streaming proxy
    fwd_headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    }
    if range_header:
        fwd_headers['Range'] = range_header
    r = requests.get(url, headers=fwd_headers, stream=True, timeout=(30, 120))
    excluded = {'content-encoding', 'transfer-encoding', 'connection'}
    resp_headers = [(k, v) for k, v in r.headers.items() if k.lower() not in excluded]
    return r.iter_content(chunk_size=8192), r.status_code, resp_headers

# ==============================================================================
# RECOVERY LOGIC
# ==============================================================================

def resume_incomplete_tasks():
    """Startup task recovery."""
    print("=" * 50)
    print("[STARTUP] Starting crash recovery for MyEdit service...")
    try:
        recovery_result = db.recover_stale_tasks()
        if recovery_result['failed_count'] > 0:
            print(f"[STARTUP] Marked {recovery_result['failed_count']} tasks as failed (never logged in)")
    except Exception as e:
        print(f"[STARTUP] Error during stale task recovery: {e}")
        recovery_result = {'needs_check': []}

    needs_check = recovery_result.get('needs_check', [])
    for t in needs_check:
        db.update_task_status(t['task_id'], 'failed')
        if t.get('account_email') and t.get('api_key_id'):
            db.release_account(t['api_key_id'], t['account_email'])

    try:
        tasks = db.get_incomplete_tasks()
        for t in tasks:
            db.update_task_status(t['task_id'], 'failed')
            if t.get('account_email') and t.get('api_key_id'):
                db.release_account(t['api_key_id'], t['account_email'])
    except Exception as e:
        print(f"[STARTUP] Error during task resume: {e}")
    print("[STARTUP] Crash recovery complete.")
    print("=" * 50)
