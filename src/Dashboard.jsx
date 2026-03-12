import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";

const WS_URL = "ws://localhost:8000/ws";
const METRICS_URL = "http://localhost:8000/metrics";
const RECONNECT_DELAY_MS = 1000;
const METRICS_POLL_MS = 200;
const TRACKING = "-0.03em";
const COLORS = {
  background: "#f5f3ef",
  panel: "#f8f6f2",
  text: "#111827",
  muted: "#6b7280",
  border: "#e8e3da",
  quiet: "#d2cbc1",
  error: "#b42318",
};

const GEN_COLORS = {
  gen_z: "#f97316",
  gen_x: "#0f766e",
  millennial: "#60a5fa",
  boomer: "#eab308",
  regional: "#0f766e",
  mixed: "#4b5563",
  unknown: "#4b5563",
};

function sanitizeFlags(flags) {
  if (!Array.isArray(flags) || flags.length === 0) return [];
  const sorted = [...flags]
    .filter(
      (flag) =>
        typeof flag?.start === "number" && typeof flag?.end === "number" && flag.end > flag.start,
    )
    .sort((a, b) => a.start - b.start || a.end - b.end);

  const merged = [];
  let edge = -1;
  for (const flag of sorted) {
    if (flag.start < edge) continue;
    merged.push(flag);
    edge = flag.end;
  }
  return merged;
}

function mergeFlags(existing, incoming) {
  return sanitizeFlags([...(existing ?? []), ...(incoming ?? [])]);
}

function findExplanationIndex(explanations, lineId, term) {
  const normalizedTerm = term.trim().toLowerCase();
  return explanations.findIndex(
    (explanation) =>
      explanation.line_id === lineId && explanation.term.trim().toLowerCase() === normalizedTerm,
  );
}

