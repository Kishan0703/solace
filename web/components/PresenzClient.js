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

function audioChunksToWavBlob(chunks, sampleRate) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + totalLength * 2);
  const view = new DataView(buffer);

  const writeString = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + totalLength * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, totalLength * 2, true);

  let offset = 44;
  chunks.forEach((chunk) => {
    for (let index = 0; index < chunk.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[index]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  });

  return new Blob([buffer], { type: "audio/wav" });
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
          }
        }, 1200);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
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
            Upload documents, audio, chat exports, photos, and video. Everything is kept ready for chat and voice.
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
            <strong>Status:</strong> {job.status} · {job.progress}%
          </div>
        ) : null}
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
            <p style={{ fontSize: "0.85rem", color: "var(--ink-soft)", lineHeight: 1.6 }}>Ready for chat and voice.</p>
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
  const [sourceHits, setSourceHits] = useState([]);
  const logRef = useRef(null);

  async function loadContext() {
    try {
      const [profileData, memoryData, historyData] = await Promise.all([
        api(`/api/profiles/${profileId}`),
        api(`/api/memories/${profileId}`),
        api(`/api/chat-history/${profileId}`),
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
      const response = await api(`/api/chat`, {
        method: "POST",
        body: JSON.stringify({ loved_one_id: profileId, message, mode: "text" }),
      });
      setTone(response.emotion_detected || "neutral");
      setShowTone((response.emotion_detected || "neutral") !== "neutral");
      setSourceHits(response.memory_refs || []);
      setMessage("");
      await loadContext();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
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
    <div className="chatShell">
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
                className="button"
                style={{ marginLeft: 8 }}
                disabled={!profile || profile.voice_clone_status === "processing" || profile.voice_clone_status === "ready"}
                onClick={async () => {
                  try {
                    await api(`/api/loved-ones/${profileId}/voice/clone`, { method: "POST" });
                    await loadContext();
                  } catch (err) {
                    console.error(err);
                    // ignore, loadContext will show updated status
                    await loadContext();
                  }
                }}
                title="Create voice clone from uploaded sample"
              >
                Create Clone
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
                placeholder="Ask anything about this person."
                style={{ minHeight: 72 }}
              />
            </label>
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
    </div>
  );
}

/* ─── CallScreen ─────────────────────────────────────────────── */
export function CallScreen({ profileId }) {
  const router = useRouter();
  const [profile, setProfile] = useState(null);
  const [history, setHistory] = useState([]);
  const [status, setStatus] = useState("Ready to listen");
  const [transcript, setTranscript] = useState("");
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [isSupported, setIsSupported] = useState(true);
  const mediaStreamRef = useRef(null);
  const audioContextRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const processorNodeRef = useRef(null);
  const zeroGainRef = useRef(null);
  const finalizeRecordingRef = useRef(null);
  const chunksRef = useRef([]);
  const sampleRateRef = useRef(16000);
  const stopTimerRef = useRef(null);
  const audioRef = useRef(null);

  async function loadProfile() {
    try {
      const data = await api(`/api/profiles/${profileId}`);
      setProfile(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
    }
  }

  async function loadHistory() {
    try {
      const data = await api(`/api/chat-history/${profileId}`);
      setHistory(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
    }
  }

  useEffect(() => {
    loadProfile();
    loadHistory();
  }, [profileId]);

  useEffect(() => {
    const supported = Boolean(navigator.mediaDevices?.getUserMedia && (window.AudioContext || window.webkitAudioContext));
    setIsSupported(supported);
    if (!supported) {
      setStatus("Voice recording is not supported in this browser");
    }
    return () => {
      if (stopTimerRef.current) {
        clearTimeout(stopTimerRef.current);
      }
      try {
        processorNodeRef.current?.disconnect?.();
        sourceNodeRef.current?.disconnect?.();
        zeroGainRef.current?.disconnect?.();
        audioContextRef.current?.close?.();
      } catch {
        // ignore
      }
      mediaStreamRef.current?.getTracks?.().forEach((track) => track.stop());
      finalizeRecordingRef.current = null;
    };
  }, []);

  useEffect(() => {
    const lastAssistant = [...history].reverse().find((item) => item.role === "assistant" && item.audio_url);
    if (!lastAssistant || !audioRef.current) return;
    audioRef.current.src = lastAssistant.audio_url;
    audioRef.current.play().catch(() => {
      // autoplay may be blocked until user gesture
    });
  }, [history]);

  async function sendVoiceTurn(messageText) {
    const text = messageText.trim();
    if (!text) return;
    setSpeaking(true);
    setStatus("Thinking…");
    try {
      const response = await api(`/api/chat`, {
        method: "POST",
        body: JSON.stringify({ loved_one_id: profileId, message: text, mode: "voice" }),
      });
      setTranscript(text);
      setHistory((current) => [
        ...current,
        {
          id: `user-${Date.now()}`,
          role: "user",
          text,
          audio_url: null,
          emotional_tone: response.applied_tone || "neutral",
          created_at: new Date().toISOString(),
        },
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          text: response.response_text,
          audio_url: response.response_audio_url,
          emotional_tone: response.emotion_detected || "neutral",
          created_at: new Date().toISOString(),
        },
      ]);
      setStatus(response.response_audio_url ? "Voice reply ready" : "Reply ready");
      setError("");
      if (response.response_audio_url && audioRef.current) {
        audioRef.current.src = response.response_audio_url;
        await audioRef.current.play();
      }
      await loadHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err.message);
      setStatus("Could not send voice turn");
    } finally {
      setSpeaking(false);
    }
  }

  async function uploadRecordedAudio(blob) {
    const form = new FormData();
    form.append("audio", blob, "voice.wav");
    const response = await api(`/api/chat/${profileId}/voice-turn`, {
      method: "POST",
      body: form,
    });
    const spokenText = response.transcript || transcript || "";
    setTranscript(spokenText);
    setHistory((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: "user",
        text: spokenText,
        audio_url: null,
        emotional_tone: response.emotional_tone || "neutral",
        created_at: new Date().toISOString(),
      },
      {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        text: response.text,
        audio_url: response.audio_url,
        emotional_tone: response.emotional_tone || "neutral",
        created_at: new Date().toISOString(),
      },
    ]);
    setStatus(response.audio_url ? "Voice reply ready" : "Reply ready");
    setError("");
    if (response.audio_url && audioRef.current) {
      audioRef.current.src = response.audio_url;
      await audioRef.current.play();
    }
    await loadHistory();
  }

  async function startListening() {
    if (!isSupported) return;
    if (speaking) return;
    setTranscript("");
    setStatus("Listening… say something like hi");
    setListening(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;
      chunksRef.current = [];
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        throw new Error("Audio recording is not supported in this browser");
      }
      const audioContext = new AudioContextClass();
      audioContextRef.current = audioContext;
      sampleRateRef.current = audioContext.sampleRate || 16000;
      const source = audioContext.createMediaStreamSource(stream);
      sourceNodeRef.current = source;
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorNodeRef.current = processor;
      const zeroGain = audioContext.createGain();
      zeroGain.gain.value = 0;
      zeroGainRef.current = zeroGain;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        chunksRef.current.push(new Float32Array(input));
      };
      source.connect(processor);
      processor.connect(zeroGain);
      zeroGain.connect(audioContext.destination);
      const finalizeRecording = async () => {
        finalizeRecordingRef.current = null;
        try {
          const blob = audioChunksToWavBlob(chunksRef.current, sampleRateRef.current);
          chunksRef.current = [];
          if (!blob.size) {
            setStatus("No speech detected. Try again.");
            return;
          }
          setSpeaking(true);
          setStatus("Transcribing…");
          await uploadRecordedAudio(blob);
        } catch (err) {
          setStatus("Could not process that voice note");
        } finally {
          setSpeaking(false);
          setListening(false);
          try {
            processor.disconnect();
            source.disconnect();
            zeroGain.disconnect();
            await audioContext.close();
          } catch {
            // ignore
          }
          mediaStreamRef.current?.getTracks?.().forEach((track) => track.stop());
          mediaStreamRef.current = null;
          audioContextRef.current = null;
          sourceNodeRef.current = null;
          processorNodeRef.current = null;
          zeroGainRef.current = null;
        }
      };
      if (stopTimerRef.current) {
        clearTimeout(stopTimerRef.current);
      }
      finalizeRecordingRef.current = finalizeRecording;
      stopTimerRef.current = setTimeout(() => {
        finalizeRecording();
      }, 6500);
    } catch (err) {
      setListening(false);
      setStatus("Please allow microphone access and try again");
    }
  }

  function stopListening() {
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    const finalizeRecording = finalizeRecordingRef.current;
    if (finalizeRecording) {
      finalizeRecordingRef.current = null;
      finalizeRecording();
      return;
    }
    try {
      processorNodeRef.current?.disconnect?.();
      sourceNodeRef.current?.disconnect?.();
      zeroGainRef.current?.disconnect?.();
      audioContextRef.current?.close?.();
    } finally {
      setListening(false);
      mediaStreamRef.current?.getTracks?.().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      audioContextRef.current = null;
      sourceNodeRef.current = null;
      processorNodeRef.current = null;
      zeroGainRef.current = null;
    }
  }

  async function sendTypedTurn() {
    await sendVoiceTurn(transcript);
  }

  function onComposerKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendTypedTurn();
    }
  }

  return (
    <div className="panel panelPad stack">
      <div className="sectionTitle">Talk with them</div>
      <p className="lead" style={{ marginBottom: 0 }}>
        Press start, say hi, and the assistant will answer back in voice.
      </p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button className="button" onClick={listening ? stopListening : startListening} type="button" disabled={!isSupported || speaking}>
          {listening ? "Stop listening" : "Start talking"}
        </button>
        <button className="ghostButton" onClick={() => sendVoiceTurn(transcript)} type="button" disabled={speaking || !transcript.trim()}>
          Send current words
        </button>
      </div>
      <div className="card" style={{ fontSize: "0.85rem" }}>
        {status}
      </div>
      <label className="field">
        <span>Transcript</span>
        <textarea
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          onKeyDown={onComposerKeyDown}
          placeholder="You can also type here if your browser doesn’t support voice input."
        />
      </label>
      <button
        className="ghostButton"
        disabled={speaking || !transcript.trim()}
        onClick={sendTypedTurn}
        type="button"
      >
        Reply with voice
      </button>
      <audio ref={audioRef} controls style={{ width: "100%" }} />
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
