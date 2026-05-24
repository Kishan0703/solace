"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, setToken } from "@/lib/api";

export default function AuthForm({ mode }) {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const isSignup = mode === "signup";

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const payload = isSignup
        ? { email: form.email, password: form.password, name: form.name }
        : { email: form.email, password: form.password };
      const data = await api(`/api/auth/${isSignup ? "signup" : "login"}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setToken(data.token);
      router.push("/dashboard");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="authShell">
      {/* Left panel — marketing copy */}
      <section className="panel panelPad stack">
        <div className="eyebrow">{isSignup ? "Get started" : "Welcome back"}</div>
        <h1 className="heroTitle" style={{ maxWidth: "10ch" }}>
          {isSignup
            ? "Build a presence from real memory."
            : "Return to your companion workspace."}
        </h1>
        <p className="lead">
          {isSignup
            ? "Create an account, define the relationship, upload real source material, and start a grounded conversation."
            : "Log back in to continue refining the profile, upload more context, and chat against your memory vault."}
        </p>
        <div className="card">
          <strong style={{ fontSize: "0.9rem", display: "block", marginBottom: 6 }}>
            What makes this useful
          </strong>
          <p className="lead" style={{ fontSize: "0.85rem" }}>
            You are not just naming a character. You are creating a profile that can recall
            uploaded documents, transcripts, and memory fragments.
          </p>
        </div>
        <div className="tagRow">
          <div className="tag">Grounded answers</div>
          <div className="tag">Private profiles</div>
          <div className="tag">Voice replies</div>
        </div>
      </section>

      {/* Right panel — form */}
      <form className="panel panelPad stack" onSubmit={onSubmit} style={{ gap: 18 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            {isSignup ? "Create account" : "Log in"}
          </div>
          <h2
            style={{
              fontFamily: "'Lora', Georgia, serif",
              fontSize: "1.75rem",
              fontWeight: 600,
              letterSpacing: "-0.03em",
              lineHeight: 1.2,
            }}
          >
            {isSignup ? "Start with a clean, private workspace." : "Pick up where you left off."}
          </h2>
        </div>

        {isSignup && (
          <label className="field">
            <span>Name</span>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Your name"
              required
            />
          </label>
        )}

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="you@example.com"
            required
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            placeholder="••••••••"
            required
          />
        </label>

        {error ? <div className="danger">{error}</div> : null}

        <button className="button" disabled={loading} type="submit" style={{ width: "100%", minHeight: 46 }}>
          {loading ? "Working…" : isSignup ? "Create account" : "Log in"}
        </button>

        <p style={{ fontSize: "0.82rem", color: "var(--ink-soft)", textAlign: "center" }}>
          {isSignup ? (
            <>Already have an account? <a href="/login" style={{ color: "var(--accent)", fontWeight: 500 }}>Log in</a></>
          ) : (
            <>No account yet? <a href="/signup" style={{ color: "var(--accent)", fontWeight: 500 }}>Sign up</a></>
          )}
        </p>
      </form>
    </main>
  );
}
