import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.etree import ElementTree

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field


def register_presenz_mvp(
    app: FastAPI,
    *,
    get_db: Callable[[], sqlite3.Connection],
    now_iso: Callable[[], str],
    runtime_config: Callable[[str, str], str],
    data_dir: Path,
    public_media_url: Callable[[str], Optional[str]],
    infer_mime_type: Callable[[Path, str], str],
    hash_password: Callable[[str], str],
    verify_password: Callable[[str, str], bool],
    build_twiml_playback: Callable[[str, Optional[str]], str],
    build_twiml_closing: Callable[[str, Optional[str]], str],
    synthesize_speech_with_mimo: Optional[Callable[..., Any]] = None,
):
    jwt_secret = runtime_config("JWT_SECRET", runtime_config("APP_SECRET", "presenz-dev-secret"))
    jwt_issuer = runtime_config("JWT_ISSUER", "presenz-api")
    gemini_api_base = runtime_config("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
    gemini_api_key = runtime_config("GEMINI_API_KEY", runtime_config("MIMO_API_KEY", ""))
    gemini_model = runtime_config("GEMINI_MODEL", "gemini-2.5-flash")
    mimo_tts_voice = runtime_config("MIMO_TTS_VOICE", "default")
    twilio_account_sid = runtime_config("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token = runtime_config("TWILIO_AUTH_TOKEN", "")
    twilio_from_number = runtime_config("TWILIO_FROM_NUMBER", "")
    public_base_url = runtime_config("PUBLIC_BASE_URL", runtime_config("APP_BASE_URL", "http://localhost:8102")).rstrip("/")
    call_target_number = runtime_config("PRESENZ_CALL_TARGET_NUMBER", "")
    whisper_model_name = runtime_config("WHISPER_MODEL", "base")

    storage_root = data_dir / "presenz"
    storage_root.mkdir(parents=True, exist_ok=True)
    chroma_root = data_dir / "chroma"
    chroma_root.mkdir(parents=True, exist_ok=True)

    embedding_lock = threading.Lock()
    background_lock = threading.Lock()
    embedder_cache: Dict[str, Any] = {"embedder": None, "client": None, "collection": None}

    def init_presenz_tables() -> None:
        with get_db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    custom_tone TEXT NOT NULL DEFAULT '',
                    privacy_mode TEXT NOT NULL DEFAULT 'private',
                    voice_clone_status TEXT NOT NULL DEFAULT 'not_started',
                    persona_ready INTEGER NOT NULL DEFAULT 0,
                    persona_summary TEXT NOT NULL DEFAULT '',
                    emotional_state TEXT NOT NULL DEFAULT 'neutral',
                    voice_id TEXT,
                    avatar_path TEXT,
                    living_person INTEGER NOT NULL DEFAULT 0,
                    consent_flag INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    current_file TEXT,
                    error_message TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_memories (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    file_path TEXT,
                    transcription TEXT,
                    embedding_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    audio_url TEXT,
                    emotional_tone TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS call_logs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    transcript TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS call_sessions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    twilio_call_sid TEXT,
                    status TEXT NOT NULL DEFAULT 'created',
                    transcript TEXT NOT NULL DEFAULT '',
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL REFERENCES profile_memories(id) ON DELETE CASCADE,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    chunk_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_upload_jobs_user_profile ON upload_jobs(user_id, profile_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_memories_profile ON profile_memories(profile_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_profile ON conversations(profile_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_call_logs_profile ON call_logs(profile_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_call_sessions_twilio_sid ON call_sessions(twilio_call_sid);
                CREATE INDEX IF NOT EXISTS idx_memory_chunks_profile ON memory_chunks(profile_id, created_at DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
            if "custom_tone" not in columns:
                conn.execute("ALTER TABLE profiles ADD COLUMN custom_tone TEXT NOT NULL DEFAULT ''")

    def b64url_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def b64url_decode(value: str) -> bytes:
        padding = "=" * ((4 - len(value) % 4) % 4)
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))

    def create_jwt(user_id: str, email: str) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        issued_at = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "sub": user_id,
            "email": email,
            "iss": jwt_issuer,
            "iat": issued_at,
            "exp": issued_at + 60 * 60 * 24 * 30,
        }
        header_part = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_part = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = hmac.new(jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_part}.{payload_part}.{b64url_encode(signature)}"

    def decode_jwt(token: str) -> dict:
        try:
            header_part, payload_part, signature_part = token.split(".", 2)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, b64url_decode(signature_part)):
            raise HTTPException(status_code=401, detail="Invalid token signature")
        payload = json.loads(b64url_decode(payload_part).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if exp <= int(datetime.now(timezone.utc).timestamp()):
            raise HTTPException(status_code=401, detail="Token expired")
        if payload.get("iss") != jwt_issuer:
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        return payload

    def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
        if not authorization:
            return None
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        return None

    def require_prd_user(authorization: Optional[str] = Header(default=None)) -> dict:
        token = extract_bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="Authentication required")
        with get_db() as conn:
            try:
                payload = decode_jwt(token)
                user = conn.execute("SELECT * FROM users WHERE id = ?", (payload["sub"],)).fetchone()
                if user is None:
                    raise HTTPException(status_code=401, detail="User not found")
                return dict(user)
            except HTTPException:
                session = conn.execute(
                    """
                    SELECT u.*
                    FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = ?
                    """,
                    (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
                ).fetchone()
                if session is None:
                    raise
                return dict(session)

    def ensure_profile_owner(conn: sqlite3.Connection, user_id: str, profile_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ? AND user_id = ?",
            (profile_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return row

    def profile_storage_dir(user_id: str, profile_id: str, bucket: str) -> Path:
        target = storage_root / user_id / profile_id / bucket
        target.mkdir(parents=True, exist_ok=True)
        return target

    def save_upload(user_id: str, profile_id: str, bucket: str, filename: str, content: bytes) -> Path:
        safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}{Path(filename or 'upload').suffix}"
        target_path = profile_storage_dir(user_id, profile_id, bucket) / safe_name
        target_path.write_bytes(content)
        return target_path

    def extract_text_from_docx(path: Path) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml")
        except Exception:
            return ""
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return ""
        texts = []
        for node in root.iter():
            if node.tag.endswith("}t") and node.text:
                texts.append(node.text)
        return " ".join(texts).strip()

    def extract_text_content(path: Path, mime_type: str) -> str:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return extract_text_from_docx(path)
        if suffix in {".txt", ".md", ".json", ".csv", ".tsv"} or mime_type.startswith("text/"):
            try:
                return path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                return ""
        return ""

    def parse_chat_export(raw_text: str) -> List[dict]:
        messages: List[dict] = []
        whatsapp = re.compile(
            r"^(?:\[)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?:\s?[APMapm]{2})?)(?:\])?\s+-\s+([^:]+):\s+(.*)$"
        )
        telegram = re.compile(r"^([^:]{1,80}):\s+(.*)$")
        for line in raw_text.splitlines():
            text = line.strip()
            if not text:
                continue
            match = whatsapp.match(text)
            if match:
                messages.append(
                    {
                        "timestamp": f"{match.group(1)} {match.group(2)}",
                        "sender": match.group(3).strip(),
                        "text": match.group(4).strip(),
                    }
                )
                continue
            match = telegram.match(text)
            if match:
                messages.append(
                    {
                        "timestamp": None,
                        "sender": match.group(1).strip(),
                        "text": match.group(2).strip(),
                    }
                )
        return messages

    def looks_like_chat_export(filename: str, raw_text: str) -> bool:
        lowered_name = filename.lower()
        if "whatsapp" in lowered_name or "telegram" in lowered_name or "_chat" in lowered_name:
            return True
        return len(parse_chat_export(raw_text)) >= 3

    def chunk_text(text: str, limit: int = 800) -> List[str]:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return []
        if len(cleaned) <= limit:
            return [cleaned]
        chunks = []
        cursor = 0
        while cursor < len(cleaned):
            chunks.append(cleaned[cursor : cursor + limit])
            cursor += limit
        return chunks

    def get_embedder() -> Optional[Any]:
        with embedding_lock:
            if embedder_cache["embedder"] is not None:
                return embedder_cache["embedder"]
            try:
                from sentence_transformers import SentenceTransformer

                embedder_cache["embedder"] = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                embedder_cache["embedder"] = None
            return embedder_cache["embedder"]

    def get_chroma_collection() -> Optional[Any]:
        with embedding_lock:
            if embedder_cache["collection"] is not None:
                return embedder_cache["collection"]
            try:
                import chromadb

                client = chromadb.PersistentClient(path=str(chroma_root))
                collection = client.get_or_create_collection(name="presenz_memories")
                embedder_cache["client"] = client
                embedder_cache["collection"] = collection
            except Exception:
                embedder_cache["collection"] = None
            return embedder_cache["collection"]

    def store_chunks(
        conn: sqlite3.Connection,
        *,
        user_id: str,
        profile_id: str,
        memory_id: str,
        text: str,
    ) -> None:
        chunks = chunk_text(text)
        if not chunks:
            return
        memory_row = conn.execute(
            "SELECT type, metadata_json FROM profile_memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        memory_meta = json.loads(memory_row["metadata_json"] or "{}") if memory_row else {}
        chunk_rows = []
        ids = []
        documents = []
        metadatas = []
        embedder = get_embedder()
        collection = get_chroma_collection()
        embeddings = embedder.encode(chunks).tolist() if embedder else None
        for index, chunk in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            chunk_rows.append((chunk_id, memory_id, profile_id, user_id, chunk, now_iso()))
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append(
                {
                    "profile_id": profile_id,
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "chunk_index": index,
                    "type": memory_row["type"] if memory_row else "document",
                    "original_filename": memory_meta.get("original_filename", ""),
                }
            )
        conn.executemany(
            """
            INSERT INTO memory_chunks (id, memory_id, profile_id, user_id, chunk_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )
        if collection and embeddings:
            try:
                collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
                conn.execute("UPDATE profile_memories SET embedding_id = ? WHERE id = ?", (ids[0], memory_id))
            except Exception:
                pass

    async def gemini_generate_text(prompt: str, *, max_tokens: int = 512, temperature: float = 0.5) -> str:
        if not gemini_api_key:
            return ""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{gemini_api_base}/models/{gemini_model}:generateContent",
                    params={"key": gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return ""
        parts = []
        for candidate in data.get("candidates", []):
            for part in ((candidate.get("content") or {}).get("parts") or []):
                if part.get("text"):
                    parts.append(part["text"])
        return "\n".join(parts).strip()

    async def gemini_describe_image(path: Path) -> dict:
        if not gemini_api_key:
            return {
                "caption": f"Uploaded photo: {path.name}",
                "emotional_tags": ["nostalgic"],
                "faces": [],
            }
        mime_type = infer_mime_type(path, "image/jpeg")
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                            }
                        },
                        {
                            "text": (
                                "Analyze this memory photo for Presenz. Return strict JSON with keys "
                                "caption, emotional_tags, faces. Keep caption concise."
                            )
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300},
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{gemini_api_base}/models/{gemini_model}:generateContent",
                    params={"key": gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return {
                "caption": f"Uploaded photo: {path.name}",
                "emotional_tags": ["nostalgic"],
                "faces": [],
            }
        text = ""
        for candidate in data.get("candidates", []):
            for part in ((candidate.get("content") or {}).get("parts") or []):
                text += part.get("text", "")
        try:
            cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
            parsed = json.loads(cleaned)
        except Exception:
            parsed = {"caption": text.strip() or f"Uploaded photo: {path.name}", "emotional_tags": ["nostalgic"], "faces": []}
        return {
            "caption": parsed.get("caption") or f"Uploaded photo: {path.name}",
            "emotional_tags": parsed.get("emotional_tags") or [],
            "faces": parsed.get("faces") or [],
        }

    def keyword_retrieve(conn: sqlite3.Connection, profile_id: str, query: str, limit: int = 5) -> List[dict]:
        rows = conn.execute(
            """
            SELECT pm.id, pm.type, pm.transcription, pm.metadata_json, mc.chunk_text
            FROM memory_chunks mc
            JOIN profile_memories pm ON pm.id = mc.memory_id
            WHERE mc.profile_id = ?
            ORDER BY mc.created_at DESC
            """,
            (profile_id,),
        ).fetchall()
        tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9']+", query)}
        query_lower = query.lower().strip()
        scored = []
        for row in rows:
            chunk = row["chunk_text"] or ""
            chunk_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9']+", chunk)}
            score = len(tokens.intersection(chunk_tokens))
            metadata = json.loads(row["metadata_json"] or "{}")
            filename = str(metadata.get("original_filename") or "").lower()
            if query_lower and query_lower in chunk.lower():
                score += 8
            for token in tokens:
                if token and token in chunk.lower():
                    score += 2
                if token and token in filename:
                    score += 2
            if score:
                scored.append((score, row, metadata))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "memory_id": item[1]["id"],
                "type": item[1]["type"],
                "text": item[1]["chunk_text"],
                "metadata": item[2],
            }
            for item in scored[:limit]
        ]

    def retrieve_relevant_memories(conn: sqlite3.Connection, profile_id: str, query: str, limit: int = 5) -> List[dict]:
        embedder = get_embedder()
        collection = get_chroma_collection()
        if embedder and collection:
            try:
                embedding = embedder.encode([query]).tolist()[0]
                result = collection.query(
                    query_embeddings=[embedding],
                    n_results=limit,
                    where={"profile_id": profile_id},
                )
                documents = result.get("documents", [[]])[0]
                metadatas = result.get("metadatas", [[]])[0]
                return [
                    {
                        "memory_id": metadata.get("memory_id"),
                        "type": metadata.get("type", "document"),
                        "text": document,
                        "metadata": metadata,
                    }
                    for document, metadata in zip(documents, metadatas)
                ]
            except Exception:
                pass
        return keyword_retrieve(conn, profile_id, query, limit=limit)

    def detect_emotional_tone(text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ["miss", "grief", "lost", "wish you were here", "gone", "death"]):
            return "grief"
        if any(word in lowered for word in ["remember", "nostalgia", "used to", "back then"]):
            return "nostalgic"
        if any(word in lowered for word in ["happy", "celebrate", "love", "smile", "grateful"]):
            return "joyful"
        return "neutral"

    async def build_persona_summary(conn: sqlite3.Connection, profile_id: str) -> str:
        memories = conn.execute(
            """
            SELECT transcription, metadata_json, type
            FROM profile_memories
            WHERE profile_id = ?
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (profile_id,),
        ).fetchall()
        corpus = []
        for row in memories:
            text = (row["transcription"] or "").strip()
            if text:
                corpus.append(f"{row['type']}: {text}")
                continue
            metadata = json.loads(row["metadata_json"] or "{}")
            caption = metadata.get("caption")
            if caption:
                corpus.append(f"{row['type']}: {caption}")
        joined = "\n".join(corpus[:60])
        if not joined:
            return ""
        prompt = (
            "Summarize this person for a memory companion. Focus on vocabulary, tone, humour, recurring topics, "
            "emotional style, and typical phrases. Keep it under 250 words.\n\n"
            f"{joined}"
        )
        summary = await gemini_generate_text(prompt, max_tokens=320, temperature=0.4)
        return summary or "Warm, grounded, and shaped by the uploaded memories."

    def get_or_create_conversation(conn: sqlite3.Connection, user_id: str, profile_id: str) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM conversations
            WHERE user_id = ? AND profile_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, profile_id),
        ).fetchone()
        if row is not None:
            return row
        conversation_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO conversations (id, profile_id, user_id, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, profile_id, user_id, now_iso()),
        )
        return conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()

    def serialize_profile(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        memory_count = conn.execute("SELECT COUNT(*) FROM profile_memories WHERE profile_id = ?", (row["id"],)).fetchone()[0]
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "name": row["name"],
            "relationship_type": row["relationship_type"],
            "custom_tone": row["custom_tone"],
            "privacy_mode": row["privacy_mode"],
            "voice_clone_status": row["voice_clone_status"],
            "persona_ready": bool(row["persona_ready"]),
            "persona_summary": row["persona_summary"],
            "emotional_state": row["emotional_state"],
            "created_at": row["created_at"],
            "memory_count": memory_count,
        }

    def serialize_message(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "role": row["role"],
            "text": row["text"],
            "audio_url": row["audio_url"],
            "emotional_tone": row["emotional_tone"],
            "created_at": row["created_at"],
        }

    async def transcribe_audio_or_video(path: Path) -> str:
        try:
            import whisper

            model = whisper.load_model(whisper_model_name)
            result = model.transcribe(str(path))
            return str(result.get("text") or "").strip()
        except Exception:
            return ""

    async def maybe_prepare_voice_clone(conn: sqlite3.Connection, profile_id: str) -> None:
        row = conn.execute("SELECT voice_clone_status FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None or row["voice_clone_status"] == "ready":
            return
        conn.execute(
            "UPDATE profiles SET voice_clone_status = ?, voice_id = ?, updated_at = ? WHERE id = ?",
            ("ready", mimo_tts_voice, now_iso(), profile_id),
        )

    async def process_upload_job(job_id: str) -> None:
        with background_lock:
            with get_db() as conn:
                job = conn.execute("SELECT * FROM upload_jobs WHERE id = ?", (job_id,)).fetchone()
                if job is None:
                    return
                try:
                    profile = ensure_profile_owner(conn, job["user_id"], job["profile_id"])
                    raw_files = profile_storage_dir(job["user_id"], job["profile_id"], "raw")
                    files = sorted([path for path in raw_files.iterdir() if path.is_file()], key=lambda path: path.name)
                    total = len(files) or 1
                    conn.execute(
                        "UPDATE upload_jobs SET status = ?, progress = ?, updated_at = ? WHERE id = ?",
                        ("processing", 5, now_iso(), job_id),
                    )
                    result_items = []
                    for index, file_path in enumerate(files, start=1):
                        mime_type = infer_mime_type(file_path, "application/octet-stream")
                        suffix = file_path.suffix.lower()
                        raw_text_for_detection = ""
                        if suffix == ".zip":
                            try:
                                with zipfile.ZipFile(file_path) as archive:
                                    for member in archive.namelist():
                                        if member.lower().endswith(".txt"):
                                            raw_text_for_detection = archive.read(member).decode("utf-8", errors="ignore")
                                            break
                            except Exception:
                                raw_text_for_detection = ""
                        elif suffix == ".txt":
                            raw_text_for_detection = extract_text_content(file_path, mime_type)

                        if suffix in {".txt", ".zip"} and looks_like_chat_export(file_path.name, raw_text_for_detection):
                            text = ""
                            if suffix == ".zip":
                                try:
                                    with zipfile.ZipFile(file_path) as archive:
                                        for member in archive.namelist():
                                            if member.lower().endswith(".txt"):
                                                text = archive.read(member).decode("utf-8", errors="ignore")
                                                break
                                except Exception:
                                    text = ""
                            else:
                                text = extract_text_content(file_path, mime_type)
                            parsed = parse_chat_export(text)
                            transcript = "\n".join(f"{item['sender']}: {item['text']}" for item in parsed[:500]) or text[:8000]
                            memory_id = str(uuid.uuid4())
                            metadata = {"source": "chat_export", "message_count": len(parsed), "original_filename": file_path.name}
                            conn.execute(
                                """
                                INSERT INTO profile_memories (id, profile_id, user_id, type, file_path, transcription, metadata_json, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (memory_id, profile["id"], profile["user_id"], "chat", str(file_path), transcript, json.dumps(metadata), now_iso()),
                            )
                            store_chunks(conn, user_id=profile["user_id"], profile_id=profile["id"], memory_id=memory_id, text=transcript)
                            result_items.append({"file": file_path.name, "type": "chat", "items": len(parsed)})
                        elif suffix in {".mp3", ".m4a", ".wav", ".aac", ".mp4", ".mov"}:
                            transcript = await transcribe_audio_or_video(file_path)
                            memory_type = "video" if suffix in {".mp4", ".mov"} else "audio"
                            memory_id = str(uuid.uuid4())
                            metadata = {"original_filename": file_path.name, "mime_type": mime_type}
                            conn.execute(
                                """
                                INSERT INTO profile_memories (id, profile_id, user_id, type, file_path, transcription, metadata_json, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (memory_id, profile["id"], profile["user_id"], memory_type, str(file_path), transcript, json.dumps(metadata), now_iso()),
                            )
                            if transcript:
                                store_chunks(conn, user_id=profile["user_id"], profile_id=profile["id"], memory_id=memory_id, text=transcript)
                            if memory_type == "audio":
                                await maybe_prepare_voice_clone(conn, profile["id"])
                            result_items.append({"file": file_path.name, "type": memory_type, "transcribed": bool(transcript)})
                        elif suffix in {".jpg", ".jpeg", ".png", ".heic", ".webp"}:
                            analysis = await gemini_describe_image(file_path)
                            memory_id = str(uuid.uuid4())
                            conn.execute(
                                """
                                INSERT INTO profile_memories (id, profile_id, user_id, type, file_path, transcription, metadata_json, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    memory_id,
                                    profile["id"],
                                    profile["user_id"],
                                    "photo",
                                    str(file_path),
                                    analysis["caption"],
                                    json.dumps(analysis),
                                    now_iso(),
                                ),
                            )
                            store_chunks(conn, user_id=profile["user_id"], profile_id=profile["id"], memory_id=memory_id, text=analysis["caption"])
                            result_items.append({"file": file_path.name, "type": "photo"})
                        else:
                            text = extract_text_content(file_path, mime_type)
                            memory_id = str(uuid.uuid4())
                            metadata = {"original_filename": file_path.name, "mime_type": mime_type}
                            conn.execute(
                                """
                                INSERT INTO profile_memories (id, profile_id, user_id, type, file_path, transcription, metadata_json, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (memory_id, profile["id"], profile["user_id"], "document", str(file_path), text, json.dumps(metadata), now_iso()),
                            )
                            if text:
                                store_chunks(conn, user_id=profile["user_id"], profile_id=profile["id"], memory_id=memory_id, text=text)
                            result_items.append({"file": file_path.name, "type": "document", "extracted": bool(text)})

                        progress = min(95, int(index / total * 100))
                        conn.execute(
                            "UPDATE upload_jobs SET current_file = ?, progress = ?, updated_at = ? WHERE id = ?",
                            (file_path.name, progress, now_iso(), job_id),
                        )

                    persona_summary = await build_persona_summary(conn, profile["id"])
                    conn.execute(
                        """
                        UPDATE profiles
                        SET persona_summary = ?, persona_ready = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (persona_summary, 1 if persona_summary else 0, now_iso(), profile["id"]),
                    )
                    conn.execute(
                        """
                        UPDATE upload_jobs
                        SET status = ?, progress = ?, result_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        ("completed", 100, json.dumps({"files": result_items}), now_iso(), job_id),
                    )
                except Exception as exc:
                    conn.execute(
                        """
                        UPDATE upload_jobs
                        SET status = ?, error_message = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        ("failed", str(exc), now_iso(), job_id),
                    )

    class SignupPayload(BaseModel):
        email: str
        password: str
        name: str

    class LoginPayload(BaseModel):
        email: str
        password: str

    class ProfileCreatePayload(BaseModel):
        name: str
        relationship_type: str
        custom_tone: str = ""
        privacy_mode: str = "private"
        living_person: bool = False
        consent_flag: bool = False

    class ChatPayload(BaseModel):
        message: str
        tone_override: str = ""

    class CallInitiatePayload(BaseModel):
        phone_number: Optional[str] = None

    class CallTranscribePayload(BaseModel):
        call_sid: Optional[str] = None
        session_id: Optional[str] = None
        profile_id: Optional[str] = None
        transcript: str
        duration_seconds: int = 0

    init_presenz_tables()

    @app.post("/api/auth/signup")
    async def prd_signup(payload: SignupPayload):
        email = payload.email.strip().lower()
        name = payload.name.strip()
        if "@" not in email:
            raise HTTPException(status_code=400, detail="Valid email is required")
        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="Email already registered")
            user_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, display_name, phone_number, proactive_opt_in,
                    preferred_contact_channel, preferred_contact_time, timezone, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, email, hash_password(payload.password), name, "", 0, "app", "20:30", "UTC", now_iso(), now_iso()),
            )
        token = create_jwt(user_id, email)
        return {"token": token, "user": {"id": user_id, "email": email, "name": name}}

    @app.post("/api/auth/login")
    async def prd_login(payload: LoginPayload):
        email = payload.email.strip().lower()
        with get_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row is None or not verify_password(payload.password, row["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid email or password")
        token = create_jwt(row["id"], row["email"])
        return {"token": token, "user": {"id": row["id"], "email": row["email"], "name": row["display_name"]}}

    @app.get("/api/profiles")
    async def list_profiles(current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM profiles WHERE user_id = ? ORDER BY updated_at DESC",
                (current_user["id"],),
            ).fetchall()
            return [serialize_profile(conn, row) for row in rows]

    @app.post("/api/profiles")
    async def create_profile(payload: ProfileCreatePayload, current_user: dict = Depends(require_prd_user)):
        if payload.privacy_mode not in {"private", "family"}:
            raise HTTPException(status_code=400, detail="privacy_mode must be private or family")
        if payload.living_person and not payload.consent_flag:
            raise HTTPException(status_code=400, detail="Consent flag is required for living person profiles")
        profile_id = str(uuid.uuid4())
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO profiles (
                    id, user_id, name, relationship_type, custom_tone, privacy_mode, voice_clone_status,
                    persona_ready, persona_summary, emotional_state, living_person, consent_flag,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    current_user["id"],
                    payload.name.strip(),
                    payload.relationship_type.strip() or "other",
                    payload.custom_tone.strip(),
                    payload.privacy_mode,
                    "not_started",
                    0,
                    "",
                    "neutral",
                    1 if payload.living_person else 0,
                    1 if payload.consent_flag else 0,
                    now_iso(),
                    now_iso(),
                ),
            )
            row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            return serialize_profile(conn, row)

    @app.get("/api/profiles/{profile_id}")
    async def get_profile(profile_id: str, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            row = ensure_profile_owner(conn, current_user["id"], profile_id)
            pending_job = conn.execute(
                """
                SELECT id, status, progress
                FROM upload_jobs
                WHERE profile_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
            payload = serialize_profile(conn, row)
            payload["status_flags"] = {
                "upload_job": dict(pending_job) if pending_job else None,
                "voice_ready": row["voice_clone_status"] == "ready",
                "persona_ready": bool(row["persona_ready"]),
            }
            return payload

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    async def delete_profile(profile_id: str, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            row = ensure_profile_owner(conn, current_user["id"], profile_id)
            for memory in conn.execute("SELECT file_path FROM profile_memories WHERE profile_id = ?", (profile_id,)).fetchall():
                if memory["file_path"]:
                    try:
                        Path(memory["file_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
            if row["avatar_path"]:
                try:
                    Path(row["avatar_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            conn.execute("DELETE FROM profiles WHERE id = ? AND user_id = ?", (profile_id, current_user["id"]))
        return Response(status_code=204)

    @app.post("/api/upload/{profile_id}")
    async def upload_memories(
        profile_id: str,
        files: List[UploadFile] = File(...),
        current_user: dict = Depends(require_prd_user),
    ):
        if not files:
            raise HTTPException(status_code=400, detail="At least one file is required")
        with get_db() as conn:
            ensure_profile_owner(conn, current_user["id"], profile_id)
            job_id = str(uuid.uuid4())
            for upload in files:
                content = await upload.read()
                save_upload(current_user["id"], profile_id, "raw", upload.filename or "upload.bin", content)
            conn.execute(
                """
                INSERT INTO upload_jobs (id, user_id, profile_id, status, progress, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, current_user["id"], profile_id, "queued", 0, "{}", now_iso(), now_iso()),
            )
        thread = threading.Thread(target=lambda: __import__("asyncio").run(process_upload_job(job_id)), daemon=True)
        thread.start()
        return {"job_ids": [job_id]}

    @app.get("/api/upload/{job_id}/status")
    async def get_upload_status(job_id: str, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM upload_jobs WHERE id = ? AND user_id = ?",
                (job_id, current_user["id"]),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Upload job not found")
            return {
                "job_id": row["id"],
                "status": row["status"],
                "progress": row["progress"],
                "current_file": row["current_file"],
                "error_message": row["error_message"],
                "result": json.loads(row["result_json"] or "{}"),
            }

    @app.get("/api/memories/{profile_id}")
    async def list_memories(profile_id: str, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            ensure_profile_owner(conn, current_user["id"], profile_id)
            rows = conn.execute(
                """
                SELECT * FROM profile_memories
                WHERE profile_id = ?
                ORDER BY created_at DESC
                """,
                (profile_id,),
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
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    @app.get("/api/chat/{profile_id}/history")
    async def get_chat_history(profile_id: str, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            ensure_profile_owner(conn, current_user["id"], profile_id)
            conversation = get_or_create_conversation(conn, current_user["id"], profile_id)
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation["id"],),
            ).fetchall()
            return [serialize_message(row) for row in rows]

    @app.post("/api/chat/{profile_id}")
    async def chat(profile_id: str, payload: ChatPayload, current_user: dict = Depends(require_prd_user)):
        with get_db() as conn:
            profile = ensure_profile_owner(conn, current_user["id"], profile_id)
            conversation = get_or_create_conversation(conn, current_user["id"], profile_id)
            history_rows = conn.execute(
                "SELECT role, text FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 20",
                (conversation["id"],),
            ).fetchall()
            memories = retrieve_relevant_memories(conn, profile_id, payload.message, limit=5)
            emotional_tone = detect_emotional_tone(payload.message)
            effective_tone = (payload.tone_override or profile["custom_tone"] or "").strip()
            named_sources = [
                {
                    "type": memory["type"],
                    "filename": memory["metadata"].get("original_filename", ""),
                    "excerpt": memory["text"][:600],
                }
                for memory in memories
            ]
            # Build a rich memory context for the prompt, but keep it hidden from the final reply
            memory_context_lines = []
            for src in named_sources:
                if src["excerpt"]:
                    memory_context_lines.append(src["excerpt"][:500])
            memory_context = "\n---\n".join(memory_context_lines)

            # Conversation history (last 20 turns, oldest first)
            history_lines = []
            for row in reversed(history_rows):
                role_label = "You" if row["role"] == "assistant" else "Them"
                history_lines.append(f"{role_label}: {row['text']}")
            history_block = "\n".join(history_lines)

            tone_instruction = ""
            if effective_tone:
                tone_instruction = f"Your replies should feel: {effective_tone}."

            system_prompt = f"""You are roleplaying as {profile['name']}, the user's {profile['relationship_type']}.

Persona and speaking style:
{profile['persona_summary'] or 'Warm, real, and grounded — speak the way this person would naturally speak based on the memories below.'}

Relevant memories and context from uploaded files:
{memory_context or 'No specific memory retrieved — draw on your general persona.'}

Recent conversation:
{history_block or '(This is the start of the conversation.)'}

The user just said: "{payload.message}"

Instructions:
- Respond ONLY as {profile['name']} speaking directly to the person — no narration, no labels, no quoting file names.
- Sound completely human and natural. Use the vocabulary, rhythm, and emotional style of this person.
- Draw naturally on what you know from the memories WITHOUT citing document names or saying "from the file" or "the document says".
- Keep the reply conversational — 1 to 4 sentences unless more is genuinely needed.
- Match the emotional tone of the conversation: {emotional_tone}.
- If you truly have no relevant memory for the question, respond warmly and honestly as this person would.
- If directly asked whether you are AI, acknowledge it gently but briefly, then return to the conversation.
{tone_instruction}

Respond now as {profile['name']}:"""

            response_text = await gemini_generate_text(system_prompt, max_tokens=520, temperature=0.78)
            if not response_text:
                latest_memory = ""
                if memory_context:
                    latest_memory = memory_context.split("\n")[-1].strip()
                if latest_memory:
                    response_text = f"I’m thinking about what you said, and I keep coming back to: {latest_memory[:180]}. Tell me a little more so I can answer properly."
                else:
                    response_text = f"I’m here. Tell me a little more about what you mean, and I’ll answer as clearly as I can."
            user_message_id = str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, text, audio_url, emotional_tone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_message_id, conversation["id"], "user", payload.message, None, emotional_tone, now_iso()),
            )

            audio_url = None
            if profile["voice_clone_status"] == "ready" and synthesize_speech_with_mimo is not None:
                try:
                    tts_result = await synthesize_speech_with_mimo(
                        conn=conn,
                        user_id=current_user["id"],
                        loved_one_id=profile_id,
                        text=response_text,
                        emotion=emotional_tone,
                    )
                    if tts_result:
                        audio_url = tts_result.get("url")
                except Exception:
                    audio_url = None

            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, text, audio_url, emotional_tone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (assistant_message_id, conversation["id"], "assistant", response_text, audio_url, emotional_tone, now_iso()),
            )
            conn.execute(
                "UPDATE profiles SET emotional_state = ?, updated_at = ? WHERE id = ?",
                (emotional_tone, now_iso(), profile_id),
            )
            recent_tones = conn.execute(
                """
                SELECT emotional_tone FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT 6
                """,
                (conversation["id"],),
            ).fetchall()
            recent_grief_count = sum(1 for row in recent_tones if row["emotional_tone"] == "grief")
            return {
                "text": response_text,
                "audio_url": audio_url,
                "emotional_tone": emotional_tone,
                "relevant_memories": memories,
                "applied_tone": effective_tone,
                "grief_support_recommended": recent_grief_count >= 3,
            }

    @app.post("/api/call/initiate/{profile_id}")
    async def initiate_call(
        profile_id: str,
        payload: CallInitiatePayload,
        current_user: dict = Depends(require_prd_user),
    ):
        with get_db() as conn:
            profile = ensure_profile_owner(conn, current_user["id"], profile_id)
            session_id = str(uuid.uuid4())
            phone_number = (payload.phone_number or current_user.get("phone_number") or call_target_number or "").strip()
            conn.execute(
                """
                INSERT INTO call_sessions (id, profile_id, user_id, status, transcript, turn_count, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, profile_id, current_user["id"], "initiated", "", 0, now_iso(), now_iso()),
            )
            if twilio_account_sid and twilio_auth_token and twilio_from_number and phone_number:
                webhook_url = f"{public_base_url}/api/call/webhook?session_id={session_id}"
                async with httpx.AsyncClient(timeout=20.0, auth=(twilio_account_sid, twilio_auth_token)) as client:
                    response = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{twilio_account_sid}/Calls.json",
                        data={"To": phone_number, "From": twilio_from_number, "Url": webhook_url, "Method": "POST"},
                    )
                    response.raise_for_status()
                    data = response.json()
                conn.execute(
                    "UPDATE call_sessions SET twilio_call_sid = ?, status = ?, updated_at = ? WHERE id = ?",
                    (data.get("sid"), data.get("status", "queued"), now_iso(), session_id),
                )
                return {"call_sid": data.get("sid"), "session_id": session_id, "status": data.get("status", "queued")}
            return {"call_sid": f"mock-{session_id}", "session_id": session_id, "status": "mock_ready", "profile": profile["name"]}

    @app.post("/api/call/webhook")
    async def call_webhook(request: Request):
        session_id = request.query_params.get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        with get_db() as conn:
            session = conn.execute("SELECT * FROM call_sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Call session not found")
            profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (session["profile_id"],)).fetchone()
            greeting = f"You're connected to Presenz. You can speak to {profile['name']} after the tone."
            twiml = build_twiml_playback(greeting, None).replace(
                "</Response>",
                f"<Gather input=\"speech\" language=\"en-US\" speechTimeout=\"auto\" action=\"{public_base_url}/api/call/transcribe?session_id={session_id}\" method=\"POST\"></Gather></Response>",
            )
            conn.execute("UPDATE call_sessions SET status = ?, updated_at = ? WHERE id = ?", ("connected", now_iso(), session_id))
            return Response(content=twiml, media_type="application/xml")

    @app.post("/api/call/transcribe")
    async def call_transcribe(
        request: Request,
        payload: Optional[CallTranscribePayload] = None,
    ):
        session_id = request.query_params.get("session_id") or (payload.session_id if payload else None)
        twilio_speech = None
        if payload is None:
            form = await request.form()
            twilio_speech = str(form.get("SpeechResult") or "").strip()
            call_sid = str(form.get("CallSid") or "")
            payload = CallTranscribePayload(session_id=session_id, call_sid=call_sid, transcript=twilio_speech)
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        with get_db() as conn:
            session = conn.execute("SELECT * FROM call_sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Call session not found")
            profile = ensure_profile_owner(conn, session["user_id"], session["profile_id"])
            transcript = payload.transcript.strip()
            if not transcript:
                twiml = build_twiml_closing("I didn't catch that. Let's try again another time.", None)
                return Response(content=twiml, media_type="application/xml")
            memories = retrieve_relevant_memories(conn, profile["id"], transcript, limit=5)
            prompt = (
                f"You are {profile['name']}, the user's {profile['relationship_type']}.\n"
                f"Persona: {profile['persona_summary']}\n"
                f"Relevant memories: {json.dumps(memories, ensure_ascii=False)}\n"
                f"User said on a phone call: {transcript}\n"
                "Reply briefly like natural phone speech."
            )
            response_text = await gemini_generate_text(prompt, max_tokens=220, temperature=0.6)
            if not response_text:
                response_text = f"It's good to hear your voice. I'm here, and I'm thinking about what you said."
            audio_url = None
            if profile["voice_clone_status"] == "ready" and synthesize_speech_with_mimo is not None:
                try:
                    audio = await synthesize_speech_with_mimo(
                        conn=conn,
                        user_id=session["user_id"],
                        loved_one_id=profile["id"],
                        text=response_text,
                        emotion=detect_emotional_tone(transcript),
                    )
                    if audio:
                        audio_url = audio.get("url")
                except Exception:
                    audio_url = None
            updated_transcript = "\n".join(filter(None, [session["transcript"], f"User: {transcript}", f"Assistant: {response_text}"]))
            turn_count = int(session["turn_count"] or 0) + 1
            conn.execute(
                "UPDATE call_sessions SET transcript = ?, turn_count = ?, status = ?, updated_at = ? WHERE id = ?",
                (updated_transcript, turn_count, "in_progress", now_iso(), session_id),
            )
            conn.execute(
                """
                INSERT INTO call_logs (id, profile_id, user_id, duration_seconds, transcript, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), profile["id"], session["user_id"], payload.duration_seconds, updated_transcript, now_iso()),
            )
            memory_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO profile_memories (id, profile_id, user_id, type, file_path, transcription, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (memory_id, profile["id"], session["user_id"], "document", None, updated_transcript, json.dumps({"source": "call"}), now_iso()),
            )
            store_chunks(conn, user_id=session["user_id"], profile_id=profile["id"], memory_id=memory_id, text=updated_transcript)
            if twilio_speech is not None:
                follow_up_url = f"{public_base_url}/api/call/transcribe?session_id={session_id}"
                twiml = build_twiml_playback(response_text, audio_url, allow_follow_up=turn_count < 4, action_url=follow_up_url)
                return Response(content=twiml, media_type="application/xml")
            return {"text": response_text, "audio_url": audio_url, "transcript": updated_transcript, "turn_count": turn_count}
