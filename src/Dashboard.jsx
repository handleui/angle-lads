import { useEffect, useRef, useState } from "react";

const GEN_COLORS = {
  gen_z: "#0070f3",
  millennial: "#7928ca",
  boomer: "#f5a623",
  regional: "#50e3c2",
};

function renderText(text, flags) {
  if (!flags || flags.length === 0) return text;

  const parts = [];
  let cursor = 0;

  for (const flag of flags) {
    if (flag.start > cursor) {
      parts.push(text.slice(cursor, flag.start));
    }
    parts.push(
      <span
        key={flag.start}
        style={flagged(flag.generation)}
        title={`${flag.term}: ${flag.definition}`}
      >
        {text.slice(flag.start, flag.end)}
      </span>,
    );
    cursor = flag.end;
  }

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }

  return parts;
}

function renderTiming(timingMs) {
  if (!timingMs) return null;
  const llm = timingMs.llm_roundtrip;
  const e2e = timingMs.final_to_explanation;
  if (typeof llm !== "number" || typeof e2e !== "number") return null;
  return `llm ${llm}ms · e2e ${e2e}ms`;
}

export function Dashboard() {
  const [lines, setLines] = useState([]);
  const [interim, setInterim] = useState("");
  const [explanations, setExplanations] = useState([]);
  const [startedAt] = useState(() => new Date());
  const [connected, setConnected] = useState(false);
  const endRef = useRef(null);
  const nextId = useRef(0);

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8000/ws");

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "final") {
        const id = nextId.current++;
        setLines((prev) => [...prev, { id, text: msg.text, flags: msg.flags }]);
        setInterim("");
      } else if (msg.type === "interim") {
        setInterim(msg.text);
      } else if (msg.type === "explanation") {
        const id = nextId.current++;
        setExplanations((prev) => [...prev, { id, ...msg }].slice(Math.max(prev.length - 39, 0)));
      }
    };

    return () => ws.close();
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on every state change
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines, interim]);

  const time = startedAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div style={root}>
      <header style={head}>
        <span style={dot(connected)} />
        <span style={timestamp}>{time}</span>
      </header>
      <div style={transcript}>
        {lines.map((line) => (
          <span key={line.id}>{renderText(line.text, line.flags)} </span>
        ))}
        {interim && <span style={ghost}>{interim}</span>}
        <div ref={endRef} />
      </div>
      <section style={panel}>
        <h2 style={panelTitle}>Context Explanations</h2>
        {explanations.length === 0 && (
          <p style={empty}>Waiting for likely intergenerational confusion terms...</p>
        )}
        {explanations
          .slice()
          .reverse()
          .map((item) => (
            <article key={item.id} style={card}>
              <header style={cardHead}>
                <strong style={term}>{item.term}</strong>
                <span style={badge}>
                  {item.target_generation} · {(item.confidence * 100).toFixed(0)}%
                </span>
              </header>
              <p style={lineStyle}>{item.text}</p>
              <p style={definition}>{item.definition}</p>
              <p style={why}>{item.why_in_context}</p>
              {renderTiming(item.timing_ms) && <p style={timing}>{renderTiming(item.timing_ms)}</p>}
            </article>
          ))}
      </section>
    </div>
  );
}

const root = {
  maxWidth: 640,
  margin: "0 auto",
  padding: "48px 24px",
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

const head = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 32,
};

const dot = (on) => ({
  width: 6,
  height: 6,
  borderRadius: "50%",
  backgroundColor: on ? "#000" : "#ccc",
  transition: "background-color 0.2s",
});

const timestamp = {
  fontSize: 13,
  color: "#999",
  fontVariantNumeric: "tabular-nums",
};

const transcript = {
  fontSize: 15,
  lineHeight: 1.8,
  color: "#111",
  letterSpacing: "-0.01em",
};

const ghost = {
  color: "#ccc",
};

const flagged = (generation) => ({
  borderBottom: `2px solid ${GEN_COLORS[generation] || "#999"}`,
  cursor: "help",
});

const panel = {
  marginTop: 36,
  borderTop: "1px solid #eee",
  paddingTop: 20,
};

const panelTitle = {
  margin: 0,
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "#777",
};

const empty = {
  marginTop: 12,
  color: "#999",
  fontSize: 14,
};

const card = {
  marginTop: 12,
  border: "1px solid #ececec",
  borderRadius: 10,
  padding: 12,
  background: "#fafafa",
};

const cardHead = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 12,
};

const term = {
  fontSize: 15,
};

const badge = {
  fontSize: 11,
  color: "#555",
  border: "1px solid #ddd",
  borderRadius: 999,
  padding: "2px 8px",
};

const lineStyle = {
  margin: "8px 0 0 0",
  fontSize: 13,
  color: "#666",
  fontStyle: "italic",
};

const definition = {
  margin: "8px 0 0 0",
  fontSize: 14,
  color: "#111",
};

const why = {
  margin: "6px 0 0 0",
  fontSize: 13,
  color: "#333",
};

const timing = {
  margin: "8px 0 0 0",
  fontSize: 11,
  color: "#777",
  fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, monospace',
};
