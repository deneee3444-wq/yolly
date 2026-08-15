"""
Service Module - Self-Contained MyEdit Online Integration
Integrates with myEditOnline services.
Uses SpamOK temp mail for on-the-fly account registration and verification.
Saves created accounts directly to the database.
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
# MYEDIT API ENDPOINT'LERI VE SABITLER
# ==============================================================================
INIT_URL = "https://cse.cyberlink.com/cse/v2/init"
SIGNUP_URL = "https://cse.cyberlink.com/cse/v2/signup"
LOGIN_URL = "https://cse.cyberlink.com/cse/v2/login"
DAILY_BONUS_URL = "https://credit.cyberlink.com/v1/member/daily-bonus/get"
CREDIT_KEY_URL = "https://credit.cyberlink.com/v1/app/key"
CREDIT_TASK_BONUS_GET_URL = "https://credit.cyberlink.com/v1/app/task-bonus/get"
CREDIT_MEMBER_REMAIN_URL = "https://credit.cyberlink.com/v2/member/remain"
SUB_AUTH_URL = "https://myedit.online/api/cloud/subscriptions/auth"

MYEDIT_TTI_URL = "https://myedit.online/tti/effect"
MYEDIT_VGEN_URL = "https://myedit.online/vgen/effect"
MYEDIT_VGEN_BUSY_URL = "https://myedit.online/vgen/effect/busy"

AES_IV = b"CyberLinkCSE"  # CSE modulu icin 12 byte sabit IV
CREDIT_IV = b"CyberLinkCredit"  # Credit modulu icin 16 byte IV ve AAD
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

# ==============================================================================
# RESIM MODEL CONFIGURATIONS
# ==============================================================================
IMAGE_MODELS_CONFIG = {
    "flux_2_pro": {
        "name": "Flux 2 Pro",
        "vendor": "BlackForest",
        "actionId_prefix": "genimage_1_img_blackforest_flux2pro",
        "promptLength": 2500,
        "ref_img_limit": 4,
        "supported_resolutions": ["1K", "2K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 2, "2K": 5},
            "enable": {"1K": 3, "2K": 6}
        },
        "default_style": "Style_047_None_Flux"
    },
    "flux_dev": {
        "name": "Flux Dev 1",
        "vendor": "CyberLink",
        "actionId_prefix": "genimage_1_img_cyberlink_fluxdev1",
        "promptLength": 800,
        "ref_img_limit": 0,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 1}
        },
        "default_style": "Style_047_None_Flux"
    },
    "z_image": {
        "name": "Z-Image",
        "vendor": "CyberLink",
        "actionId_prefix": "genimage_1_img_cyberlink_zimageturbo",
        "promptLength": 2500,
        "ref_img_limit": 0,
        "supported_resolutions": ["1K", "2K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 1, "2K": 2}
        },
        "default_style": "Style_047_None_Flux"
    },
    "stable_diffusion": {
        "name": "Stable Diffusion XL",
        "vendor": "CyberLink",
        "actionId_prefix": "genimage_1_img_cyberlink_stablediffusion",
        "promptLength": 2500,
        "ref_img_limit": 1,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 1},
            "enable": {"1K": 1}
        },
        "default_style": "Style_Default",
        "effect_type": "TtiStyleRef"
    },
    "gemini_3_1_flash_lite": {
        "name": "Gemini 3.1 Flash Lite (Nano Banana 2 Lite)",
        "vendor": "Google",
        "actionId_prefix": "genimage_1_img_google_gemini3.1flashlite",
        "promptLength": 2500,
        "ref_img_limit": 14,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 2},
            "enable": {"1K": 2}
        },
        "default_style": "Style_3003_Custom_Gemini"
    },
    "gemini_3_1_flash": {
        "name": "Gemini 3.1 Flash (Nano Banana 2)",
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
        "name": "Gemini 3 Pro (Nano Banana Pro)",
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
    "kling_o1": {
        "name": "Kling O1",
        "vendor": "Kling",
        "actionId_prefix": "genimage_1_img_kling_o1",
        "promptLength": 2500,
        "ref_img_limit": 10,
        "supported_resolutions": ["1K", "2K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3, "2K": 3},
            "enable": {"1K": 3, "2K": 3, "4K": 5}
        },
        "default_style": "Style_5001_Custom_Kling"
    },
    "kling_o3": {
        "name": "Kling O3",
        "vendor": "Kling",
        "actionId_prefix": "genimage_1_img_kling_o3",
        "promptLength": 2500,
        "ref_img_limit": 10,
        "supported_resolutions": ["1K", "2K", "4K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3, "2K": 3, "4K": 5},
            "enable": {"1K": 3, "2K": 3, "4K": 5}
        },
        "default_style": "Style_5001_Custom_Kling"
    },
    "seedream_5_lite": {
        "name": "SeeDream 5.0 Lite",
        "vendor": "ByteDance",
        "actionId_prefix": "genimage_1_img_bytedance_seedream5.0lite",
        "promptLength": 600,
        "ref_img_limit": 14,
        "supported_resolutions": ["2K", "3K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"2K": 3, "3K": 3},
            "enable": {"2K": 3, "3K": 3}
        },
        "default_style": "Style_9000_Custom_Seedream"
    },
    "gpt_image_1": {
        "name": "GPT-Image-1",
        "vendor": "OpenAI",
        "actionId_prefix": "genimage_1_img_openai_gptimage1",
        "promptLength": 2500,
        "ref_img_limit": 16,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 2},
            "enable": {"1K": 2}
        },
        "default_style": "Style_1002_Custom_ChatGPT"
    },
    "gpt_image_1_5": {
        "name": "GPT-Image-1.5",
        "vendor": "OpenAI",
        "actionId_prefix": "genimage_1_img_openai_gptimage1.5",
        "promptLength": 2500,
        "ref_img_limit": 16,
        "supported_resolutions": ["1K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3},
            "enable": {"1K": 3}
        },
        "default_style": "Style_1002_Custom_ChatGPT"
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
    },
    "seedream_5_pro": {
        "name": "SeeDream 5.0 Pro",
        "vendor": "ByteDance",
        "actionId_prefix": "genimage_1_img_bytedance_seedream5.0pro",
        "promptLength": 600,
        "ref_img_limit": 10,
        "supported_resolutions": ["1K", "2K"],
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "credits": {
            "none": {"1K": 3, "2K": 5},
            "enable": {"1K": 3, "2K": 5}
        },
        "default_style": "Style_9000_Custom_Seedream"
    },
    "gemini_2_5_flash": {
        "name": "Gemini 2.5 Flash (Nano Banana)",
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
    }
}

# ==============================================================================
# VIDEO MODEL CONFIGURATIONS
# ==============================================================================
VIDEO_MODELS_CONFIG = {
    "seedance_2_0_fast": {
        "name": "Seedance 2.0 Fast",
        "model": "dreamina-seedance-2-0-fast-260128",
        "vendor": "BytePlus",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p"],
            "TextToVideo": ["480p", "720p"],
            "ReferenceToVideo": ["480p", "720p"],
        },
        "action_id": "genvideo_1_sec_bytedance_seedance2.0fast_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_bytedance_custom_seedance2.0fast_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "480p"): 3, ("none", "720p"): 6,
            ("vendor", "480p"): 3, ("vendor", "720p"): 6,
        },
        "credit": 3,
        "mode": "fast",
        "reference_media_limit": {
            "supported_types": ["image", "video"],
            "max_images": 9,
            "max_videos": 3,
            "max_total": 12,
            "max_video_duration": 15,
        },
    },
    "seedance_2_0_mini": {
        "name": "Seedance 2.0 Mini",
        "model": "dreamina-seedance-2-0-mini-260615",
        "vendor": "BytePlus",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["480p", "720p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["480p", "720p"],
            "TextToVideo": ["480p", "720p"],
            "ReferenceToVideo": ["480p", "720p"],
        },
        "action_id": "genvideo_1_sec_bytedance_seedance2.0mini_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_bytedance_custom_seedance2.0mini_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "480p"): 2, ("none", "720p"): 4,
            ("vendor", "480p"): 2, ("vendor", "720p"): 4,
        },
        "credit": 2,
        "mode": "mini",
        "reference_media_limit": {
            "supported_types": ["image", "video"],
            "max_images": 9,
            "max_videos": 3,
            "max_total": 12,
            "max_video_duration": 15,
        },
    },
    "seedance_2_0_pro": {
        "name": "Seedance 2.0 Pro",
        "model": "dreamina-seedance-2-0-260128",
        "vendor": "BytePlus",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["480p", "720p", "1080p"],
            "ReferenceToVideo": ["480p", "720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_bytedance_seedance2.0std_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_bytedance_custom_seedance2.0std_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "480p"): 4, ("none", "720p"): 8, ("none", "1080p"): 18,
            ("vendor", "480p"): 4, ("vendor", "720p"): 8, ("vendor", "1080p"): 18,
        },
        "credit": 4,
        "mode": "std",
        "reference_media_limit": {
            "supported_types": ["image", "video"],
            "max_images": 9,
            "max_videos": 3,
            "max_total": 12,
            "max_video_duration": 15,
        },
    },
    "happy_horse_1_1": {
        "name": "Happy Horse 1.1",
        "model": {
            "TextToVideo": "happyhorse-1.1-t2v",
            "ImageToVideo": "happyhorse-1.1-i2v",
            "ReferenceToVideo": "happyhorse-1.1-r2v",
        },
        "vendor": "Alibaba",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["720p", "1080p"],
            "ReferenceToVideo": ["720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_alibaba_happyhorse1.1_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_alibaba_custom_happyhorse1.1_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "720p"): 5, ("none", "1080p"): 8,
            ("vendor", "720p"): 5, ("vendor", "1080p"): 8,
        },
        "credit": 5,
        "default_sound": "vendor",
        "reference_media_limit": {
            "supported_types": ["image"],
            "max_images": 9,
        },
    },
    "wan_2_7": {
        "name": "Wan 2.7",
        "model": {
            "TextToVideo": "wan2.7-t2v",
            "ImageToVideo": "wan2.7-i2v",
            "ReferenceToVideo": "wan2.7-r2v",
        },
        "vendor": "Alibaba",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["720p", "1080p"],
            "ReferenceToVideo": ["720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_alibaba_wan2.7_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_alibaba_custom_wan2.7_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "720p"): 5, ("none", "1080p"): 8,
            ("vendor", "720p"): 5, ("vendor", "1080p"): 8,
        },
        "credit": 5,
        "default_sound": "vendor",
        "mode": "std",
        "reference_media_limit": {
            "supported_types": ["image", "video"],
            "max_images": 5,
            "max_videos": 5,
            "max_total": 5,
            "max_video_duration": 30,
        },
    },
    "veo_3_1_fast": {
        "name": "Veo 3.1 Fast",
        "model": "veo-3.1-fast-generate-preview",
        "vendor": "Google",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p", "1080p", "4k"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 6, 8],
        "action_id": "genvideo_1_sec_google_veo3.1fast_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_google_custom_veo3.1fast_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "720p"): 5,
            ("ImageToVideo", "none", "1080p"): 5,
            ("ImageToVideo", "none", "4k"): 5,
            ("ImageToVideo", "vendor", "720p"): 8,
            ("ImageToVideo", "vendor", "1080p"): 8,
            ("ImageToVideo", "vendor", "4k"): 8,
            ("TextToVideo", "none", "720p"): 5,
            ("TextToVideo", "none", "1080p"): 7,
            ("TextToVideo", "none", "4k"): 14,
            ("TextToVideo", "vendor", "720p"): 7,
            ("TextToVideo", "vendor", "1080p"): 9,
            ("TextToVideo", "vendor", "4k"): 16,
        },
        "credit": 5,
        "mode": "fast",
    },
    "veo_3_1": {
        "name": "Veo 3.1",
        "model": "veo-3.1-generate-preview",
        "vendor": "Google",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p", "1080p", "4k"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 6, 8],
        "action_id": "genvideo_1_sec_google_veo3.1std_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_google_custom_veo3.1std_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "720p"): 10,
            ("ImageToVideo", "none", "1080p"): 10,
            ("ImageToVideo", "none", "4k"): 10,
            ("ImageToVideo", "vendor", "720p"): 18,
            ("ImageToVideo", "vendor", "1080p"): 18,
            ("ImageToVideo", "vendor", "4k"): 18,
            ("TextToVideo", "none", "720p"): 10,
            ("TextToVideo", "none", "1080p"): 10,
            ("TextToVideo", "none", "4k"): 20,
            ("TextToVideo", "vendor", "720p"): 18,
            ("TextToVideo", "vendor", "1080p"): 18,
            ("TextToVideo", "vendor", "4k"): 30,
        },
        "credit": 10,
        "mode": "std",
    },
    "veo_3_1_lite": {
        "name": "Veo 3.1 Lite",
        "model": "veo-3.1-lite-generate-001",
        "vendor": "Google",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 6, 8],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["720p", "1080p"],
        },
        "supported_durations_by_mode": {
            "ImageToVideo": [4, 6, 8],
            "TextToVideo": [4, 8],
        },
        "action_id": "genvideo_1_sec_google_veo3.1lite_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_google_custom_veo3.1lite_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "720p"): 2, ("none", "1080p"): 3,
            ("vendor", "720p"): 3, ("vendor", "1080p"): 5,
        },
        "credit": 3,
        "mode": "std",
    },
    "kling_o3": {
        "name": "Kling O3",
        "model": "kling-v3-omni",
        "vendor": "Kling",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p", "1080p", "4k"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p", "1080p"],
            "TextToVideo": ["720p", "1080p", "4k"],
            "ReferenceToVideo": ["720p", "1080p", "4k"],
        },
        "action_id": "genvideo_1_sec_kling_o3pro_{sound}_1080p",
        "action_id_overrides": {
            "720p": "genvideo_1_sec_kling_o3std_{sound}_720p",
            "1080p": "genvideo_1_sec_kling_o3pro_{sound}_1080p",
            "4k": "genvideo_1_sec_kling_o34k_{sound}_4k",
        },
        "action_id_i2v_overrides": {
            "720p": "genvideo_1_sec_kling_custom_o3std_{sound}_{frame_mode}_720p",
            "1080p": "genvideo_1_sec_kling_custom_o3pro_{sound}_{frame_mode}_1080p",
        },
        "credit_map": {
            ("none", "720p"): 4, ("none", "1080p"): 5, ("none", "4k"): 14,
            ("vendor", "720p"): 5, ("vendor", "1080p"): 6, ("vendor", "4k"): 14,
        },
        "credit": 5,
        "reference_media_limit": {
            "supported_types": ["image"],
            "max_images": 7,
        },
    },
    "kling_3_0": {
        "name": "Kling 3.0",
        "model": "kling-v3",
        "vendor": "Kling",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p", "1080p", "4k"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p", "1080p"],
            "TextToVideo": ["720p", "1080p", "4k"],
        },
        "action_id": "genvideo_1_sec_kling_3.0pro_{sound}_1080p",
        "action_id_overrides": {
            "720p": "genvideo_1_sec_kling_3.0std_{sound}_720p",
            "1080p": "genvideo_1_sec_kling_3.0pro_{sound}_1080p",
            "4k": "genvideo_1_sec_kling_3.04k_{sound}_4k",
        },
        "action_id_i2v_overrides": {
            "720p": "genvideo_1_sec_kling_custom_3.0std_{sound}_{frame_mode}_720p",
            "1080p": "genvideo_1_sec_kling_custom_3.0pro_{sound}_{frame_mode}_1080p",
        },
        "credit_map": {
            ("none", "720p"): 4, ("none", "1080p"): 5, ("none", "4k"): 14,
            ("vendor", "720p"): 6, ("vendor", "1080p"): 8, ("vendor", "4k"): 14,
        },
        "credit": 5,
    },
    "kling_2_6": {
        "name": "Kling 2.6",
        "model": "kling-v2-6",
        "vendor": "Kling",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10],
        "action_id": "genvideo_1_sec_kling_2.6pro_{sound}_1080p",
        "action_id_i2v": "genvideo_1_sec_kling_custom_2.6pro_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "1080p"): 5,
            ("ImageToVideo", "vendor", "1080p"): 8,
            ("TextToVideo", "none", "1080p"): 4,
            ("TextToVideo", "vendor", "1080p"): 8,
        },
        "credit": 5,
        "mode": "pro",
    },
    "kling_2_5_std": {
        "name": "Kling 2.5 Standard",
        "model": "kling-v2-5-turbo",
        "vendor": "Kling",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10],
        "action_id": "genvideo_1_sec_kling_2.5std_{sound}_720p",
        "action_id_i2v": "genvideo_1_sec_kling_custom_2.5std_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "720p"): 3,
            ("ImageToVideo", "vendor", "720p"): 3,
            ("TextToVideo", "none", "720p"): 2,
            ("TextToVideo", "vendor", "720p"): 2,
        },
        "credit": 3,
        "mode": "std",
    },
    "kling_2_5_pro": {
        "name": "Kling 2.5 Pro",
        "model": "kling-v2-5-turbo",
        "vendor": "Kling",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10],
        "action_id": "genvideo_1_sec_kling_2.5pro_{sound}_1080p",
        "action_id_i2v": "genvideo_1_sec_kling_custom_2.5pro_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "1080p"): 5,
            ("ImageToVideo", "vendor", "1080p"): 5,
            ("TextToVideo", "none", "1080p"): 4,
            ("TextToVideo", "vendor", "1080p"): 4,
        },
        "credit": 5,
        "mode": "pro",
    },
    "vidu_q3_turbo": {
        "name": "Vidu Q3 Turbo",
        "model": "viduq3-turbo",
        "vendor": "Vidu",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [4, 8, 16],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p"],
            "TextToVideo": ["540p", "720p", "1080p"],
            "ReferenceToVideo": ["540p", "720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_vidu_q3turbo_{sound}_{resolution}",
        "action_id_i2v_overrides": {
            "720p": "genvideo_1_sec_vidu_custom_q3turbo_{sound}_{frame_mode}_720p",
        },
        "credit_map": {
            ("none", "540p"): 2, ("none", "720p"): 3, ("none", "1080p"): 4,
            ("vendor", "540p"): 2, ("vendor", "720p"): 3, ("vendor", "1080p"): 4,
        },
        "credit": 4,
        "default_sound": "vendor",
        "mode": "turbo",
        "reference_media_limit": {
            "supported_types": ["image"],
            "max_images": 7,
        },
    },
    "vidu_q3_pro": {
        "name": "Vidu Q3 Pro",
        "model": "viduq3-pro",
        "vendor": "Vidu",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [4, 8, 16],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["540p", "720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_vidu_q3pro_{sound}_{resolution}",
        "action_id_i2v_overrides": {
            "1080p": "genvideo_1_sec_vidu_custom_q3pro_{sound}_{frame_mode}_1080p",
        },
        "credit_map": {
            ("none", "540p"): 3, ("none", "720p"): 5, ("none", "1080p"): 6,
            ("vendor", "540p"): 3, ("vendor", "720p"): 5, ("vendor", "1080p"): 6,
        },
        "credit": 6,
        "default_sound": "vendor",
        "mode": "pro",
    },
    "vidu_q2": {
        "name": "Vidu Q2",
        "model": "viduq2-turbo",
        "vendor": "Vidu",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [4, 8, 16],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p"],
            "TextToVideo": ["540p", "720p", "1080p"],
        },
        "supported_durations_by_mode": {
            "ImageToVideo": [4, 8, 16],
            "TextToVideo": [5, 10],
        },
        "action_id": "genvideo_1_sec_vidu_q2_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_vidu_custom_q2turbo_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "540p"): 3,
            ("ImageToVideo", "none", "720p"): 3,
            ("ImageToVideo", "none", "1080p"): 3,
            ("ImageToVideo", "vendor", "540p"): 3,
            ("ImageToVideo", "vendor", "720p"): 3,
            ("ImageToVideo", "vendor", "1080p"): 3,
            ("TextToVideo", "none", "540p"): 1,
            ("TextToVideo", "none", "720p"): 2,
            ("TextToVideo", "none", "1080p"): 3,
            ("TextToVideo", "vendor", "540p"): 1,
            ("TextToVideo", "vendor", "720p"): 2,
            ("TextToVideo", "vendor", "1080p"): 3,
        },
        "credit": 3,
        "mode": "turbo",
    },
    "pixverse_v6": {
        "name": "PixVerse V6",
        "model": "v6",
        "vendor": "Pixverse",
        "supported_modes": ["TextToVideo", "ImageToVideo", "ReferenceToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["1080p"],
            "TextToVideo": ["540p", "720p", "1080p"],
            "ReferenceToVideo": ["540p", "720p", "1080p"],
        },
        "action_id": "genvideo_1_sec_motivai_pixverse6_{sound}_{resolution}",
        "action_id_i2v_overrides": {
            "720p": "genvideo_1_sec_motivai_custom_pixverse6_{sound}_{frame_mode}_720p",
            "1080p": "genvideo_1_sec_motivai_custom_pixverse6_{sound}_{frame_mode}_1080p",
        },
        "credit_map": {
            ("ImageToVideo", "none", "single", "720p"): 2,
            ("ImageToVideo", "none", "startend", "720p"): 3,
            ("ImageToVideo", "vendor", "single", "720p"): 2,
            ("ImageToVideo", "vendor", "startend", "720p"): 3,
            ("ImageToVideo", "none", "single", "1080p"): 4,
            ("ImageToVideo", "none", "startend", "1080p"): 6,
            ("ImageToVideo", "vendor", "single", "1080p"): 4,
            ("ImageToVideo", "vendor", "startend", "1080p"): 6,
            ("none", "540p"): 1,
            ("none", "720p"): 2,
            ("none", "1080p"): 4,
            ("vendor", "540p"): 2,
            ("vendor", "720p"): 3,
            ("vendor", "1080p"): 6,
        },
        "credit": 4,
        "mode": "std",
        "reference_media_limit": {
            "supported_types": ["image"],
            "max_images": 7,
        },
    },
    "sora_2_std": {
        "name": "Sora 2 Standard",
        "model": "sora-2",
        "vendor": "OpenAI",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 8, 12],
        "action_id": "genvideo_1_sec_openai_sora2_{sound}_720p",
        "action_id_i2v": "genvideo_1_sec_openai_custom_sora2std_{sound}_{frame_mode}",
        "credit_map": {
            ("none", "720p"): 6,
            ("vendor", "720p"): 6,
        },
        "credit": 6,
        "mode": "std",
        "default_sound": "vendor",
    },
    "sora_2_pro": {
        "name": "Sora 2 Pro",
        "model": "sora-2-pro",
        "vendor": "OpenAI",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single"],
        "supported_resolutions": ["720p", "1080p"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [4, 8, 12],
        "action_id": "genvideo_1_sec_openai_sora2pro_{sound}",
        "action_id_overrides": {
            "720p": "genvideo_1_sec_openai_sora2pro_{sound}_720p",
            "1080p": "genvideo_1_sec_openai_sora2pro_{sound}_1080p",
        },
        "action_id_i2v": "genvideo_1_sec_openai_custom_sora2pro_{sound}_{frame_mode}",
        "credit_map": {
            ("ImageToVideo", "none", "720p"): 18,
            ("ImageToVideo", "none", "1080p"): 18,
            ("ImageToVideo", "vendor", "720p"): 18,
            ("ImageToVideo", "vendor", "1080p"): 18,
            ("TextToVideo", "none", "720p"): 16,
            ("TextToVideo", "vendor", "720p"): 16,
            ("TextToVideo", "none", "1080p"): 28,
            ("TextToVideo", "vendor", "1080p"): 28,
        },
        "credit": 18,
        "mode": "pro",
        "default_sound": "vendor",
    },
    "seedance_2_5": {
        "name": "Seedance 2.5",
        "model": "dreamina-seedance-2-5-260628",
        "vendor": "BytePlus",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single", "startend"],
        "supported_resolutions": ["480p", "720p"],
        "supported_resolutions_by_mode": {
            "ImageToVideo": ["720p"],
            "TextToVideo": ["480p", "720p"],
        },
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_durations": [5, 10, 15, 30],
        "action_id": "genvideo_1_sec_bytedance_seedance2.5std_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_bytedance_custom_seedance2.5std_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "480p"): 5, ("none", "720p"): 10,
            ("vendor", "480p"): 5, ("vendor", "720p"): 10,
        },
        "credit": 10,
        "mode": "std",
        "reference_media_limit": {
            "supported_types": ["image", "video"],
            "max_images": 20,
            "max_videos": 10,
            "max_total": 20,
            "max_video_duration": 30,
        },
    },
    "gemini_omni_flash": {
        "name": "Gemini Omni Flash",
        "model": "gemini-omni-flash-preview",
        "vendor": "Google",
        "supported_modes": ["TextToVideo", "ImageToVideo"],
        "supported_frame_modes": ["single"],
        "supported_resolutions": ["720p"],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_durations": [5, 10],
        "action_id": "genvideo_1_sec_google_geminiomniflash_{sound}_{resolution}",
        "action_id_i2v": "genvideo_1_sec_google_custom_geminiomniflash_{sound}_{frame_mode}_{resolution}",
        "credit_map": {
            ("none", "720p"): 5,
            ("vendor", "720p"): 5,
        },
        "credit": 5,
        "mode": "std",
        "default_sound": "vendor",
        "reference_media_limit": {
            "supported_types": ["image"],
            "max_images": 5,
        },
    }
}

MODELS = {} # Compatibility mapping

AVAILABLE_MODELS = {
    "image": [
        {
            "id": "flux_2_pro",
            "name": "Flux 2 Pro",
            "description": "Flux 2 Pro by BlackForest - Supports up to 4 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 4,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "flux_dev",
            "name": "Flux Dev 1",
            "description": "Flux Dev 1 by CyberLink",
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 800
        },
        {
            "id": "z_image",
            "name": "Z-Image",
            "description": "Z-Image by CyberLink",
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "stable_diffusion",
            "name": "Stable Diffusion XL",
            "description": "Stable Diffusion XL by CyberLink - Supports 1 Reference Image",
            "supports_reference_images": True,
            "max_reference_images": 1,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "gemini_3_1_flash_lite",
            "name": "Gemini 3.1 Flash Lite (Nano Banana 2 Lite)",
            "description": "Gemini 3.1 Flash Lite (Nano Banana 2 Lite) by Google - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "gemini_3_1_flash",
            "name": "Gemini 3.1 Flash (Nano Banana 2)",
            "description": "Gemini 3.1 Flash (Nano Banana 2) by Google - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K", "4K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "gemini_3_pro",
            "name": "Gemini 3 Pro (Nano Banana Pro)",
            "description": "Gemini 3 Pro (Nano Banana Pro) by Google - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K", "4K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "kling_o1",
            "name": "Kling O1",
            "description": "Kling O1 by Kling - Supports up to 10 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 10,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "kling_o3",
            "name": "Kling O3",
            "description": "Kling O3 by Kling - Supports up to 10 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 10,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K", "4K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "seedream_5_lite",
            "name": "SeeDream 5.0 Lite",
            "description": "SeeDream 5.0 Lite by ByteDance - Supports up to 14 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 14,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["2K", "3K"],
            "default_size": "1:1",
            "default_resolution": "2K",
            "max_prompt_length": 600
        },
        {
            "id": "gpt_image_1",
            "name": "GPT-Image-1",
            "description": "GPT-Image-1 by OpenAI - Supports up to 16 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 16,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "gpt_image_1_5",
            "name": "GPT-Image-1.5",
            "description": "GPT-Image-1.5 by OpenAI - Supports up to 16 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 16,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        },
        {
            "id": "gpt_image_2",
            "name": "GPT-Image-2",
            "description": "GPT-Image-2 by OpenAI - Supports up to 16 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 16,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 8000
        },
        {
            "id": "seedream_5_pro",
            "name": "SeeDream 5.0 Pro",
            "description": "SeeDream 5.0 Pro by ByteDance - Supports up to 10 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 10,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K", "2K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 600
        },
        {
            "id": "gemini_2_5_flash",
            "name": "Gemini 2.5 Flash (Nano Banana)",
            "description": "Gemini 2.5 Flash (Nano Banana) by Google - Supports up to 3 Reference Images",
            "supports_reference_images": True,
            "max_reference_images": 3,
            "supported_sizes": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supported_resolutions": ["1K"],
            "default_size": "1:1",
            "default_resolution": "1K",
            "max_prompt_length": 2500
        }
    ],
    "video": [
        {
            "id": "seedance_2_0_fast",
            "name": "Seedance 2.0 Fast",
            "description": "Seedance 2.0 Fast by BytePlus - Supports Start/End Frame, up to 9 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 9,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["480p", "720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "seedance_2_0_mini",
            "name": "Seedance 2.0 Mini",
            "description": "Seedance 2.0 Mini by BytePlus - Supports Start/End Frame, up to 9 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 9,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["480p", "720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 1000
        },
        {
            "id": "seedance_2_0_pro",
            "name": "Seedance 2.0 Pro",
            "description": "Seedance 2.0 Pro by BytePlus - Supports Start/End Frame, up to 9 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 9,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "happy_horse_1_1",
            "name": "Happy Horse 1.1",
            "description": "Happy Horse 1.1 by Alibaba - Supports Start/End Frame, up to 9 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 9,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "wan_2_7",
            "name": "Wan 2.7",
            "description": "Wan 2.7 model by Alibaba - Supports Start/End Frame, up to 5 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 5,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["720p", "1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "veo_3_1_fast",
            "name": "Veo 3.1 Fast",
            "description": "Veo 3.1 Fast by Google - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 6, 8],
            "supported_resolutions": ["720p", "1080p", "4k"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 6,
            "max_prompt_length": 2000
        },
        {
            "id": "veo_3_1",
            "name": "Veo 3.1",
            "description": "Veo 3.1 by Google - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 6, 8],
            "supported_resolutions": ["720p", "1080p", "4k"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 6,
            "max_prompt_length": 2000
        },
        {
            "id": "veo_3_1_lite",
            "name": "Veo 3.1 Lite",
            "description": "Veo 3.1 Lite by Google - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 8],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 4,
            "max_prompt_length": 2000
        },
        {
            "id": "kling_o3",
            "name": "Kling O3",
            "description": "Kling O3 by Kling - Supports Start/End Frame, up to 7 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 7,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["720p", "1080p", "4k"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "kling_3_0",
            "name": "Kling 3.0",
            "description": "Kling 3.0 by Kling - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["720p", "1080p", "4k"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "kling_2_6",
            "name": "Kling 2.6",
            "description": "Kling 2.6 by Kling - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "kling_2_5_std",
            "name": "Kling 2.5 Standard",
            "description": "Kling 2.5 Standard by Kling - Supports Start Frame",
            "supports_start_frame": True,
            "supports_end_frame": False,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10],
            "supported_resolutions": ["720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "kling_2_5_pro",
            "name": "Kling 2.5 Pro",
            "description": "Kling 2.5 Pro by Kling - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "vidu_q3_turbo",
            "name": "Vidu Q3 Turbo",
            "description": "Vidu Q3 Turbo by Vidu - Supports Start/End Frame, up to 7 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 7,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [4, 8, 16],
            "supported_resolutions": ["720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 4,
            "max_prompt_length": 2000
        },
        {
            "id": "vidu_q3_pro",
            "name": "Vidu Q3 Pro",
            "description": "Vidu Q3 Pro by Vidu - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [4, 8, 16],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 4,
            "max_prompt_length": 2000
        },
        {
            "id": "vidu_q2",
            "name": "Vidu Q2",
            "description": "Vidu Q2 by Vidu - Supports Start/End Frame",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 8],
            "supported_resolutions": ["720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "pixverse_v6",
            "name": "PixVerse V6",
            "description": "PixVerse V6 by Pixverse - Supports Start/End Frame, up to 7 Reference Images",
            "supports_start_frame": True,
            "supports_end_frame": True,
            "supports_reference_images": True,
            "max_reference_images": 7,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15],
            "supported_resolutions": ["1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 5,
            "max_prompt_length": 2000
        },
        {
            "id": "sora_2_std",
            "name": "Sora 2 Standard",
            "description": "Sora 2 Standard by OpenAI - Supports Start Frame",
            "supports_start_frame": True,
            "supports_end_frame": False,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 8, 12],
            "supported_resolutions": ["720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 8,
            "max_prompt_length": 2000
        },
        {
            "id": "sora_2_pro",
            "name": "Sora 2 Pro",
            "description": "Sora 2 Pro by OpenAI - Supports Start Frame",
            "supports_start_frame": True,
            "supports_end_frame": False,
            "supports_reference_images": False,
            "max_reference_images": 0,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [4, 8, 12],
            "supported_resolutions": ["720p", "1080p"],
            "default_size": "16:9",
            "default_resolution": "1080p",
            "default_duration": 8,
            "max_prompt_length": 2000
        },
        {
            "id": "seedance_2_5",
            "name": "Seedance 2.5",
            "description": "Seedance 2.5 by BytePlus - Supports Start Frame, up to 20 Reference Images & 10 Videos",
            "supports_start_frame": True,
            "supports_end_frame": False,
            "supports_reference_images": True,
            "max_reference_images": 20,
            "supported_sizes": ["16:9", "9:16", "1:1"],
            "supported_durations": [5, 10, 15, 30],
            "supported_resolutions": ["480p", "720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 9900
        },
        {
            "id": "gemini_omni_flash",
            "name": "Gemini Omni Flash",
            "description": "Gemini Omni Flash by Google - Supports up to 5 Reference Images",
            "supports_start_frame": False,
            "supports_end_frame": False,
            "supports_reference_images": True,
            "max_reference_images": 5,
            "supported_sizes": ["16:9", "9:16"],
            "supported_durations": [5, 10],
            "supported_resolutions": ["720p"],
            "default_size": "16:9",
            "default_resolution": "720p",
            "default_duration": 5,
            "max_prompt_length": 3500
        }
    ],
    "tts": [],
    "music": []
}

def get_available_models(mode=None):
    if mode:
        return AVAILABLE_MODELS.get(mode, [])
    return AVAILABLE_MODELS

# ==============================================================================
# MYEDIT ONLINE ALTYAPI VE KRIPTOGRAFİK YARDIMCILAR (SINGLE FILE)
# ==============================================================================

def generate_random_spamok_email(length=12):
    """Rastgele spamok.com e-postasi uretir."""
    username = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return username, f"{username}@spamok.com"

def get_activation_link_from_spamok(username: str, timeout_seconds: int = 60):
    """SpamOK API'sini sorgulayarak CyberLink aktivasyon linkini otomatik ceker."""
    spamok_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "*/*",
        "Origin": "https://spamok.com",
        "Referer": "https://spamok.com/",
        "x-asdasd-platform-id": "blazor-en-us",
        "x-asdasd-platform-version": "blazor-1.0.0",
    }
    
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            inbox_url = f"https://api.spamok.com/v2/EmailBox/{username}"
            resp = requests.get(inbox_url, headers=spamok_headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                mails = data.get("mails", [])
                for mail in mails:
                    subject = mail.get("subject", "")
                    from_domain = mail.get("fromDomain", "")
                    if "activate" in subject.lower() or "cyberlink" in from_domain.lower() or "myedit" in mail.get("fromDisplay", "").lower():
                        mail_id = mail.get("id")
                        
                        detail_url = f"https://api.spamok.com/v2/Email/{username}/{mail_id}"
                        detail_resp = requests.get(detail_url, headers=spamok_headers, timeout=10)
                        if detail_resp.status_code == 200:
                            mail_detail = detail_resp.json()
                            html_content = mail_detail.get("messageHtml", "") or mail_detail.get("messagePlain", "")
                            
                            match = re.search(r'https://membership\.cyberlink\.com/prog/event/autoedm/trace_mem\.jsp\?[^\s"\'<>]+', html_content)
                            if match:
                                return match.group(0).replace("&amp;", "&")
                            
                            match2 = re.search(r'https://mauth\.cyberlink\.com/member-auth/public/active-member\?[^\s"\'<>]+', html_content)
                            if match2:
                                return match2.group(0).replace("&amp;", "&")
        except Exception:
            pass
        time.sleep(2)
    return None

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
    user_data = {
        "email": email,
        "pwd": password,
        "lang": lang,
        "rec_upgrade": "0",
        "country": country,
        "sid": "myedit",
    }
    payload, aes_key = create_payload(user_data)
    res = requests.post(SIGNUP_URL, json=payload, headers=HEADERS, timeout=30)
    res.raise_for_status()
    body = res.json()
    if "response" in body:
        return decrypt_response(body["response"], aes_key)
    return body

def activate_account(activation_url: str):
    session = requests.Session()
    session.headers.update({
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    resp = session.get(activation_url, allow_redirects=True, timeout=30)
    return "welcome" in resp.url or resp.status_code == 200

def login(email: str, password: str, lang: str = "enu", country: str = "US"):
    user_data = {
        "email": email,
        "pwd": password,
        "lang": lang,
        "rec_upgrade": "0",
        "country": country,
        "sid": "myedit",
    }
    payload, aes_key = create_payload(user_data)
    res = requests.post(LOGIN_URL, json=payload, headers=HEADERS, timeout=30)
    res.raise_for_status()
    body = res.json()
    if "response" in body:
        return decrypt_response(body["response"], aes_key)
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
    """Kullanicinin tum aktif task bonuslarini (kredilerini) ve gunluk bonusunu toplar (toplam 174 kredi)."""
    print("\n[Bonuses] Tum gunluk ve gorev bonuslari toplaniyor...")
    
    # 1. Gunluk Bonus (+3 Kredi)
    try:
        daily_res = get_daily_bonus(member_token)
        print(f"  -> Gunluk Bonus Toplama Sonucu: {daily_res.get('result', daily_res)}")
    except Exception as e:
        print(f"  [!] Gunluk bonus toplama hatasi: {e}")
        
    # 2. Aktif Gorev Bonuslari (Toplam 171 Kredi)
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
            
    # Toplam Kredi Durumunu Yazdir
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
# MAIN EXECUTION FUNCTIONS
# ==============================================================================

def generate_ai_image_service(
    member_token: str,
    user_prompt: str = "a majestic fantasy landscape, digital art, highly detailed 8k",
    image_paths: list = None,
    model_key: str = "flux_2_pro",
    style_id: str = None,
    aspect_ratio: str = "1:1",
    resolution: str = "1K",
    batch_size: str = "1",
    output_format: str = "jpeg",
    filename_prefix: str = "",
    task_id: str = None,
):
    if model_key not in IMAGE_MODELS_CONFIG:
        raise ValueError(f"Unsupported model: {model_key}")

    model_data = IMAGE_MODELS_CONFIG[model_key]
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

    is_style_ref = model_data.get("effect_type") == "TtiStyleRef"
    try:
        b_size = int(batch_size)
    except ValueError:
        b_size = 1

    if is_style_ref:
        feature_id_val = "TtiStyleRef"
        action_id_val = f"gen_{b_size}_img"
        total_credit_cost = 1 * b_size
    else:
        feature_id_val = "TextToImage"
        mode_key = "enable" if has_reference else "none"
        credit_cost = model_data["credits"][mode_key][resolution]
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

    version_val = "4" if ("flux" in model_key or model_key == "z_image") else "5"
    if (version_val == "5" or is_style_ref) and not loaded_images_bytes:
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
        if is_style_ref:
            filename_val = "TEXT_TO_IMAGE_STYLE_source"
        else:
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
            if is_style_ref:
                fname = "TEXT_TO_IMAGE_STYLE_source"
            else:
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
        "unit": 1 if is_style_ref else int(batch_size),
        "total_credit": total_credit_cost
    }
    consumption_param = encrypt_myedit_aes_gcm(
        aes_key,
        json.dumps(consumption_data, separators=(',', ':')),
        req_ts_ms,
        s_id_int
    )

    if is_style_ref:
        form_data_apply = {
            "source": str(uploaded_sources_list[0]) if uploaded_sources_list else "1",
            "aspect_ratio": aspect_ratio,
            "user_prompt": user_prompt,
            "need_translate": "true",
            "need_bad_word_check": "false",
            "batch_size": str(batch_size),
            "consumption": consumption_param,
            "cloud_sync": "true",
            "alias": alias_str,
            "effect": "TtiStyleRef",
        }
    else:
        form_data_apply = {
            "style_id": style_id,
            "style_prompt": "",
            "version": version_val,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
            "user_prompt": user_prompt,
            "batch_size": str(batch_size),
            "consumption": consumption_param,
            "cloud_sync": "true",
            "alias": alias_str,
            "effect": "TextToImage",
        }

        if "flux" not in model_key:
            form_data_apply["resolution"] = resolution
        if has_reference or "flux" not in model_key:
            form_data_apply["sources"] = sources_str
        if "flux" in model_key:
            form_data_apply["need_translate"] = "true"
            form_data_apply["need_bad_word_check"] = "false"

    files_apply = {k: (None, str(v)) for k, v in form_data_apply.items()}

    resp_apply = requests.patch(apply_url, files=files_apply, headers=apply_headers, timeout=30)
    resp_apply.raise_for_status()

    apply_json = resp_apply.json()
    task_id = apply_json.get("task_id")
    polling = apply_json.get("polling", {})
    delay = polling.get("delay", 5)

    max_attempts = 120
    decrypted_files = []
    for i in range(max_attempts):
        time.sleep(delay)
        req_ts_ms = int(time.time() * 1000)
        enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
        poll_url = f"{MYEDIT_TTI_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}/{task_id}"

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
                task_info = f.get("task", {})
                # Her zaman ilk oturum anahtari (p_key) kullanilmalidir.
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
            return {"status": "Failed", "error": poll_json, "reference_urls": uploaded_reference_urls}

    return {"status": "Timeout", "reference_urls": uploaded_reference_urls}

def generate_ai_video_service(
    member_token: str,
    user_prompt: str = "a cute astronaut cat floating in space station, cinematic lighting",
    model_key: str = "seedance_2_0_fast",
    aspect_ratio: str = "16:9",
    resolution: str = "480p",
    processing_duration: int = 5,
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
    model_data = VIDEO_MODELS_CONFIG.get(model_key, VIDEO_MODELS_CONFIG["seedance_2_0_fast"])
    if isinstance(model_data["model"], dict):
        model_name_str = model_data["model"].get(effect_mode, list(model_data["model"].values())[0])
    else:
        model_name_str = model_data["model"]
    vendor_str = model_data["vendor"]

    if effect_mode == "ReferenceToVideo":
        limit = model_data.get("reference_media_limit", {})
        supported_types = limit.get("supported_types", ["image"])
        num_images = len(ref_images) if ref_images else 0
        num_videos = len(ref_videos) if ref_videos else 0
        
        if num_images > 0 and "image" not in supported_types:
            raise ValueError("Reference images not supported.")
        if num_videos > 0 and "video" not in supported_types:
            raise ValueError("Reference videos not supported.")
            
        max_images = limit.get("max_images")
        max_videos = limit.get("max_videos")
        max_total = limit.get("max_total", (max_images or 0) + (max_videos or 0))
        
        if max_images and num_images > max_images:
            raise ValueError("Image count exceeds limit.")
        if max_videos and num_videos > max_videos:
            raise ValueError("Video count exceeds limit.")
        if max_total and (num_images + num_videos) > max_total:
            raise ValueError("Total references exceed limit.")

    SORA_RESOLUTION_MAP = {
        ("720p", "16:9"): "1280:720",
        ("720p", "9:16"): "720:1280",
        ("1080p", "16:9"): "1792:1024",
        ("1080p", "9:16"): "1024:1792",
    }

    if effect_mode == "ImageToVideo" and last_image_path:
        frame_mode = "startend"

    prepared_media = []
    if effect_mode == "ImageToVideo" and source_image_path and os.path.exists(source_image_path):
        target_w, target_h = None, None
        if vendor_str == "OpenAI":
            sora_res = SORA_RESOLUTION_MAP.get((resolution, aspect_ratio), "1280:720")
            target_w, target_h = map(int, sora_res.split(":"))
        else:
            try:
                ar_w, ar_h = map(int, aspect_ratio.split(":"))
            except Exception:
                ar_w, ar_h = 16, 9
            
            h_val = 720
            if resolution == "480p": h_val = 480
            elif resolution == "540p": h_val = 540
            elif resolution == "1080p": h_val = 1080
            elif resolution == "4k": h_val = 2160
            
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
            
    elif effect_mode == "ReferenceToVideo":
        if ref_images:
            for idx, item in enumerate(ref_images):
                img_path = item["path"] if isinstance(item, dict) else item
                if os.path.exists(img_path):
                    prepared_media.append({"path": img_path, "tag": f"@image{idx+1}", "type": "image", "is_temp": False})
        if ref_videos:
            for idx, item in enumerate(ref_videos):
                vid_path = item["path"] if isinstance(item, dict) else item
                if os.path.exists(vid_path):
                    prepared_media.append({"path": vid_path, "tag": f"@video{idx+1}", "type": "video", "is_temp": False})

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
            overrides_t2v = model_data.get("action_id_overrides", {})
            if resolution in overrides_t2v:
                action_id_str = overrides_t2v[resolution].format(sound=action_id_sound, resolution=resolution)
            else:
                action_id_str = model_data["action_id"].format(sound=action_id_sound, resolution=resolution)
    else:
        overrides = model_data.get("action_id_overrides", {})
        if resolution in overrides:
            action_id_str = overrides[resolution].format(sound=action_id_sound, resolution=resolution)
        else:
            action_id_str = model_data["action_id"].format(sound=action_id_sound, resolution=resolution)
            
    credit_map = model_data.get("credit_map")
    if credit_map:
        credit_cost = credit_map.get((effect_mode, sound, frame_mode, resolution))
        if credit_cost is None:
            credit_cost = credit_map.get((effect_mode, sound, resolution))
        if credit_cost is None:
            credit_cost = credit_map.get((sound, resolution))
        if credit_cost is None:
            credit_cost = model_data.get("credit", 3)
    else:
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
    }

    model_mode = model_data.get("mode")
    if not model_mode:
        model_mode = "pro" if resolution in ["1080p", "4k"] else "std"

    if prepared_media:
        media_info_list = init_json.get("media_info", [])
        media_ids = [m["id"] for m in media_info_list if "id" in m]
        first_frame_id = media_ids[0] if len(media_ids) > 0 else 1
        end_frame_id = media_ids[1] if len(media_ids) > 1 else None

        if effect_mode == "ImageToVideo":
            img_w, img_h = get_image_dimensions(prepared_media[0]["path"])
            image_file_size = os.path.getsize(prepared_media[0]["path"])

            if vendor_str in ["Alibaba", "Pixverse", "BytePlus"]:
                form_data_apply["width"] = str(img_w)
                form_data_apply["height"] = str(img_h)
                if vendor_str == "Alibaba":
                    form_data_apply["aspect_ratio"] = aspect_ratio
                    form_data_apply["resolution"] = resolution.upper()
                    if model_mode:
                        form_data_apply["mode"] = model_mode
                else:
                    form_data_apply["mode"] = model_mode
                
                if end_frame_id is not None:
                    form_data_apply["sources"] = json.dumps([first_frame_id, end_frame_id])
                    image_list_obj = [
                        {"media_id": first_frame_id, "filesize": image_file_size, "width": img_w, "height": img_h, "type": "first_frame"},
                        {"media_id": end_frame_id, "filesize": os.path.getsize(prepared_media[1]["path"]), "width": img_w, "height": img_h, "type": "end_frame"}
                    ]
                else:
                    form_data_apply["sources"] = json.dumps([first_frame_id])
                    image_list_obj = [
                        {"media_id": first_frame_id, "filesize": image_file_size, "width": img_w, "height": img_h, "type": "first_frame"}
                    ]
                form_data_apply["image_list"] = json.dumps(image_list_obj, separators=(",", ":"))

            elif vendor_str == "OpenAI":
                sora_res = SORA_RESOLUTION_MAP.get((resolution, aspect_ratio), "1280:720")
                target_w, target_h = sora_res.split(":")
                form_data_apply["width"] = target_w
                form_data_apply["height"] = target_h
                form_data_apply["resolution"] = sora_res
                form_data_apply["sources"] = json.dumps([first_frame_id])
            else:
                form_data_apply["width"] = str(img_w)
                form_data_apply["height"] = str(img_h)
                form_data_apply["mode"] = model_mode
                
                if end_frame_id is not None:
                    form_data_apply["sources"] = json.dumps([first_frame_id, -1, end_frame_id])
                    last_image_file_size = os.path.getsize(prepared_media[1]["path"])
                    last_image_obj = {"media_id": end_frame_id, "filesize": last_image_file_size, "width": img_w, "height": img_h}
                    form_data_apply["last_image"] = json.dumps(last_image_obj, separators=(",", ":"))
                else:
                    form_data_apply["sources"] = json.dumps([first_frame_id])
                    
        elif effect_mode == "ReferenceToVideo":
            form_data_apply["sources"] = json.dumps(media_ids)
            form_data_apply["effect"] = "RefToVideo"
            image_list_obj = []
            video_list_obj = []
            for j, item in enumerate(prepared_media):
                if item["type"] == "image":
                    w_j, h_j = get_image_dimensions(item["path"])
                    size_j = os.path.getsize(item["path"])
                    image_list_obj.append({"media_id": media_ids[j], "filesize": size_j, "width": w_j, "height": h_j, "tag": item["tag"]})
                elif item["type"] == "video":
                    size_j = os.path.getsize(item["path"])
                    video_list_obj.append({"media_id": media_ids[j], "tag": item["tag"], "filesize": size_j})
            form_data_apply["image_list"] = json.dumps(image_list_obj, separators=(",", ":"))
            form_data_apply["video_list"] = json.dumps(video_list_obj, separators=(",", ":"))
            form_data_apply["audio_spec"] = json.dumps({"mode": "native"}, separators=(",", ":"))
            form_data_apply["aspect_ratio"] = aspect_ratio
            form_data_apply["resolution"] = resolution
            if model_mode:
                form_data_apply["mode"] = model_mode
    else:
        form_data_apply["aspect_ratio"] = aspect_ratio
        form_data_apply["resolution"] = resolution
        if model_mode:
            form_data_apply["mode"] = model_mode

    files_apply = {k: (None, str(v)) for k, v in form_data_apply.items()}
    resp_apply = requests.patch(apply_url, files=files_apply, headers=apply_headers, timeout=60)
    resp_apply.raise_for_status()

    apply_json = resp_apply.json()
    task_id = apply_json.get("task_id")
    polling = apply_json.get("polling", {})
    delay = polling.get("delay", 5)

    max_attempts = 120
    decrypted_files = []
    for i in range(max_attempts):
        time.sleep(delay)
        req_ts_ms = int(time.time() * 1000)
        enc_token_hex = encrypt_myedit_aes_gcm_hex(aes_key, raw_session_token, req_ts_ms, s_id_int)
        poll_url = f"{MYEDIT_VGEN_URL}/{enc_token_hex}-{s_id_str}-{req_ts_ms}/{task_id}"

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
                task_info = f.get("task", {})
                # Her zaman ilk oturum anahtari (p_key) kullanilmalidir.
                enc_key = init_json.get("p_key")
                enc_iv = init_json.get("p_iv")

                if furl:
                    # If this is a thumbnail URL returned instead of the video, fetch the real mp4 URL
                    if "thumbnail" in furl.lower() and idx == 0:
                        try:
                            import re
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
                                    # Find the mp4 file in the consume files list
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
                                    rf_task = mp4_file.get("task", {})
                                    # Her zaman ilk oturum anahtari kullanilmaya devam edilmelidir.
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
            return {"status": "Failed", "error": poll_json, "reference_urls": uploaded_reference_urls}

    cleanup_temp_images()
    return {"status": "Timeout", "reference_urls": uploaded_reference_urls}

# ==============================================================================
# service.py LOGIC & WRAPPERS
# ==============================================================================

def create_myedit_account(api_key_id):
    """Creates a new MyEdit account dynamically on-the-fly.
    Uses SpamOK for temp mail. Saves account to database.
    """
    try:
        username, email = generate_random_spamok_email(12)
        password = "CyberLink123!"

        # 1. Signup
        signup_res = signup(email, password)
        if signup_res.get("status") != "OK":
            print(f"[-] Signup failed: {signup_res}")
            return None, None

        # 2. Get activation link
        activation_url = get_activation_link_from_spamok(username, timeout_seconds=45)
        if not activation_url:
            print("[-] Activation link not received.")
            return None, None

        # 3. Activate
        if not activate_account(activation_url):
            print("[-] Activation not verified, trying login anyway...")

        # 4. Login
        login_res = login(email, password)
        member_token = login_res.get("memberToken")
        if not member_token:
            print("[-] Login failed, no memberToken.")
            return None, None
        
        # 5. Collect all daily and task bonuses (174 credits total)
        try:
            collect_all_bonuses(member_token)
        except Exception as e:
            print(f"[!] Bonus collection failed: {e}")

        # Add account to database
        db.add_account(api_key_id, email, password)
        print(f"[+] Successfully registered and saved MyEdit account: {email}")

        return member_token, email
    except Exception as e:
        print(f"[-] Account creation exception: {e}")
        return None, None

def link_new_account_to_task(api_key_id, email, task_id):
    """Updates database to link and deduct quota."""
    conn = db.get_connection()
    cursor = conn.cursor()
    consumed_email = email
    try:
        # 1. Mark on-the-fly account as used
        if db.DB_TYPE == 'postgresql':
            cursor.execute(
                'UPDATE accounts SET used = 1 WHERE api_key_id = %s AND email = %s',
                (api_key_id, email)
            )
        else:
            cursor.execute(
                'UPDATE accounts SET used = 1 WHERE api_key_id = ? AND email = ?',
                (api_key_id, email)
            )

        # 2. Find a random unused account for this client
        if db.DB_TYPE == 'postgresql':
            cursor.execute(
                'SELECT email FROM accounts WHERE api_key_id = %s AND used = 0 AND email != %s',
                (api_key_id, email)
            )
        else:
            cursor.execute(
                'SELECT email FROM accounts WHERE api_key_id = ? AND used = 0 AND email != ?',
                (api_key_id, email)
            )

        rows = cursor.fetchall()
        if rows:
            emails = []
            for r in rows:
                if isinstance(r, dict):
                    emails.append(r['email'])
                elif hasattr(r, 'keys') or isinstance(r, tuple) or isinstance(r, list):
                    emails.append(r[0])
                else:
                    emails.append(r['email'])

            if emails:
                chosen_email = random.choice(emails)
                print(f"[QUOTA] Consuming random unused account: {chosen_email}")

                # 3. Mark the chosen random account as used = 1
                if db.DB_TYPE == 'postgresql':
                    cursor.execute(
                        'UPDATE accounts SET used = 1 WHERE api_key_id = %s AND email = %s',
                        (api_key_id, chosen_email)
                    )
                else:
                    cursor.execute(
                        'UPDATE accounts SET used = 1 WHERE api_key_id = ? AND email = ?',
                        (api_key_id, chosen_email)
                    )
                consumed_email = chosen_email

        # 4. Link the task to the consumed email
        if task_id:
            if db.DB_TYPE == 'postgresql':
                cursor.execute(
                    'UPDATE tasks SET account_email = %s WHERE task_id = %s',
                    (consumed_email, task_id)
                )
            else:
                cursor.execute(
                    'UPDATE tasks SET account_email = ? WHERE task_id = ?',
                    (consumed_email, task_id)
                )
        conn.commit()
    except Exception as e:
        print(f"Error linking account and consuming quota: {e}")
        conn.rollback()
    finally:
        conn.close()
    return consumed_email

def create_myedit_account_wrapper(api_key_id):
    """Wrapper function matching Yolly's naming structure."""
    return create_myedit_account(api_key_id)

def login_with_retry_and_link(api_key_id, task_id=None):
    """On-the-fly registration wrapper. Tries creating an account up to 5 times.
    Links the successful account to the task.
    """
    for _ in range(5):
        member_token, email = create_myedit_account_wrapper(api_key_id)
        if member_token and email:
            consumed_email = link_new_account_to_task(api_key_id, email, task_id)
            return member_token, {"email": consumed_email}
    return None, None

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

def process_image_task(task_id, params, api_key_id):
    temp_files = []
    try:
        db.update_task_status(task_id, 'running')

        member_token, account = login_with_retry_and_link(api_key_id, task_id)
        if not member_token:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, "Service temporarily unavailable.")
            return

        prompt = params.get('prompt', '')
        model = params.get('model', 'flux_2_pro')
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

        # Claim bonus before starting
        model_data = IMAGE_MODELS_CONFIG.get(model)
        if not model_data:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, f"Unsupported model: {model}")
            db.release_account(api_key_id, account['email'])
            return

        is_style_ref = model_data.get("effect_type") == "TtiStyleRef"
        feature_id = "TtiStyleRef" if is_style_ref else "TextToImage"
        claim_task_bonus(member_token, feature_id)
        check_task_bonus(member_token, feature_id)

        # Update database token
        token_data = json.dumps({
            "member_token": member_token,
            "reference_images": params.get('reference_images', []),
            "reference_videos": params.get('reference_videos', []),
            "start_frame": params.get('start_frame'),
            "end_frame": params.get('end_frame')
        })
        db.update_task_token(task_id, token_data)
        db.add_task_log(task_id, f"Task id: {task_id}")

        # Run generate_ai_image_service
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

        if result.get("status") == "Done":
            completed_files = result.get("files", [])
            dec_metadata = result.get("decryption_metadata")
            token_data_dict = {
                "member_token": member_token,
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
            else:
                db.update_task_status(task_id, 'failed')
                db.add_task_log(task_id, "No generated files returned.")
                db.release_account(api_key_id, account['email'])
        elif result.get("status") == "Timeout":
            db.update_task_status(task_id, 'timeout')
            db.release_account(api_key_id, account['email'])
        else:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, f"Submission error: {result.get('error', 'unknown error')}")
            db.release_account(api_key_id, account['email'])

    except Exception as e:
        db.update_task_status(task_id, 'error')
        db.add_task_log(task_id, str(e))
        if 'account' in locals() and account:
            db.release_account(api_key_id, account['email'])
    finally:
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

