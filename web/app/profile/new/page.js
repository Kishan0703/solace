"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";

export default function NewProfilePage() {
  const router = useRouter();
  const [form, setForm] = useState({
    name: "",
    relationship_type: "parent",
    custom_tone: "",
    privacy_mode: "private",
    living_person: false,
    consent_flag: false,
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function createProfile(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api("/api/profiles", {
        method: "POST",
        body: JSON.stringify(form),
      });
      router.push(`/profile/${data.id}`);
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

  return (
    <main style={{ maxWidth: 560, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <div className="eyebrow" style={{ marginBottom: 8 }}>New Profile</div>
        <h1
          style={{
            fontFamily: "'Lora', Georgia, serif",
            fontSize: "2rem",
            fontWeight: 600,
            letterSpacing: "-0.03em",
          }}
        >
          Create a new presence.
        </h1>
      </div>

      <form className="panel panelPad stack" onSubmit={createProfile} style={{ gap: 18 }}>
        <label className="field">
          <span>Name</span>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Who are you creating this for?"
            required
          />
        </label>

        <label className="field">
          <span>Relationship</span>
          <select
            value={form.relationship_type}
            onChange={(e) => setForm({ ...form, relationship_type: e.target.value })}
          >
            <option value="parent">Parent</option>
            <option value="partner">Partner</option>
            <option value="sibling">Sibling</option>
            <option value="friend">Friend</option>
            <option value="other">Other</option>
          </select>
        </label>

        <label className="field">
          <span>Tone guidance <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(optional)</span></span>
          <textarea
            value={form.custom_tone}
            onChange={(e) => setForm({ ...form, custom_tone: e.target.value })}
            placeholder="Warm, direct, practical, funny, short replies, gentle reassurance…"
            style={{ minHeight: 80 }}
          />
        </label>

        <label className="field">
          <span>Privacy</span>
          <select
            value={form.privacy_mode}
            onChange={(e) => setForm({ ...form, privacy_mode: e.target.value })}
          >
            <option value="private">Private — only you</option>
            <option value="family">Family — shared access</option>
          </select>
        </label>

        <div className="divider" />

        <label
          className="row"
          style={{ alignItems: "center", gap: 10, cursor: "pointer" }}
        >
          <input
            type="checkbox"
            checked={form.living_person}
            onChange={(e) => setForm({ ...form, living_person: e.target.checked })}
            style={{ width: 16, height: 16, flexShrink: 0 }}
          />
          <span style={{ fontSize: "0.88rem", color: "var(--ink-soft)" }}>
            This is a living person profile
          </span>
        </label>

        <label
          className="row"
          style={{ alignItems: "center", gap: 10, cursor: "pointer" }}
        >
          <input
            type="checkbox"
            checked={form.consent_flag}
            onChange={(e) => setForm({ ...form, consent_flag: e.target.checked })}
            style={{ width: 16, height: 16, flexShrink: 0 }}
          />
          <span style={{ fontSize: "0.88rem", color: "var(--ink-soft)" }}>
            I confirm I have consent to create this profile
          </span>
        </label>

        {error ? <div className="danger">{error}</div> : null}

        <button
          className="button"
          type="submit"
          disabled={loading}
          style={{ width: "100%", minHeight: 46 }}
        >
          {loading ? "Creating…" : "Create profile"}
        </button>
      </form>
    </main>
  );
}
