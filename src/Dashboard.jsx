import { useEffect, useRef, useState } from "react";

const WS_URL = "ws://localhost:8000/ws";
const METRICS_URL = "http://localhost:8000/metrics";
const RECONNECT_DELAY_MS = 1000;
const METRICS_POLL_MS = 2000;
const COLORS = {
  text: "#0f172a",
  muted: "#64748b",
  border: "#edf2f7",
  error: "#b42318",
};
const TRACKING = "-0.03em";

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

function renderStatusDetail(connected, connectionError, pipelineState) {
  if (!connected) return connectionError || "conectando";
  if (!pipelineState) return "cargando";
  return [
    pipelineState.status,
    `audio ${pipelineState.audio_chunks_sent}`,
    `queue ${pipelineState.ai_queue_depth}`,
  ].join(" · ");
}

export function Dashboard() {
  const [lines, setLines] = useState([]);
  const [interim, setInterim] = useState("");
  const [explanations, setExplanations] = useState([]);
  const [startedAt] = useState(() => new Date());
  const [connected, setConnected] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [pipelineError, setPipelineError] = useState("");
  const [pipelineState, setPipelineState] = useState(null);
  const endRef = useRef(null);
  const nextId = useRef(0);

  useEffect(() => {
    let ws;
    let reconnectTimer;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;

      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setConnected(true);
        setConnectionError("");
      };

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
          setExplanations((prev) => [...prev, { id, ...msg }].slice(-40));
        }
      };

      ws.onerror = () => {
        setConnectionError("WebSocket no disponible. Esperando backend…");
      };

      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        setConnectionError("WebSocket no disponible. Esperando backend…");
        reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      cancelled = true;
      window.clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadMetrics = async () => {
      try {
        const res = await fetch(METRICS_URL);
        if (!res.ok) {
          throw new Error(`metrics returned ${res.status}`);
        }
        const data = await res.json();
        if (cancelled) return;
        const error = data.pipeline?.last_error;
        setPipelineError(typeof error === "string" ? error : "");
        setPipelineState(data.pipeline ?? null);
      } catch {
        if (cancelled) return;
        setPipelineError("");
        setPipelineState(null);
      }
    };

    loadMetrics();
    const interval = window.setInterval(loadMetrics, METRICS_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
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
        <div style={headLeft}>
          <span style={dot(connected)} />
          <span style={timestamp}>{time}</span>
        </div>
        <p style={statusRail}>{renderStatusDetail(connected, connectionError, pipelineState)}</p>
      </header>
      {pipelineError && <p style={errorText}>error del pipeline · {pipelineError}</p>}
      {pipelineState?.deepgram_detail && (
        <p style={metaLine}>deepgram · {pipelineState.deepgram_detail}</p>
      )}
      {pipelineState?.last_transcript && (
        <p style={metaLine}>
          ultima {pipelineState.last_transcript_kind} · {pipelineState.last_transcript}
        </p>
      )}
      <section style={contextCard}>
        <p style={panelTitle}>Contexto</p>
        {explanations.length === 0 && (
          <p style={empty}>Esperando terminos que puedan causar confusion generacional...</p>
        )}
        {explanations
          .slice()
          .reverse()
          .map((item) => (
            <article key={item.id} style={entry}>
              <p style={entryMeta}>
                {item.term} · {item.target_generation} · {(item.confidence * 100).toFixed(0)}%
              </p>
              <p style={lineStyle}>{item.text}</p>
              <p style={definition}>{item.definition}</p>
              <p style={why}>{item.why_in_context}</p>
              {renderTiming(item.timing_ms) && <p style={timing}>{renderTiming(item.timing_ms)}</p>}
            </article>
          ))}
      </section>
      <div style={transcript}>
        {lines.map((line) => (
          <span key={line.id}>{renderText(line.text, line.flags)} </span>
        ))}
        {interim && <span style={ghost}>{interim}</span>}
        <div ref={endRef} />
      </div>
    </div>
  );
}

const root = {
  maxWidth: 760,
  margin: "0 auto",
  padding: "32px 24px 40px",
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  color: COLORS.text,
};

const head = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 24,
  marginBottom: 18,
};

const headLeft = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const dot = (on) => ({
  width: 6,
  height: 6,
  borderRadius: "50%",
  backgroundColor: on ? COLORS.text : COLORS.border,
  transition: "background-color 0.2s",
});

const timestamp = {
  fontSize: 13,
  color: COLORS.text,
  fontVariantNumeric: "tabular-nums",
  letterSpacing: TRACKING,
};

const statusRail = {
  margin: 0,
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  textAlign: "right",
};

const errorText = {
  margin: "0 0 8px 0",
  fontSize: 13,
  color: COLORS.error,
  letterSpacing: TRACKING,
};

const metaLine = {
  margin: "0 0 8px 0",
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const contextCard = {
  marginTop: 16,
  marginBottom: 24,
  padding: "14px 16px",
  border: `1px solid ${COLORS.border}`,
  borderRadius: 10,
};

const transcript = {
  fontSize: 15,
  lineHeight: 1.75,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const ghost = {
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const flagged = (generation) => ({
  borderBottom: `2px solid ${GEN_COLORS[generation] || "#999"}`,
  cursor: "help",
});

const panelTitle = {
  margin: 0,
  fontSize: 13,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const empty = {
  margin: "10px 0 0 0",
  color: COLORS.muted,
  fontSize: 14,
  letterSpacing: TRACKING,
};

const entry = {
  paddingTop: 14,
};

const entryMeta = {
  margin: 0,
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const lineStyle = {
  margin: "6px 0 0 0",
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const definition = {
  margin: "8px 0 0 0",
  fontSize: 14,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const why = {
  margin: "6px 0 0 0",
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const timing = {
  margin: "8px 0 0 0",
  fontSize: 11,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, monospace',
};