function buildParagraphs(lines) {
  const paragraphs = [];
  let current = [];
  let totalLength = 0;
  let sentenceCount = 0;

  for (const line of lines) {
    current.push(line);
    totalLength += line.text.length;
    const trimmed = line.text.trim();
    if (/[.!?…]["')\]]*$/.test(trimmed)) {
      sentenceCount += 1;
    }
    const strongBreak = /[?!]["')\]]*$/.test(trimmed);
    const shouldBreak = strongBreak || totalLength >= 320 || sentenceCount >= 3;
    if (shouldBreak) {
      paragraphs.push(current);
      current = [];
      totalLength = 0;
      sentenceCount = 0;
    }
  }

  if (current.length > 0) {
    paragraphs.push(current);
  }

  return paragraphs;
}

function renderText(text, flags, lineId, explanations, onSelectFlag) {
  const safeFlags = sanitizeFlags(flags);
  if (safeFlags.length === 0) return text;

  const parts = [];
  let cursor = 0;

  for (const [index, flag] of safeFlags.entries()) {
    if (flag.start > cursor) {
      parts.push(text.slice(cursor, flag.start));
    }
    parts.push(
      <button
        key={`${flag.start}-${flag.end}-${index}`}
        type="button"
        style={flagged(flag.generation)}
        title={`${flag.term}: ${flag.definition}`}
        onClick={() => {
          const explanationIndex = findExplanationIndex(explanations, lineId, flag.term);
          onSelectFlag(lineId, flag, explanationIndex);
        }}
      >
        {text.slice(flag.start, flag.end)}
      </button>,
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
  return `llm ${llm}ms · total ${e2e}ms`;
}

function getStatusColor(connected, pipelineError) {
  if (!connected || pipelineError) return "#dc2626";
  return "#16a34a";
}

function renderAudioBars(level) {
  return [0, 1, 2, 3, 4].map((index) => {
    const active = level >= (index + 1) * 20;
    return <span key={index} style={audioBar(active)} />;
  });
}

function CharacterPortrait({ generation = "unknown", compact = false }) {
  const accent = GEN_COLORS[generation] || COLORS.text;
  const skin =
    generation === "boomer" ? "#f4d5b5" : generation === "millennial" ? "#e8c7b4" : "#efd2c2";
  const hair =
    generation === "gen_z" ? "#1f2937" : generation === "millennial" ? "#374151" : "#52525b";
  const bust =
    generation === "regional" ? "#0f766e" : generation === "millennial" ? "#93c5fd" : "#f5d0a2";
  const width = compact ? 96 : 132;
  const height = compact ? 116 : 152;

  return (
    <svg viewBox="0 0 132 152" width={width} height={height} aria-hidden="true" style={portraitSvg}>
      <rect x="1" y="1" width="130" height="150" rx="20" fill="#fbf8f2" stroke="#e8e3da" />
      <circle cx="66" cy="54" r="31" fill={skin} />
      <path d="M35 54c2-23 15-35 31-35s30 10 32 31c-9-8-18-11-31-11-12 0-23 4-32 15Z" fill={hair} />
      <circle cx="55" cy="57" r="2.4" fill="#111827" />
      <circle cx="77" cy="57" r="2.4" fill="#111827" />
      <path
        d="M57 73c5 4 13 4 18 0"
        stroke="#111827"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      <path d="M34 118c6-21 18-32 32-32 16 0 29 10 35 32" fill={bust} />
      <path d="M23 138c8-26 23-40 43-40 22 0 37 13 44 40" fill={accent} opacity="0.18" />
      <circle cx="102" cy="25" r="10" fill={accent} opacity="0.16" />
      <circle cx="30" cy="31" r="6" fill={accent} opacity="0.1" />
    </svg>
  );
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
  const [selectedFlag, setSelectedFlag] = useState(null);
  const [compact, setCompact] = useState(() => window.innerWidth < 980);
  const endRef = useRef(null);
  const PROVISIONAL_SETTLE_MS = 1600;

  const upsertFinalLine = useEffectEvent((msg) => {
    setLines((prev) => {
      const sourceId = typeof msg.source_id === "string" ? msg.source_id : null;
      if (sourceId) {
        const existingIndex = prev.findIndex((line) => line.sourceId === sourceId);
        if (existingIndex >= 0) {
          const next = [...prev];
          next[existingIndex] = {
            ...next[existingIndex],
            id: msg.id ?? next[existingIndex].id,
            text: msg.text,
            flags: mergeFlags(next[existingIndex].flags, msg.flags ?? []),
            provisional: Boolean(msg.provisional),
            sourceId,
            updatedAt: Date.now(),
          };
          return next;
        }
      }

      return [
        ...prev,
        {
          id: msg.id,
          text: msg.text,
          flags: msg.flags ?? [],
          provisional: Boolean(msg.provisional),
          sourceId,
          updatedAt: Date.now(),
        },
      ];
    });
  });

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

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === "final") {
          upsertFinalLine(msg);
          setInterim("");
          return;
        }

        if (msg.type === "interim") {
          setInterim(msg.text);
          return;
        }

        if (msg.type === "explanation") {
          setLines((prev) =>
            prev.map((line) =>
              line.id === msg.line_id
                ? { ...line, flags: mergeFlags(line.flags, msg.flags) }
                : line,
            ),
          );
          setExplanations((prev) => {
            const next = [...prev, msg].slice(-40);
            setActiveExplanationIndex(next.length - 1);
            setSelectedFlag(null);
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
    const onResize = () => setCompact(window.innerWidth < 980);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
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
        const diff = audioLevel - prev;
        const eased = prev + diff * (diff > 0 ? 0.26 : 0.12);
        return Math.abs(eased - audioLevel) < 0.5 ? audioLevel : eased;
      });
      frameId = window.requestAnimationFrame(animate);
    };

    frameId = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frameId);
  }, [audioLevel]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      const now = Date.now();
      setLines((prev) => {
        let changed = false;
        const next = prev.map((line) => {
          if (!line.provisional || now - (line.updatedAt ?? now) < PROVISIONAL_SETTLE_MS) {
            return line;
          }
          changed = true;
          return { ...line, provisional: false };
        });
        return changed ? next : prev;
      });
    }, 400);

    return () => window.clearInterval(interval);
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional live scroll
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines, interim]);

  const time = startedAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  const activeExplanation = explanations[activeExplanationIndex] ?? null;
  const selectedExplanation =
    selectedFlag && selectedFlag.explanationIndex >= 0
      ? explanations[selectedFlag.explanationIndex]
      : null;
  const panelExplanation = selectedFlag ? selectedExplanation : activeExplanation;
  const activeLineId = selectedFlag?.lineId ?? panelExplanation?.line_id ?? null;
  const paragraphs = useMemo(() => buildParagraphs(lines), [lines]);
  const layout = useMemo(
    () =>
      compact
        ? { ...bodyGrid, gridTemplateColumns: "minmax(0, 1fr)", gap: 0 }
        : { ...bodyGrid, gridTemplateColumns: "minmax(0, 1.55fr) minmax(320px, 0.85fr)" },
    [compact],
  );

  const selectFlag = (lineId, flag, explanationIndex) => {
    setSelectedFlag({ lineId, flag, explanationIndex });
    if (explanationIndex >= 0) {
      setActiveExplanationIndex(explanationIndex);
    }
  };

  return (
    <div style={page}>
      <style>{`
        .angle-lads-scroll {
          scrollbar-width: none;
          -ms-overflow-style: none;
        }
        .angle-lads-scroll::-webkit-scrollbar {
          display: none;
        }
      `}</style>
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

        <div style={layout}>
          <section style={transcriptShell}>
            <div style={fadeTop} />
            <div style={transcriptPanel(compact)} className="angle-lads-scroll">
              <div style={transcriptCopy}>
                {lines.length === 0 && !interim && (
                  <p style={emptyTranscript}>Habla para empezar a transcribir.</p>
                )}
                {paragraphs.map((paragraph) => (
                  <p
                    key={`${paragraph[0]?.id}-${paragraph.at(-1)?.id}`}
                    style={transcriptParagraph}
                  >
                    {paragraph.map((line, lineIndex) => (
                      <span
                        key={line.id}
                        style={transcriptLine(
                          line.id === activeLineId && line.flags.length > 0,
                          line.provisional,
                        )}
                      >
                        {renderText(line.text, line.flags, line.id, explanations, selectFlag)}
                        {lineIndex < paragraph.length - 1 ? " " : ""}
                      </span>
                    ))}
                  </p>
                ))}
                {interim && <p style={ghost}>{interim}</p>}
                <div ref={endRef} />
              </div>
            </div>
            <div style={fadeBottom} />
          </section>

          <aside style={sidePanel(compact)} className="angle-lads-scroll">
            <section style={contextCard(compact)}>
              <div style={contextHead}>
                <p style={panelTitle}>Contexto</p>
                <div style={contextNav}>
                  <button
                    type="button"
                    style={navButton}
                    onClick={() => {
                      setSelectedFlag(null);
                      setActiveExplanationIndex((prev) => Math.max(0, prev - 1));
                    }}
                    disabled={activeExplanationIndex === 0}
                    aria-label="anterior"
                  >
                    &#8249;
                  </button>
                  <span style={counter}>
                    {explanations.length === 0
                      ? "0"
                      : `${activeExplanationIndex + 1}/${explanations.length}`}
                  </span>
                  <button
                    type="button"
                    style={navButton}
                    onClick={() => {
                      setSelectedFlag(null);
                      setActiveExplanationIndex((prev) =>
                        Math.min(explanations.length - 1, prev + 1),
                      );
                    }}
                    disabled={activeExplanationIndex >= explanations.length - 1}
                    aria-label="siguiente"
                  >
                    &#8250;
                  </button>
                </div>
              </div>
              {pipelineState?.ai_error && <p style={cardMeta}>IA · {pipelineState.ai_error}</p>}
              {!panelExplanation && !selectedFlag && (
                <p style={empty}>Esperando un término que valga la pena explicar.</p>
              )}
              {selectedFlag && !selectedExplanation && (
                <article style={entry}>
                  <div style={portraitWrap(compact)}>
                    <CharacterPortrait
                      generation={selectedFlag.flag.generation}
                      compact={compact}
                    />
                  </div>
                  <div style={tagRow}>
                    <span style={termTag(selectedFlag.flag.generation)}>
                      {selectedFlag.flag.term}
                    </span>
                    {!compact && <span style={entryMeta}>{selectedFlag.flag.generation}</span>}
                  </div>
                  <p style={definition}>{selectedFlag.flag.definition}</p>
                </article>
              )}
              {panelExplanation && (
                <article style={entry}>
                  <div style={portraitWrap(compact)}>
                    <CharacterPortrait
                      generation={panelExplanation.target_generation}
                      compact={compact}
                    />
                  </div>
                  <div style={tagRow}>
                    <span style={termTag(panelExplanation.target_generation)}>
                      {panelExplanation.term}
                    </span>
                    {!compact && (
                      <span style={entryMeta}>
                        {panelExplanation.target_generation} ·{" "}
                        {(panelExplanation.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <p style={definition}>{panelExplanation.definition}</p>
                  {!compact && <p style={why}>{panelExplanation.why_in_context}</p>}
                  {!compact && renderTiming(panelExplanation.timing_ms) && (
                    <p style={timing}>{renderTiming(panelExplanation.timing_ms)}</p>
                  )}
                </article>
              )}
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}

const page = {
  height: "100vh",
  background: COLORS.background,
  overflow: "hidden",
  overscrollBehavior: "none",
};

const root = {
  maxWidth: 1160,
  margin: "0 auto",
  padding: "32px 28px 40px",
  height: "100vh",
  boxSizing: "border-box",
  overflow: "hidden",
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
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
  gap: 10,
};

const dot = (color) => ({
  width: 8,
  height: 8,
  borderRadius: "50%",
  backgroundColor: color,
  transition: "background-color 0.2s",
});

const timestamp = {
  margin: 0,
  fontSize: 14,
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
  gap: 4,
};

const audioBar = (active) => ({
  width: 3,
  height: 16,
  backgroundColor: active ? COLORS.text : "#b8c1cf",
  transition: "background-color 80ms linear",
});

const errorText = {
  margin: "0 0 16px 0",
  fontSize: 13,
  color: COLORS.error,
  letterSpacing: TRACKING,
};

const bodyGrid = {
  display: "grid",
  alignItems: "start",
  gap: 28,
  height: "calc(100vh - 120px)",
};

const transcriptShell = {
  position: "relative",
  minWidth: 0,
  height: "100%",
};

const transcriptPanel = (compact) => ({
  minWidth: 0,
  height: "100%",
  overflowY: "auto",
  overscrollBehavior: "contain",
  paddingRight: compact ? 0 : 8,
  paddingBottom: compact ? 220 : 0,
});

const transcriptCopy = {
  fontSize: 16,
  lineHeight: 1.72,
  color: COLORS.text,
  letterSpacing: TRACKING,
  paddingBottom: 12,
};

const transcriptLine = (active, provisional) => ({
  display: "inline",
  color: active ? "#020617" : provisional ? "#334155" : COLORS.text,
  opacity: active ? 1 : provisional ? 0.72 : 0.98,
});

const transcriptParagraph = {
  margin: "0 0 16px 0",
};

const fadeTop = {
  pointerEvents: "none",
  position: "absolute",
  top: 0,
  left: 0,
  right: 0,
  height: 20,
  background: "linear-gradient(180deg, rgba(245,243,239,1) 0%, rgba(245,243,239,0) 100%)",
  zIndex: 2,
};

const fadeBottom = {
  pointerEvents: "none",
  position: "absolute",
  left: 0,
  right: 0,
  bottom: 0,
  height: 28,
  background: "linear-gradient(0deg, rgba(245,243,239,1) 0%, rgba(245,243,239,0) 100%)",
  zIndex: 2,
};

const emptyTranscript = {
  margin: "0 0 4px 0",
  fontSize: 16,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const ghost = {
  margin: 0,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  opacity: 0.72,
};

const sidePanel = (compact) => ({
  minWidth: 0,
  height: compact ? "auto" : "100%",
  overflowY: compact ? "visible" : "auto",
  overscrollBehavior: compact ? "auto" : "contain",
  paddingRight: compact ? 16 : 2,
  paddingLeft: compact ? 16 : 0,
  paddingBottom: compact ? 16 : 0,
  position: compact ? "fixed" : "relative",
  left: compact ? 0 : "auto",
  right: compact ? 0 : "auto",
  bottom: compact ? 0 : "auto",
  zIndex: compact ? 20 : "auto",
});

const contextCard = (compact) => ({
  padding: compact ? "16px" : "16px 18px 18px",
  border: `1px solid ${COLORS.border}`,
  background: COLORS.panel,
  borderLeft: `1px solid ${COLORS.border}`,
  borderRight: `1px solid ${COLORS.border}`,
  borderBottom: `1px solid ${COLORS.border}`,
  maxHeight: compact ? 176 : "none",
  overflowY: compact ? "auto" : "visible",
  borderRadius: compact ? 14 : 0,
});

const contextHead = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const panelTitle = {
  margin: 0,
  fontSize: 13,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const contextNav = {
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const navButton = {
  border: 0,
  background: "transparent",
  padding: 0,
  margin: 0,
  color: COLORS.text,
  fontSize: 22,
  lineHeight: 1,
  letterSpacing: TRACKING,
  cursor: "pointer",
};

const counter = {
  minWidth: 34,
  textAlign: "center",
  fontSize: 12,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const cardMeta = {
  margin: "12px 0 0 0",
  fontSize: 13,
  color: COLORS.error,
  letterSpacing: TRACKING,
};

const empty = {
  margin: "20px 0 0 0",
  color: COLORS.muted,
  fontSize: 14,
  lineHeight: 1.6,
  letterSpacing: TRACKING,
};

const entry = {
  marginTop: 18,
};

const portraitWrap = (compact) => ({
  marginBottom: compact ? 12 : 16,
});

const portraitSvg = {
  display: "block",
  maxWidth: "100%",
};

const tagRow = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};

const termTag = (generation) => ({
  display: "inline-flex",
  alignItems: "center",
  minHeight: 28,
  padding: "0 10px",
  color: GEN_COLORS[generation] || COLORS.text,
  border: `1px solid ${GEN_COLORS[generation] || COLORS.quiet}`,
  background: "transparent",
  fontSize: 13,
  letterSpacing: TRACKING,
});

const entryMeta = {
  margin: 0,
  fontSize: 12,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const definition = {
  margin: "14px 0 0 0",
  fontSize: 16,
  lineHeight: 1.6,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const why = {
  margin: "10px 0 0 0",
  fontSize: 14,
  lineHeight: 1.6,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const timing = {
  margin: "12px 0 0 0",
  fontSize: 11,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, monospace',
};

const flagged = (generation) => ({
  appearance: "none",
  border: 0,
  background: "transparent",
  padding: 0,
  margin: 0,
  font: "inherit",
  lineHeight: "inherit",
  letterSpacing: TRACKING,
  color: GEN_COLORS[generation] || COLORS.text,
  textDecorationLine: "underline",
  textDecorationThickness: 2,
  textDecorationColor: GEN_COLORS[generation] || COLORS.quiet,
  textUnderlineOffset: 3,
  cursor: "pointer",
});
