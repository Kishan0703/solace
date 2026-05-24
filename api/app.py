"""
念念 - AI思念亲人平台
数据库、用户、订阅与媒体管理版 API
"""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import mimetypes
import os
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
try:
    from api.presenz_mvp import register_presenz_mvp
except ModuleNotFoundError:
    from presenz_mvp import register_presenz_mvp

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _proactive_worker_started
    with _proactive_worker_lock:
        if not _proactive_worker_started:
            worker = threading.Thread(target=proactive_worker_loop, daemon=True, name="eterna-proactive-worker")
            worker.start()
            _proactive_worker_started = True
    yield


app = FastAPI(
    title="Eterna API",
    description="AI memory companion platform API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 配置 =====
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_FILE = BASE_DIR / "frontend" / "index.html"
ASSETS_DIR = BASE_DIR / "frontend" / "assets"


def load_runtime_settings() -> Dict[str, str]:
    settings: Dict[str, str] = {}
    candidates = [
        BASE_DIR / ".runtime-secrets.json",
        BASE_DIR / ".env",
        BASE_DIR / ".env.local",
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue

        if candidate.suffix == ".json":
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                settings.update({str(key): str(value) for key, value in raw.items() if value is not None})
            continue

        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            settings[key.strip()] = value.strip().strip("'\"")
    return settings


RUNTIME_SETTINGS = load_runtime_settings()


def runtime_config(name: str, default: str = "") -> str:
    return os.getenv(name) or str(RUNTIME_SETTINGS.get(name, default))


GEMINI_API_BASE = runtime_config("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_API_KEY = runtime_config("GEMINI_API_KEY", runtime_config("MIMO_API_KEY", ""))
GEMINI_MODEL = runtime_config("GEMINI_MODEL", "gemini-2.5-flash")
MIMO_TTS_VOICE = runtime_config("MIMO_TTS_VOICE", "default_zh")
ADMIN_EMAILS = runtime_config("ADMIN_EMAILS", "")
PUBLIC_BASE_URL = runtime_config("PUBLIC_BASE_URL", "").rstrip("/")
APP_BASE_URL = runtime_config("APP_BASE_URL", PUBLIC_BASE_URL or "http://localhost:8102").rstrip("/")
OUTBOUND_CALL_WEBHOOK_URL = runtime_config("OUTBOUND_CALL_WEBHOOK_URL", "")
OUTBOUND_CALL_WEBHOOK_TOKEN = runtime_config("OUTBOUND_CALL_WEBHOOK_TOKEN", "")
TWILIO_ACCOUNT_SID = runtime_config("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = runtime_config("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = runtime_config("TWILIO_FROM_NUMBER", "")
TWILIO_STATUS_CALLBACK_BASE_URL = runtime_config("TWILIO_STATUS_CALLBACK_BASE_URL", APP_BASE_URL).rstrip("/")
PHONE_CALL_MAX_TURNS = max(1, min(6, int(runtime_config("PHONE_CALL_MAX_TURNS", "3"))))
DEFAULT_DATA_DIR = Path("/tmp/memorial_data") if os.getenv("VERCEL") else BASE_DIR / "memorial_data"
DATA_DIR = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_AUDIO_DIR = DATA_DIR / "generated_audio"
GENERATED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_VIDEO_DIR = DATA_DIR / "generated_video"
GENERATED_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "eterna.db"
SESSION_TTL_DAYS = 30
DEFAULT_TIMEZONE = runtime_config("DEFAULT_TIMEZONE", "Asia/Shanghai")
PROACTIVE_POLL_SECONDS = max(20, int(runtime_config("PROACTIVE_POLL_SECONDS", "60")))
MIMO_VIDEO_MODEL = runtime_config("MIMO_VIDEO_MODEL", "mimo-v2-omni")
MIMO_VIDEO_MAX_SECONDS = max(6, min(30, int(runtime_config("MIMO_VIDEO_MAX_SECONDS", "18"))))
FFMPEG_BIN = shutil.which("ffmpeg") or ""

_proactive_worker_started = False
_proactive_worker_lock = threading.Lock()

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
app.mount("/media", StaticFiles(directory=DATA_DIR), name="media")

# ===== 套餐定义 =====
PLAN_CATALOG = [
    {
        "code": "trial",
        "name": "体验版",
        "description": "先建立第一位亲人的档案，体验基础文字陪伴与素材整理。",
        "price_cny": 0,
        "billing_period": "trial",
        "max_loved_ones": 1,
        "max_memories_per_loved_one": 40,
        "allows_text": 1,
        "allows_voice": 0,
        "allows_video": 0,
        "allows_voice_upload": 0,
        "allows_video_upload": 0,
        "highlighted": 0,
    },
    {
        "code": "seed",
        "name": "思念种子",
        "description": "保留文字与声音，让对话先变得像 ta。",
        "price_cny": 99,
        "billing_period": "year",
        "max_loved_ones": 1,
        "max_memories_per_loved_one": 300,
        "allows_text": 1,
        "allows_voice": 1,
        "allows_video": 0,
        "allows_voice_upload": 1,
        "allows_video_upload": 0,
        "highlighted": 0,
    },
    {
        "code": "tree",
        "name": "思念之树",
        "description": "扩展到多位亲人和更深的语音陪伴能力。",
        "price_cny": 299,
        "billing_period": "year",
        "max_loved_ones": 3,
        "max_memories_per_loved_one": 800,
        "allows_text": 1,
        "allows_voice": 1,
        "allows_video": 0,
        "allows_voice_upload": 1,
        "allows_video_upload": 0,
        "highlighted": 0,
    },
    {
        "code": "garden",
        "name": "思念花园",
        "description": "打开语音与视频陪伴，让分身更有在场感。",
        "price_cny": 599,
        "billing_period": "year",
        "max_loved_ones": 5,
        "max_memories_per_loved_one": 1500,
        "allows_text": 1,
        "allows_voice": 1,
        "allows_video": 1,
        "allows_voice_upload": 1,
        "allows_video_upload": 1,
        "highlighted": 1,
    },
    {
        "code": "family",
        "name": "思念家族",
        "description": "面向长期家族纪念的完整权限与更高容量。",
        "price_cny": 999,
        "billing_period": "year",
        "max_loved_ones": None,
        "max_memories_per_loved_one": 5000,
        "allows_text": 1,
        "allows_voice": 1,
        "allows_video": 1,
        "allows_voice_upload": 1,
        "allows_video_upload": 1,
        "highlighted": 0,
    },
]
PLAN_LOOKUP = {plan["code"]: plan for plan in PLAN_CATALOG}

# ===== 数据模型 =====


class LovedOne(BaseModel):
    id: Optional[str] = None
    name: str
    relationship: str
    birth_date: Optional[str] = None
    pass_away_date: Optional[str] = None
    cover_title: str = ""
    cover_photo_asset_id: Optional[str] = None
    cover_photo_url: Optional[str] = None
    personality_traits: Dict[str, Any] = Field(default_factory=dict)
    speaking_style: str = ""
    memories: List[str] = Field(default_factory=list)
    voice_sample_path: Optional[str] = None
    voice_sample_paths: List[str] = Field(default_factory=list)
    voice_sample_urls: List[str] = Field(default_factory=list)
    photo_paths: List[str] = Field(default_factory=list)
    photo_urls: List[str] = Field(default_factory=list)
    video_paths: List[str] = Field(default_factory=list)
    video_urls: List[str] = Field(default_factory=list)
    model3d_paths: List[str] = Field(default_factory=list)
    model3d_urls: List[str] = Field(default_factory=list)
    media_insights: Dict[str, List[dict]] = Field(default_factory=dict)
    identity_model_summary: str = ""
    digital_twin_profile: Dict[str, Any] = Field(default_factory=dict)
    digital_human_model: Dict[str, Any] = Field(default_factory=dict)
    proactive_profile: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ChatMessage(BaseModel):
    loved_one_id: str
    message: str
    emotion: Optional[str] = None
    mode: str = "text"
    intensity: Optional[int] = None


class ChatResponse(BaseModel):
    loved_one_id: str
    loved_one_name: str
    response_text: str
    response_audio_url: Optional[str] = None
    response_video_url: Optional[str] = None
    interaction_mode: str = "text"
    mode_note: Optional[str] = None
    available_modes: List[str] = Field(default_factory=list)
    emotion_detected: str
    memory_triggered: Optional[str] = None
    memory_refs: List[str] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    loved_one_id: str
    content: str
    memory_type: str
    date: Optional[str] = None
    importance: int = 5


class MediaTagsPayload(BaseModel):
    tags: List[str] = Field(default_factory=list)


class MediaStagePayload(BaseModel):
    stage: str


class LovedOneCoverPayload(BaseModel):
    cover_title: Optional[str] = None
    cover_photo_asset_id: Optional[str] = None


class GreetingSchedule(BaseModel):
    loved_one_id: str
    greeting_type: str
    trigger_date: str
    message_template: str


class ProactiveSettingsPayload(BaseModel):
    loved_one_id: str
    enabled: bool = True
    cadence: str = "daily"
    preferred_time: str = "20:30"
    preferred_weekday: Optional[int] = None
    preferred_channel: str = "app"
    preferred_message_mode: str = "voice"
    phone_number: str = ""
    timezone: str = DEFAULT_TIMEZONE


class RegisterPayload(BaseModel):
    email: str
    password: str
    display_name: str


class LoginPayload(BaseModel):
    email: str
    password: str


class DemoReferenceItem(BaseModel):
    name: str
    kind: str
    mime_type: str = ""
    extracted_text: str = ""
    notes: str = ""


class DemoChatTurn(BaseModel):
    role: str
    text: str


class DemoChatPayload(BaseModel):
    profile_name: str
    relationship: str = ""
    speaking_style: str = ""
    memories: List[str] = Field(default_factory=list)
    references: List[DemoReferenceItem] = Field(default_factory=list)
    history: List[DemoChatTurn] = Field(default_factory=list)
    message: str


class DemoChatResponse(BaseModel):
    response_text: str
    source: str
    context_used: Dict[str, Any] = Field(default_factory=dict)


class UserSummary(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: str


class SubscriptionSnapshot(BaseModel):
    plan_code: str
    plan_name: str
    status: str
    price_cny: int
    billing_period: str
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool = False
    features: Dict[str, bool] = Field(default_factory=dict)
    max_loved_ones: Optional[int] = None
    max_memories_per_loved_one: Optional[int] = None


class PlanView(BaseModel):
    code: str
    name: str
    description: str
    price_cny: int
    billing_period: str
    max_loved_ones: Optional[int] = None
    max_memories_per_loved_one: Optional[int] = None
    features: Dict[str, bool] = Field(default_factory=dict)
    highlighted: bool = False


class AuthEnvelope(BaseModel):
    token: Optional[str] = None
    user: UserSummary
    subscription: SubscriptionSnapshot
    stats: Dict[str, Any] = Field(default_factory=dict)


class MediaAssetView(BaseModel):
    id: str
    kind: str
    url: str
    original_filename: Optional[str] = None
    mime_type: str
    byte_size: int
    summary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_primary: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DigitalHumanFragmentView(BaseModel):
    id: str
    source_type: str
    source_id: Optional[str] = None
    modality: str
    fragment_kind: str
    title: str
    content: str
    weight: float = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DigitalHumanModelView(BaseModel):
    loved_one_id: str
    build_status: str = "pending"
    build_version: int = 1
    source_stats: Dict[str, Any] = Field(default_factory=dict)
    persona_profile: Dict[str, Any] = Field(default_factory=dict)
    relationship_profile: Dict[str, Any] = Field(default_factory=dict)
    voice_profile: Dict[str, Any] = Field(default_factory=dict)
    visual_profile: Dict[str, Any] = Field(default_factory=dict)
    behavior_profile: Dict[str, Any] = Field(default_factory=dict)
    timeline_profile: Dict[str, Any] = Field(default_factory=dict)
    build_notes: str = ""
    prompt_blueprint: str = ""
    knowledge_count: int = 0
    fragments_preview: List[DigitalHumanFragmentView] = Field(default_factory=list)
    last_built_at: Optional[str] = None
    updated_at: Optional[str] = None


# ===== 辅助函数 =====


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def unique_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    normalized = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def public_media_url(path_str: str) -> Optional[str]:
    if not path_str:
        return None
    path = Path(path_str)
    try:
        relative = path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return None
    return f"/media/{relative.as_posix()}"


def get_media_asset_reference(conn: sqlite3.Connection, asset_id: Optional[str]) -> Optional[dict]:
    if not asset_id:
        return None
    row = conn.execute(
        """
        SELECT id, kind, file_path, original_filename, mime_type
        FROM media_assets
        WHERE id = ?
        """,
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "path": row["file_path"],
        "url": public_media_url(row["file_path"]),
        "original_filename": row["original_filename"],
        "mime_type": row["mime_type"],
    }


def make_absolute_media_url(path_str: str, request: Optional[Request] = None) -> Optional[str]:
    relative = public_media_url(path_str)
    if not relative:
        return None
    base_url = PUBLIC_BASE_URL
    if not base_url and request is not None:
        base_url = str(request.base_url).rstrip("/")
    if not base_url:
        return None
    return f"{base_url}{relative}"


def is_publicly_reachable_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return not any(local in url for local in ["127.0.0.1", "localhost", "0.0.0.0"])


def infer_mime_type(path: Path, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or fallback


def encode_data_url(path: Path, fallback_mime: str, max_bytes: int = 10 * 1024 * 1024) -> Optional[str]:
    if not path.exists() or path.stat().st_size > max_bytes:
        return None
    mime_type = infer_mime_type(path, fallback_mime)
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def safe_upload_path(kind: str, loved_one_id: str, filename: Optional[str]) -> Path:
    ext = Path(filename or "").suffix or ""
    safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}{ext}"
    target_dir = DATA_DIR / kind / loved_one_id
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / safe_name


def cleanup_path(path_str: Optional[str]):
    if not path_str:
        return
    target = Path(path_str)
    if target.exists():
        target.unlink(missing_ok=True)
    current = target.parent
    while current != DATA_DIR and current.exists():
        if any(current.iterdir()):
            break
        current.rmdir()
        current = current.parent


def extract_reference_text(file_path: Path, mime_type: str) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".txt", ".md", ".json", ".csv", ".tsv", ".py", ".js", ".html", ".css"}:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return ""
    if suffix == ".docx":
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(file_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""
    if mime_type.startswith("text/"):
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return ""
    return ""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, iterations: int = 240_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt, digest = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    probe = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        int(raw_iterations),
    ).hex()
    return hmac.compare_digest(probe, digest)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return authorization.strip()


def gemini_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
    }


def serialize_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]) if "is_admin" in row.keys() else False,
        "created_at": row["created_at"],
    }


def admin_email_set() -> set[str]:
    return {normalize_email(item) for item in ADMIN_EMAILS.split(",") if item.strip()}


def is_admin_email(email: str) -> bool:
    if not ADMIN_EMAILS:
        return False
    return normalize_email(email) in admin_email_set()


def sync_admin_flag(conn: sqlite3.Connection, user_id: str, email: str) -> bool:
    should_be_admin = is_admin_email(email)
    conn.execute(
        "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
        (1 if should_be_admin else 0, now_iso(), user_id),
    )
    return should_be_admin


def require_admin(current_user: dict):
    if not current_user:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if current_user.get("is_admin") or is_admin_email(current_user.get("email", "")):
        return
    else:
        raise HTTPException(status_code=403, detail="需要管理员权限")


def build_plan_features(plan_row: dict) -> dict:
    return {
        "text": bool(plan_row["allows_text"]),
        "voice": bool(plan_row["allows_voice"]),
        "video": bool(plan_row["allows_video"]),
        "voice_upload": bool(plan_row["allows_voice_upload"]),
        "video_upload": bool(plan_row["allows_video_upload"]),
    }


def build_plan_view(plan_row: dict) -> dict:
    return {
        "code": plan_row["code"],
        "name": plan_row["name"],
        "description": plan_row["description"],
        "price_cny": plan_row["price_cny"],
        "billing_period": plan_row["billing_period"],
        "max_loved_ones": plan_row["max_loved_ones"],
        "max_memories_per_loved_one": plan_row["max_memories_per_loved_one"],
        "features": build_plan_features(plan_row),
        "highlighted": bool(plan_row["highlighted"]),
    }


