const segments = [
  ["STT", 145, "#63a7ff"],
  ["RAG", 38, "#35d07f"],
  ["LLM", 210, "#f1c84b"],
  ["MCP", 42, "#b28cff"],
  ["TTS", 95, "#ff8f70"],
  ["Transport", 32, "#9aa7b7"]
] as const;

export function Waterfall() {
  const total = segments.reduce((sum, [, value]) => sum + value, 0);

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Trace Waterfall</h2>
        <span>{total} ms total</span>
      </div>
      <div className="waterfall">
        {segments.map(([label, value, color]) => (
          <div className="waterfallRow" key={label}>
            <span>{label}</span>
            <div className="barTrack">
              <div
                className="bar"
                style={{ width: `${(value / total) * 100}%`, background: color }}
              />
            </div>
            <strong>{value} ms</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