def process_video_task(task_id, params, api_key_id):
    temp_files = []
    try:
        db.update_task_status(task_id, 'running')

        member_token, account = login_with_retry_and_link(api_key_id, task_id)
        if not member_token:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, "Service temporarily unavailable.")
            return

        prompt = params.get('prompt', '')
        model = params.get('model', 'wan_2_7')
        aspect_ratio = params.get('size', '16:9')
        resolution = params.get('resolution', '720p')
        duration = int(params.get('duration', 5))
        sound = params.get('sound', 'vendor')

        input_mode = "TextToVideo"
        source_image_path = None
        last_image_path = None
        ref_images = []
        ref_videos = []
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

        # Handle reference images / videos
        images = params.get('reference_images', [])
        videos = params.get('reference_videos', [])
        if images or videos:
            input_mode = "ReferenceToVideo"
            for img_b64 in images:
                temp_img = save_b64_to_temp_file(img_b64)
                temp_files.append(temp_img)
                ref_images.append(temp_img)
            for vid_b64 in videos:
                temp_vid = save_b64_to_temp_file(vid_b64, suffix=".mp4")
                temp_files.append(temp_vid)
                ref_videos.append(temp_vid)

        # Claim bonus before starting
        model_data = VIDEO_MODELS_CONFIG.get(model)
        if not model_data:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, f"Unsupported model: {model}")
            db.release_account(api_key_id, account['email'])
            return

        claim_task_bonus(member_token, feature_id=input_mode)
        check_task_bonus(member_token, feature_id=input_mode)

        # Update database token
        token_data = json.dumps({
            "member_token": member_token,
            "reference_images": params.get('reference_images', []),
            "reference_videos": params.get('reference_videos', []),
            "start_frame": params.get('start_frame'),
            "end_frame": params.get('end_frame')
        })
        db.update_task_token(task_id, token_data)
        db.add_task_log(task_id, f"Task id: {task_id}")

        # Run generate_ai_video_service
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
            ref_images=ref_images if ref_images else None,
            ref_videos=ref_videos if ref_videos else None,
            frame_mode=frame_mode,
            filename_prefix=f"task_{task_id}",
            task_id=task_id
        )

        if result.get("status") == "Done":
            completed_files = result.get("files", [])
            video_file = next((f for f in completed_files if ".mp4" in f.lower() and "thumbnail" not in f.lower()), None)
            dec_metadata = result.get("decryption_metadata")
            token_data_dict = {
                "member_token": member_token,
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
            else:
                db.update_task_status(task_id, 'completed', completed_files[0] if completed_files else "")
        elif result.get("status") == "Timeout":
            db.update_task_status(task_id, 'timeout')
            db.release_account(api_key_id, account['email'])
        else:
            db.update_task_status(task_id, 'failed')
            db.add_task_log(task_id, f"Submission error: {result.get('error', 'unknown error')}")
            db.release_account(api_key_id, account['email'])

    except Exception as e:
        db.update_task_status(task_id, 'error')
        db.add_task_log(task_id, str(e))
        if 'account' in locals() and account:
            db.release_account(api_key_id, account['email'])
    finally:
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

def process_tts_task(task_id, params, api_key_id):
    db.update_task_status(task_id, 'failed')
    db.add_task_log(task_id, "TTS is not supported by this service.")

def process_music_task(task_id, params, api_key_id):
    db.update_task_status(task_id, 'failed')
    db.add_task_log(task_id, "Music is not supported by this service.")

def get_tts_voices(api_key_id):
    return [], "TTS not supported by this service"



def proxy_request(url, range_header=None):
    """Local or HTTP Proxy implementation for serving files."""
    import urllib.parse as urlparse
    import json
    import re
    import base64

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
        # Local file path
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

    # 2. Check if this is a private MyEdit S3 URL
    is_myedit_s3 = "cl-aol-media" in url or "cyberlink" in url
    
    if is_myedit_s3:
        import hashlib
        parsed_url = urlparse.urlparse(url)
        url_path = parsed_url.path  # e.g. /Vgen/results/Credit/59723825/thumbnail.jpg or /source/Tti/2zcmqgbdbdrks/input.1.jpg
        
        # Create MD5 hash of the url_path to serve as a unique, safe filename
        url_path_hash = hashlib.md5(url_path.encode('utf-8')).hexdigest()
        ext = ".mp4" if url_path.lower().endswith(".mp4") else ".jpg"
        
        cache_dir = "cache"
        if not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir)
            except Exception:
                pass
        
        local_cache_path = os.path.join(cache_dir, f"{url_path_hash}{ext}")

        # A. If already cached locally, stream it directly from disk (supports seek/range!)
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

        # B. If not cached, lookup task in database to fetch decryption keys / base64
        conn = db.get_connection()
        cursor = conn.cursor()
        task_row = None
        try:
            # We look for url_path in result_url or reference_image_urls
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

            # Case B1: Requesting a reference image/video from the database
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
                    
                    # Write decoded data to local cache
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

            # Case B2: Requesting a VGEN result video/image (requires decryption if keys are present)
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
                        # File is encrypted, decrypt it
                        aes_key = bytes.fromhex(dec_aes_key)
                        dec_enc_key = dec_enc_key.replace(" ", "+")
                        dec_enc_iv = dec_enc_iv.replace(" ", "+")

                        raw_key = decrypt_myedit_aes_gcm(aes_key, dec_enc_key, int(dec_ts_ms), 0)
                        raw_iv = decrypt_myedit_aes_gcm(aes_key, dec_enc_iv, int(dec_ts_ms), 0)
                        aesgcm = AESGCM(raw_key)
                        decrypted_bytes = aesgcm.decrypt(raw_iv, raw_bytes, None)
                    except Exception as dec_err:
                        print(f"Decryption failed (might be unencrypted thumbnail/media): {dec_err}")
                        decrypted_bytes = raw_bytes
                else:
                    # File is not encrypted, use raw bytes directly
                    decrypted_bytes = raw_bytes

                # Write to local cache
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

    # 3. Standard HTTP url streaming proxy (for other non-MyEdit URLs)
    fwd_headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    }
    if range_header:
        fwd_headers['Range'] = range_header
    r = requests.get(url, headers=fwd_headers, stream=True, timeout=(30, 120))
    excluded = {'content-encoding', 'transfer-encoding', 'connection'}
    resp_headers = [(k, v) for k, v in r.headers.items() if k.lower() not in excluded]
    return r.iter_content(chunk_size=8192), r.status_code, resp_headers

# --- Recovery Logic ---

def resume_incomplete_tasks():
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
