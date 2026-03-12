import { useEffect, useRef, useState } from "react";

const WS_URL = "ws://localhost:8000/ws";
const METRICS_URL = "http://localhost:8000/metrics";
const RECONNECT_DELAY_MS = 1000;
const METRICS_POLL_MS = 120;
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

function getStatusColor(connected, pipelineError) {
  if (!connected || pipelineError) return "#dc2626";
  return "#16a34a";
}

function renderAudioBars(level) {
  return [0, 1, 2, 3, 4].map((index) => {
    const active = level >= (index + 1) * 20;
    return (
      <span key={index} style={audioBarTrack}>
        <span style={audioBarFill(active)} />
      </span>
    );
  });
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
  const [audioLevel, setAudioLevel] = useState(0);
  const [displayAudioLevel, setDisplayAudioLevel] = useState(0);
  const [activeExplanationIndex, setActiveExplanationIndex] = useState(0);
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
          setExplanations((prev) => {
            const next = [...prev, { id, ...msg }].slice(-40);
            setActiveExplanationIndex(next.length - 1);
            return next;
          });
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
        setAudioLevel(data.pipeline?.audio_level ?? 0);
      } catch {
        if (cancelled) return;
        setPipelineError("");
        setPipelineState(null);
        setAudioLevel(0);
      }
    };

    loadMetrics();
    const interval = window.setInterval(loadMetrics, METRICS_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    let frameId;

    const animate = () => {
      setDisplayAudioLevel((prev) => {
        const next = prev + (audioLevel - prev) * 0.2;
        return Math.abs(next - audioLevel) < 0.5 ? audioLevel : next;
      });
      frameId = window.requestAnimationFrame(animate);
    };

    frameId = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frameId);
  }, [audioLevel]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on every state change
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines, interim]);

  const time = startedAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const activeExplanation = explanations[activeExplanationIndex] ?? null;

  return (
    <div style={root}>
      <header style={head}>
        <div style={headLeft}>
          <span style={dot(getStatusColor(connected, pipelineError))} />
          <span style={timestamp}>{time}</span>
        </div>
        <div style={statusRail}>
          <div
            role="img"
            style={audioMeter}
            aria-label={`nivel de audio ${Math.round(displayAudioLevel)}`}
          >
            {renderAudioBars(displayAudioLevel)}
          </div>
          {!connected && <p style={statusText}>{connectionError}</p>}
        </div>
      </header>
      {pipelineError && <p style={errorText}>error del pipeline · {pipelineError}</p>}
      <section style={contextCard}>
        <div style={contextHead}>
          <p style={panelTitle}>Contexto</p>
          <div style={contextNav}>
            <button
              type="button"
              style={navButton}
              onClick={() => setActiveExplanationIndex((prev) => Math.max(0, prev - 1))}
              disabled={activeExplanationIndex === 0}
            >
              ←
            </button>
            <button
              type="button"
              style={navButton}
              onClick={() =>
                setActiveExplanationIndex((prev) => Math.min(explanations.length - 1, prev + 1))
              }
              disabled={activeExplanationIndex >= explanations.length - 1}
            >
              →
            </button>
          </div>
        </div>
        {pipelineState?.ai_error && <p style={cardMeta}>IA · {pipelineState.ai_error}</p>}
        {!activeExplanation && (
          <p style={empty}>Esperando terminos que puedan causar confusion generacional...</p>
        )}
        {activeExplanation && (
          <article key={activeExplanation.id} style={entry}>
            <p style={entryMeta}>
              {activeExplanation.term} · {activeExplanation.target_generation} ·{" "}
              {(activeExplanation.confidence * 100).toFixed(0)}%
            </p>
            <p style={definition}>{activeExplanation.definition}</p>
            <p style={why}>{activeExplanation.why_in_context}</p>
            {renderTiming(activeExplanation.timing_ms) && (
              <p style={timing}>{renderTiming(activeExplanation.timing_ms)}</p>
            )}
          </article>
        )}
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

const dot = (color) => ({
  width: 6,
  height: 6,
  borderRadius: "50%",
  backgroundColor: color,
  transition: "background-color 0.2s",
});

const timestamp = {
  fontSize: 13,
  color: COLORS.text,
  fontVariantNumeric: "tabular-nums",
  letterSpacing: TRACKING,
};

const statusRail = {
  display: "flex",
  alignItems: "center",
  gap: 12,
};

const statusText = {
  margin: 0,
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  textAlign: "right",
};

const audioMeter = {
  display: "flex",
  alignItems: "flex-end",
  gap: 3,
};

const audioBarTrack = {
  display: "block",
  width: 3,
  height: 16,
  backgroundColor: "#cbd5e1",
  overflow: "hidden",
};

const audioBarFill = (active) => ({
  display: "block",
  width: 3,
  height: 16,
  backgroundColor: active ? COLORS.text : "transparent",
  transition: "background-color 90ms linear",
});

const errorText = {
  margin: "0 0 8px 0",
  fontSize: 13,
  color: COLORS.error,
  letterSpacing: TRACKING,
};

const contextCard = {
  position: "sticky",
  top: 18,
  zIndex: 10,
  marginTop: 16,
  marginBottom: 24,
  padding: "14px 16px",
  border: `1px solid ${COLORS.border}`,
  borderRadius: 10,
  backgroundColor: "rgba(255, 255, 255, 0.96)",
};

const contextHead = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const contextNav = {
  display: "flex",
  alignItems: "center",
  gap: 6,
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

const navButton = {
  width: 24,
  height: 24,
  border: `1px solid ${COLORS.border}`,
  borderRadius: 999,
  background: "transparent",
  color: COLORS.text,
  fontSize: 13,
  lineHeight: "24px",
  padding: 0,
  cursor: "pointer",
};

const cardMeta = {
  margin: "10px 0 0 0",
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const empty = {
  margin: "10px 0 0 0",
  color: COLORS.muted,
  fontSize: 14,
  letterSpacing: TRACKING,
};

const entry = {
  paddingTop: 12,
};

const entryMeta = {
  margin: 0,
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
