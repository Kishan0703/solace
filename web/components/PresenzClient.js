"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";

/* ─── Helpers ────────────────────────────────────────────────── */
function MemoryTypeLabel(type) {
  return (
    { document: "Document", chat: "Chat Export", audio: "Audio", video: "Video", photo: "Photo" }[type] || type
  );
}

function formatTime(value) {
  try {
    return new Date(value).toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

/* ─── VoiceStatusBadge ───────────────────────────────────────── */
export function VoiceStatusBadge({ status }) {
  const ready = status === "ready";
  const label =
    ready ? "Voice Ready" : status === "processing" ? "Processing…" : "Voice Pending";
  return (
    <div className="pill">
      <span className={`statusDot ${ready ? "ready" : ""}`} />
      {label}
    </div>
  );
}

/* ─── EmotionalToast ─────────────────────────────────────────── */
export function EmotionalToast({ tone, show }) {
  if (!show) return null;
  return <div className="toast">Conversation tone detected: {tone}</div>;
}

/* ─── PresenceCard ───────────────────────────────────────────── */
export function PresenceCard({ profile }) {
  return (
    <Link className="presenceCard" href={`/profile/${profile.id}`}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem", marginBottom: 2 }}>{profile.name}</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>{profile.relationship_type}</div>
        </div>
        <VoiceStatusBadge status={profile.voice_clone_status} />
      </div>
      <p className="lead" style={{ fontSize: "0.82rem", marginBottom: 12 }}>
        {profile.custom_tone
          ? `Tone: ${profile.custom_tone}`
          : "No tone guidance set yet."}
      </p>
      <div className="tag" style={{ display: "inline-flex" }}>Open profile →</div>
    </Link>
  );
}

/* ─── SourceList ─────────────────────────────────────────────── */
function SourceList({ memories, title = "Uploaded Sources", empty = "No files uploaded yet." }) {
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="sectionTitle">{title}</div>
      <div className="sourceList">
        {memories.length
          ? memories.map((memory) => {
              const filename =
                memory.metadata?.original_filename ||
                memory.file_path?.split("/").pop() ||
                `${memory.type}-${memory.id}`;
              const excerpt =
                memory.transcription ||
                memory.metadata?.caption ||
                "No extracted text available yet.";
              return (
                <article className="sourceItem" key={memory.id}>
                  <div
                    className="row"
                    style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}
                  >
                    <div className="sourceName" style={{ flex: 1, minWidth: 0 }}>{filename}</div>
                    <div className="pill" style={{ flexShrink: 0 }}>
                      {MemoryTypeLabel(memory.type)}
                    </div>
                  </div>
                  <div className="memoryMeta">{formatTime(memory.created_at)}</div>
                  <div className="sourceExcerpt">{excerpt}</div>
                </article>
              );
            })
          : <div style={{ color: "var(--ink-faint)", fontSize: "0.82rem", padding: "8px 0" }}>{empty}</div>}
      </div>
    </div>
  );
}

