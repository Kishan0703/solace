"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { VoiceStatusBadge, useProfile } from "@/components/PresenzClient";

export default function ProfileHomePage() {
  const params = useParams();
  const { profile } = useProfile(params.id);

  if (!profile) {
    return (
      <div style={{ padding: "48px 0", color: "var(--ink-faint)", fontSize: "0.9rem" }}>
        Loading…
      </div>
    );
  }

  return (
    <main className="stack" style={{ gap: 24 }}>
      <section className="hero">
        {/* Left — profile info */}
        <div className="panel panelPad stack">
          <div className="eyebrow">{profile.relationship_type}</div>
          <h1 className="heroTitle" style={{ maxWidth: "14ch" }}>{profile.name}</h1>
          <p className="lead">
            {profile.persona_summary || "Upload memories to generate the persona summary."}
          </p>
          <div className="row">
            <VoiceStatusBadge status={profile.voice_clone_status} />
            <div className="pill">AI Companion</div>
            {profile.custom_tone && (
              <div className="pill">Tone: {profile.custom_tone}</div>
            )}
          </div>
        </div>

        {/* Right — actions */}
        <div className="panel panelPad stack">
          <div className="sectionTitle">Actions</div>
          <Link
            className="button"
            href={`/profile/${profile.id}/chat`}
            style={{ justifyContent: "center" }}
          >
            Open chat
          </Link>
          <Link
            className="ghostButton"
            href={`/profile/${profile.id}/upload`}
            style={{ justifyContent: "center" }}
          >
            Upload files
          </Link>
          <Link
            className="ghostButton"
            href={`/profile/${profile.id}/memories`}
            style={{ justifyContent: "center" }}
          >
            Review memories
          </Link>
          <Link
            className="ghostButton"
            href={`/profile/${profile.id}/call`}
            style={{ justifyContent: "center" }}
          >
            Start a call
          </Link>
        </div>
      </section>
    </main>
  );
}