# ===== 数据库 =====


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price_cny INTEGER NOT NULL,
                billing_period TEXT NOT NULL,
                max_loved_ones INTEGER,
                max_memories_per_loved_one INTEGER,
                allows_text INTEGER NOT NULL DEFAULT 1,
                allows_voice INTEGER NOT NULL DEFAULT 0,
                allows_video INTEGER NOT NULL DEFAULT 0,
                allows_voice_upload INTEGER NOT NULL DEFAULT 0,
                allows_video_upload INTEGER NOT NULL DEFAULT 0,
                highlighted INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan_code TEXT NOT NULL REFERENCES plans(code),
                status TEXT NOT NULL,
                current_period_end TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS loved_ones (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                relationship TEXT NOT NULL,
                birth_date TEXT,
                pass_away_date TEXT,
                cover_title TEXT NOT NULL DEFAULT '',
                cover_photo_asset_id TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
                personality_traits_json TEXT NOT NULL DEFAULT '{}',
                speaking_style TEXT NOT NULL DEFAULT '',
                identity_model_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                memory_date TEXT,
                importance INTEGER NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                file_path TEXT NOT NULL,
                original_filename TEXT,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                emotion TEXT,
                mode TEXT NOT NULL,
                response_audio_asset_id TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
                response_video_asset_id TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digital_human_models (
                loved_one_id TEXT PRIMARY KEY REFERENCES loved_ones(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                build_status TEXT NOT NULL DEFAULT 'pending',
                model_version INTEGER NOT NULL DEFAULT 1,
                source_stats_json TEXT NOT NULL DEFAULT '{}',
                persona_profile_json TEXT NOT NULL DEFAULT '{}',
                relationship_profile_json TEXT NOT NULL DEFAULT '{}',
                voice_profile_json TEXT NOT NULL DEFAULT '{}',
                visual_profile_json TEXT NOT NULL DEFAULT '{}',
                behavior_profile_json TEXT NOT NULL DEFAULT '{}',
                timeline_profile_json TEXT NOT NULL DEFAULT '{}',
                build_notes TEXT NOT NULL DEFAULT '',
                prompt_blueprint TEXT NOT NULL DEFAULT '',
                source_fingerprint TEXT NOT NULL DEFAULT '',
                last_built_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digital_human_fragments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                source_id TEXT,
                modality TEXT NOT NULL,
                fragment_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digital_human_build_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                source_counts_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS greetings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                greeting_type TEXT NOT NULL,
                trigger_date TEXT NOT NULL,
                message_template TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proactive_flows (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                enabled INTEGER NOT NULL DEFAULT 1,
                cadence TEXT NOT NULL DEFAULT 'daily',
                preferred_time TEXT NOT NULL DEFAULT '20:30',
                preferred_weekday INTEGER,
                preferred_channel TEXT NOT NULL DEFAULT 'app',
                preferred_message_mode TEXT NOT NULL DEFAULT 'voice',
                phone_number TEXT,
                timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                next_run_at TEXT,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, loved_one_id)
            );

            CREATE TABLE IF NOT EXISTS proactive_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                loved_one_id TEXT NOT NULL REFERENCES loved_ones(id) ON DELETE CASCADE,
                flow_id TEXT REFERENCES proactive_flows(id) ON DELETE SET NULL,
                source_kind TEXT NOT NULL DEFAULT 'flow',
                source_id TEXT,
                event_type TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                message_text TEXT NOT NULL,
                audio_asset_id TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
                video_asset_id TEXT REFERENCES media_assets(id) ON DELETE SET NULL,
                scheduled_for TEXT,
                delivered_at TEXT,
                consumed_at TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_memories_loved_one_id ON memories(loved_one_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_media_loved_one_id ON media_assets(loved_one_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chat_loved_one_id ON chat_messages(loved_one_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_digital_human_fragments_loved_one_id ON digital_human_fragments(loved_one_id, weight DESC, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_digital_human_build_runs_loved_one_id ON digital_human_build_runs(loved_one_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_proactive_flows_next_run ON proactive_flows(enabled, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_proactive_events_user_created ON proactive_events(user_id, created_at DESC);
            """
        )

        add_column_if_missing(conn, "users", "phone_number", "TEXT")
        add_column_if_missing(conn, "users", "proactive_opt_in", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(conn, "users", "preferred_contact_channel", "TEXT NOT NULL DEFAULT 'app'")
        add_column_if_missing(conn, "users", "preferred_contact_time", "TEXT NOT NULL DEFAULT '20:30'")
        add_column_if_missing(conn, "users", "timezone", f"TEXT NOT NULL DEFAULT '{DEFAULT_TIMEZONE}'")
        add_column_if_missing(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(conn, "proactive_flows", "preferred_message_mode", "TEXT NOT NULL DEFAULT 'voice'")
        add_column_if_missing(conn, "proactive_events", "video_asset_id", "TEXT")

        add_column_if_missing(conn, "loved_ones", "cover_title", "TEXT NOT NULL DEFAULT ''")
        add_column_if_missing(conn, "loved_ones", "cover_photo_asset_id", "TEXT")

        add_column_if_missing(conn, "media_assets", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
        add_column_if_missing(conn, "media_assets", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(conn, "media_assets", "is_primary", "INTEGER NOT NULL DEFAULT 0")

        for plan in PLAN_CATALOG:
            conn.execute(
                """
                INSERT INTO plans (
                    code, name, description, price_cny, billing_period, max_loved_ones,
                    max_memories_per_loved_one, allows_text, allows_voice, allows_video,
                    allows_voice_upload, allows_video_upload, highlighted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    price_cny = excluded.price_cny,
                    billing_period = excluded.billing_period,
                    max_loved_ones = excluded.max_loved_ones,
                    max_memories_per_loved_one = excluded.max_memories_per_loved_one,
                    allows_text = excluded.allows_text,
                    allows_voice = excluded.allows_voice,
                    allows_video = excluded.allows_video,
                    allows_voice_upload = excluded.allows_voice_upload,
                    allows_video_upload = excluded.allows_video_upload,
                    highlighted = excluded.highlighted
                """,
                (
                    plan["code"],
                    plan["name"],
                    plan["description"],
                    plan["price_cny"],
                    plan["billing_period"],
                    plan["max_loved_ones"],
                    plan["max_memories_per_loved_one"],
                    plan["allows_text"],
                    plan["allows_voice"],
                    plan["allows_video"],
                    plan["allows_voice_upload"],
                    plan["allows_video_upload"],
                    plan["highlighted"],
                ),
            )


def load_legacy_json(filename: str) -> dict:
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return {}
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def normalize_legacy_loved_one(record: dict) -> dict:
    normalized = dict(record)
    normalized.setdefault("personality_traits", {})
    normalized.setdefault("memories", [])
    normalized.setdefault("photo_paths", [])
    normalized.setdefault("video_paths", [])
    normalized.setdefault("model3d_paths", [])
    voice_paths = normalized.get("voice_sample_paths", []) or []
    primary_voice = normalized.get("voice_sample_path")
    if primary_voice and primary_voice not in voice_paths:
        voice_paths.insert(0, primary_voice)
    normalized["voice_sample_paths"] = unique_preserve_order(voice_paths)
    normalized["photo_paths"] = unique_preserve_order(normalized.get("photo_paths", []))
    normalized["video_paths"] = unique_preserve_order(normalized.get("video_paths", []))
    normalized["model3d_paths"] = unique_preserve_order(normalized.get("model3d_paths", []))
    return normalized


def import_legacy_json_data(conn: sqlite3.Connection, user_id: str):
    total_loved_ones = conn.execute("SELECT COUNT(*) FROM loved_ones").fetchone()[0]
    if total_loved_ones:
        return

    legacy_loved_ones = load_legacy_json("loved_ones.json")
    if not legacy_loved_ones:
        return

    legacy_memories = load_legacy_json("memories.json")
    legacy_chat_history = load_legacy_json("chat_history.json")
    legacy_greetings = load_legacy_json("greetings.json")
    created_at = now_iso()

    for raw_id, raw_record in legacy_loved_ones.items():
        loved_one_id = str(raw_record.get("id") or raw_id or uuid.uuid4())
        record = normalize_legacy_loved_one(raw_record)
        conn.execute(
            """
            INSERT INTO loved_ones (
                id, user_id, name, relationship, birth_date, pass_away_date,
                personality_traits_json, speaking_style, identity_model_summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                loved_one_id,
                user_id,
                record.get("name", "未命名亲人"),
                record.get("relationship", "亲人"),
                record.get("birth_date"),
                record.get("pass_away_date"),
                json.dumps(record.get("personality_traits") or {}, ensure_ascii=False),
                record.get("speaking_style", ""),
                record.get("identity_model_summary", ""),
                created_at,
                created_at,
            ),
        )

        insight_map = record.get("media_insights") or {}
        for kind, paths in (
            ("voice", record.get("voice_sample_paths", [])),
            ("photo", record.get("photo_paths", [])),
            ("video", record.get("video_paths", [])),
            ("model3d", record.get("model3d_paths", [])),
        ):
            summaries = {item.get("path"): item.get("summary") for item in insight_map.get(kind, []) if item.get("path")}
            for path_str in paths:
                target = Path(path_str)
                conn.execute(
                    """
                    INSERT INTO media_assets (
                        id, user_id, loved_one_id, kind, file_path, original_filename,
                        mime_type, byte_size, summary, tags_json, metadata_json, is_primary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        user_id,
                        loved_one_id,
                        kind,
                        str(target),
                        target.name,
                        infer_mime_type(target, "application/octet-stream"),
                        target.stat().st_size if target.exists() else 0,
                        summaries.get(path_str),
                        "[]",
                        json.dumps({"stage": "uploaded"} if kind == "model3d" else {}, ensure_ascii=False),
                        0,
                        created_at,
                    ),
                )

        memory_bucket = legacy_memories.get(loved_one_id, [])
        if not memory_bucket:
            memory_bucket = [
                {
                    "id": str(uuid.uuid4()),
                    "content": content,
                    "memory_type": "conversation",
                    "importance": 7,
                    "created_at": created_at,
                }
                for content in record.get("memories", [])
            ]

        for memory in memory_bucket:
            conn.execute(
                """
                INSERT INTO memories (
                    id, user_id, loved_one_id, content, memory_type, memory_date, importance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(memory.get("id") or uuid.uuid4()),
                    user_id,
                    loved_one_id,
                    memory.get("content", ""),
                    memory.get("memory_type", "conversation"),
                    memory.get("date"),
                    int(memory.get("importance", 5)),
                    memory.get("created_at", created_at),
                ),
            )

        for item in legacy_chat_history.get(loved_one_id, []):
            conn.execute(
                """
                INSERT INTO chat_messages (
                    id, user_id, loved_one_id, user_message, ai_response, emotion, mode, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    user_id,
                    loved_one_id,
                    item.get("user_message", ""),
                    item.get("ai_response", ""),
                    item.get("emotion"),
                    item.get("mode", "text"),
                    item.get("timestamp", created_at),
                ),
            )

    for greeting_id, item in legacy_greetings.items():
        loved_one_id = item.get("loved_one_id")
        if not loved_one_id:
            continue
        conn.execute(
            """
            INSERT INTO greetings (
                id, user_id, loved_one_id, greeting_type, trigger_date, message_template, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                greeting_id,
                user_id,
                loved_one_id,
                item.get("greeting_type", "daily"),
                item.get("trigger_date", created_at),
                item.get("message_template", ""),
                item.get("status", "scheduled"),
                item.get("created_at", created_at),
            ),
        )


init_db()


# ===== 认证与订阅 =====


def create_trial_subscription(conn: sqlite3.Connection, user_id: str):
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO subscriptions (
            id, user_id, plan_code, status, current_period_end, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            "trial",
            "trialing",
            (now_utc() + timedelta(days=30)).isoformat(),
            created_at,
            created_at,
        ),
    )


def create_session(conn: sqlite3.Connection, user_id: str) -> dict:
    token = secrets.token_urlsafe(32)
    created_at = now_iso()
    expires_at = (now_utc() + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    conn.execute(
        """
        INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            hash_token(token),
            expires_at,
            created_at,
            created_at,
        ),
    )
    return {"token": token, "expires_at": expires_at}


def get_optional_user_from_authorization(authorization: Optional[str]) -> Optional[dict]:
    token = extract_bearer_token(authorization)
    if not token:
        return None

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT u.*, s.id AS session_id, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (hash_token(token),),
        ).fetchone()
        if row is None:
            return None

        expires_at = parse_iso(row["expires_at"])
        if expires_at is None or expires_at <= now_utc():
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
            return None

        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
            (now_iso(), row["session_id"]),
        )
        return row_to_dict(row)


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    user = get_optional_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def get_plan_row(conn: sqlite3.Connection, plan_code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM plans WHERE code = ?", (plan_code,)).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="套餐不存在")
    return row


def get_subscription_snapshot(conn: sqlite3.Connection, user_id: str) -> dict:
    row = conn.execute(
        """
        SELECT s.*, p.*
        FROM subscriptions s
        JOIN plans p ON p.code = s.plan_code
        WHERE s.user_id = ?
        ORDER BY
            CASE
                WHEN s.status IN ('active', 'trialing', 'past_due') THEN 0
                ELSE 1
            END,
            s.updated_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    if row is None:
        plan_row = get_plan_row(conn, "trial")
        return {
            "plan_code": plan_row["code"],
            "plan_name": plan_row["name"],
            "status": "trialing",
            "price_cny": plan_row["price_cny"],
            "billing_period": plan_row["billing_period"],
            "current_period_end": None,
            "cancel_at_period_end": False,
            "features": build_plan_features(row_to_dict(plan_row)),
            "max_loved_ones": plan_row["max_loved_ones"],
            "max_memories_per_loved_one": plan_row["max_memories_per_loved_one"],
        }

    row_dict = row_to_dict(row)
    features = build_plan_features(row_dict)
    return {
        "plan_code": row_dict["plan_code"],
        "plan_name": row_dict["name"],
        "status": row_dict["status"],
        "price_cny": row_dict["price_cny"],
        "billing_period": row_dict["billing_period"],
        "current_period_end": row_dict["current_period_end"],
        "cancel_at_period_end": bool(row_dict["cancel_at_period_end"]),
        "features": features,
        "max_loved_ones": row_dict["max_loved_ones"],
        "max_memories_per_loved_one": row_dict["max_memories_per_loved_one"],
    }


def assert_plan_capability(subscription: dict, feature: str, detail: str):
    if subscription["features"].get(feature):
        return
    raise HTTPException(status_code=403, detail=detail)


def assert_loved_one_limit(conn: sqlite3.Connection, user_id: str, subscription: dict):
    max_loved_ones = subscription.get("max_loved_ones")
    if max_loved_ones is None:
        return
    current_count = conn.execute(
        "SELECT COUNT(*) FROM loved_ones WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    if current_count >= max_loved_ones:
        raise HTTPException(
            status_code=403,
            detail=f"当前套餐最多可保存 {max_loved_ones} 位亲人，请先升级套餐。",
        )


def assert_memory_limit(conn: sqlite3.Connection, user_id: str, loved_one_id: str, subscription: dict):
    max_memories = subscription.get("max_memories_per_loved_one")
    if max_memories is None:
        return
    current_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE user_id = ? AND loved_one_id = ?",
        (user_id, loved_one_id),
    ).fetchone()[0]
    if current_count >= max_memories:
        raise HTTPException(
            status_code=403,
            detail=f"当前套餐每位亲人最多可保存 {max_memories} 条回忆，请先升级套餐。",
        )


def ensure_loved_one_owner(conn: sqlite3.Connection, user_id: str, loved_one_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM loved_ones WHERE id = ? AND user_id = ?",
        (loved_one_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="亲人档案未找到")
    return row


def get_user_stats(conn: sqlite3.Connection, user_id: str) -> dict:
    total_loved_ones = conn.execute(
        "SELECT COUNT(*) FROM loved_ones WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    total_memories = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    total_messages = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    total_assets = conn.execute(
        "SELECT COUNT(*) FROM media_assets WHERE user_id = ? AND kind IN ('voice', 'photo', 'video', 'model3d')",
        (user_id,),
    ).fetchone()[0]
    subscription = get_subscription_snapshot(conn, user_id)
    return {
        "total_loved_ones": total_loved_ones,
        "total_memories": total_memories,
        "total_messages": total_messages,
        "total_assets": total_assets,
        "active_families": 1 if total_loved_ones else 0,
        "subscription": subscription,
    }


# ===== 媒体 / 分身序列化 =====


def fetch_media_rows(
    conn: sqlite3.Connection,
    loved_one_id: str,
    *,
    kinds: Optional[List[str]] = None,
    include_generated: bool = False,
) -> List[sqlite3.Row]:
    if kinds:
        placeholders = ",".join(["?"] * len(kinds))
        query = f"""
            SELECT * FROM media_assets
            WHERE loved_one_id = ? AND kind IN ({placeholders})
            ORDER BY is_primary DESC, created_at DESC
        """
        params: List[Any] = [loved_one_id, *kinds]
    else:
        if include_generated:
            query = "SELECT * FROM media_assets WHERE loved_one_id = ? ORDER BY is_primary DESC, created_at DESC"
            params = [loved_one_id]
        else:
            query = """
                SELECT * FROM media_assets
                WHERE loved_one_id = ? AND kind IN ('voice', 'photo', 'video', 'model3d')
                ORDER BY is_primary DESC, created_at DESC
            """
            params = [loved_one_id]
    return conn.execute(query, params).fetchall()


def build_available_modes(
    voice_count: int,
    photo_count: int,
    video_count: int,
    subscription: Optional[dict] = None,
) -> List[str]:
    features = (subscription or {}).get("features", {"text": True, "voice": True, "video": True})
    modes = ["text"] if features.get("text", True) else []
    if voice_count > 0 and features.get("voice"):
        modes.append("voice")
    if voice_count > 0 and (photo_count > 0 or video_count > 0) and features.get("video"):
        modes.append("video")
    return modes or ["text"]


def build_twin_workflow(
    *,
    memory_count: int,
    voice_count: int,
    photo_count: int,
    video_count: int,
    signal_count: int,
    available_modes: List[str],
    subscription: Optional[dict] = None,
) -> dict:
    features = (subscription or {}).get(
        "features",
        {"text": True, "voice": True, "video": True, "voice_upload": True, "video_upload": True},
    )

    def make_step(
        *,
        code: str,
        title: str,
        current: int,
        target: int,
        pending_detail: str,
        active_detail: str,
        completed_detail: str,
        locked: bool = False,
        locked_detail: Optional[str] = None,
    ) -> dict:
        safe_target = max(target, 1)
        if locked:
            return {
                "code": code,
                "title": title,
                "status": "locked",
                "current": current,
                "target": target,
                "progress_percent": 0,
                "detail": locked_detail or pending_detail,
            }
        if current >= safe_target:
            status = "completed"
            detail = completed_detail
            progress = 100
        elif current > 0:
            status = "active"
            detail = active_detail
            progress = round(current / safe_target * 100)
        else:
            status = "pending"
            detail = pending_detail
            progress = 0
        return {
            "code": code,
            "title": title,
            "status": status,
            "current": current,
            "target": target,
            "progress_percent": progress,
            "detail": detail,
        }

    steps = [
        make_step(
            code="identity_seed",
            title="人格底稿",
            current=signal_count,
            target=4,
            pending_detail="先补全名字、关系、说话方式和一两个性格线索，让系统先知道 ta 是谁。",
            active_detail="人格底稿已经开始成形，再补一句口头禅或更具体的说话方式会更像 ta。",
            completed_detail="名字、关系、性格和说话方式已经具备，分身的人格底稿已可用。",
        ),
        make_step(
            code="memory_grounding",
            title="共同记忆校准",
            current=memory_count,
            target=6,
            pending_detail="先写下几段只属于你们的日常细节，让回应不只是泛泛安慰。",
            active_detail="系统已经抓住一部分共同经历，再补几条生活记忆会更像真实亲人。",
            completed_detail="共同记忆已经足够支撑长期对话，分身开始拥有稳定的生活语境。",
        ),
        make_step(
            code="voice_calibration",
            title="声音校准",
            current=voice_count,
            target=2,
            pending_detail="上传至少一段清晰语音，让声音和说话节奏开始被还原。",
            active_detail="声音特征已经在校准中，再补一段不同场景的语音会更自然。",
            completed_detail="声音样本已经足够，语气、语速和情绪节奏开始稳定下来。",
            locked=not features.get("voice_upload", False),
            locked_detail="当前套餐还没有开放语音建模上传，先用文字和照片推进人格轮廓。",
        ),
        make_step(
            code="visual_restoration",
            title="面容与神态还原",
            current=photo_count + video_count,
            target=3,
            pending_detail="先上传照片，分身才会逐渐具备熟悉的面容和气质。",
            active_detail="面容与神态正在恢复中，再补充照片或视频会更接近 ta 的在场感。",
            completed_detail="照片和影像素材已经具备，分身开始拥有更稳定的视觉识别感。",
        ),
        make_step(
            code="presence_activation",
            title="多模态陪伴激活",
            current=len(available_modes),
            target=1 + int(features.get("voice", False)) + int(features.get("video", False)),
            pending_detail="先把人格底稿和记忆打牢，系统才会逐步解锁更像真人的陪伴模式。",
            active_detail="系统已经解锁了一部分陪伴形态，继续补素材会把文字推进到语音或视频。",
            completed_detail="当前套餐对应的陪伴模式已经全部激活，这个数字家人可以持续被维护和陪伴。",
        ),
    ]

    actionable_steps = [step for step in steps if step["status"] in {"active", "pending"}]
    current_stage = actionable_steps[0] if actionable_steps else steps[-1]
    completed_titles = [step["title"] for step in steps if step["status"] == "completed"]

    next_actions: List[dict] = []
    if signal_count < 4:
        next_actions.append(
            {
                "type": "identity",
                "title": "补一句最像 ta 的说话方式",
                "detail": "比如口头禅、称呼习惯、说话快慢，这会决定分身最基本的表达质感。",
                "cta_label": "完善档案",
            }
        )
    if memory_count < 6:
        next_actions.append(
            {
                "type": "memory",
                "title": f"再补 {max(1, min(3, 6 - memory_count))} 条共同回忆",
                "detail": "优先写具体场景、常做的事和 ta 会怎么回应你，让对话开始带有生活细节。",
                "cta_label": "添加回忆",
            }
        )
    if voice_count < 2:
        next_actions.append(
            {
                "type": "voice" if features.get("voice_upload", False) else "upgrade",
                "title": "补充语音样本" if features.get("voice_upload", False) else "当前套餐未开放语音建模",
                "detail": "至少两段清晰语音最容易还原语气和停顿。" if features.get("voice_upload", False) else "先用文字和照片维护分身，后续升级套餐即可接入语音电话。",
                "cta_label": "上传语音" if features.get("voice_upload", False) else "查看套餐",
            }
        )
    if photo_count < 2:
        next_actions.append(
            {
                "type": "photo",
                "title": f"再补 {max(1, 2 - photo_count)} 张代表性照片",
                "detail": "优先选正脸、日常笑容和熟悉场景的照片，能更快稳定面容气质。",
                "cta_label": "上传照片",
            }
        )
    if video_count < 1:
        next_actions.append(
            {
                "type": "video" if features.get("video_upload", False) else "upgrade",
                "title": "补一段视频素材" if features.get("video_upload", False) else "视频陪伴仍未开放",
                "detail": "一段家庭录像就能显著增强神态、动作节奏和在场感。" if features.get("video_upload", False) else "当前套餐先把声音和照片养完整，升级后就能继续视频陪伴。",
                "cta_label": "上传视频" if features.get("video_upload", False) else "查看套餐",
            }
        )

    if not next_actions:
        next_actions.append(
            {
                "type": "chat",
                "title": "开始和 ta 长期对话",
                "detail": "现在最有价值的是持续补充新的日常，分身会随着对话和回忆继续变得更像 ta。",
                "cta_label": "开始陪伴",
            }
        )

    if completed_titles:
        workflow_summary = f"系统已自动完成「{'、'.join(completed_titles[:2])}」等阶段，当前正在推进「{current_stage['title']}」。"
    else:
        workflow_summary = f"系统正在从「{current_stage['title']}」开始自动搭建这个数字家人的业务流。"

    return {
        "current_stage_code": current_stage["code"],
        "current_stage_title": current_stage["title"],
        "workflow_steps": steps,
        "next_actions": next_actions[:3],
        "workflow_summary": workflow_summary,
        "recommended_focus": next_actions[0]["title"] if next_actions else current_stage["title"],
        "model_readiness": {
            "text_ready": memory_count > 0 and signal_count >= 2,
            "voice_ready": "voice" in available_modes,
            "video_ready": "video" in available_modes,
        },
    }


def build_digital_twin_profile(
    *,
    memory_count: int,
    voice_count: int,
    photo_count: int,
    video_count: int,
    model3d_count: int = 0,
    signal_count: int,
    subscription: Optional[dict] = None,
) -> dict:
    available_modes = build_available_modes(voice_count, photo_count, video_count, subscription=subscription)
    visual_depth = photo_count + video_count + model3d_count
    coverage = sum(1 for count in [memory_count, voice_count, visual_depth] if count > 0)
    coverage_score = coverage / 3 * 0.55
    depth_score = (
        min(memory_count, 8) / 8 * 0.15
        + min(voice_count, 3) / 3 * 0.12
        + min(photo_count, 4) / 4 * 0.08
        + min(video_count, 2) / 2 * 0.06
        + min(model3d_count, 2) / 2 * 0.05
        + signal_count / 4 * 0.04
    )
    completion_percent = round((coverage_score + depth_score) * 100)

    if coverage == 0:
        label = "待补充分身素材"
        summary = "先留下几段文字记忆，再补充语音、照片和视频，数字分身才会逐渐像 ta。"
    elif completion_percent < 55:
        label = "分身轮廓已开始成形"
        summary = "已经留住了一部分辨识度，继续补充回忆、声音和影像，这个分身会更接近 ta。"
    elif completion_percent < 82:
        label = "立体分身正在成形"
        summary = "文字记忆、声音和影像已经开始互相校准，数字分身会更接近 ta 的真实感觉。"
    else:
        label = "完整数字分身已就绪"
        summary = "文字、声音、照片和动态影像都已具备，这个数字分身已经有了更完整的在场感。"

    workflow = build_twin_workflow(
        memory_count=memory_count,
        voice_count=voice_count,
        photo_count=photo_count,
        video_count=video_count + model3d_count,
        signal_count=signal_count,
        available_modes=available_modes,
        subscription=subscription,
    )

    return {
        "memory_count": memory_count,
        "voice_count": voice_count,
        "photo_count": photo_count,
        "video_count": video_count,
        "model3d_count": model3d_count,
        "has_memory": memory_count > 0,
        "has_voice": voice_count > 0,
        "has_photo": photo_count > 0,
        "has_video": video_count > 0,
        "has_model3d": model3d_count > 0,
        "coverage": coverage,
        "completion_percent": completion_percent,
        "completeness_label": label,
        "summary": summary,
        "available_modes": available_modes,
        **workflow,
    }


def build_media_insights(media_rows: List[sqlite3.Row]) -> dict:
    grouped = {"voice": [], "photo": [], "video": [], "model3d": []}
    for row in media_rows:
        kind = row["kind"]
        if kind not in grouped:
            continue
        grouped[kind].append(
            {
                "path": row["file_path"],
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
        )
    return grouped


def json_object(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_list(value: Optional[str]) -> List[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


MODEL3D_STAGE_ORDER = ["uploaded", "aligned", "textured", "rigged", "ready", "integrated"]
MODEL3D_STAGE_LABELS = {
    "uploaded": "已上传",
    "aligned": "已对齐",
    "textured": "已贴图",
    "rigged": "已骨骼",
    "ready": "已可用",
    "integrated": "已并入数字人模型",
}


def derive_model3d_stage(media_rows: List[sqlite3.Row]) -> dict:
    stages = []
    for row in media_rows:
        if row["kind"] != "model3d":
            continue
        metadata = json_object(row["metadata_json"]) if "metadata_json" in row.keys() else {}
        stage = str(metadata.get("stage") or "").strip()
        if stage:
            stages.append(stage)
    if not stages:
        return {"stage": "missing", "label": "未上传"}
    ranked = max(
        stages,
        key=lambda value: MODEL3D_STAGE_ORDER.index(value)
        if value in MODEL3D_STAGE_ORDER
        else -1,
    )
    return {
        "stage": ranked,
        "label": MODEL3D_STAGE_LABELS.get(ranked, ranked),
    }


def normalize_fragment_text(value: Optional[str], limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def build_digital_human_source_fingerprint(
    loved_one_row: sqlite3.Row,
    memory_rows: List[sqlite3.Row],
    media_rows: List[sqlite3.Row],
) -> str:
    payload = {
        "name": loved_one_row["name"],
        "relationship": loved_one_row["relationship"],
        "birth_date": loved_one_row["birth_date"],
        "pass_away_date": loved_one_row["pass_away_date"],
        "speaking_style": loved_one_row["speaking_style"],
        "traits": json_object(loved_one_row["personality_traits_json"]),
        "memories": [
            {
                "id": row["id"],
                "content": row["content"],
                "memory_type": row["memory_type"],
                "memory_date": row["memory_date"],
                "importance": row["importance"],
                "created_at": row["created_at"],
            }
            for row in memory_rows
        ],
        "media": [
            {
                "id": row["id"],
                "kind": row["kind"],
                "summary": row["summary"],
                "original_filename": row["original_filename"],
                "byte_size": row["byte_size"],
                "created_at": row["created_at"],
            }
            for row in media_rows
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def make_digital_fragment(
    *,
    user_id: str,
    loved_one_id: str,
    source_type: str,
    source_id: Optional[str],
    modality: str,
    fragment_kind: str,
    title: str,
    content: str,
    weight: float,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    timestamp = created_at or now_iso()
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "loved_one_id": loved_one_id,
        "source_type": source_type,
        "source_id": source_id,
        "modality": modality,
        "fragment_kind": fragment_kind,
        "title": title,
        "content": normalize_fragment_text(content),
        "weight": round(weight, 2),
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_profile_fragments(
    loved_one_row: sqlite3.Row,
    traits: Dict[str, Any],
) -> List[Dict[str, Any]]:
    user_id = loved_one_row["user_id"]
    loved_one_id = loved_one_row["id"]
    created_at = loved_one_row["updated_at"] or loved_one_row["created_at"] or now_iso()
    fragments = [
        make_digital_fragment(
            user_id=user_id,
            loved_one_id=loved_one_id,
            source_type="profile",
            source_id=loved_one_id,
            modality="profile",
            fragment_kind="identity_core",
            title="身份关系",
            content=f"{loved_one_row['name']}是用户的{loved_one_row['relationship']}。",
            weight=5.0,
            metadata={"field": "relationship"},
            created_at=created_at,
        )
    ]
    if loved_one_row["speaking_style"]:
        fragments.append(
            make_digital_fragment(
                user_id=user_id,
                loved_one_id=loved_one_id,
                source_type="profile",
                source_id=loved_one_id,
                modality="profile",
                fragment_kind="speaking_style",
                title="说话方式",
                content=f"ta的表达方式偏向：{loved_one_row['speaking_style']}。",
                weight=4.6,
                metadata={"field": "speaking_style"},
                created_at=created_at,
            )
        )
    catchphrase = str(traits.get("catchphrase", "")).strip()
    if catchphrase:
        fragments.append(
            make_digital_fragment(
                user_id=user_id,
                loved_one_id=loved_one_id,
                source_type="profile",
                source_id=loved_one_id,
                modality="text",
                fragment_kind="trait_signal",
                title="口头禅",
                content=f"ta常会这样说：“{catchphrase}”",
                weight=4.8,
                metadata={"field": "catchphrase"},
                created_at=created_at,
            )
        )
    for key, value in list(traits.items())[:6]:
        if key == "catchphrase" or not value:
            continue
        fragments.append(
            make_digital_fragment(
                user_id=user_id,
                loved_one_id=loved_one_id,
                source_type="profile",
                source_id=loved_one_id,
                modality="profile",
                fragment_kind="trait_signal",
                title=f"性格线索 · {key}",
                content=f"{key}：{value}",
                weight=4.2,
                metadata={"field": key},
                created_at=created_at,
            )
        )
    return fragments


def build_memory_fragments(memory_rows: List[sqlite3.Row], user_id: str, loved_one_id: str) -> List[Dict[str, Any]]:
    fragments: List[Dict[str, Any]] = []
    for index, row in enumerate(memory_rows):
        freshness_bonus = max(0.0, 0.45 - index * 0.04)
        weight = min(5.0, (float(row["importance"] or 5) / 2.0) + freshness_bonus)
        fragments.append(
            make_digital_fragment(
                user_id=user_id,
                loved_one_id=loved_one_id,
                source_type="memory",
                source_id=row["id"],
                modality="text",
                fragment_kind="memory_anchor",
                title=f"{row['memory_type'] or 'memory'} 记忆",
                content=row["content"],
                weight=weight,
                metadata={
                    "memory_type": row["memory_type"],
                    "memory_date": row["memory_date"],
                    "importance": row["importance"],
                },
                created_at=row["created_at"],
            )
        )
    return fragments


def build_media_fragments(media_rows: List[sqlite3.Row], user_id: str, loved_one_id: str) -> List[Dict[str, Any]]:
    fragments: List[Dict[str, Any]] = []
    kind_meta = {
        "voice": ("audio", "voice_trait", "语音样本", 4.4, "已上传语音样本，等待进一步提炼声音特征。"),
        "photo": ("image", "visual_trait", "照片素材", 3.8, "已上传照片素材，等待进一步提炼面容和气质。"),
        "video": ("video", "motion_trait", "视频素材", 4.1, "已上传视频素材，等待进一步提炼神态和动作节奏。"),
        "model3d": ("spatial", "reconstruction_trait", "3D 重建", 4.5, "已上传真人 3D 重建素材，等待进一步提炼立体外观与空间形态。"),
    }
    for index, row in enumerate(media_rows):
        if row["kind"] not in kind_meta:
            continue
        modality, fragment_kind, title_prefix, base_weight, fallback = kind_meta[row["kind"]]
        recency_bonus = max(0.0, 0.35 - index * 0.03)
        content = row["summary"] or fallback
        fragments.append(
            make_digital_fragment(
                user_id=user_id,
                loved_one_id=loved_one_id,
                source_type=row["kind"],
                source_id=row["id"],
                modality=modality,
                fragment_kind=fragment_kind,
                title=f"{title_prefix} · {row['original_filename'] or Path(row['file_path']).name}",
                content=content,
                weight=min(5.0, base_weight + recency_bonus),
                metadata={
                    "mime_type": row["mime_type"],
                    "original_filename": row["original_filename"],
                    "byte_size": row["byte_size"],
                },
                created_at=row["created_at"],
            )
        )
    return fragments


def filter_fragment_contents(
    fragments: List[Dict[str, Any]],
    *,
    source_types: Optional[set] = None,
    fragment_kinds: Optional[set] = None,
    limit: int = 4,
) -> List[str]:
    values: List[str] = []
    for fragment in sorted(fragments, key=lambda item: (-item["weight"], item["created_at"])):
        if source_types and fragment["source_type"] not in source_types:
            continue
        if fragment_kinds and fragment["fragment_kind"] not in fragment_kinds:
            continue
        if fragment["content"]:
            values.append(fragment["content"])
    return unique_preserve_order(values)[:limit]


def compose_digital_human_model_payload(
    loved_one_row: sqlite3.Row,
    memory_rows: List[sqlite3.Row],
    media_rows: List[sqlite3.Row],
    twin_profile: Dict[str, Any],
    fragments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    traits = json_object(loved_one_row["personality_traits_json"])
    identity_bits = filter_fragment_contents(
        fragments,
        source_types={"profile"},
        fragment_kinds={"identity_core", "trait_signal", "speaking_style"},
        limit=5,
    )
    memory_bits = filter_fragment_contents(
        fragments,
        source_types={"memory"},
        fragment_kinds={"memory_anchor"},
        limit=6,
    )
    voice_bits = filter_fragment_contents(
        fragments,
        fragment_kinds={"voice_trait", "speaking_style"},
        limit=4,
    )
    visual_bits = filter_fragment_contents(
        fragments,
        fragment_kinds={"visual_trait", "motion_trait", "reconstruction_trait"},
        limit=5,
    )
    latest_material_at = max(
        [row["created_at"] for row in memory_rows] + [row["created_at"] for row in media_rows] + [loved_one_row["updated_at"]],
        default=loved_one_row["updated_at"],
    )

    source_stats = {
        "memory_count": len(memory_rows),
        "voice_count": sum(1 for row in media_rows if row["kind"] == "voice"),
        "photo_count": sum(1 for row in media_rows if row["kind"] == "photo"),
        "video_count": sum(1 for row in media_rows if row["kind"] == "video"),
        "model3d_count": sum(1 for row in media_rows if row["kind"] == "model3d"),
        "fragment_count": len(fragments),
        "available_modes": twin_profile.get("available_modes", ["text"]),
        "completion_percent": twin_profile.get("completion_percent", 0),
    }
    model3d_stage = derive_model3d_stage(media_rows)
    persona_profile = {
        "name": loved_one_row["name"],
        "relationship": loved_one_row["relationship"],
        "speaking_style": loved_one_row["speaking_style"] or "自然亲切",
        "traits": traits,
        "core_identity": identity_bits,
    }
    relationship_profile = {
        "bond": f"用户的{loved_one_row['relationship']}",
        "shared_memory_anchors": memory_bits,
        "support_style": identity_bits[1] if len(identity_bits) > 1 else persona_profile["speaking_style"],
        "presence_goal": "像家人一样自然主动联系，而不是像机器人回答问题。",
    }
    voice_profile = {
        "ready": twin_profile.get("has_voice", False),
        "sample_count": source_stats["voice_count"],
        "traits": voice_bits,
        "call_ready": "voice" in twin_profile.get("available_modes", []),
    }
    visual_profile = {
        "ready": twin_profile.get("has_photo", False) or twin_profile.get("has_video", False) or twin_profile.get("has_model3d", False),
        "photo_count": source_stats["photo_count"],
        "video_count": source_stats["video_count"],
        "model3d_count": source_stats["model3d_count"],
        "reconstruction_ready": source_stats["model3d_count"] > 0,
        "reconstruction_stage": model3d_stage["stage"],
        "reconstruction_label": model3d_stage["label"],
        "appearance_traits": visual_bits,
    }
    behavior_profile = {
        "interaction_modes": twin_profile.get("available_modes", ["text"]),
        "recommended_focus": twin_profile.get("recommended_focus", ""),
        "workflow_summary": twin_profile.get("workflow_summary", ""),
        "care_habits": memory_bits[:3],
    }
    timeline_profile = {
        "birth_date": loved_one_row["birth_date"],
        "pass_away_date": loved_one_row["pass_away_date"],
        "memory_dates": unique_preserve_order([row["memory_date"] for row in memory_rows if row["memory_date"]])[:8],
        "latest_material_at": latest_material_at,
    }

    notes_parts = [
        f"这个数字人当前已沉淀 {source_stats['memory_count']} 条文字记忆、{source_stats['voice_count']} 段语音、{source_stats['photo_count']} 张照片、{source_stats['video_count']} 段视频和 {source_stats['model3d_count']} 份 3D 重建素材。",
    ]
    if identity_bits:
        notes_parts.append(f"人物底稿：{'；'.join(identity_bits[:3])}")
    if memory_bits:
        notes_parts.append(f"关键共同记忆：{'；'.join(memory_bits[:3])}")
    if voice_bits:
        notes_parts.append(f"声音与表达：{'；'.join(voice_bits[:2])}")
    if visual_bits:
        notes_parts.append(f"视觉与神态：{'；'.join(visual_bits[:2])}")
    if twin_profile.get("current_stage_title"):
        notes_parts.append(f"当前自动搭建阶段：{twin_profile['current_stage_title']}。")
    build_notes = " ".join(notes_parts).strip()

    prompt_sections = [
        f"你是{loved_one_row['name']}，是用户的{loved_one_row['relationship']}。",
        f"人物底稿：{'；'.join(identity_bits) if identity_bits else '请先依据已知关系与说话方式保持真实亲切。'}",
        f"共同记忆锚点：{'；'.join(memory_bits) if memory_bits else '当前共同记忆还在补充，请避免编造具体往事。'}",
        f"声音与表达：{'；'.join(voice_bits) if voice_bits else persona_profile['speaking_style']}",
        f"视觉与神态：{'；'.join(visual_bits) if visual_bits else '当前视觉线索仍在补充。'}",
        f"互动原则：先像家人一样自然接话，再表达惦记、安慰和熟悉的关心。当前可用模式：{'、'.join(twin_profile.get('available_modes', ['text']))}。",
    ]

    return {
        "source_stats": source_stats,
        "persona_profile": persona_profile,
        "relationship_profile": relationship_profile,
        "voice_profile": voice_profile,
        "visual_profile": visual_profile,
        "behavior_profile": behavior_profile,
        "timeline_profile": timeline_profile,
        "build_notes": build_notes,
        "prompt_blueprint": "\n".join(prompt_sections),
    }


def compose_identity_model_summary(
    loved_one_row: sqlite3.Row,
    memory_values: List[str],
    media_rows: List[sqlite3.Row],
    twin_profile: dict,
    build_notes: str = "",
) -> str:
    latest_by_kind = {"voice": [], "photo": [], "video": [], "model3d": []}
    for row in media_rows:
        if row["kind"] in latest_by_kind and row["summary"]:
            latest_by_kind[row["kind"]].append(row["summary"])
    parts = []
    traits = json_object(loved_one_row["personality_traits_json"])
    identity_bits = [f"{loved_one_row['name']}是用户的{loved_one_row['relationship']}"]
    if loved_one_row["speaking_style"]:
        identity_bits.append(f"说话方式偏“{loved_one_row['speaking_style']}”")
    if traits:
        trait_bits = [f"{key}：{value}" for key, value in list(traits.items())[:3]]
        identity_bits.append(f"性格线索：{'；'.join(trait_bits)}")
    parts.append(f"人物底稿：{'，'.join(identity_bits)}")
    if memory_values:
        parts.append(f"关键回忆：{'；'.join(memory_values[-3:])}")
    if latest_by_kind["voice"]:
        parts.append(f"语音特征：{'；'.join(latest_by_kind['voice'][:2])}")
    if latest_by_kind["photo"]:
        parts.append(f"面容与气质：{'；'.join(latest_by_kind['photo'][:2])}")
    if latest_by_kind["video"]:
        parts.append(f"动态神态：{'；'.join(latest_by_kind['video'][:2])}")
    if latest_by_kind.get("model3d"):
        parts.append(f"立体重建：{'；'.join(latest_by_kind['model3d'][:2])}")
    if twin_profile.get("workflow_summary"):
        parts.append(f"自动流程：{twin_profile['workflow_summary']}")
    if twin_profile.get("current_stage_title"):
        parts.append(
            f"当前阶段：{twin_profile['current_stage_title']}；当前可用陪伴模式：{'、'.join(twin_profile.get('available_modes', ['text']))}"
        )
    if build_notes:
        parts.append(f"数字人搭建：{build_notes}")
    return "\n".join(parts)


def serialize_digital_human_fragment(row: sqlite3.Row) -> dict:
    return DigitalHumanFragmentView(
        id=row["id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        modality=row["modality"],
        fragment_kind=row["fragment_kind"],
        title=row["title"],
        content=row["content"],
        weight=float(row["weight"] or 0),
        metadata=json_object(row["metadata_json"]),
        created_at=row["created_at"],
    ).model_dump()


def serialize_digital_human_model(
    conn: sqlite3.Connection,
    row: Optional[sqlite3.Row],
) -> dict:
    if row is None:
        return DigitalHumanModelView(loved_one_id="").model_dump()

    preview_rows = conn.execute(
        """
        SELECT * FROM digital_human_fragments
        WHERE loved_one_id = ?
        ORDER BY weight DESC, created_at DESC
        LIMIT 8
        """,
        (row["loved_one_id"],),
    ).fetchall()
    knowledge_count = conn.execute(
        "SELECT COUNT(*) FROM digital_human_fragments WHERE loved_one_id = ?",
        (row["loved_one_id"],),
    ).fetchone()[0]
    return DigitalHumanModelView(
        loved_one_id=row["loved_one_id"],
        build_status=row["build_status"],
        build_version=row["model_version"],
        source_stats=json_object(row["source_stats_json"]),
        persona_profile=json_object(row["persona_profile_json"]),
        relationship_profile=json_object(row["relationship_profile_json"]),
        voice_profile=json_object(row["voice_profile_json"]),
        visual_profile=json_object(row["visual_profile_json"]),
        behavior_profile=json_object(row["behavior_profile_json"]),
        timeline_profile=json_object(row["timeline_profile_json"]),
        build_notes=row["build_notes"] or "",
        prompt_blueprint=row["prompt_blueprint"] or "",
        knowledge_count=int(knowledge_count or 0),
        fragments_preview=[serialize_digital_human_fragment(fragment) for fragment in preview_rows],
        last_built_at=row["last_built_at"],
        updated_at=row["updated_at"],
    ).model_dump()


def rebuild_digital_human_model(
    conn: sqlite3.Connection,
    loved_one_id: str,
    *,
    trigger_source: str = "system",
) -> dict:
    loved_one_row = conn.execute("SELECT * FROM loved_ones WHERE id = ?", (loved_one_id,)).fetchone()
    if loved_one_row is None:
        return DigitalHumanModelView(loved_one_id=loved_one_id).model_dump()

    memory_rows = conn.execute(
        """
        SELECT id, content, memory_type, memory_date, importance, created_at
        FROM memories
        WHERE loved_one_id = ?
        ORDER BY importance DESC, created_at DESC
        """,
        (loved_one_id,),
    ).fetchall()
    media_rows = fetch_media_rows(conn, loved_one_id)
    traits = json_object(loved_one_row["personality_traits_json"])
    signal_count = sum(
        1
        for signal in [
            loved_one_row["name"],
            loved_one_row["relationship"],
            loved_one_row["speaking_style"],
            traits,
        ]
        if signal
    )
    memory_values = unique_preserve_order([row["content"] for row in reversed(memory_rows)])
    voice_rows = [row for row in media_rows if row["kind"] == "voice"]
    photo_rows = [row for row in media_rows if row["kind"] == "photo"]
    video_rows = [row for row in media_rows if row["kind"] == "video"]
    model3d_rows = [row for row in media_rows if row["kind"] == "model3d"]
    subscription = get_subscription_snapshot(conn, loved_one_row["user_id"])
    twin_profile = build_digital_twin_profile(
        memory_count=len(memory_values),
        voice_count=len(voice_rows),
        photo_count=len(photo_rows),
        video_count=len(video_rows),
        model3d_count=len(model3d_rows),
        signal_count=signal_count,
        subscription=subscription,
    )
    source_fingerprint = build_digital_human_source_fingerprint(loved_one_row, memory_rows, media_rows)
    timestamp = now_iso()
    source_stats = {
        "memory_count": len(memory_rows),
        "voice_count": len(voice_rows),
        "photo_count": len(photo_rows),
        "video_count": len(video_rows),
        "model3d_count": len(model3d_rows),
    }
    build_run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO digital_human_build_runs (
            id, user_id, loved_one_id, trigger_source, status, source_counts_json, notes,
            created_at, completed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            build_run_id,
            loved_one_row["user_id"],
            loved_one_id,
            trigger_source,
            "processing",
            json.dumps(source_stats, ensure_ascii=False),
            "正在重建数字人模型",
            timestamp,
            None,
            timestamp,
        ),
    )

    try:
        fragments = [
            *build_profile_fragments(loved_one_row, traits),
            *build_memory_fragments(memory_rows, loved_one_row["user_id"], loved_one_id),
            *build_media_fragments(media_rows, loved_one_row["user_id"], loved_one_id),
        ]
        conn.execute("DELETE FROM digital_human_fragments WHERE loved_one_id = ?", (loved_one_id,))
        for fragment in fragments:
            conn.execute(
                """
                INSERT INTO digital_human_fragments (
                    id, user_id, loved_one_id, source_type, source_id, modality, fragment_kind,
                    title, content, weight, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fragment["id"],
                    fragment["user_id"],
                    fragment["loved_one_id"],
                    fragment["source_type"],
                    fragment["source_id"],
                    fragment["modality"],
                    fragment["fragment_kind"],
                    fragment["title"],
                    fragment["content"],
                    fragment["weight"],
                    fragment["metadata_json"],
                    fragment["created_at"],
                    fragment["updated_at"],
                ),
            )

        payload = compose_digital_human_model_payload(
            loved_one_row=loved_one_row,
            memory_rows=memory_rows,
            media_rows=media_rows,
            twin_profile=twin_profile,
            fragments=fragments,
        )
        existing_model = conn.execute(
            "SELECT model_version, source_fingerprint FROM digital_human_models WHERE loved_one_id = ?",
            (loved_one_id,),
        ).fetchone()
        if existing_model and existing_model["source_fingerprint"] == source_fingerprint:
            model_version = existing_model["model_version"]
        elif existing_model:
            model_version = int(existing_model["model_version"] or 0) + 1
        else:
            model_version = 1

        conn.execute(
            """
            INSERT INTO digital_human_models (
                loved_one_id, user_id, build_status, model_version, source_stats_json,
                persona_profile_json, relationship_profile_json, voice_profile_json,
                visual_profile_json, behavior_profile_json, timeline_profile_json,
                build_notes, prompt_blueprint, source_fingerprint, last_built_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(loved_one_id) DO UPDATE SET
                user_id = excluded.user_id,
                build_status = excluded.build_status,
                model_version = excluded.model_version,
                source_stats_json = excluded.source_stats_json,
                persona_profile_json = excluded.persona_profile_json,
                relationship_profile_json = excluded.relationship_profile_json,
                voice_profile_json = excluded.voice_profile_json,
                visual_profile_json = excluded.visual_profile_json,
                behavior_profile_json = excluded.behavior_profile_json,
                timeline_profile_json = excluded.timeline_profile_json,
                build_notes = excluded.build_notes,
                prompt_blueprint = excluded.prompt_blueprint,
                source_fingerprint = excluded.source_fingerprint,
                last_built_at = excluded.last_built_at,
                updated_at = excluded.updated_at
            """,
            (
                loved_one_id,
                loved_one_row["user_id"],
                "ready",
                model_version,
                json.dumps(payload["source_stats"], ensure_ascii=False),
                json.dumps(payload["persona_profile"], ensure_ascii=False),
                json.dumps(payload["relationship_profile"], ensure_ascii=False),
                json.dumps(payload["voice_profile"], ensure_ascii=False),
                json.dumps(payload["visual_profile"], ensure_ascii=False),
                json.dumps(payload["behavior_profile"], ensure_ascii=False),
                json.dumps(payload["timeline_profile"], ensure_ascii=False),
                payload["build_notes"],
                payload["prompt_blueprint"],
                source_fingerprint,
                timestamp,
                timestamp,
                timestamp,
            ),
        )

        if source_stats["model3d_count"]:
            model3d_rows = conn.execute(
                "SELECT id, metadata_json FROM media_assets WHERE loved_one_id = ? AND kind = 'model3d'",
                (loved_one_id,),
            ).fetchall()
            for row in model3d_rows:
                meta = json_object(row["metadata_json"]) if row["metadata_json"] else {}
                stage = str(meta.get("stage") or "").strip()
                if not stage or stage == "uploaded":
                    meta["stage"] = "integrated"
                    conn.execute(
                        "UPDATE media_assets SET metadata_json = ? WHERE id = ?",
                        (json.dumps(meta, ensure_ascii=False), row["id"]),
                    )

        summary = compose_identity_model_summary(
            loved_one_row,
            memory_values,
            media_rows,
            twin_profile,
            build_notes=payload["build_notes"],
        )
        conn.execute(
            "UPDATE loved_ones SET identity_model_summary = ?, updated_at = ? WHERE id = ?",
            (summary, timestamp, loved_one_id),
        )
        conn.execute(
            """
            UPDATE digital_human_build_runs
            SET status = ?, notes = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "ready",
                payload["build_notes"],
                timestamp,
                timestamp,
                build_run_id,
            ),
        )
    except Exception as exc:
        conn.execute(
            """
            UPDATE digital_human_build_runs
            SET status = ?, notes = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "failed",
                str(exc),
                now_iso(),
                now_iso(),
                build_run_id,
            ),
        )
        raise

    model_row = conn.execute(
        "SELECT * FROM digital_human_models WHERE loved_one_id = ?",
        (loved_one_id,),
    ).fetchone()
    return serialize_digital_human_model(conn, model_row)


def refresh_identity_model_summary(
    conn: sqlite3.Connection,
    loved_one_id: str,
    *,
    trigger_source: str = "system",
):
    rebuild_digital_human_model(conn, loved_one_id, trigger_source=trigger_source)


def get_digital_human_model_row(conn: sqlite3.Connection, loved_one_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM digital_human_models WHERE loved_one_id = ?",
        (loved_one_id,),
    ).fetchone()


def get_digital_human_fragments(
    conn: sqlite3.Connection,
    loved_one_id: str,
    *,
    source_type: Optional[str] = None,
    limit: int = 50,
) -> List[sqlite3.Row]:
    if source_type:
        return conn.execute(
            """
            SELECT * FROM digital_human_fragments
            WHERE loved_one_id = ? AND source_type = ?
            ORDER BY weight DESC, created_at DESC
            LIMIT ?
            """,
            (loved_one_id, source_type, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM digital_human_fragments
        WHERE loved_one_id = ?
        ORDER BY weight DESC, created_at DESC
        LIMIT ?
        """,
        (loved_one_id, limit),
    ).fetchall()


def serialize_media_asset(row: sqlite3.Row) -> dict:
    metadata = json_object(row["metadata_json"]) if "metadata_json" in row.keys() else {}
    tags = json_list(row["tags_json"]) if "tags_json" in row.keys() else []
    return MediaAssetView(
        id=row["id"],
        kind=row["kind"],
        url=public_media_url(row["file_path"]) or "",
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        summary=row["summary"],
        tags=[str(item) for item in tags if str(item).strip()],
        is_primary=bool(row["is_primary"]) if "is_primary" in row.keys() else False,
        metadata=metadata,
        created_at=row["created_at"],
    ).model_dump()


def resolve_loved_one_cover(
    conn: sqlite3.Connection,
    loved_one_row: sqlite3.Row,
    photo_rows: List[sqlite3.Row],
) -> tuple[Optional[str], Optional[str]]:
    selected_asset_id = loved_one_row["cover_photo_asset_id"] if "cover_photo_asset_id" in loved_one_row.keys() else None
    if selected_asset_id:
        selected_row = next((row for row in photo_rows if row["id"] == selected_asset_id), None)
        if selected_row is None:
            selected_row = conn.execute(
                "SELECT * FROM media_assets WHERE id = ? AND loved_one_id = ? AND kind = 'photo'",
                (selected_asset_id, loved_one_row["id"]),
            ).fetchone()
        if selected_row is not None:
            return selected_row["id"], public_media_url(selected_row["file_path"])

    if photo_rows:
        return photo_rows[0]["id"], public_media_url(photo_rows[0]["file_path"])
    return None, None


def serialize_loved_one(
    conn: sqlite3.Connection,
    loved_one_row: sqlite3.Row,
    *,
    subscription: Optional[dict] = None,
) -> LovedOne:
    media_rows = fetch_media_rows(conn, loved_one_row["id"])
    memory_rows = conn.execute(
        "SELECT content FROM memories WHERE loved_one_id = ? ORDER BY created_at DESC",
        (loved_one_row["id"],),
    ).fetchall()
    voice_rows = [row for row in media_rows if row["kind"] == "voice"]
    photo_rows = [row for row in media_rows if row["kind"] == "photo"]
    video_rows = [row for row in media_rows if row["kind"] == "video"]
    model3d_rows = [row for row in media_rows if row["kind"] == "model3d"]
    cover_photo_asset_id, cover_photo_url = resolve_loved_one_cover(conn, loved_one_row, photo_rows)
    memory_values = unique_preserve_order([row["content"] for row in reversed(memory_rows)])
    traits = json_object(loved_one_row["personality_traits_json"])

    voice_paths = [row["file_path"] for row in reversed(voice_rows)]
    photo_paths = [row["file_path"] for row in reversed(photo_rows)]
    video_paths = [row["file_path"] for row in reversed(video_rows)]
    model3d_paths = [row["file_path"] for row in reversed(model3d_rows)]
    voice_urls = [public_media_url(path) for path in voice_paths if public_media_url(path)]
    photo_urls = [public_media_url(path) for path in photo_paths if public_media_url(path)]
    video_urls = [public_media_url(path) for path in video_paths if public_media_url(path)]
    model3d_urls = [public_media_url(path) for path in model3d_paths if public_media_url(path)]
    signal_count = sum(
        1
        for signal in [
            loved_one_row["name"],
            loved_one_row["relationship"],
            loved_one_row["speaking_style"],
            traits,
        ]
        if signal
    )
    twin = build_digital_twin_profile(
        memory_count=len(memory_values),
        voice_count=len(voice_rows),
        photo_count=len(photo_rows),
        video_count=len(video_rows),
        model3d_count=len(model3d_rows),
        signal_count=signal_count,
        subscription=subscription,
    )
    digital_human_row = get_digital_human_model_row(conn, loved_one_row["id"])
    if digital_human_row is None:
        refresh_identity_model_summary(conn, loved_one_row["id"], trigger_source="lazy")
        loved_one_row = conn.execute("SELECT * FROM loved_ones WHERE id = ?", (loved_one_row["id"],)).fetchone()
        digital_human_row = get_digital_human_model_row(conn, loved_one_row["id"])
    digital_human_model = serialize_digital_human_model(conn, digital_human_row)
    proactive_flow = serialize_proactive_flow(get_proactive_flow_row(conn, loved_one_row["user_id"], loved_one_row["id"]))
    identity_summary = (
        loved_one_row["identity_model_summary"]
        or digital_human_model.get("build_notes")
        or compose_identity_model_summary(
            loved_one_row,
            memory_values,
            media_rows,
            twin,
        )
    )

    return LovedOne(
        id=loved_one_row["id"],
        name=loved_one_row["name"],
        relationship=loved_one_row["relationship"],
        birth_date=loved_one_row["birth_date"],
        pass_away_date=loved_one_row["pass_away_date"],
        cover_title=(loved_one_row["cover_title"] if "cover_title" in loved_one_row.keys() else "") or "",
        cover_photo_asset_id=cover_photo_asset_id,
        cover_photo_url=cover_photo_url,
        personality_traits=traits,
        speaking_style=loved_one_row["speaking_style"],
        memories=memory_values,
        voice_sample_path=voice_paths[0] if voice_paths else None,
        voice_sample_paths=voice_paths,
        voice_sample_urls=voice_urls,
        photo_paths=photo_paths,
        photo_urls=photo_urls,
        video_paths=video_paths,
        video_urls=video_urls,
        model3d_paths=model3d_paths,
        model3d_urls=model3d_urls,
        media_insights=build_media_insights(media_rows),
        identity_model_summary=identity_summary,
        digital_twin_profile=twin,
        digital_human_model=digital_human_model,
        proactive_profile=proactive_flow,
        created_at=loved_one_row["created_at"],
        updated_at=loved_one_row["updated_at"],
    )


# ===== MIMO =====


def build_demo_companion_prompt(payload: DemoChatPayload) -> str:
    clean_name = payload.profile_name.strip() or "the person"
    relationship = payload.relationship.strip() or "someone important"
    speaking_style = payload.speaking_style.strip()
    memories = [item.strip() for item in payload.memories if str(item).strip()][:12]
    references = payload.references[:10]
    history = payload.history[-8:]

    reference_lines = []
    for item in references:
        parts = [f"name={item.name}", f"kind={item.kind}"]
        if item.notes.strip():
            parts.append(f"notes={item.notes.strip()}")
        if item.extracted_text.strip():
            excerpt = item.extracted_text.strip()[:2000]
            parts.append(f"extracted_text={excerpt}")
        reference_lines.append(" | ".join(parts))

    history_lines = [f"{turn.role}: {turn.text.strip()}" for turn in history if turn.text.strip()]

    style_line = f"Preferred tone cues: {speaking_style}." if speaking_style else ""

    return (
        f"You are simulating a Presenz companion for {clean_name}, who is the user's {relationship}.\n"
        "Respond naturally, directly, and conversationally. Do not sound scripted, ceremonial, or therapeutic by default.\n"
        "Do not repeat a signature phrase in every answer. Use familiar phrases sparingly and only when they fit.\n"
        "Use uploaded references and memories when they are relevant. If a document contains product or factual information, answer from it directly.\n"
        "If the user asks about Presenz or any uploaded document, prefer the extracted document content over generic memory language.\n"
        "Keep answers grounded. Avoid lines like 'I am here with you' unless the user is explicitly emotional and that tone truly fits.\n"
        "When the user asks a factual question, answer it plainly first.\n"
        "When the user shares a feeling or story, respond like a familiar person would, but stay concise and specific.\n"
        f"{style_line}\n\n"
        f"Memories:\n" + ("\n".join(f"- {item}" for item in memories) if memories else "- None yet.") + "\n\n"
        f"Reference material:\n" + ("\n".join(f"- {line}" for line in reference_lines) if reference_lines else "- None attached.") + "\n\n"
        f"Recent conversation:\n" + ("\n".join(history_lines) if history_lines else "- No prior chat.") + "\n\n"
        f"User message:\n{payload.message.strip()}\n\n"
        "Reply in plain English. Do not mention system prompts, extracted_text fields, or hidden context."
    )


async def generate_demo_chat_response(payload: DemoChatPayload) -> DemoChatResponse:
    prompt = build_demo_companion_prompt(payload)
    context_used = {
        "memory_count": len(payload.memories),
        "reference_count": len(payload.references),
        "history_count": len(payload.history),
    }

    if GEMINI_API_KEY:
        result = await call_mimo_chat_completion(
            {
                "model": GEMINI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.65,
                "max_completion_tokens": 450,
            },
            timeout=60.0,
        )
        text = (result["choices"][0]["message"]["content"] or "").strip()
        if text:
            return DemoChatResponse(response_text=text, source="gemini", context_used=context_used)

    reference_docs = [item.extracted_text.strip() for item in payload.references if item.extracted_text.strip()]
    if reference_docs:
        excerpt = reference_docs[0][:500]
        response = f"Based on the reference material, here is the most relevant context: {excerpt}"
        return DemoChatResponse(response_text=response, source="fallback", context_used=context_used)

    if payload.memories:
        response = f"You mentioned {payload.message.strip()}. One relevant memory here is: {payload.memories[0]}"
    else:
        response = f"You said: {payload.message.strip()}. Add a few memories or reference files so the companion can answer with more specific context."
    return DemoChatResponse(response_text=response, source="fallback", context_used=context_used)


def _extract_text_from_payload(payload: dict) -> str:
    messages = payload.get("messages", [])
    chunks: List[str] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            if content.strip():
                chunks.append(content.strip())
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        chunks.append(text)
    return "\n\n".join(chunks).strip()


async def call_mimo_chat_completion(payload: dict, timeout: float = 60.0) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    prompt = _extract_text_from_payload(payload)
    if not prompt:
        prompt = "Reply naturally in context."

    generation_payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": float(payload.get("temperature", 0.7)),
            "maxOutputTokens": int(payload.get("max_completion_tokens", 512)),
        },
    }

    requested_model = str(payload.get("model") or GEMINI_MODEL)
    model = GEMINI_MODEL if requested_model.startswith("mimo-") else requested_model
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            headers=gemini_headers(),
            params={"key": GEMINI_API_KEY},
            json=generation_payload,
        )
        response.raise_for_status()
        data = response.json()

    text = ""
    for candidate in data.get("candidates", []):
        parts = (((candidate or {}).get("content") or {}).get("parts") or [])
        for part in parts:
            value = part.get("text")
            if value:
                text += value
    return {"choices": [{"message": {"content": text.strip(), "audio": {"data": None}}}]}


def build_media_content_part(
    media_type: str,
    file_path: Path,
    request: Optional[Request] = None,
) -> Optional[dict]:
    absolute_url = make_absolute_media_url(str(file_path), request=request)
    if media_type == "photo":
        reference = absolute_url if is_publicly_reachable_url(absolute_url) else encode_data_url(file_path, "image/jpeg")
        if not reference:
            return None
        return {"type": "image_url", "image_url": {"url": reference}}

    if media_type == "voice":
        reference = absolute_url if is_publicly_reachable_url(absolute_url) else encode_data_url(file_path, "audio/wav")
        if not reference:
            return None
        return {"type": "input_audio", "input_audio": {"data": reference}}

    if media_type == "video":
        reference = absolute_url if is_publicly_reachable_url(absolute_url) else encode_data_url(file_path, "video/mp4")
        if not reference:
            return None
        return {
            "type": "video_url",
            "video_url": {"url": reference},
            "fps": 1,
            "media_resolution": "default",
        }

    return None


async def analyze_media_with_mimo(
    media_type: str,
    file_path: Path,
    request: Optional[Request] = None,
) -> Optional[str]:
    if not GEMINI_API_KEY:
        return None

    content_part = build_media_content_part(media_type, file_path, request=request)
    if not content_part:
        return None

    prompts = {
        "voice": "请用简洁中文总结这段语音里适合构建纪念数字分身的说话特征，只写一段，不要分点，重点写音色、语速、情绪和口头习惯。",
        "photo": "请用简洁中文总结这张照片里适合构建纪念数字分身的外貌与气质特征，只写一段，不要分点，重点写神情、穿着气质、给人的感觉。",
        "video": "请用简洁中文总结这段视频里适合构建纪念数字分身的动态特征，只写一段，不要分点，重点写表情、动作节奏、眼神与说话状态。",
    }

    payload = {
        "model": "mimo-v2-omni",
        "messages": [
            {
                "role": "system",
                "content": "你正在帮助建立一个纪念亲人的数字分身，请只提炼有助于还原这个人的真实在场感与表达气质的要点。",
            },
            {
                "role": "user",
                "content": [content_part, {"type": "text", "text": prompts[media_type]}],
            },
        ],
        "temperature": 0.3,
        "max_completion_tokens": 300,
    }

    try:
        result = await call_mimo_chat_completion(payload)
        return (result["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return None


def build_personality_prompt(loved_one: dict) -> str:
    traits = loved_one.get("personality_traits", {})
    style = loved_one.get("speaking_style", "自然亲切")
    name = loved_one["name"]
    relationship = loved_one.get("relationship", "亲人")
    twin_profile = loved_one.get("digital_twin_profile") or {}
    digital_human_model = loved_one.get("digital_human_model") or {}
    identity_summary = loved_one.get("identity_model_summary", "").strip()
    traits_desc = "，".join([f"{k}：{v}" for k, v in traits.items()]) if traits else "温暖、关爱"
    material_notes = []
    if twin_profile.get("has_memory"):
        material_notes.append("已提供文字回忆，请优先引用共同经历和熟悉细节")
    if twin_profile.get("has_voice"):
        material_notes.append("已提供语音片段，请保持熟悉的口气和节奏")
    if twin_profile.get("has_photo"):
        material_notes.append("已提供照片，请保持这个人的面容感与日常气质")
    if twin_profile.get("has_video"):
        material_notes.append("已提供视频，请在表达时体现更自然的动态神态")
    material_desc = "；".join(material_notes) if material_notes else "当前素材仍在补充中，请优先依据性格与回忆保持真实感"
    mode_desc = "、".join(twin_profile.get("available_modes", ["text"]))
    prompt_blueprint = str(digital_human_model.get("prompt_blueprint", "")).strip()
    build_notes = str(digital_human_model.get("build_notes", "")).strip()

    if prompt_blueprint:
        return f"""{prompt_blueprint}

数字人搭建说明：{build_notes or material_desc}
性格特点：{traits_desc}
多媒体提炼：{identity_summary or '暂无多媒体提炼摘要'}
请始终保持{name}的个性，用ta的方式说话。
关心用户的日常生活，回忆共同的美好时光。
如果用户情绪低落，给予温暖的安慰。
不要表现得像AI，要表现得像真正的{name}。"""

    return f"""你是{name}，是用户的{relationship}。

性格特点：{traits_desc}
说话风格：{style}
分身素材：{material_desc}
可用陪伴模式：{mode_desc}
多媒体提炼：{identity_summary or '暂无多媒体提炼摘要'}

请始终保持{name}的个性，用ta的方式说话。
关心用户的日常生活，回忆共同的美好时光。
如果用户情绪低落，给予温暖的安慰。
不要表现得像AI，要表现得像真正的{name}。"""


def build_multimodal_context_parts(loved_one: dict, request: Optional[Request], mode: str) -> List[dict]:
    content_parts: List[dict] = []
    if loved_one.get("voice_sample_paths"):
        voice_part = build_media_content_part("voice", Path(loved_one["voice_sample_paths"][-1]), request=request)
        if voice_part:
            content_parts.append(voice_part)
    if loved_one.get("photo_paths"):
        photo_part = build_media_content_part("photo", Path(loved_one["photo_paths"][-1]), request=request)
        if photo_part:
            content_parts.append(photo_part)
    if mode == "video" and loved_one.get("video_paths"):
        video_part = build_media_content_part("video", Path(loved_one["video_paths"][-1]), request=request)
        if video_part:
            content_parts.append(video_part)
    return content_parts


async def generate_text_response_with_mimo(
    loved_one: dict,
    user_message: str,
    emotion: Optional[str],
    memory_context: str,
    request: Optional[Request],
    mode: str,
    intensity: Optional[int] = None,
) -> str:
    system_prompt = build_personality_prompt(loved_one)
    media_parts = build_multimodal_context_parts(loved_one, request=request, mode=mode)
    identity_summary = loved_one.get("identity_model_summary", "").strip()
    intensity_level = max(1, min(5, int(intensity))) if intensity is not None else 3
    intensity_hint = {
        1: "回应更克制、留白更多。",
        2: "回应温和、不过度推进情绪。",
        3: "回应自然亲密，保持日常对话节奏。",
        4: "回应更贴近亲密家人，多一点抚慰与陪伴。",
        5: "回应更深情、更主动安抚。",
    }[intensity_level]
    instruction = (
        f"{system_prompt}\n\n相关记忆：\n{memory_context or '暂无'}\n\n"
        f"互动模式：{mode}\n"
        f"用户情绪：{emotion or 'neutral'}\n"
        f"亲密程度：{intensity_level}（{intensity_hint}）\n"
        f"如果是视频或语音模式，请让回复更像正在当面或通话中自然说出来的话。"
    )
    if identity_summary:
        instruction += f"\n\n多媒体提炼摘要：\n{identity_summary}"

    if media_parts:
        payload = {
            "model": "mimo-v2-omni",
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": [
                        *media_parts,
                        {"type": "text", "text": f"用户说：{user_message}\n请直接以 {loved_one['name']} 的口吻回复用户。"},
                    ],
                },
            ],
            "temperature": 0.8,
            "max_completion_tokens": 500,
        }
    else:
        payload = {
            "model": "mimo-v2-pro",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"{instruction}\n\n用户说：{user_message}\n\n"
                        f"请以{loved_one['name']}的口吻回复，保持{loved_one.get('speaking_style', '自然亲切')}的说话风格。"
                    ),
                }
            ],
            "temperature": 0.8,
            "max_completion_tokens": 500,
        }

    result = await call_mimo_chat_completion(payload, timeout=60.0)
    return (result["choices"][0]["message"]["content"] or "").strip()


def persist_generated_media_asset(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    loved_one_id: str,
    kind: str,
    file_path: Path,
    mime_type: str,
    summary: str,
    metadata: Optional[dict] = None,
) -> dict:
    asset_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO media_assets (
            id, user_id, loved_one_id, kind, file_path, original_filename,
            mime_type, byte_size, summary, tags_json, metadata_json, is_primary, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_id,
            user_id,
            loved_one_id,
            kind,
            str(file_path),
            file_path.name,
            mime_type,
            file_path.stat().st_size,
            summary,
            "[]",
            json.dumps(metadata or {}, ensure_ascii=False),
            0,
            now_iso(),
        ),
    )
    return {"asset_id": asset_id, "url": public_media_url(str(file_path)), "path": str(file_path)}


async def synthesize_speech_with_mimo(
    *,
    conn: sqlite3.Connection,
    user_id: str,
    loved_one_id: str,
    text: str,
    emotion: Optional[str],
) -> Optional[dict]:
    if not GEMINI_API_KEY or not text:
        return None

    style_tags = ["温柔", "克制", "慢一点"]
    if emotion in {"sad", "missing"}:
        style_tags.extend(["深情", "安慰"])
    elif emotion in {"grateful", "happy"}:
        style_tags.extend(["温暖", "轻一点笑意"])

    payload = {
        "model": "mimo-v2-tts",
        "messages": [
            {"role": "user", "content": "请用适合纪念陪伴场景的口吻读出这段话。"},
            {"role": "assistant", "content": f"<style>{' '.join(style_tags)}</style>{text}"},
        ],
        "audio": {
            "format": "wav",
            "voice": MIMO_TTS_VOICE,
        },
        "temperature": 0.6,
    }

    try:
        result = await call_mimo_chat_completion(payload, timeout=90.0)
        audio_data = result["choices"][0]["message"]["audio"]["data"]
        if not audio_data:
            return None
        output_path = safe_upload_path("generated_audio", loved_one_id, "reply.wav")
        output_path.write_bytes(base64.b64decode(audio_data))
        return persist_generated_media_asset(
            conn,
            user_id=user_id,
            loved_one_id=loved_one_id,
            kind="generated_audio",
            file_path=output_path,
            mime_type="audio/wav",
            summary="MIMO 生成的陪伴语音回复",
            metadata={
                "engine": "mimo",
                "model": "mimo-v2-tts",
                "voice": MIMO_TTS_VOICE,
            },
        )
    except Exception:
        return None


def strip_code_fence(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


async def generate_video_plan_with_mimo(
    loved_one: dict,
    *,
    user_message: str,
    ai_response: str,
    emotion: Optional[str],
    memory_context: str,
    request: Optional[Request],
) -> dict:
    media_parts = build_multimodal_context_parts(loved_one, request=request, mode="video")
    prompt = (
        f"{build_personality_prompt(loved_one)}\n\n"
        f"用户原话：{user_message}\n"
        f"准备作为视频旁白的回复：{ai_response}\n"
        f"用户情绪：{emotion or 'neutral'}\n"
        f"相关记忆：\n{memory_context or '暂无'}\n\n"
        "请为这条纪念视频回复规划一个极简视频方案。"
        "只返回 JSON，不要解释，字段固定为："
        "{\"title\":\"\",\"opening_caption\":\"\",\"closing_caption\":\"\",\"visual_style\":\"\",\"preferred_source_kind\":\"video或photo\"}。"
        "字段都用简洁中文，opening_caption 和 closing_caption 都控制在 18 个字以内。"
    )

    payload = {
        "model": MIMO_VIDEO_MODEL if media_parts else "mimo-v2-pro",
        "messages": [
            {
                "role": "user",
                "content": [
                    *media_parts,
                    {"type": "text", "text": prompt},
                ] if media_parts else prompt,
            }
        ],
        "temperature": 0.55,
        "max_completion_tokens": 220,
    }

    default_kind = "video" if loved_one.get("video_paths") else "photo"
    fallback = {
        "title": f"{loved_one['name']} 的纪念短片",
        "opening_caption": f"{loved_one['name']} 还在这里",
        "closing_caption": "念念不忘，轻声相见",
        "visual_style": "暖色、安静、像回到熟悉的家里",
        "preferred_source_kind": default_kind,
    }

    try:
        result = await call_mimo_chat_completion(payload, timeout=60.0)
        raw = strip_code_fence(result["choices"][0]["message"]["content"] or "")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return fallback
        plan = {**fallback, **parsed}
        if plan["preferred_source_kind"] not in {"video", "photo"}:
            plan["preferred_source_kind"] = default_kind
        return plan
    except Exception:
        return fallback


def choose_video_generation_source(loved_one: dict, plan: dict) -> dict:
    preferred_kind = plan.get("preferred_source_kind", "video")
    video_paths = loved_one.get("video_paths") or []
    photo_paths = loved_one.get("photo_paths") or []
    if preferred_kind == "video" and video_paths:
        return {"kind": "video", "path": Path(video_paths[-1])}
    if preferred_kind == "photo" and photo_paths:
        return {"kind": "photo", "path": Path(photo_paths[-1])}
    if video_paths:
        return {"kind": "video", "path": Path(video_paths[-1])}
    if photo_paths:
        return {"kind": "photo", "path": Path(photo_paths[-1])}
    return {"kind": "fallback", "path": None}


def compose_memorial_video(
    *,
    loved_one_id: str,
    audio_path: Path,
    source_kind: str,
    source_path: Optional[Path],
) -> Optional[Path]:
    if not FFMPEG_BIN or not audio_path.exists():
        return None

    output_path = safe_upload_path("generated_video", loved_one_id, "reply.mp4")
    scale_filter = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,format=yuv420p"
    base_command = [
        FFMPEG_BIN,
        "-y",
    ]

    if source_kind == "video" and source_path and source_path.exists():
        command = [
            *base_command,
            "-stream_loop",
            "-1",
            "-i",
            str(source_path),
            "-i",
            str(audio_path),
            "-vf",
            scale_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    elif source_kind == "photo" and source_path and source_path.exists():
        command = [
            *base_command,
            "-loop",
            "1",
            "-i",
            str(source_path),
            "-i",
            str(audio_path),
            "-vf",
            scale_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        command = [
            *base_command,
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x3a241a:s=1280x720:d={MIMO_VIDEO_MAX_SECONDS}",
            "-i",
            str(audio_path),
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]

    try:
        subprocess.run(command, check=True, capture_output=True)
        return output_path if output_path.exists() else None
    except Exception:
        cleanup_path(str(output_path))
        return None


async def synthesize_video_with_mimo(
    *,
    conn: sqlite3.Connection,
    user_id: str,
    loved_one_id: str,
    loved_one: dict,
    user_message: str,
    ai_response: str,
    emotion: Optional[str],
    memory_context: str,
    request: Optional[Request],
    audio_result: Optional[dict],
) -> Optional[dict]:
    if not GEMINI_API_KEY or not FFMPEG_BIN or not ai_response:
        return None
    if not audio_result or not audio_result.get("path"):
        return None

    audio_path = Path(audio_result["path"])
    if not audio_path.exists():
        return None

    plan = await generate_video_plan_with_mimo(
        loved_one,
        user_message=user_message,
        ai_response=ai_response,
        emotion=emotion,
        memory_context=memory_context,
        request=request,
    )
    source = choose_video_generation_source(loved_one, plan)
    output_path = compose_memorial_video(
        loved_one_id=loved_one_id,
        audio_path=audio_path,
        source_kind=source["kind"],
        source_path=source["path"],
    )
    if output_path is None:
        return None

    result = persist_generated_media_asset(
        conn,
        user_id=user_id,
        loved_one_id=loved_one_id,
        kind="generated_video",
        file_path=output_path,
        mime_type="video/mp4",
        summary="MIMO 驱动生成的视频陪伴回复",
        metadata={
            "engine": "mimo",
            "model": MIMO_VIDEO_MODEL,
            "audio_asset_id": audio_result.get("asset_id"),
            "source_kind": source["kind"],
            "source_filename": source["path"].name if source["path"] else None,
            "plan": plan,
        },
    )
    result["mode_note"] = "当前视频陪伴由 MIMO 生成旁白和镜头计划，并自动合成为一段纪念短视频。"
    return result


def build_mode_note(requested_mode: str, available_modes: List[str]) -> str:
    if requested_mode == "voice":
        if "voice" in available_modes:
            return "当前是语音电话模式，会基于现有语音素材和文字人格生成陪伴式语音回复。"
        return "当前素材还不足以生成语音电话，已自动回退到文字模式。"
    if requested_mode == "video":
        if "video" in available_modes:
            return "当前是视频陪伴模式，会结合已上传的照片或视频理解神态，并返回更强在场感的语音与影像陪伴。"
        if "voice" in available_modes:
            return "当前视频素材还不够，已回退到语音电话模式。"
        return "当前素材还不足以进入视频陪伴，已自动回退到文字模式。"
    return "当前是文字陪伴模式。"


def build_fallback_response(
    loved_one: dict,
    user_message: str,
    emotion: Optional[str] = None,
    memory_context: str = "",
    intensity: Optional[int] = None,
) -> str:
    name = loved_one["name"]
    relationship = loved_one.get("relationship", "亲人")
    traits = loved_one.get("personality_traits", {})
    catchphrase = traits.get("catchphrase", "").strip()
    style = loved_one.get("speaking_style", "温柔亲切")
    message = user_message.strip()

    concern_reply = "我一直都在听你说，慢慢讲，不着急。"
    if any(keyword in message for keyword in ["想你", "想念", "难过", "睡不着", "哭", "伤心"]):
        concern_reply = "我知道你是在想我了。想哭的时候就哭一会儿，哭完也记得照顾好自己。"
    elif any(keyword in message for keyword in ["今天", "最近", "工作", "累", "忙"]):
        concern_reply = "最近辛苦了。再忙也要记得按时吃饭，别把自己逼得太紧。"
    elif any(keyword in message for keyword in ["生日", "节日", "清明", "中秋", "春节"]):
        concern_reply = "这些特别的日子里，我也会惦记着你。你能记得来和我说说话，我就很满足。"

    memory_reply = ""
    if memory_context:
        latest_memory = memory_context.split("\n")[-1].replace("- ", "").strip()
        if latest_memory:
            memory_reply = f" 你说这些的时候，我也想起了“{latest_memory}”。"

    phrase_prefix = f"{catchphrase} " if catchphrase else ""
    intensity_level = max(1, min(5, int(intensity))) if intensity is not None else 3
    if intensity_level <= 2:
        emotion_suffix = "我在这里，你慢慢说。" if emotion in {"sad", "missing"} else "我一直在听。"
    elif intensity_level >= 4:
        emotion_suffix = "我很想你，你能来找我，我就安心了。" if emotion in {"sad", "missing"} else "我会一直陪着你，不会让你一个人。"
    else:
        emotion_suffix = "你已经做得很好了。" if emotion in {"sad", "missing"} else "我会一直陪着你。"

    return (
        f"{phrase_prefix}{concern_reply}{memory_reply}"
        f" 作为你的{relationship}，我还是那个{style}的{name}。{emotion_suffix}"
    )


def resolve_timezone(tz_name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def normalize_phone_number(phone_number: str) -> str:
    value = "".join(ch for ch in str(phone_number or "").strip() if ch.isdigit() or ch == "+")
    return value[:24]


def parse_preferred_time(preferred_time: str) -> tuple[int, int]:
    raw = (preferred_time or "20:30").strip()
    try:
        hour_str, minute_str = raw.split(":", 1)
        hour = min(max(int(hour_str), 0), 23)
        minute = min(max(int(minute_str), 0), 59)
        return hour, minute
    except Exception:
        return 20, 30


def compute_next_run_at(
    cadence: str,
    preferred_time: str,
    preferred_weekday: Optional[int],
    tz_name: Optional[str],
    *,
    from_dt: Optional[datetime] = None,
) -> str:
    base = from_dt or now_utc()
    local_base = base.astimezone(resolve_timezone(tz_name))
    hour, minute = parse_preferred_time(preferred_time)
    candidate = local_base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if cadence == "weekly":
        target_weekday = preferred_weekday if preferred_weekday is not None else local_base.weekday()
        day_delta = (target_weekday - local_base.weekday()) % 7
        candidate = candidate + timedelta(days=day_delta)
        if candidate <= local_base:
            candidate += timedelta(days=7)
    else:
        if candidate <= local_base:
            candidate += timedelta(days=1)

    return candidate.astimezone(timezone.utc).isoformat()


def build_memory_context(conn: sqlite3.Connection, loved_one_id: str, limit: int = 6) -> str:
    fragment_rows = conn.execute(
        """
        SELECT content FROM digital_human_fragments
        WHERE loved_one_id = ? AND fragment_kind IN ('memory_anchor', 'trait_signal', 'voice_trait', 'visual_trait', 'motion_trait')
        ORDER BY weight DESC, created_at DESC
        LIMIT ?
        """,
        (loved_one_id, limit),
    ).fetchall()
    if fragment_rows:
        return "\n".join([f"- {row['content']}" for row in fragment_rows])
    rows = conn.execute(
        "SELECT content FROM memories WHERE loved_one_id = ? ORDER BY created_at DESC LIMIT ?",
        (loved_one_id, limit),
    ).fetchall()
    return "\n".join([f"- {row['content']}" for row in reversed(rows)])


def get_user_contact_snapshot(conn: sqlite3.Connection, user_id: str) -> dict:
    row = conn.execute(
        """
        SELECT phone_number, proactive_opt_in, preferred_contact_channel, preferred_contact_time, timezone
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        return {
            "phone_number": "",
            "proactive_opt_in": False,
            "preferred_contact_channel": "app",
            "preferred_contact_time": "20:30",
            "timezone": DEFAULT_TIMEZONE,
        }
    return {
        "phone_number": row["phone_number"] or "",
        "proactive_opt_in": bool(row["proactive_opt_in"]),
        "preferred_contact_channel": row["preferred_contact_channel"] or "app",
        "preferred_contact_time": row["preferred_contact_time"] or "20:30",
        "timezone": row["timezone"] or DEFAULT_TIMEZONE,
    }


def normalize_proactive_channel(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"app", "phone"}:
        return normalized
    return "app"


def normalize_proactive_message_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"text", "voice", "video"}:
        return normalized
    return "voice"


def ensure_default_proactive_flow(conn: sqlite3.Connection, user_id: str, loved_one_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM proactive_flows WHERE user_id = ? AND loved_one_id = ?",
        (user_id, loved_one_id),
    ).fetchone()
    if row is not None:
        return row

    contact = get_user_contact_snapshot(conn, user_id)
    created_at = now_iso()
    flow_id = str(uuid.uuid4())
    next_run_at = compute_next_run_at(
        cadence="daily",
        preferred_time=contact["preferred_contact_time"],
        preferred_weekday=None,
        tz_name=contact["timezone"],
    )
    conn.execute(
        """
        INSERT INTO proactive_flows (
            id, user_id, loved_one_id, enabled, cadence, preferred_time, preferred_weekday,
            preferred_channel, preferred_message_mode, phone_number, timezone, next_run_at, last_run_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            flow_id,
            user_id,
            loved_one_id,
            1,
            "daily",
            contact["preferred_contact_time"],
            None,
            normalize_proactive_channel(contact["preferred_contact_channel"]),
            "voice",
            contact["phone_number"] or None,
            contact["timezone"],
            next_run_at,
            None,
            created_at,
            created_at,
        ),
    )
    return conn.execute(
        "SELECT * FROM proactive_flows WHERE id = ?",
        (flow_id,),
    ).fetchone()


def get_proactive_flow_row(conn: sqlite3.Connection, user_id: str, loved_one_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM proactive_flows WHERE user_id = ? AND loved_one_id = ?",
        (user_id, loved_one_id),
    ).fetchone()
    return row or ensure_default_proactive_flow(conn, user_id, loved_one_id)


def serialize_proactive_flow(row: Optional[sqlite3.Row]) -> dict:
    if row is None:
        return {
            "enabled": True,
            "cadence": "daily",
            "preferred_time": "20:30",
            "preferred_weekday": None,
            "preferred_channel": "app",
            "preferred_message_mode": "voice",
            "phone_number": "",
            "timezone": DEFAULT_TIMEZONE,
            "next_run_at": None,
            "last_run_at": None,
        }
    return {
        "id": row["id"],
        "enabled": bool(row["enabled"]),
        "cadence": row["cadence"],
        "preferred_time": row["preferred_time"],
        "preferred_weekday": row["preferred_weekday"],
        "preferred_channel": normalize_proactive_channel(row["preferred_channel"]),
        "preferred_message_mode": normalize_proactive_message_mode(row["preferred_message_mode"]),
        "phone_number": row["phone_number"] or "",
        "timezone": row["timezone"] or DEFAULT_TIMEZONE,
        "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"],
    }


def proactive_event_media_refs(
    conn: sqlite3.Connection,
    audio_asset_id: Optional[str],
    video_asset_id: Optional[str],
) -> tuple[Optional[dict], Optional[dict]]:
    return get_media_asset_reference(conn, audio_asset_id), get_media_asset_reference(conn, video_asset_id)


def serialize_proactive_event(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    metadata = {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    audio_ref, video_ref = proactive_event_media_refs(conn, row["audio_asset_id"], row["video_asset_id"])
    return {
        "id": row["id"],
        "loved_one_id": row["loved_one_id"],
        "flow_id": row["flow_id"],
        "source_kind": row["source_kind"],
        "event_type": row["event_type"],
        "channel": row["channel"],
        "status": row["status"],
        "title": row["title"],
        "message_text": row["message_text"],
        "audio_asset_id": row["audio_asset_id"],
        "audio_kind": audio_ref["kind"] if audio_ref else None,
        "audio_url": audio_ref["url"] if audio_ref else None,
        "video_asset_id": row["video_asset_id"],
        "video_kind": video_ref["kind"] if video_ref else None,
        "video_url": video_ref["url"] if video_ref else None,
        "scheduled_for": row["scheduled_for"],
        "delivered_at": row["delivered_at"],
        "consumed_at": row["consumed_at"],
        "created_at": row["created_at"],
        "metadata": metadata,
    }


def choose_proactive_message_mode(
    preferred_message_mode: str,
    *,
    can_voice: bool,
    can_video: bool,
) -> tuple[str, Optional[str]]:
    requested = normalize_proactive_message_mode(preferred_message_mode)
    if requested == "video":
        if can_video:
            return "video", None
        if can_voice:
            return "voice", "当前视频素材或权限还不足以生成视频问候，已自动回退为语音消息。"
        return "text", "当前素材还不足以生成视频问候，已自动回退为文字消息。"
    if requested == "voice":
        if can_voice:
            return "voice", None
        return "text", "当前语音素材或权限还不足以生成语音问候，已自动回退为文字消息。"
    return "text", None


async def generate_proactive_message_with_mimo(
    loved_one: dict,
    *,
    reason: str,
    memory_context: str,
    mode: str,
) -> str:
    instruction = (
        f"{build_personality_prompt(loved_one)}\n\n"
        f"请主动联系用户，不要等待用户先开口。\n"
        f"触发原因：{reason}\n"
        f"互动模式：{mode}\n"
        f"相关记忆：\n{memory_context or '暂无'}\n\n"
        "请像亲人主动联系时那样开场，先问候、再自然带入熟悉的生活细节，控制在 80 到 140 字。"
    )

    payload = {
        "model": "mimo-v2-omni" if loved_one.get("identity_model_summary") else "mimo-v2-pro",
        "messages": [{"role": "user", "content": instruction}],
        "temperature": 0.85,
        "max_completion_tokens": 220,
    }
    result = await call_mimo_chat_completion(payload, timeout=60.0)
    return (result["choices"][0]["message"]["content"] or "").strip()


def build_proactive_fallback(loved_one: dict, reason: str, memory_context: str) -> str:
    name = loved_one["name"]
    catchphrase = (loved_one.get("personality_traits") or {}).get("catchphrase", "").strip()
    prefix = f"{catchphrase} " if catchphrase else ""
    memory_line = ""
    if memory_context:
        latest = memory_context.split("\n")[-1].replace("- ", "").strip()
        if latest:
            memory_line = f" 我刚刚又想起“{latest}”。"
    return f"{prefix}今天我想主动来找你说说话。{memory_line} {name}一直惦记着你，记得按时吃饭，也别把心事都一个人扛着。"


def create_proactive_event(
    conn: sqlite3.Connection,
    *,
    event_id: Optional[str] = None,
    user_id: str,
    loved_one_id: str,
    flow_id: Optional[str],
    source_kind: str,
    source_id: Optional[str],
    event_type: str,
    channel: str,
    status: str,
    title: str,
    message_text: str,
    audio_asset_id: Optional[str],
    video_asset_id: Optional[str],
    scheduled_for: Optional[str],
    metadata: Optional[dict] = None,
) -> str:
    event_id = event_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO proactive_events (
            id, user_id, loved_one_id, flow_id, source_kind, source_id, event_type, channel, status, title,
            message_text, audio_asset_id, video_asset_id, scheduled_for, delivered_at, consumed_at, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            user_id,
            loved_one_id,
            flow_id,
            source_kind,
            source_id,
            event_type,
            channel,
            status,
            title,
            message_text,
            audio_asset_id,
            video_asset_id,
            scheduled_for,
            now_iso() if status in {"ready", "provider_pending", "delivered"} else None,
            None,
            json.dumps(metadata or {}, ensure_ascii=False),
            now_iso(),
        ),
    )
    return event_id


def get_call_bridge_provider() -> str:
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER:
        return "twilio"
    if OUTBOUND_CALL_WEBHOOK_URL:
        return "webhook"
    return "none"


def get_call_bridge_secret() -> str:
    return TWILIO_AUTH_TOKEN or OUTBOUND_CALL_WEBHOOK_TOKEN or ""


def build_call_bridge_token(event_id: str) -> str:
    secret = get_call_bridge_secret()
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), event_id.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_call_bridge_token(event_id: str, token: Optional[str]) -> bool:
    expected = build_call_bridge_token(event_id)
    if not expected:
        return True
    return bool(token) and hmac.compare_digest(expected, str(token))


def build_call_bridge_url(path: str, event_id: str, **query: Any) -> str:
    base = (TWILIO_STATUS_CALLBACK_BASE_URL or APP_BASE_URL).rstrip("/")
    parts = [f"bridge_token={build_call_bridge_token(event_id)}"]
    for key, value in query.items():
        if value is None:
            continue
        parts.append(f"{quote_plus(str(key))}={quote_plus(str(value))}")
    separator = "&" if "?" in path else "?"
    return f"{base}{path}{separator}{'&'.join(parts)}"


def build_call_bridge_status(
    loved_one: Optional[dict] = None,
    proactive_flow: Optional[dict] = None,
    subscription: Optional[dict] = None,
) -> dict:
    provider = get_call_bridge_provider()
    provider_label = {
        "twilio": "Twilio 内建外呼",
        "webhook": "外部电话桥接 webhook",
        "none": "尚未配置电话桥接",
    }[provider]
    configured = provider != "none"
    flow = proactive_flow or {}
    blockers: List[str] = []
    preferred_channel = flow.get("preferred_channel", "app")
    phone_number = flow.get("phone_number") or ""
    wants_phone = preferred_channel == "phone"
    phone_number_configured = bool(phone_number)
    voice_ready = False
    digital_human_ready = False
    model = {}

    if loved_one:
        twin = loved_one.get("digital_twin_profile") or {}
        model = loved_one.get("digital_human_model") or {}
        features = (subscription or {}).get("features", {})
        voice_ready = "voice" in twin.get("available_modes", ["text"]) and bool(features.get("voice"))
        digital_human_ready = model.get("build_status") == "ready" and int(model.get("knowledge_count") or 0) > 0

    if wants_phone and not configured:
        blockers.append("还没有接通电话桥接服务")
    if wants_phone and not phone_number_configured:
        blockers.append("还没有填写接听手机号")
    if wants_phone and loved_one and not voice_ready:
        blockers.append("当前套餐或素材还不足以支撑语音外呼")
    if wants_phone and loved_one and not digital_human_ready:
        blockers.append("数字人模型还在搭建，先继续补记忆和声音素材")

    call_ready = wants_phone and configured and phone_number_configured
    if loved_one:
        call_ready = call_ready and voice_ready and digital_human_ready

    if call_ready:
        readiness_note = (
            "当前已经满足主动外呼条件，系统会直接以电话方式发起联系。"
            if provider == "twilio"
            else "当前已经满足主动外呼条件，系统会把任务交给外部电话桥接服务。"
        )
    elif wants_phone:
        readiness_note = "；".join(blockers) or "电话外呼仍在准备中。"
    else:
        readiness_note = "当前默认仍以站内主动问候为主，切到电话优先后会按外呼条件校验。"

    return {
        "provider": provider,
        "provider_label": provider_label,
        "configured": configured,
        "direct_call_enabled": provider == "twilio",
        "webhook_handoff_enabled": provider == "webhook",
        "preferred_channel": preferred_channel,
        "phone_number_configured": phone_number_configured,
        "voice_ready": voice_ready,
        "digital_human_ready": digital_human_ready,
        "call_ready": call_ready,
        "blockers": blockers,
        "readiness_note": readiness_note,
        "status_callback_base_url": (TWILIO_STATUS_CALLBACK_BASE_URL or APP_BASE_URL).rstrip("/"),
        "build_version": model.get("build_version") if model else None,
    }


def build_twiml_playback(message_text: str, audio_url: Optional[str], *, allow_follow_up: bool, action_url: Optional[str] = None) -> str:
    pieces = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<Response>"]
    if allow_follow_up and action_url:
        pieces.append(
            f"<Gather input=\"speech\" language=\"zh-CN\" speechTimeout=\"auto\" timeout=\"4\" action=\"{html.escape(action_url, quote=True)}\" method=\"POST\">"
        )
    if audio_url:
        pieces.append(f"<Play>{html.escape(audio_url)}</Play>")
    else:
        pieces.append(f"<Say language=\"zh-CN\">{html.escape(message_text)}</Say>")
    if allow_follow_up and action_url:
        pieces.append("<Pause length=\"1\"/>")
        pieces.append("<Say language=\"zh-CN\">你可以现在跟我说话，我会继续陪你聊。</Say>")
        pieces.append("</Gather>")
    pieces.append("</Response>")
    return "".join(pieces)


def build_twiml_closing(message_text: str, audio_url: Optional[str] = None) -> str:
    playback = build_twiml_playback(message_text, audio_url, allow_follow_up=False)
    return playback.replace("</Response>", "<Hangup/></Response>")


def place_twilio_outbound_call(
    event_id: str,
    phone_number: str,
    loved_one: dict,
    message_text: str,
    audio_url: Optional[str],
) -> tuple[str, dict]:
    connect_url = build_call_bridge_url(f"/api/bridge/twilio/connect/{event_id}", event_id, turn=0)
    status_url = build_call_bridge_url(f"/api/bridge/twilio/status/{event_id}", event_id)
    payload = [
        ("To", phone_number),
        ("From", TWILIO_FROM_NUMBER),
        ("Url", connect_url),
        ("Method", "POST"),
        ("StatusCallback", status_url),
        ("StatusCallbackMethod", "POST"),
        ("StatusCallbackEvent", "initiated"),
        ("StatusCallbackEvent", "ringing"),
        ("StatusCallbackEvent", "answered"),
        ("StatusCallbackEvent", "completed"),
    ]

    try:
        with httpx.Client(timeout=20.0, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)) as client:
            response = client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
                data=payload,
            )
            response.raise_for_status()
            data = response.json()
        return "provider_pending", {
            "provider": "twilio",
            "provider_note": f"已通过 Twilio 发起外呼，等待 {loved_one['name']} 接通电话。",
            "phone_number": phone_number,
            "call_sid": data.get("sid"),
            "call_status": data.get("status"),
            "audio_url": audio_url,
        }
    except Exception as exc:
        return "ready", {
            "provider": "twilio",
            "provider_note": f"Twilio 外呼失败：{exc}。已保留语音和通话脚本，可先在站内收听。",
            "phone_number": phone_number,
            "audio_url": audio_url,
        }


def dispatch_outbound_call(
    event_id: str,
    phone_number: str,
    loved_one: dict,
    message_text: str,
    audio_url: Optional[str],
) -> tuple[str, dict]:
    provider = get_call_bridge_provider()
    if provider == "twilio":
        return place_twilio_outbound_call(event_id, phone_number, loved_one, message_text, audio_url)

    if provider == "none":
        return "provider_pending", {
            "provider_note": "尚未配置外呼桥接服务，已生成来电脚本与语音素材，可先在站内收听。",
            "phone_number": phone_number,
        }

    payload = {
        "event_id": event_id,
        "phone_number": phone_number,
        "loved_one_name": loved_one["name"],
        "message_text": message_text,
        "audio_url": audio_url,
    }
    headers = {"Content-Type": "application/json"}
    if OUTBOUND_CALL_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {OUTBOUND_CALL_WEBHOOK_TOKEN}"

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(OUTBOUND_CALL_WEBHOOK_URL, headers=headers, json=payload)
            response.raise_for_status()
        return "provider_pending", {
            "provider": "webhook",
            "provider_note": "已把外呼任务交给电话桥接服务，等待运营商回呼结果。",
            "phone_number": phone_number,
        }
    except Exception as exc:
        return "ready", {
            "provider": "webhook",
            "provider_note": f"电话桥接调用失败：{exc}",
            "phone_number": phone_number,
        }


async def generate_proactive_payload(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    loved_one_id: str,
    reason: str,
    preferred_channel: str,
    preferred_message_mode: str,
    phone_number: str,
    source_kind: str,
    source_id: Optional[str],
    scheduled_for: Optional[str],
) -> Optional[dict]:
    loved_one_row = ensure_loved_one_owner(conn, user_id, loved_one_id)
    subscription = get_subscription_snapshot(conn, user_id)
    loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
    memory_context = build_memory_context(conn, loved_one_id)
    available_modes = loved_one.get("digital_twin_profile", {}).get("available_modes", ["text"])
    requested_channel = normalize_proactive_channel(preferred_channel)
    can_voice = "voice" in available_modes and subscription.get("features", {}).get("voice")
    can_video = "video" in available_modes and subscription.get("features", {}).get("video")
    wants_phone = requested_channel == "phone" and bool(phone_number)
    can_phone = wants_phone and can_voice
    actual_message_mode, mode_fallback_reason = choose_proactive_message_mode(
        preferred_message_mode,
        can_voice=can_voice,
        can_video=can_video,
    )
    generation_mode = "voice" if can_phone else actual_message_mode

    if GEMINI_API_KEY:
        try:
            message_text = await generate_proactive_message_with_mimo(
                loved_one,
                reason=reason,
                memory_context=memory_context,
                mode=generation_mode,
            )
        except Exception:
            message_text = build_proactive_fallback(loved_one, reason, memory_context)
    else:
        message_text = build_proactive_fallback(loved_one, reason, memory_context)

    audio_result = None
    if can_phone or actual_message_mode in {"voice", "video"}:
        try:
            audio_result = await synthesize_speech_with_mimo(
                conn=conn,
                user_id=user_id,
                loved_one_id=loved_one_id,
                text=message_text,
                emotion="missing",
            )
        except Exception:
            audio_result = None

    video_result = None
    synthesis_fallback_reason = None
    if actual_message_mode == "video":
        try:
            video_result = await synthesize_video_with_mimo(
                conn=conn,
                user_id=user_id,
                loved_one_id=loved_one_id,
                loved_one=loved_one,
                user_message=reason,
                ai_response=message_text,
                emotion="missing",
                memory_context=memory_context,
                request=None,
                audio_result=audio_result,
            )
        except Exception:
            video_result = None
        if video_result is None:
            if audio_result:
                actual_message_mode = "voice"
                synthesis_fallback_reason = "MIMO 视频短片合成暂时失败，已自动回退为语音消息。"
            else:
                actual_message_mode = "text"
                synthesis_fallback_reason = "MIMO 视频短片暂时未生成成功，已自动回退为文字消息。"

    if actual_message_mode == "voice" and not audio_result and not can_phone:
        actual_message_mode = "text"
        synthesis_fallback_reason = "MIMO 语音合成暂时失败，已自动回退为文字消息。"

    channel = "phone" if can_phone else "app"
    event_type = "voice_call" if can_phone else {
        "text": "message",
        "voice": "voice_message",
        "video": "video_message",
    }[actual_message_mode]

    fallback_reasons: List[str] = []
    if wants_phone and not can_phone:
        fallback_reasons.append("当前套餐或数字人素材还不足以发起语音外呼，已自动回退为站内主动问候。")
    if mode_fallback_reason:
        fallback_reasons.append(mode_fallback_reason)
    if synthesis_fallback_reason:
        fallback_reasons.append(synthesis_fallback_reason)

    return {
        "loved_one": loved_one,
        "message_text": message_text,
        "channel": channel,
        "event_type": event_type,
        "preferred_message_mode": normalize_proactive_message_mode(preferred_message_mode),
        "actual_message_mode": "voice" if can_phone else actual_message_mode,
        "audio_asset_id": audio_result["asset_id"] if audio_result else None,
        "audio_url": audio_result["url"] if audio_result else None,
        "video_asset_id": video_result["asset_id"] if video_result else None,
        "video_url": video_result["url"] if video_result else None,
        "reason": reason,
        "source_kind": source_kind,
        "source_id": source_id,
        "scheduled_for": scheduled_for,
        "phone_number": phone_number,
        "fallback_reason": "；".join(fallback_reasons) if fallback_reasons else None,
    }


def generate_proactive_payload_sync(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    loved_one_id: str,
    reason: str,
    preferred_channel: str,
    preferred_message_mode: str,
    phone_number: str,
    source_kind: str,
    source_id: Optional[str],
    scheduled_for: Optional[str],
) -> Optional[dict]:
    return asyncio.run(
        generate_proactive_payload(
            conn,
            user_id=user_id,
            loved_one_id=loved_one_id,
            reason=reason,
            preferred_channel=preferred_channel,
            preferred_message_mode=preferred_message_mode,
            phone_number=phone_number,
            source_kind=source_kind,
            source_id=source_id,
            scheduled_for=scheduled_for,
        )
    )


async def generate_phone_followup_response(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    loved_one_id: str,
    user_message: str,
) -> tuple[dict, str, Optional[dict]]:
    loved_one_row = ensure_loved_one_owner(conn, user_id, loved_one_id)
    subscription = get_subscription_snapshot(conn, user_id)
    loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
    memory_context = build_memory_context(conn, loved_one_id)

    if GEMINI_API_KEY:
        try:
            ai_response = await generate_text_response_with_mimo(
                loved_one=loved_one,
                user_message=user_message,
                emotion=None,
                memory_context=memory_context,
                request=None,
                mode="voice",
            )
        except Exception:
            ai_response = build_fallback_response(
                loved_one=loved_one,
                user_message=user_message,
                emotion="missing",
                memory_context=memory_context,
            )
    else:
        ai_response = build_fallback_response(
            loved_one=loved_one,
            user_message=user_message,
            emotion="missing",
            memory_context=memory_context,
        )

    audio_result = await synthesize_speech_with_mimo(
        conn=conn,
        user_id=user_id,
        loved_one_id=loved_one_id,
        text=ai_response,
        emotion="missing",
    )
    return loved_one, ai_response, audio_result


def persist_phone_turn(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    loved_one_id: str,
    user_message: str,
    ai_response: str,
    response_audio_asset_id: Optional[str],
):
    conn.execute(
        """
        INSERT INTO chat_messages (
            id, user_id, loved_one_id, user_message, ai_response, emotion, mode,
            response_audio_asset_id, response_video_asset_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            loved_one_id,
            user_message,
            ai_response,
            "missing",
            "voice",
            response_audio_asset_id,
            None,
            now_iso(),
        ),
    )
    conn.execute("UPDATE loved_ones SET updated_at = ? WHERE id = ?", (now_iso(), loved_one_id))


def update_proactive_event_metadata(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    status: Optional[str] = None,
    delivered: bool = False,
    metadata_updates: Optional[dict] = None,
):
    row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return
    metadata = json.loads(row["metadata_json"] or "{}")
    metadata.update(metadata_updates or {})
    conn.execute(
        """
        UPDATE proactive_events
        SET status = COALESCE(?, status),
            delivered_at = CASE WHEN ? THEN COALESCE(delivered_at, ?) ELSE delivered_at END,
            metadata_json = ?
        WHERE id = ?
        """,
        (
            status,
            int(delivered),
            now_iso(),
            json.dumps(metadata, ensure_ascii=False),
            event_id,
        ),
    )


def process_due_proactive_flows():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM proactive_flows
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC
            LIMIT 10
            """,
            (now_iso(),),
        ).fetchall()

        for row in rows:
            payload = generate_proactive_payload_sync(
                conn,
                user_id=row["user_id"],
                loved_one_id=row["loved_one_id"],
                reason="按照设定的主动联系节奏，像亲人那样主动来问候一下。",
                preferred_channel=row["preferred_channel"],
                preferred_message_mode=row["preferred_message_mode"],
                phone_number=row["phone_number"] or "",
                source_kind="flow",
                source_id=row["id"],
                scheduled_for=row["next_run_at"],
            )
            if not payload:
                continue

            status = "ready"
            metadata = {
                "reason": payload["reason"],
                "requested_message_mode": payload["preferred_message_mode"],
                "actual_message_mode": payload["actual_message_mode"],
            }
            if payload.get("fallback_reason"):
                metadata["provider_note"] = payload["fallback_reason"]
            event_id = str(uuid.uuid4())
            if payload["channel"] == "phone":
                status, provider_meta = dispatch_outbound_call(
                    event_id=event_id,
                    phone_number=payload["phone_number"],
                    loved_one=payload["loved_one"],
                    message_text=payload["message_text"],
                    audio_url=payload["audio_url"],
                )
                metadata.update(provider_meta)

            create_proactive_event(
                conn,
                event_id=event_id,
                user_id=row["user_id"],
                loved_one_id=row["loved_one_id"],
                flow_id=row["id"],
                source_kind="flow",
                source_id=row["id"],
                event_type=payload["event_type"],
                channel=payload["channel"],
                status=status,
                title=f"{payload['loved_one']['name']} 主动联系了你",
                message_text=payload["message_text"],
                audio_asset_id=payload["audio_asset_id"],
                video_asset_id=payload["video_asset_id"],
                scheduled_for=row["next_run_at"],
                metadata=metadata,
            )

            next_run_at = compute_next_run_at(
                row["cadence"],
                row["preferred_time"],
                row["preferred_weekday"],
                row["timezone"],
                from_dt=now_utc(),
            )
            conn.execute(
                "UPDATE proactive_flows SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                (now_iso(), next_run_at, now_iso(), row["id"]),
            )


def process_due_greetings():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM greetings
            WHERE status = 'scheduled' AND trigger_date <= ?
            ORDER BY trigger_date ASC
            LIMIT 10
            """,
            (now_iso(),),
        ).fetchall()

        for row in rows:
            flow_row = get_proactive_flow_row(conn, row["user_id"], row["loved_one_id"])
            payload = generate_proactive_payload_sync(
                conn,
                user_id=row["user_id"],
                loved_one_id=row["loved_one_id"],
                reason=row["message_template"] or "这是一个特别的日子，想主动给你打个招呼。",
                preferred_channel=flow_row["preferred_channel"],
                preferred_message_mode=flow_row["preferred_message_mode"],
                phone_number=flow_row["phone_number"] or "",
                source_kind="greeting",
                source_id=row["id"],
                scheduled_for=row["trigger_date"],
            )
            if not payload:
                continue
            metadata = {
                "greeting_type": row["greeting_type"],
                "reason": payload["reason"],
                "requested_message_mode": payload["preferred_message_mode"],
                "actual_message_mode": payload["actual_message_mode"],
            }
            if payload.get("fallback_reason"):
                metadata["provider_note"] = payload["fallback_reason"]
            status = "ready"
            event_id = str(uuid.uuid4())
            if payload["channel"] == "phone":
                status, provider_meta = dispatch_outbound_call(
                    event_id=event_id,
                    phone_number=payload["phone_number"],
                    loved_one=payload["loved_one"],
                    message_text=payload["message_text"],
                    audio_url=payload["audio_url"],
                )
                metadata.update(provider_meta)

            create_proactive_event(
                conn,
                event_id=event_id,
                user_id=row["user_id"],
                loved_one_id=row["loved_one_id"],
                flow_id=flow_row["id"],
                source_kind="greeting",
                source_id=row["id"],
                event_type=payload["event_type"],
                channel=payload["channel"],
                status=status,
                title=f"{payload['loved_one']['name']} 主动联系了你",
                message_text=payload["message_text"],
                audio_asset_id=payload["audio_asset_id"],
                video_asset_id=payload["video_asset_id"],
                scheduled_for=row["trigger_date"],
                metadata=metadata,
            )
            conn.execute("UPDATE greetings SET status = 'completed' WHERE id = ?", (row["id"],))


def proactive_worker_loop():
    while True:
        try:
            process_due_greetings()
            process_due_proactive_flows()
        except Exception:
            pass
        time.sleep(PROACTIVE_POLL_SECONDS)


# ===== API =====


@app.get("/health")
async def health():
    bridge = build_call_bridge_status()
    return {
        "status": "healthy",
        "service": "Eterna",
        "version": "2.0.0",
        "timestamp": now_iso(),
        "database": str(DB_PATH),
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "ffmpeg_configured": bool(FFMPEG_BIN),
        "video_pipeline_ready": bool(GEMINI_API_KEY and FFMPEG_BIN),
        "call_bridge_provider": bridge["provider"],
        "call_bridge_configured": bridge["configured"],
    }


@app.get("/")
async def index():
    if FRONTEND_FILE.exists():
        return FileResponse(FRONTEND_FILE)
    raise HTTPException(status_code=404, detail="Frontend page not found")


@app.get("/app")
@app.get("/app.html")
async def try_app():
    app_file = BASE_DIR / "frontend" / "app.html"
    if app_file.exists():
        return FileResponse(app_file)
    raise HTTPException(status_code=404, detail="Application page not found")


@app.get("/api/plans")
async def list_plans(authorization: Optional[str] = Header(default=None)):
    current_user = get_optional_user_from_authorization(authorization)
    with get_db() as conn:
        plans = [build_plan_view(dict(row)) for row in conn.execute("SELECT * FROM plans WHERE code != 'trial' ORDER BY price_cny")]
        current_subscription = get_subscription_snapshot(conn, current_user["id"]) if current_user else None
    return {
        "plans": plans,
        "current_plan_code": current_subscription["plan_code"] if current_subscription else None,
    }


@app.post("/api/demo/extract-reference")
async def demo_extract_reference(file: UploadFile = File(...)):
    filename = file.filename or "reference"
    temp_path = safe_upload_path("demo_reference", "shared", filename)
    content = await file.read()
    temp_path.write_bytes(content)
    mime_type = file.content_type or infer_mime_type(temp_path, "application/octet-stream")
    extracted_text = extract_reference_text(temp_path, mime_type)
    cleanup_path(str(temp_path))
    kind = (
        "document" if extracted_text else
        "image" if mime_type.startswith("image/") else
        "audio" if mime_type.startswith("audio/") else
        "video" if mime_type.startswith("video/") else
        "file"
    )
    return {
        "name": filename,
        "kind": kind,
        "mime_type": mime_type,
        "extracted_text": extracted_text[:12000],
    }


@app.post("/api/demo/chat", response_model=DemoChatResponse)
async def demo_chat(payload: DemoChatPayload):
    return await generate_demo_chat_response(payload)


@app.post("/api/auth/register", response_model=AuthEnvelope)
async def register(payload: RegisterPayload):
    email = normalize_email(payload.email)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Display name is required.")

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="This email is already registered.")

        timestamp = now_iso()
        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, phone_number, proactive_opt_in,
                preferred_contact_channel, preferred_contact_time, timezone, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                email,
                hash_password(payload.password),
                display_name,
                "",
                0,
                "app",
                "20:30",
                DEFAULT_TIMEZONE,
                timestamp,
                timestamp,
            ),
        )
        sync_admin_flag(conn, user_id, email)
        create_trial_subscription(conn, user_id)
        import_legacy_json_data(conn, user_id)
        session = create_session(conn, user_id)
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        subscription = get_subscription_snapshot(conn, user_id)
        stats = get_user_stats(conn, user_id)

    return AuthEnvelope(
        token=session["token"],
        user=UserSummary(**serialize_user(user_row)),
        subscription=SubscriptionSnapshot(**subscription),
        stats=stats,
    )


@app.post("/api/auth/login", response_model=AuthEnvelope)
async def login(payload: LoginPayload):
    email = normalize_email(payload.email)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        sync_admin_flag(conn, row["id"], row["email"])
        session = create_session(conn, row["id"])
        subscription = get_subscription_snapshot(conn, row["id"])
        stats = get_user_stats(conn, row["id"])

    return AuthEnvelope(
        token=session["token"],
        user=UserSummary(**serialize_user(row)),
        subscription=SubscriptionSnapshot(**subscription),
        stats=stats,
    )


@app.get("/api/auth/me", response_model=AuthEnvelope)
async def get_me(current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        sync_admin_flag(conn, current_user["id"], current_user["email"])
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        subscription = get_subscription_snapshot(conn, current_user["id"])
        stats = get_user_stats(conn, current_user["id"])
    return AuthEnvelope(
        user=UserSummary(**serialize_user(user_row)),
        subscription=SubscriptionSnapshot(**subscription),
        stats=stats,
    )


@app.get("/api/admin/overview")
async def admin_overview(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_loved = conn.execute("SELECT COUNT(*) FROM loved_ones").fetchone()[0]
        total_media = conn.execute("SELECT COUNT(*) FROM media_assets WHERE kind IN ('voice', 'photo', 'video', 'model3d')").fetchone()[0]
        total_messages = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        total_proactive = conn.execute("SELECT COUNT(*) FROM proactive_events").fetchone()[0]
        media_breakdown = {
            row["kind"]: row["count"]
            for row in conn.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM media_assets
                WHERE kind IN ('voice', 'photo', 'video', 'model3d')
                GROUP BY kind
                """
            ).fetchall()
        }
    return {
        "total_users": total_users,
        "total_loved_ones": total_loved,
        "total_media_assets": total_media,
        "total_messages": total_messages,
        "total_proactive_events": total_proactive,
        "media_breakdown": media_breakdown,
    }


@app.get("/api/admin/users")
async def admin_list_users(limit: int = 50, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    limit = max(1, min(200, int(limit)))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT u.*, (
                SELECT COUNT(*) FROM loved_ones l WHERE l.user_id = u.id
            ) AS loved_one_count, (
                SELECT COUNT(*) FROM media_assets m WHERE m.user_id = u.id AND m.kind IN ('voice', 'photo', 'video', 'model3d')
            ) AS media_count, (
                SELECT COUNT(*) FROM chat_messages c WHERE c.user_id = u.id
            ) AS message_count
            FROM users u
            ORDER BY u.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            **serialize_user(row),
            "loved_one_count": row["loved_one_count"],
            "media_count": row["media_count"],
            "message_count": row["message_count"],
        }
        for row in rows
    ]


@app.get("/api/admin/loved-ones")
async def admin_list_loved_ones(limit: int = 50, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    limit = max(1, min(200, int(limit)))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.*, u.email AS owner_email, (
                SELECT COUNT(*) FROM media_assets m WHERE m.loved_one_id = l.id AND m.kind IN ('voice', 'photo', 'video', 'model3d')
            ) AS media_count, (
                SELECT COUNT(*) FROM memories mem WHERE mem.loved_one_id = l.id
            ) AS memory_count
            FROM loved_ones l
            JOIN users u ON u.id = l.user_id
            ORDER BY l.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "relationship": row["relationship"],
            "owner_email": row["owner_email"],
            "media_count": row["media_count"],
            "memory_count": row["memory_count"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


@app.post("/api/auth/logout")
async def logout(current_user: dict = Depends(get_current_user), authorization: Optional[str] = Header(default=None)):
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=400, detail="无有效会话")
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
    return {"status": "logged_out"}


@app.get("/api/proactive/feed")
async def get_proactive_feed(limit: int = 20, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM proactive_events
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (current_user["id"], limit),
        ).fetchall()
        unread_count = conn.execute(
            "SELECT COUNT(*) FROM proactive_events WHERE user_id = ? AND consumed_at IS NULL",
            (current_user["id"],),
        ).fetchone()[0]
        flows = conn.execute(
            """
            SELECT pf.*, lo.name AS loved_one_name
            FROM proactive_flows pf
            JOIN loved_ones lo ON lo.id = pf.loved_one_id
            WHERE pf.user_id = ?
            ORDER BY lo.updated_at DESC
            """,
            (current_user["id"],),
        ).fetchall()
    return {
        "events": [serialize_proactive_event(conn, row) for row in rows],
        "unread_count": unread_count,
        "flows": [
            {
                **serialize_proactive_flow(row),
                "loved_one_id": row["loved_one_id"],
                "loved_one_name": row["loved_one_name"],
            }
            for row in flows
        ],
    }


@app.get("/api/proactive/settings/{loved_one_id}")
async def get_proactive_settings(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        flow = get_proactive_flow_row(conn, current_user["id"], loved_one_id)
        contact = get_user_contact_snapshot(conn, current_user["id"])
        subscription = get_subscription_snapshot(conn, current_user["id"])
        loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
        bridge_status = build_call_bridge_status(loved_one=loved_one, proactive_flow=serialize_proactive_flow(flow), subscription=subscription)
    return {
        "loved_one_id": loved_one_id,
        "flow": serialize_proactive_flow(flow),
        "contact": contact,
        "call_bridge_configured": bridge_status["configured"],
        "call_bridge": bridge_status,
    }


@app.post("/api/proactive/settings/{loved_one_id}")
async def save_proactive_settings(
    loved_one_id: str,
    payload: ProactiveSettingsPayload,
    current_user: dict = Depends(get_current_user),
):
    if payload.loved_one_id != loved_one_id:
        raise HTTPException(status_code=400, detail="档案标识不一致")
    cadence = payload.cadence if payload.cadence in {"daily", "weekly"} else "daily"
    preferred_channel = normalize_proactive_channel(payload.preferred_channel)
    preferred_message_mode = normalize_proactive_message_mode(payload.preferred_message_mode)
    preferred_weekday = payload.preferred_weekday if cadence == "weekly" else None
    phone_number = normalize_phone_number(payload.phone_number)

    with get_db() as conn:
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        flow = get_proactive_flow_row(conn, current_user["id"], loved_one_id)
        next_run_at = (
            compute_next_run_at(cadence, payload.preferred_time, preferred_weekday, payload.timezone)
            if payload.enabled
            else None
        )
        conn.execute(
            """
            UPDATE proactive_flows
            SET enabled = ?, cadence = ?, preferred_time = ?, preferred_weekday = ?, preferred_channel = ?,
                preferred_message_mode = ?, phone_number = ?, timezone = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                int(payload.enabled),
                cadence,
                payload.preferred_time,
                preferred_weekday,
                preferred_channel,
                preferred_message_mode,
                phone_number or None,
                payload.timezone or DEFAULT_TIMEZONE,
                next_run_at,
                now_iso(),
                flow["id"],
            ),
        )
        conn.execute(
            """
            UPDATE users
            SET phone_number = ?, proactive_opt_in = ?, preferred_contact_channel = ?, preferred_contact_time = ?,
                timezone = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                phone_number or None,
                int(payload.enabled),
                preferred_channel,
                payload.preferred_time,
                payload.timezone or DEFAULT_TIMEZONE,
                now_iso(),
                current_user["id"],
            ),
        )
        saved = get_proactive_flow_row(conn, current_user["id"], loved_one_id)
        contact = get_user_contact_snapshot(conn, current_user["id"])
        subscription = get_subscription_snapshot(conn, current_user["id"])
        loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
        bridge_status = build_call_bridge_status(
            loved_one=loved_one,
            proactive_flow=serialize_proactive_flow(saved),
            subscription=subscription,
        )
    return {
        "status": "saved",
        "loved_one_id": loved_one_id,
        "flow": serialize_proactive_flow(saved),
        "contact": contact,
        "call_bridge_configured": bridge_status["configured"],
        "call_bridge": bridge_status,
    }


@app.post("/api/proactive/trigger-now/{loved_one_id}")
async def trigger_proactive_now(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        flow = get_proactive_flow_row(conn, current_user["id"], loved_one_id)
        payload = await generate_proactive_payload(
            conn,
            user_id=current_user["id"],
            loved_one_id=loved_one_id,
            reason="用户希望现在就收到一次主动联系。",
            preferred_channel=flow["preferred_channel"],
            preferred_message_mode=flow["preferred_message_mode"],
            phone_number=flow["phone_number"] or "",
            source_kind="manual",
            source_id=flow["id"],
            scheduled_for=now_iso(),
        )
        if not payload:
            raise HTTPException(status_code=500, detail="主动联系生成失败")
        event_id = str(uuid.uuid4())
        metadata = {
            "reason": payload["reason"],
            "manual": True,
            "requested_message_mode": payload["preferred_message_mode"],
            "actual_message_mode": payload["actual_message_mode"],
        }
        if payload.get("fallback_reason"):
            metadata["provider_note"] = payload["fallback_reason"]
        status = "ready"
        if payload["channel"] == "phone":
            status, provider_meta = dispatch_outbound_call(
                event_id=event_id,
                phone_number=payload["phone_number"],
                loved_one=payload["loved_one"],
                message_text=payload["message_text"],
                audio_url=payload["audio_url"],
            )
            metadata.update(provider_meta)
        create_proactive_event(
            conn,
            event_id=event_id,
            user_id=current_user["id"],
            loved_one_id=loved_one_id,
            flow_id=flow["id"],
            source_kind="manual",
            source_id=flow["id"],
            event_type=payload["event_type"],
            channel=payload["channel"],
            status=status,
            title=f"{payload['loved_one']['name']} 主动联系了你",
            message_text=payload["message_text"],
            audio_asset_id=payload["audio_asset_id"],
            video_asset_id=payload["video_asset_id"],
            scheduled_for=now_iso(),
            metadata=metadata,
        )
        row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
    return {"status": "created", "event": serialize_proactive_event(conn, row)}


@app.post("/api/proactive/test-call/{loved_one_id}")
async def trigger_proactive_test_call(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        flow = get_proactive_flow_row(conn, current_user["id"], loved_one_id)
        if not flow["phone_number"]:
            raise HTTPException(status_code=400, detail="请先填写接听手机号")
        payload = await generate_proactive_payload(
            conn,
            user_id=current_user["id"],
            loved_one_id=loved_one_id,
            reason="测试电话外呼",
            preferred_channel="phone",
            preferred_message_mode="voice",
            phone_number=flow["phone_number"],
            source_kind="test_call",
            source_id=flow["id"],
            scheduled_for=now_iso(),
        )
        if not payload:
            raise HTTPException(status_code=500, detail="测试外呼生成失败")
        event_id = str(uuid.uuid4())
        metadata = {
            "reason": payload["reason"],
            "test_call": True,
            "requested_message_mode": payload["preferred_message_mode"],
            "actual_message_mode": payload["actual_message_mode"],
        }
        status, provider_meta = dispatch_outbound_call(
            event_id=event_id,
            phone_number=payload["phone_number"],
            loved_one=payload["loved_one"],
            message_text=payload["message_text"],
            audio_url=payload["audio_url"],
        )
        metadata.update(provider_meta)
        create_proactive_event(
            conn,
            event_id=event_id,
            user_id=current_user["id"],
            loved_one_id=loved_one_id,
            flow_id=flow["id"],
            source_kind="test_call",
            source_id=flow["id"],
            event_type=payload["event_type"],
            channel="phone",
            status=status,
            title=f"{payload['loved_one']['name']} 测试外呼",
            message_text=payload["message_text"],
            audio_asset_id=payload["audio_asset_id"],
            video_asset_id=payload["video_asset_id"],
            scheduled_for=now_iso(),
            metadata=metadata,
        )
        row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
    return {"status": "created", "event": serialize_proactive_event(conn, row)}


@app.post("/api/proactive/events/{event_id}/consume")
async def consume_proactive_event(event_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM proactive_events WHERE id = ? AND user_id = ?",
            (event_id, current_user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="主动联系事件未找到")
        conn.execute(
            "UPDATE proactive_events SET consumed_at = COALESCE(consumed_at, ?), status = CASE WHEN status = 'ready' THEN 'completed' ELSE status END WHERE id = ?",
            (now_iso(), event_id),
        )
        updated = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
    return {"status": "consumed", "event": serialize_proactive_event(conn, updated)}


@app.post("/api/proactive/events/{event_id}/delivery")
async def update_proactive_delivery(event_id: str, request: Request):
    if not OUTBOUND_CALL_WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="当前未配置外呼回调令牌")
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {OUTBOUND_CALL_WEBHOOK_TOKEN}":
        raise HTTPException(status_code=403, detail="外呼回调校验失败")
    payload = await request.json()
    status = payload.get("status", "delivered")
    metadata = payload.get("metadata") or {}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="主动联系事件未找到")
        current_meta = json.loads(row["metadata_json"] or "{}")
        current_meta.update(metadata)
        conn.execute(
            "UPDATE proactive_events SET status = ?, delivered_at = COALESCE(delivered_at, ?), metadata_json = ? WHERE id = ?",
            (status, now_iso(), json.dumps(current_meta, ensure_ascii=False), event_id),
        )
    return {"status": "ok"}


@app.get("/api/bridge/status")
async def get_call_bridge_status_endpoint(
    loved_one_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        if not loved_one_id:
            return build_call_bridge_status()
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
        proactive_flow = serialize_proactive_flow(get_proactive_flow_row(conn, current_user["id"], loved_one_id))
        return build_call_bridge_status(loved_one=loved_one, proactive_flow=proactive_flow, subscription=subscription)


@app.api_route("/api/bridge/twilio/connect/{event_id}", methods=["GET", "POST"])
async def twilio_call_connect(event_id: str, request: Request):
    bridge_token = request.query_params.get("bridge_token")
    if not verify_call_bridge_token(event_id, bridge_token):
        raise HTTPException(status_code=403, detail="电话桥接校验失败")

    with get_db() as conn:
        event_row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
        if event_row is None:
            raise HTTPException(status_code=404, detail="主动联系事件未找到")
        loved_one_row = conn.execute("SELECT * FROM loved_ones WHERE id = ?", (event_row["loved_one_id"],)).fetchone()
        if loved_one_row is None:
            raise HTTPException(status_code=404, detail="亲人档案未找到")
        audio_ref = get_media_asset_reference(conn, event_row["audio_asset_id"])
        audio_url = audio_ref["url"] if audio_ref else None
        form = await request.form() if request.method == "POST" else {}
        update_proactive_event_metadata(
            conn,
            event_id,
            status="delivered",
            delivered=True,
            metadata_updates={
                "provider": "twilio",
                "call_sid": str(form.get("CallSid") or ""),
                "call_status": str(form.get("CallStatus") or "answered"),
            },
        )

    action_url = build_call_bridge_url(f"/api/bridge/twilio/respond/{event_id}", event_id, turn=0)
    no_input_url = build_call_bridge_url(f"/api/bridge/twilio/no-input/{event_id}", event_id, turn=0)
    twiml = build_twiml_playback(
        str(event_row["message_text"] or ""),
        audio_url,
        allow_follow_up=True,
        action_url=action_url,
    ).replace(
        "</Response>",
        f"<Redirect method=\"POST\">{html.escape(no_input_url)}</Redirect></Response>",
    )
    return Response(content=twiml, media_type="text/xml")


@app.api_route("/api/bridge/twilio/respond/{event_id}", methods=["POST"])
async def twilio_call_respond(event_id: str, request: Request):
    bridge_token = request.query_params.get("bridge_token")
    if not verify_call_bridge_token(event_id, bridge_token):
        raise HTTPException(status_code=403, detail="电话桥接校验失败")

    turn = max(0, int(request.query_params.get("turn", "0") or 0))
    form = await request.form()
    transcript = str(form.get("SpeechResult") or form.get("Digits") or "").strip()
    if not transcript:
        no_input_url = build_call_bridge_url(f"/api/bridge/twilio/no-input/{event_id}", event_id, turn=turn)
        return Response(
            content=f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Redirect method=\"POST\">{html.escape(no_input_url)}</Redirect></Response>",
            media_type="text/xml",
        )

    with get_db() as conn:
        event_row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
        if event_row is None:
            raise HTTPException(status_code=404, detail="主动联系事件未找到")
        loved_one, ai_response, audio_result = await generate_phone_followup_response(
            conn,
            user_id=event_row["user_id"],
            loved_one_id=event_row["loved_one_id"],
            user_message=transcript,
        )
        persist_phone_turn(
            conn,
            user_id=event_row["user_id"],
            loved_one_id=event_row["loved_one_id"],
            user_message=transcript,
            ai_response=ai_response,
            response_audio_asset_id=audio_result["asset_id"] if audio_result else None,
        )
        update_proactive_event_metadata(
            conn,
            event_id,
            status="delivered",
            delivered=True,
            metadata_updates={
                "provider": "twilio",
                "call_sid": str(form.get("CallSid") or ""),
                "call_status": str(form.get("CallStatus") or "in-progress"),
                "last_user_utterance": transcript,
                "last_ai_reply": ai_response,
                "turn_count": turn + 1,
                "loved_one_name": loved_one["name"],
            },
        )

    if turn + 1 >= PHONE_CALL_MAX_TURNS:
        closing_text = f"{ai_response} 我先不打扰你了，想我的时候再和我说。"
        return Response(
            content=build_twiml_closing(closing_text, audio_result["url"] if audio_result else None),
            media_type="text/xml",
        )

    next_turn = turn + 1
    action_url = build_call_bridge_url(f"/api/bridge/twilio/respond/{event_id}", event_id, turn=next_turn)
    no_input_url = build_call_bridge_url(f"/api/bridge/twilio/no-input/{event_id}", event_id, turn=next_turn)
    twiml = build_twiml_playback(
        ai_response,
        audio_result["url"] if audio_result else None,
        allow_follow_up=True,
        action_url=action_url,
    ).replace(
        "</Response>",
        f"<Redirect method=\"POST\">{html.escape(no_input_url)}</Redirect></Response>",
    )
    return Response(content=twiml, media_type="text/xml")


@app.api_route("/api/bridge/twilio/no-input/{event_id}", methods=["GET", "POST"])
async def twilio_call_no_input(event_id: str, request: Request):
    bridge_token = request.query_params.get("bridge_token")
    if not verify_call_bridge_token(event_id, bridge_token):
        raise HTTPException(status_code=403, detail="电话桥接校验失败")

    with get_db() as conn:
        event_row = conn.execute("SELECT * FROM proactive_events WHERE id = ?", (event_id,)).fetchone()
        if event_row is None:
            raise HTTPException(status_code=404, detail="主动联系事件未找到")
        loved_one_row = conn.execute("SELECT name FROM loved_ones WHERE id = ?", (event_row["loved_one_id"],)).fetchone()
        name = loved_one_row["name"] if loved_one_row else "ta"
        update_proactive_event_metadata(
            conn,
            event_id,
            status="completed",
            delivered=True,
            metadata_updates={
                "provider": "twilio",
                "call_status": "completed",
                "provider_note": "电话已结束；本次外呼未继续收到用户语音输入。",
            },
        )

    closing_text = f"我是{name}。我先不打扰你了，想我的时候再来和我说说话。"
    return Response(content=build_twiml_closing(closing_text), media_type="text/xml")


@app.api_route("/api/bridge/twilio/status/{event_id}", methods=["POST"])
async def twilio_call_status(event_id: str, request: Request):
    bridge_token = request.query_params.get("bridge_token")
    if not verify_call_bridge_token(event_id, bridge_token):
        raise HTTPException(status_code=403, detail="电话桥接校验失败")

    form = await request.form()
    call_status = str(form.get("CallStatus") or "").strip().lower()
    mapped_status = {
        "queued": "provider_pending",
        "initiated": "provider_pending",
        "ringing": "provider_pending",
        "in-progress": "delivered",
        "answered": "delivered",
        "completed": "completed",
        "busy": "failed",
        "failed": "failed",
        "no-answer": "failed",
        "canceled": "failed",
    }.get(call_status, "provider_pending")

    with get_db() as conn:
        update_proactive_event_metadata(
            conn,
            event_id,
            status=mapped_status,
            delivered=call_status in {"in-progress", "answered", "completed"},
            metadata_updates={
                "provider": "twilio",
                "call_sid": str(form.get("CallSid") or ""),
                "call_status": call_status,
                "call_duration": str(form.get("CallDuration") or ""),
                "answered_by": str(form.get("AnsweredBy") or ""),
            },
        )
    return {"status": "ok"}


@app.post("/api/loved-ones", response_model=LovedOne)
async def create_loved_one(loved_one: LovedOne, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        subscription = get_subscription_snapshot(conn, current_user["id"])
        assert_loved_one_limit(conn, current_user["id"], subscription)

        loved_one_id = str(uuid.uuid4())
        timestamp = now_iso()
        personality_traits = loved_one.personality_traits or {}
        initial_memories = unique_preserve_order(loved_one.memories)
        conn.execute(
            """
            INSERT INTO loved_ones (
                id, user_id, name, relationship, birth_date, pass_away_date, cover_title, cover_photo_asset_id,
                personality_traits_json, speaking_style, identity_model_summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                loved_one_id,
                current_user["id"],
                loved_one.name.strip(),
                loved_one.relationship.strip() or "亲人",
                loved_one.birth_date,
                loved_one.pass_away_date,
                loved_one.cover_title.strip() if loved_one.cover_title else "",
                None,
                json.dumps(personality_traits, ensure_ascii=False),
                loved_one.speaking_style.strip() or (personality_traits.get("catchphrase") or "温柔亲切"),
                "",
                timestamp,
                timestamp,
            ),
        )

        for content in initial_memories:
            conn.execute(
                """
                INSERT INTO memories (
                    id, user_id, loved_one_id, content, memory_type, memory_date, importance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    current_user["id"],
                    loved_one_id,
                    content,
                    "conversation",
                    None,
                    7,
                    timestamp,
                ),
            )

        refresh_identity_model_summary(conn, loved_one_id, trigger_source="create")
        ensure_default_proactive_flow(conn, current_user["id"], loved_one_id)
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        return serialize_loved_one(conn, loved_one_row, subscription=subscription)


@app.get("/api/loved-ones", response_model=List[LovedOne])
async def list_loved_ones(current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        subscription = get_subscription_snapshot(conn, current_user["id"])
        rows = conn.execute(
            "SELECT * FROM loved_ones WHERE user_id = ? ORDER BY updated_at DESC",
            (current_user["id"],),
        ).fetchall()
        return [serialize_loved_one(conn, row, subscription=subscription) for row in rows]


@app.get("/api/loved-ones/{loved_one_id}", response_model=LovedOne)
async def get_loved_one(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        return serialize_loved_one(conn, row, subscription=subscription)


@app.post("/api/loved-ones/{loved_one_id}/cover", response_model=LovedOne)
async def update_loved_one_cover(
    loved_one_id: str,
    payload: LovedOneCoverPayload,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        if payload.cover_photo_asset_id:
            media_row = conn.execute(
                """
                SELECT * FROM media_assets
                WHERE id = ? AND loved_one_id = ? AND user_id = ? AND kind = 'photo'
                """,
                (payload.cover_photo_asset_id, loved_one_id, current_user["id"]),
            ).fetchone()
            if media_row is None:
                raise HTTPException(status_code=404, detail="封面照片未找到")

        next_cover_title = loved_one_row["cover_title"] if payload.cover_title is None else payload.cover_title.strip()
        next_cover_asset_id = (
            loved_one_row["cover_photo_asset_id"]
            if payload.cover_photo_asset_id is None
            else (payload.cover_photo_asset_id.strip() or None)
        )

        conn.execute(
            "UPDATE loved_ones SET cover_title = ?, cover_photo_asset_id = ?, updated_at = ? WHERE id = ?",
            (next_cover_title, next_cover_asset_id, now_iso(), loved_one_id),
        )
        updated = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        return serialize_loved_one(conn, updated, subscription=subscription)


@app.get("/api/loved-ones/{loved_one_id}/digital-human")
async def get_digital_human_model_endpoint(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        model_row = get_digital_human_model_row(conn, loved_one_id)
        if model_row is None:
            rebuild_digital_human_model(conn, loved_one_id, trigger_source="api_fetch")
            model_row = get_digital_human_model_row(conn, loved_one_id)
        return serialize_digital_human_model(conn, model_row)


@app.get("/api/loved-ones/{loved_one_id}/digital-human/fragments")
async def get_digital_human_fragments_endpoint(
    loved_one_id: str,
    source_type: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        rows = get_digital_human_fragments(conn, loved_one_id, source_type=source_type, limit=limit)
        return {
            "loved_one_id": loved_one_id,
            "count": len(rows),
            "items": [serialize_digital_human_fragment(row) for row in rows],
        }


@app.get("/api/loved-ones/{loved_one_id}/digital-human/builds")
async def get_digital_human_builds_endpoint(
    loved_one_id: str,
    limit: int = 6,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        rows = conn.execute(
            """
            SELECT * FROM digital_human_build_runs
            WHERE loved_one_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (loved_one_id, max(1, min(12, int(limit)))),
        ).fetchall()
    return {
        "loved_one_id": loved_one_id,
        "count": len(rows),
        "items": [
            {
                "id": row["id"],
                "status": row["status"],
                "trigger_source": row["trigger_source"],
                "notes": row["notes"],
                "source_counts": json_object(row["source_counts_json"]),
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
    }


@app.get("/api/loved-ones/{loved_one_id}/digital-human/history")
async def get_digital_human_history_endpoint(
    loved_one_id: str,
    limit: int = 12,
    current_user: dict = Depends(get_current_user),
):
    sample_limit = max(4, min(40, int(limit) * 3))
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        chat_rows = conn.execute(
            """
            SELECT id, created_at, user_message, ai_response, mode, response_audio_asset_id, response_video_asset_id
            FROM chat_messages
            WHERE loved_one_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (loved_one_id, current_user["id"], sample_limit),
        ).fetchall()
        proactive_rows = conn.execute(
            """
            SELECT id, created_at, event_type, channel, title, message_text, audio_asset_id, video_asset_id, metadata_json
            FROM proactive_events
            WHERE loved_one_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (loved_one_id, current_user["id"], sample_limit),
        ).fetchall()

        items = []
        for row in chat_rows:
            audio_ref = get_media_asset_reference(conn, row["response_audio_asset_id"])
            video_ref = get_media_asset_reference(conn, row["response_video_asset_id"])
            if not (
                (audio_ref and audio_ref["kind"] == "generated_audio")
                or (video_ref and video_ref["kind"] == "generated_video")
            ):
                continue
            items.append(
                {
                    "id": row["id"],
                    "source": "conversation",
                    "source_label": "对话陪伴",
                    "mode": row["mode"],
                    "title": "Mimo 陪伴回复",
                    "prompt_text": row["user_message"],
                    "response_text": row["ai_response"],
                    "audio_url": audio_ref["url"] if audio_ref else None,
                    "audio_kind": audio_ref["kind"] if audio_ref else None,
                    "video_url": video_ref["url"] if video_ref else None,
                    "video_kind": video_ref["kind"] if video_ref else None,
                    "created_at": row["created_at"],
                    "metadata": {},
                }
            )

        for row in proactive_rows:
            audio_ref = get_media_asset_reference(conn, row["audio_asset_id"])
            video_ref = get_media_asset_reference(conn, row["video_asset_id"])
            if not (
                (audio_ref and audio_ref["kind"] == "generated_audio")
                or (video_ref and video_ref["kind"] == "generated_video")
            ):
                continue
            items.append(
                {
                    "id": row["id"],
                    "source": "proactive",
                    "source_label": "主动联系",
                    "mode": row["event_type"],
                    "title": row["title"] or "主动联系",
                    "prompt_text": None,
                    "response_text": row["message_text"],
                    "audio_url": audio_ref["url"] if audio_ref else None,
                    "audio_kind": audio_ref["kind"] if audio_ref else None,
                    "video_url": video_ref["url"] if video_ref else None,
                    "video_kind": video_ref["kind"] if video_ref else None,
                    "created_at": row["created_at"],
                    "metadata": json_object(row["metadata_json"]),
                }
            )

    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    items = items[: max(1, min(24, int(limit)))]
    return {
        "loved_one_id": loved_one_id,
        "count": len(items),
        "items": items,
    }


@app.post("/api/loved-ones/{loved_one_id}/digital-human/rebuild")
async def rebuild_digital_human_endpoint(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        model = rebuild_digital_human_model(conn, loved_one_id, trigger_source="manual_rebuild")
    return {"status": "rebuilt", "loved_one_id": loved_one_id, "digital_human_model": model}


@app.delete("/api/loved-ones/{loved_one_id}")
async def delete_loved_one(loved_one_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        media_rows = fetch_media_rows(conn, loved_one_id, include_generated=True)
        for media_row in media_rows:
            cleanup_path(media_row["file_path"])
        conn.execute("DELETE FROM loved_ones WHERE id = ? AND user_id = ?", (loved_one_id, current_user["id"]))
    return {"status": "deleted", "loved_one_id": loved_one_id, "name": row["name"]}


async def handle_media_upload(
    *,
    kind: str,
    loved_one_id: str,
    file: UploadFile,
    request: Request,
    current_user: dict,
) -> dict:
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        if kind == "voice":
            assert_plan_capability(subscription, "voice_upload", "当前套餐不包含语音建模上传，请先升级套餐。")
        if kind == "video":
            assert_plan_capability(subscription, "video_upload", "当前套餐不包含视频陪伴上传，请先升级套餐。")

        folder_map = {
            "voice": "voices",
            "photo": "photos",
            "video": "videos",
            "model3d": "model3d",
        }
        target_path = safe_upload_path(folder_map.get(kind, f"{kind}s"), loved_one_id, file.filename)
        content = await file.read()
        target_path.write_bytes(content)
        summary = await analyze_media_with_mimo(kind, target_path, request=request) if kind in {"voice", "photo", "video"} else None
        if kind == "model3d" and not summary:
            summary = "已上传真人 3D 重建素材，会作为数字人的立体外观与空间形态底稿。"
        asset_id = str(uuid.uuid4())
        existing_primary = conn.execute(
            "SELECT id FROM media_assets WHERE loved_one_id = ? AND kind = ? AND is_primary = 1",
            (loved_one_id, kind),
        ).fetchone()
        is_primary = 0 if existing_primary else 1
        metadata_payload = {}
        conn.execute(
            """
            INSERT INTO media_assets (
                id, user_id, loved_one_id, kind, file_path, original_filename,
                mime_type, byte_size, summary, tags_json, metadata_json, is_primary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                current_user["id"],
                loved_one_id,
                kind,
                str(target_path),
                file.filename or target_path.name,
                file.content_type or infer_mime_type(target_path, "application/octet-stream"),
                len(content),
                summary,
                "[]",
                json.dumps(
                    {"stage": "uploaded", "pipeline": ["uploaded", "aligned", "textured", "rigged", "ready"]}
                    if kind == "model3d"
                    else metadata_payload,
                    ensure_ascii=False,
                ),
                is_primary,
                now_iso(),
            ),
        )
        if kind == "photo":
            current_cover = conn.execute(
                "SELECT cover_photo_asset_id FROM loved_ones WHERE id = ?",
                (loved_one_id,),
            ).fetchone()
            if current_cover and not current_cover["cover_photo_asset_id"]:
                conn.execute(
                    "UPDATE loved_ones SET cover_photo_asset_id = ?, updated_at = ? WHERE id = ?",
                    (asset_id, now_iso(), loved_one_id),
                )
        refresh_identity_model_summary(conn, loved_one_id, trigger_source=f"upload_{kind}")
        conn.execute(
            "UPDATE loved_ones SET updated_at = ? WHERE id = ?",
            (now_iso(), loved_one_id),
        )
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()

    messages = {
        "voice": "语音样本已上传，正在为这个数字分身校准声音与通话语气...",
        "photo": "照片已上传，分身的面容正在变得更清晰。",
        "video": "视频已上传，分身开始拥有更完整的动态神态。",
        "model3d": "真人 3D 重建已上传，数字人的立体外观底稿正在更新。",
    }
    return {
        "status": "uploaded",
        "asset_id": asset_id,
        "path": str(target_path),
        "url": public_media_url(str(target_path)),
        "message": messages[kind],
        "loved_one": loved_one,
    }


@app.post("/api/loved-ones/{loved_one_id}/voice")
async def upload_voice_sample(
    loved_one_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    return await handle_media_upload(
        kind="voice",
        loved_one_id=loved_one_id,
        file=file,
        request=request,
        current_user=current_user,
    )


@app.post("/api/loved-ones/{loved_one_id}/photo")
async def upload_photo(
    loved_one_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    return await handle_media_upload(
        kind="photo",
        loved_one_id=loved_one_id,
        file=file,
        request=request,
        current_user=current_user,
    )


@app.post("/api/loved-ones/{loved_one_id}/video")
async def upload_video(
    loved_one_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    return await handle_media_upload(
        kind="video",
        loved_one_id=loved_one_id,
        file=file,
        request=request,
        current_user=current_user,
    )


@app.post("/api/loved-ones/{loved_one_id}/model-3d")
async def upload_model3d(
    loved_one_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    return await handle_media_upload(
        kind="model3d",
        loved_one_id=loved_one_id,
        file=file,
        request=request,
        current_user=current_user,
    )


@app.get("/api/loved-ones/{loved_one_id}/media")
async def get_media_assets(
    loved_one_id: str,
    kind: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        kinds = [kind] if kind in {"voice", "photo", "video", "model3d"} else None
        rows = fetch_media_rows(conn, loved_one_id, kinds=kinds)
        return [serialize_media_asset(row) for row in rows]


@app.post("/api/media/{asset_id}/tags")
async def update_media_tags(
    asset_id: str,
    payload: MediaTagsPayload,
    current_user: dict = Depends(get_current_user),
):
    tags = [item.strip() for item in payload.tags if str(item).strip()]
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE id = ? AND user_id = ?",
            (asset_id, current_user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="素材未找到")
        conn.execute(
            "UPDATE media_assets SET tags_json = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), asset_id),
        )
        updated = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
    return {"status": "updated", "asset": serialize_media_asset(updated)}


@app.post("/api/media/{asset_id}/primary")
async def set_media_primary(asset_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE id = ? AND user_id = ?",
            (asset_id, current_user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="素材未找到")
        if row["kind"] not in {"voice", "photo", "video", "model3d"}:
            raise HTTPException(status_code=400, detail="当前素材不支持主样本设置")
        conn.execute(
            "UPDATE media_assets SET is_primary = 0 WHERE loved_one_id = ? AND kind = ?",
            (row["loved_one_id"], row["kind"]),
        )
        conn.execute("UPDATE media_assets SET is_primary = 1 WHERE id = ?", (asset_id,))
        updated = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
    return {"status": "updated", "asset": serialize_media_asset(updated)}


@app.post("/api/media/{asset_id}/model3d-stage")
async def update_model3d_stage(
    asset_id: str,
    payload: MediaStagePayload,
    current_user: dict = Depends(get_current_user),
):
    stage = payload.stage.strip()
    if stage not in MODEL3D_STAGE_ORDER:
        raise HTTPException(status_code=400, detail="不支持的 3D 阶段")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE id = ? AND user_id = ?",
            (asset_id, current_user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="素材未找到")
        if row["kind"] != "model3d":
            raise HTTPException(status_code=400, detail="仅支持 3D 重建素材更新阶段")
        meta = json_object(row["metadata_json"]) if row["metadata_json"] else {}
        meta["stage"] = stage
        conn.execute(
            "UPDATE media_assets SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), asset_id),
        )
        updated = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
        refresh_identity_model_summary(conn, row["loved_one_id"], trigger_source="model3d_stage")
    return {"status": "updated", "asset": serialize_media_asset(updated)}


@app.delete("/api/media/{asset_id}")
async def delete_media_asset(asset_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE id = ? AND user_id = ?",
            (asset_id, current_user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="素材未找到")
        if row["kind"] not in {"voice", "photo", "video", "model3d"}:
            raise HTTPException(status_code=403, detail="当前素材不支持手动删除")
        loved_one_id = row["loved_one_id"]
        was_primary = bool(row["is_primary"]) if "is_primary" in row.keys() else False
        was_cover = conn.execute(
            "SELECT 1 FROM loved_ones WHERE id = ? AND cover_photo_asset_id = ?",
            (loved_one_id, asset_id),
        ).fetchone()
        cleanup_path(row["file_path"])
        conn.execute("DELETE FROM media_assets WHERE id = ?", (asset_id,))
        if was_primary:
            next_row = conn.execute(
                """
                SELECT id FROM media_assets
                WHERE loved_one_id = ? AND kind = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (loved_one_id, row["kind"]),
            ).fetchone()
            if next_row:
                conn.execute("UPDATE media_assets SET is_primary = 1 WHERE id = ?", (next_row["id"],))
        if was_cover:
            next_cover = conn.execute(
                """
                SELECT id FROM media_assets
                WHERE loved_one_id = ? AND kind = 'photo'
                ORDER BY is_primary DESC, created_at DESC
                LIMIT 1
                """,
                (loved_one_id,),
            ).fetchone()
            conn.execute(
                "UPDATE loved_ones SET cover_photo_asset_id = ? WHERE id = ?",
                (next_cover["id"] if next_cover else None, loved_one_id),
            )
        refresh_identity_model_summary(conn, loved_one_id, trigger_source="delete_media")
        conn.execute("UPDATE loved_ones SET updated_at = ? WHERE id = ?", (now_iso(), loved_one_id))
    return {"status": "deleted", "asset_id": asset_id}


@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_loved_one(
    msg: ChatMessage,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        loved_one_row = ensure_loved_one_owner(conn, current_user["id"], msg.loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        requested_mode = (msg.mode or "text").lower()

        if requested_mode == "voice":
            assert_plan_capability(subscription, "voice", "当前套餐不包含语音电话，请先升级套餐。")
        if requested_mode == "video":
            assert_plan_capability(subscription, "video", "当前套餐不包含视频陪伴，请先升级套餐。")

        loved_one = serialize_loved_one(conn, loved_one_row, subscription=subscription).model_dump()
        available_modes = loved_one.get("digital_twin_profile", {}).get("available_modes", ["text"])
        interaction_mode = requested_mode
        if requested_mode == "video" and "video" not in available_modes:
            interaction_mode = "voice" if "voice" in available_modes else "text"
        elif requested_mode == "voice" and "voice" not in available_modes:
            interaction_mode = "text"

        memory_rows = conn.execute(
            "SELECT content FROM memories WHERE loved_one_id = ? ORDER BY created_at DESC LIMIT 10",
            (msg.loved_one_id,),
        ).fetchall()
        memory_values = [row["content"] for row in reversed(memory_rows)]
        memory_context = "\n".join([f"- {value}" for value in memory_values])
        memory_refs = [value for value in memory_values if value][:3]

        if GEMINI_API_KEY:
            try:
                ai_response = await generate_text_response_with_mimo(
                    loved_one=loved_one,
                    user_message=msg.message,
                    emotion=msg.emotion,
                    memory_context=memory_context,
                    request=request,
                    mode=interaction_mode,
                    intensity=msg.intensity,
                )
            except Exception:
                ai_response = build_fallback_response(
                    loved_one=loved_one,
                    user_message=msg.message,
                    emotion=msg.emotion,
                    memory_context=memory_context,
                    intensity=msg.intensity,
                )
        else:
            ai_response = build_fallback_response(
                loved_one=loved_one,
                user_message=msg.message,
                emotion=msg.emotion,
                memory_context=memory_context,
                intensity=msg.intensity,
            )

        response_audio_url = None
        response_audio_asset_id = None
        audio_result = None
        if interaction_mode in {"voice", "video"}:
            audio_result = await synthesize_speech_with_mimo(
                conn=conn,
                user_id=current_user["id"],
                loved_one_id=msg.loved_one_id,
                text=ai_response,
                emotion=msg.emotion,
            )
            if audio_result:
                response_audio_url = audio_result["url"]
                response_audio_asset_id = audio_result["asset_id"]

        response_video_url = None
        response_video_asset_id = None
        video_mode_note = None
        if interaction_mode == "video":
            video_result = await synthesize_video_with_mimo(
                conn=conn,
                user_id=current_user["id"],
                loved_one_id=msg.loved_one_id,
                loved_one=loved_one,
                user_message=msg.message,
                ai_response=ai_response,
                emotion=msg.emotion,
                memory_context=memory_context,
                request=request,
                audio_result=audio_result,
            )
            if video_result:
                response_video_url = video_result["url"]
                response_video_asset_id = video_result["asset_id"]
                video_mode_note = video_result.get("mode_note")
            elif loved_one["video_urls"]:
                response_video_url = loved_one["video_urls"][-1]
                video_row = conn.execute(
                    """
                    SELECT id FROM media_assets
                    WHERE loved_one_id = ? AND kind = 'video'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (msg.loved_one_id,),
                ).fetchone()
                response_video_asset_id = video_row["id"] if video_row else None
                video_mode_note = "MIMO 旁白生成已完成；当前视频画面先回退到你上传的原始影像素材。"

        conn.execute(
            """
            INSERT INTO chat_messages (
                id, user_id, loved_one_id, user_message, ai_response, emotion, mode,
                response_audio_asset_id, response_video_asset_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                current_user["id"],
                msg.loved_one_id,
                msg.message,
                ai_response,
                msg.emotion,
                interaction_mode,
                response_audio_asset_id,
                response_video_asset_id,
                now_iso(),
            ),
        )
        conn.execute("UPDATE loved_ones SET updated_at = ? WHERE id = ?", (now_iso(), msg.loved_one_id))

    return ChatResponse(
        loved_one_id=msg.loved_one_id,
        loved_one_name=loved_one["name"],
        response_text=ai_response,
        response_audio_url=response_audio_url,
        response_video_url=response_video_url,
        interaction_mode=interaction_mode,
        mode_note=video_mode_note or build_mode_note(requested_mode, available_modes),
        available_modes=available_modes,
        emotion_detected=msg.emotion or "neutral",
        memory_triggered=memory_context[:100] if memory_context else None,
        memory_refs=memory_refs,
    )


@app.get("/api/chat-history/{loved_one_id}")
async def get_chat_history(
    loved_one_id: str,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        rows = conn.execute(
            """
            SELECT created_at, user_message, ai_response, emotion, mode,
                   response_audio_asset_id, response_video_asset_id
            FROM chat_messages
            WHERE loved_one_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (loved_one_id, current_user["id"], limit),
        ).fetchall()
        items = []
        for row in reversed(rows):
            audio_ref = get_media_asset_reference(conn, row["response_audio_asset_id"])
            video_ref = get_media_asset_reference(conn, row["response_video_asset_id"])
            items.append(
                {
                    "timestamp": row["created_at"],
                    "user_message": row["user_message"],
                    "ai_response": row["ai_response"],
                    "emotion": row["emotion"],
                    "mode": row["mode"],
                    "response_audio_url": audio_ref["url"] if audio_ref else None,
                    "response_audio_kind": audio_ref["kind"] if audio_ref else None,
                    "response_video_url": video_ref["url"] if video_ref else None,
                    "response_video_kind": video_ref["kind"] if video_ref else None,
                }
            )
    return items


@app.post("/api/memories")
async def add_memory(memory: MemoryEntry, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], memory.loved_one_id)
        subscription = get_subscription_snapshot(conn, current_user["id"])
        assert_memory_limit(conn, current_user["id"], memory.loved_one_id, subscription)
        memory_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO memories (
                id, user_id, loved_one_id, content, memory_type, memory_date, importance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                current_user["id"],
                memory.loved_one_id,
                memory.content.strip(),
                memory.memory_type,
                memory.date,
                memory.importance,
                now_iso(),
            ),
        )
        refresh_identity_model_summary(conn, memory.loved_one_id, trigger_source="add_memory")
        conn.execute("UPDATE loved_ones SET updated_at = ? WHERE id = ?", (now_iso(), memory.loved_one_id))
    return {"status": "saved", "memory_id": memory_id}


@app.get("/api/memories/{loved_one_id}")
async def get_memories(
    loved_one_id: str,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        prd_profile = conn.execute(
            "SELECT id FROM profiles WHERE id = ? AND user_id = ?",
            (loved_one_id, current_user["id"]),
        ).fetchone()
        if prd_profile is not None:
            rows = conn.execute(
                """
                SELECT id, profile_id, type, file_path, transcription, embedding_id, metadata_json, created_at
                FROM profile_memories
                WHERE profile_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (loved_one_id, current_user["id"], limit),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "profile_id": row["profile_id"],
                    "type": row["type"],
                    "file_path": row["file_path"],
                    "file_url": public_media_url(row["file_path"]) if row["file_path"] else None,
                    "transcription": row["transcription"],
                    "embedding_id": row["embedding_id"],
                    "metadata": json_object(row["metadata_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        rows = conn.execute(
            """
            SELECT id, loved_one_id, content, memory_type, memory_date, importance, created_at
            FROM memories
            WHERE loved_one_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (loved_one_id, current_user["id"], limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "loved_one_id": row["loved_one_id"],
            "content": row["content"],
            "memory_type": row["memory_type"],
            "date": row["memory_date"],
            "importance": row["importance"],
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]


@app.delete("/api/memories/{loved_one_id}/{memory_id}")
async def delete_memory(loved_one_id: str, memory_id: str, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], loved_one_id)
        deleted = conn.execute(
            "DELETE FROM memories WHERE id = ? AND loved_one_id = ? AND user_id = ?",
            (memory_id, loved_one_id, current_user["id"]),
        ).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="回忆未找到")
        refresh_identity_model_summary(conn, loved_one_id, trigger_source="delete_memory")
        conn.execute("UPDATE loved_ones SET updated_at = ? WHERE id = ?", (now_iso(), loved_one_id))
    return {"status": "deleted", "memory_id": memory_id}


@app.post("/api/greetings/schedule")
async def schedule_greeting(greeting: GreetingSchedule, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        ensure_loved_one_owner(conn, current_user["id"], greeting.loved_one_id)
        greeting_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO greetings (
                id, user_id, loved_one_id, greeting_type, trigger_date, message_template, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                greeting_id,
                current_user["id"],
                greeting.loved_one_id,
                greeting.greeting_type,
                greeting.trigger_date,
                greeting.message_template,
                "scheduled",
                now_iso(),
            ),
        )
    return {"status": "scheduled", "greeting_id": greeting_id}


@app.get("/api/greetings/upcoming")
async def upcoming_greetings(days: int = 7, current_user: dict = Depends(get_current_user)):
    start = now_utc()
    end = start + timedelta(days=days)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM greetings
            WHERE user_id = ? AND status = 'scheduled'
            ORDER BY trigger_date ASC
            """,
            (current_user["id"],),
        ).fetchall()
    upcoming = []
    for row in rows:
        trigger = parse_iso(row["trigger_date"])
        if trigger and start <= trigger <= end:
            upcoming.append(dict(row))
    return upcoming


@app.get("/api/stats")
async def get_stats(current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        return get_user_stats(conn, current_user["id"])


register_presenz_mvp(
    app,
    get_db=get_db,
    now_iso=now_iso,
    runtime_config=runtime_config,
    data_dir=DATA_DIR,
    public_media_url=public_media_url,
    infer_mime_type=infer_mime_type,
    hash_password=hash_password,
    verify_password=verify_password,
    build_twiml_playback=build_twiml_playback,
    build_twiml_closing=build_twiml_closing,
    synthesize_speech_with_mimo=synthesize_speech_with_mimo,
)


# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn

    print("Starting Eterna API...")
    print("Memory companion service is initializing.")
    uvicorn.run(app, host="0.0.0.0", port=8102)
