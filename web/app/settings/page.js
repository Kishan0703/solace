"use client";

import { useRouter } from "next/navigation";
import { setToken } from "@/lib/api";

export default function SettingsPage() {
  const router = useRouter();

  function logout() {
    setToken("");
    router.push("/login");
  }

  return (
    <main className="stack" style={{ maxWidth: 640 }}>
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Settings</div>
        <h1 className="heroTitle" style={{ fontSize: "2.2rem" }}>Account controls.</h1>
      </div>

      <div className="panel panelPad stack">
        <div>
          <div className="sectionTitle">Session</div>
          <p className="lead" style={{ fontSize: "0.88rem" }}>
            You are currently logged in. Logging out will clear your local auth token.
          </p>
        </div>
        <button
          className="ghostButton"
          onClick={logout}
          type="button"
          style={{ alignSelf: "flex-start" }}
        >
          Log out
        </button>
      </div>

      <div className="panel panelPad stack">
        <div>
          <div className="sectionTitle">Data &amp; Privacy</div>
          <p className="lead" style={{ fontSize: "0.88rem" }}>
            This MVP includes auth token storage, account-level routing, and manual Vercel
            environment setup. Data export and full account deletion policy still require
            external operational handling.
          </p>
        </div>
      </div>
    </main>
  );
}