/* ─── UploadZone ─────────────────────────────────────────────── */
export function UploadZone({ profileId }) {
  const router = useRouter();
  const [files, setFiles] = useState([]);
  const [job, setJob] = useState(null);
  const [memories, setMemories] = useState([]);
  const [error, setError] = useState("");

  async function loadMemories() {
    try {
      setMemories(await api(`/api/memories/${profileId}`));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.push("/login");
    }
  }

  useEffect(() => {
    loadMemories();
  }, [profileId]);

  async function submit() {
    if (!files.length) return;
    setError("");
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    try {
      const data = await api(`/api/upload/${profileId}`, { method: "POST", body: form });
      const jobId = data.job_ids?.[0];
      if (jobId) {
        const timer = setInterval(async () => {
          try {
            const status = await api(`/api/upload/${jobId}/status`);
            setJob(status);
            if (status.status === "completed" || status.status === "failed") {
              clearInterval(timer);
              loadMemories();
              setFiles([]);
            }
          } catch (err) {
            clearInterval(timer);
            if (err instanceof ApiError && err.status === 401) {
              router.push("/login");
              return;
            }
            setError(err.message);
          }
        }, 1200);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
    }
  }

  return (
    <div className="workspace">
      <section className="panel panelPad stack">
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Uploads</div>
          <h1 style={{ fontFamily: "'Lora', Georgia, serif", fontSize: "2rem", fontWeight: 600, letterSpacing: "-0.03em", marginBottom: 10 }}>
            Build the memory base.
          </h1>
          <p className="lead">
            Upload documents, audio, chat exports, photos, and video. The app extracts
            usable text so the chat can answer from it later.
          </p>
        </div>
        <label className="field">
          <span>Select files</span>
          <input multiple type="file" onChange={(e) => setFiles(Array.from(e.target.files || []))} />
        </label>
        {files.length ? (
          <div className="uploadQueue">
            {files.map((file) => (
              <div className="sourceItem" key={`${file.name}-${file.size}`}>
                <div className="sourceName">{file.name}</div>
                <div className="memoryMeta">{Math.round(file.size / 1024)} KB</div>
              </div>
            ))}
          </div>
        ) : null}
        <div className="composerBar">
          <div className="helperText">Supports .docx, .txt, .zip, audio, video, and images.</div>
          <button className="button" onClick={submit} type="button">
            Upload files
          </button>
        </div>
        {job ? (
          <div className="card">
            <strong>Status:</strong> {job.status} · {job.progress}% ·{" "}
            {job.current_file || "queued"}
          </div>
        ) : null}
        {error ? <div className="danger">{error}</div> : null}
      </section>

      <aside className="panel panelPad">
        <SourceList memories={memories} title="Processed Files" />
      </aside>
    </div>
  );
}

/* ─── MemoryTimeline ─────────────────────────────────────────── */
export function MemoryTimeline({ memories }) {
  return (
    <div className="timeline">
      {memories.map((memory) => {
        const filename =
          memory.metadata?.original_filename ||
          memory.file_path?.split("/").pop() ||
          memory.type;
        return (
          <article className="card" key={memory.id}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <strong style={{ fontSize: "0.9rem" }}>{filename}</strong>
              <span className="memoryMeta">{formatTime(memory.created_at)}</span>
            </div>
            <div className="memoryMeta" style={{ marginBottom: 6 }}>
              {MemoryTypeLabel(memory.type)}
            </div>
            <p style={{ fontSize: "0.85rem", color: "var(--ink-soft)", lineHeight: 1.6 }}>
              {memory.transcription || memory.metadata?.caption || "No extracted text available yet."}
            </p>
          </article>
        );
      })}
    </div>
  );
}

/* ─── ChatWindow ─────────────────────────────────────────────── */
export function ChatWindow({ profileId }) {
  const router = useRouter();
  const [profile, setProfile] = useState(null);
  const [memories, setMemories] = useState([]);
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState("");
  const [toneOverride, setToneOverride] = useState("");
  const [loading, setLoading] = useState(false);
  const [tone, setTone] = useState("neutral");
  const [showTone, setShowTone] = useState(false);
  const [error, setError] = useState("");
  const [sourceHits, setSourceHits] = useState([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const logRef = useRef(null);

  async function loadContext() {
    try {
      const [profileData, memoryData, historyData] = await Promise.all([
        api(`/api/profiles/${profileId}`),
        api(`/api/memories/${profileId}`),
        api(`/api/chat/${profileId}/history`),
      ]);
      setProfile(profileData);
      setMemories(memoryData);
      setHistory(historyData);
      setToneOverride(profileData.custom_tone || "");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
    }
  }

  useEffect(() => {
    loadContext();
  }, [profileId]);

  // Auto-scroll to latest message
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [history]);

  async function send() {
    if (!message.trim()) return;
    setLoading(true);
    try {
      const response = await api(`/api/chat/${profileId}`, {
        method: "POST",
        body: JSON.stringify({ message, tone_override: toneOverride }),
      });
      setTone(response.emotional_tone);
      setShowTone(response.emotional_tone !== "neutral");
      setSourceHits(response.relevant_memories || []);
      setMessage("");
      setError("");
      await loadContext();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function onComposerKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <div className={`chatShell ${showSidebar ? "" : "sidebarHidden"}`}>
      {/* ── Left: main chat column ── */}
      <section className="chatMain">
        {/* Profile info strip */}
        <div className="chatHeader panel panelPad" style={{ padding: "16px 20px" }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <span style={{ fontWeight: 700, fontSize: "1rem" }}>
                {profile ? profile.name : "…"}
              </span>
              {profile && (
                <span className="muted" style={{ marginLeft: 10, fontSize: "0.82rem" }}>
                  {profile.relationship_type}
                </span>
              )}
            </div>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              {profile?.custom_tone && (
                <div className="tag">Tone: {profile.custom_tone}</div>
              )}
              {profile && <VoiceStatusBadge status={profile.voice_clone_status} />}
              <button
                className="ghostButton"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 12px",
                  fontSize: "0.82rem",
                  cursor: "pointer",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--line)"
                }}
                onClick={() => setShowSidebar(!showSidebar)}
                title={showSidebar ? "Hide Files" : "Show Files"}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <rect width="18" height="18" x="3" y="3" rx="2" />
                  <path d="M9 3v18" />
                  {showSidebar ? (
                    <path d="M16 15l-3-3 3-3" />
                  ) : (
                    <path d="M13 9l3 3-3 3" />
                  )}
                </svg>
                <span>{showSidebar ? "Hide Files" : "Show Files"}</span>
              </button>
            </div>
          </div>
        </div>

        {/* Chat body: log + composer in one flex panel */}
        <div className="chatBody panel">
          {/* Message log */}
          <div className="chatLog" ref={logRef}>
            {history.length === 0 && (
              <div className="chatEmptyState">
                <strong>Start the conversation</strong>
                Ask something about the uploaded files, memories, or transcripts.
              </div>
            )}
            {history.map((item) => (
              <div className={`bubble ${item.role}`} key={item.id}>
                {item.text}
                {item.audio_url ? (
                  <audio controls src={item.audio_url} style={{ width: "100%", marginTop: 10 }} />
                ) : null}
                <div className="bubbleMeta">
                  {item.emotional_tone || "neutral"} · {formatTime(item.created_at)}
                </div>
              </div>
            ))}
            {loading && (
              <div className="bubble assistant" style={{ opacity: 0.5 }}>
                Generating…
              </div>
            )}
          </div>

          {/* Composer */}
          <div className="composer">
            <label className="field">
              <span>Tone override</span>
              <input
                value={toneOverride}
                onChange={(e) => setToneOverride(e.target.value)}
                placeholder="e.g. warm, blunt, humorous, reassuring…"
              />
            </label>
            <label className="field">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={onComposerKeyDown}
                placeholder="Ask about something from an uploaded file, memory, or transcript…"
                style={{ minHeight: 72 }}
              />
            </label>
            {error ? <div className="danger">{error}</div> : null}
            <div className="composerBar">
              <span className="helperText">Enter sends · Shift+Enter for new line</span>
              <button className="button" disabled={loading || !message.trim()} onClick={send} type="button">
                {loading ? "Generating…" : "Send"}
              </button>
            </div>
          </div>

          <EmotionalToast tone={tone} show={showTone} />
        </div>
      </section>

      {/* ── Right: sidebar ── */}
      {showSidebar && (
        <aside className="chatSidebar">
          <div className="panel panelPad">
            <SourceList
              memories={sourceHits.map((item, index) => ({
                id: `${item.memory_id}-${index}`,
                type: item.type,
                transcription: item.text,
                metadata: item.metadata || {},
                created_at: new Date().toISOString(),
              }))}
              title="Sources used"
              empty="Ask a question and the closest matching sources will appear here."
            />
          </div>
          <div className="panel panelPad">
            <SourceList memories={memories.slice(0, 10)} title="Uploaded files" />
          </div>
        </aside>
      )}
    </div>
  );
}

/* ─── CallScreen ─────────────────────────────────────────────── */
export function CallScreen({ profileId }) {
  const router = useRouter();
  const [phoneNumber, setPhoneNumber] = useState("");
  const [status, setStatus] = useState("");
  const [transcript, setTranscript] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [error, setError] = useState("");

  async function initiate() {
    try {
      const data = await api(`/api/call/initiate/${profileId}`, {
        method: "POST",
        body: JSON.stringify({ phone_number: phoneNumber }),
      });
      setStatus(`${data.status} · ${data.call_sid}`);
      setSessionId(data.session_id || "");
      setError("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
    }
  }

  async function mockTurn() {
    try {
      const data = await api("/api/call/transcribe", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, transcript, duration_seconds: 8 }),
      });
      setTranscript(data.transcript);
      setStatus(`turn ${data.turn_count} complete`);
      setError("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
    }
  }

  return (
    <div className="panel panelPad stack">
      <div className="sectionTitle">Voice Call</div>
      <label className="field">
        <span>Phone number</span>
        <input value={phoneNumber} onChange={(e) => setPhoneNumber(e.target.value)} placeholder="+1…" />
      </label>
      <button className="button" onClick={initiate} type="button">
        Initiate call
      </button>
      <div className="card" style={{ fontSize: "0.85rem" }}>
        {status || "No active call session yet."}
      </div>
      <label className="field">
        <span>Mock call transcript</span>
        <textarea
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          placeholder="Use this when Twilio is not configured."
        />
      </label>
      <button
        className="ghostButton"
        disabled={!sessionId || !transcript.trim()}
        onClick={mockTurn}
        type="button"
      >
        Send mock turn
      </button>
      {error ? <div className="danger">{error}</div> : null}
    </div>
  );
}

/* ─── useProfile hook ────────────────────────────────────────── */
export function useProfile(profileId) {
  const router = useRouter();
  const [profile, setProfile] = useState(null);
  const [memories, setMemories] = useState([]);

  async function refresh() {
    try {
      const [profileData, memoryData] = await Promise.all([
        api(`/api/profiles/${profileId}`),
        api(`/api/memories/${profileId}`),
      ]);
      setProfile(profileData);
      setMemories(memoryData);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.push("/login");
    }
  }

  useEffect(() => {
    refresh();
  }, [profileId]);

  return useMemo(() => ({ profile, memories, refresh }), [profile, memories]);
}
