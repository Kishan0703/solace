"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PresenceCard } from "@/components/PresenzClient";
import { ApiError, api } from "@/lib/api";

export default function DashboardPage() {
  const router = useRouter();
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/api/profiles")
      .then((data) => {
        setProfiles(data);
        setLoading(false);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setProfiles([]);
        setLoading(false);
      });
  }, [router]);

  return (
    <main className="stack" style={{ gap: 24 }}>
      <section
        className="row"
        style={{ justifyContent: "space-between", alignItems: "flex-end" }}
      >
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Dashboard</div>
          <h1
            style={{
              fontFamily: "'Lora', Georgia, serif",
              fontSize: "2rem",
              fontWeight: 600,
              letterSpacing: "-0.03em",
            }}
          >
            Presence profiles.
          </h1>
        </div>
        <Link className="button" href="/profile/new">
          + New Profile
        </Link>
      </section>

      {loading ? (
        <div style={{ color: "var(--ink-faint)", fontSize: "0.9rem", padding: "24px 0" }}>
          Loading profiles…
        </div>
      ) : profiles.length === 0 ? (
        <div className="panel panelPad" style={{ textAlign: "center", padding: 48 }}>
          <p style={{ color: "var(--ink-soft)", marginBottom: 16 }}>
            No profiles yet. Create your first presence to get started.
          </p>
          <Link className="button" href="/profile/new">
            Create a profile
          </Link>
        </div>
      ) : (
        <section className="grid three">
          {profiles.map((profile) => (
            <PresenceCard key={profile.id} profile={profile} />
          ))}
        </section>
      )}
    </main>
  );
}
