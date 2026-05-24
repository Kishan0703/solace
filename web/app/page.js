import Link from "next/link";

export default function HomePage() {
  return (
    <main className="stack" style={{ gap: 24 }}>
      {/* Hero */}
      <section className="hero">
        <div className="panel panelPad stack">
          <div className="eyebrow">Presenz</div>
          <h1 className="heroTitle">A private AI memory companion built from what they actually said.</h1>
          <p className="lead">
            Presenz turns documents, chat exports, audio, photos, and memories into a grounded
            companion. Designed for intimate recall — not generic chatbot roleplay.
          </p>
          <div className="row">
            <Link className="button" href="/signup">Get started</Link>
            <Link className="ghostButton" href="/login">Log in</Link>
          </div>
          <div className="tagRow">
            <div className="tag">Text chat</div>
            <div className="tag">Voice replies</div>
            <div className="tag">Document recall</div>
            <div className="tag">Private profiles</div>
          </div>
        </div>

        <div className="panel panelPad stack">
          <div className="kpi">
            <div className="eyebrow">How it works</div>
            <div className="kpiNumber">Upload. Build. Talk.</div>
            <p className="lead" style={{ fontSize: "0.85rem" }}>
              The app ingests uploaded material, extracts usable memory context, and uses
              that to answer in a relationship-aware tone.
            </p>
          </div>
          <div className="divider" />
          <div className="card">
            <strong style={{ fontSize: "0.88rem", display: "block", marginBottom: 4 }}>
              1. Upload source material
            </strong>
            <p className="lead" style={{ fontSize: "0.83rem" }}>
              Letters, chat histories, audio, video, and photos are processed into searchable memory fragments.
            </p>
          </div>
          <div className="card">
            <strong style={{ fontSize: "0.88rem", display: "block", marginBottom: 4 }}>
              2. Shape the companion
            </strong>
            <p className="lead" style={{ fontSize: "0.83rem" }}>
              Set the relationship and add your own tone guidance so the voice feels right for you.
            </p>
          </div>
          <div className="card">
            <strong style={{ fontSize: "0.88rem", display: "block", marginBottom: 4 }}>
              3. Ask real questions
            </strong>
            <p className="lead" style={{ fontSize: "0.83rem" }}>
              The chat surfaces which uploaded source it is pulling from instead of answering vaguely.
            </p>
          </div>
        </div>
      </section>

      {/* Feature strip */}
      <section className="grid three">
        <article className="panel panelPad">
          <div className="sectionTitle">Grounded Memory</div>
          <p className="lead" style={{ fontSize: "0.88rem" }}>
            Answers are shaped by uploaded files and recent conversation history, not just a persona prompt.
          </p>
        </article>
        <article className="panel panelPad">
          <div className="sectionTitle">Relationship Aware</div>
          <p className="lead" style={{ fontSize: "0.88rem" }}>
            Parent, partner, sibling, friend, or other. The relationship stays part of every response.
          </p>
        </article>
        <article className="panel panelPad">
          <div className="sectionTitle">Private by Default</div>
          <p className="lead" style={{ fontSize: "0.88rem" }}>
            Each profile is isolated per account with authenticated access to files, memories, and history.
          </p>
        </article>
      </section>
    </main>
  );
}
