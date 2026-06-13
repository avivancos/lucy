import { MetricTile } from "../components/MetricTile";
import { Waterfall } from "../components/Waterfall";
import { demoSessions } from "../lib/api";

export default function Home() {
  const session = demoSessions[0];

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Lucy</p>
          <h1>Ops Command Center</h1>
        </div>
        <div className="statusPill">Live environment</div>
      </header>

      <section className="metricsGrid">
        <MetricTile
          label="Cost per minute"
          value={`$${session.cost_per_minute.toFixed(3)}`}
          detail="STT + LLM + TTS + telephony + RAG + MCP + infra"
          tone="green"
        />
        <MetricTile label="Latency p95" value="620 ms" detail="Target: sub-500 ms perceived" />
        <MetricTile label="Sentiment" value="Positive" detail="0.86 confidence" tone="green" />
        <MetricTile label="Funnel stage" value="Booked" detail="CRM event ready" tone="yellow" />
      </section>

      <section className="gridTwo">
        <section className="panel liveCall">
          <div className="panelHeader">
            <h2>Live Call</h2>
            <span>{session.id}</span>
          </div>
          <div className="transcript">
            <p><strong>Caller</strong> I want to book a demo for next week.</p>
            <p><strong>Lucy</strong> I can help with that. What day works best?</p>
            <p><strong>Caller</strong> Tuesday morning would be perfect.</p>
          </div>
          <div className="callMeta">
            <span>Node: tts_stream</span>
            <span>CRM: synced</span>
            <span>Barge-in: ready</span>
          </div>
        </section>

        <Waterfall />
      </section>

      <section className="gridThree">
        <section className="panel">
          <div className="panelHeader">
            <h2>RAG Inspector</h2>
            <span>cache hit</span>
          </div>
          <p className="muted">
            Retrieved booking policy, demo availability, and qualification notes.
          </p>
        </section>
        <section className="panel">
          <div className="panelHeader">
            <h2>Voice Naturalizer</h2>
            <span>warm / paced</span>
          </div>
          <p className="muted">
            Pace 1.05, warmth 0.72, pause 180ms, graceful interruption style.
          </p>
        </section>
        <section className="panel">
          <div className="panelHeader">
            <h2>MCP Tools</h2>
            <span>2 allowed</span>
          </div>
          <p className="muted">
            crm.upsert_lead and calendar.hold_slot are permissioned and auditable.
          </p>
        </section>
      </section>
    </main>
  );
}
