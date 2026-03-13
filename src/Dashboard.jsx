import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { PRESETS, usePresetStore } from "./presetStore";

const WS_URL = "ws://localhost:8000/ws";
const METRICS_URL = "http://localhost:8000/metrics";
const PRESET_URL = "http://localhost:8000/preset";
const MUTE_URL = "http://localhost:8000/mute";
const RECONNECT_DELAY_MS = 1000;
const WS_HEARTBEAT_MS = 15000;
const METRICS_POLL_MS = 200;
const EXPLANATION_MIN_PRESENCE_MS = 5000;
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
const CHARACTER_PORTRAITS = {
  gen_z: ["/assets/tripp.png", "/tripp.png"],
  millennial: ["/assets/trevor.png", "/trevor.png"],
  boomer: ["/assets/tina.PNG", "/assets/tina.png", "/tina.png"],
};
const CHARACTER_BACKGROUNDS = {
  gen_z: ["/assets/tripp-bg.png"],
  millennial: ["/assets/trevor-bg.png"],
  boomer: ["/assets/tina-bg.png"],
};
const CHARACTER_FRAMING = {
  gen_z: {
    width: "108%",
    height: "154%",
    right: "-16%",
    bottom: "-40%",
    objectFit: "cover",
    objectPosition: "right bottom",
  },
  millennial: {
    width: "116%",
    height: "176%",
    left: "50%",
    bottom: "-20%",
    transform: "translateX(-50%)",
    objectPosition: "center bottom",
  },
  boomer: {
    width: "125%",
    height: "197%",
    left: "41%",
    bottom: "-29%",
    transform: "translateX(-50%)",
    objectPosition: "center bottom",
  },
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

function renderAudioBars(level, muted) {
  return [0, 1, 2, 3, 4].map((index) => {
    const active = !muted && level >= (index + 1) * 20;
    return <span key={index} style={audioBar(active)} />;
  });
}

function CharacterPortrait({ generation }) {
  const portraitCandidates = CHARACTER_PORTRAITS[generation] ?? [];
  const backgroundCandidates = CHARACTER_BACKGROUNDS[generation] ?? [];
  const [portraitIndex, setPortraitIndex] = useState(0);
  const [backgroundIndex, setBackgroundIndex] = useState(0);
  const portraitSrc = portraitCandidates[portraitIndex];
  const backgroundSrc = backgroundCandidates[backgroundIndex];
  const showPortrait = Boolean(portraitSrc);
  const showBackground = Boolean(backgroundSrc);
  const isWaiting = !generation;
  const framing = CHARACTER_FRAMING[generation] ?? {};

  return (
    <div style={portraitShell(generation)}>
      {showBackground && (
        <img
          src={backgroundSrc}
          alt=""
          aria-hidden="true"
          style={portraitBackground}
          onError={() => {
            setBackgroundIndex((current) => {
              if (current >= backgroundCandidates.length - 1) return backgroundCandidates.length;
              return current + 1;
            });
          }}
        />
      )}
      <div style={portraitShade(generation)} />
      {showPortrait ? (
        <img
          src={portraitSrc}
          alt=""
          aria-hidden="true"
          style={portraitImage(framing)}
          onError={() => {
            setPortraitIndex((current) => {
              if (current >= portraitCandidates.length - 1) return portraitCandidates.length;
              return current + 1;
            });
          }}
        />
      ) : (
        <div style={portraitFallback(generation, isWaiting)} />
      )}
    </div>
  );
}

async function pushPreset(preset) {
  const res = await fetch(PRESET_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset }),
  });
  if (!res.ok) {
    throw new Error(`preset returned ${res.status}`);
  }
  const data = await res.json();
  return typeof data?.preset === "string" ? data.preset : preset;
}

async function pushMute(muted) {
  const res = await fetch(MUTE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ muted }),
  });
  if (!res.ok) {
    throw new Error(`mute returned ${res.status}`);
  }
  const data = await res.json();
  return Boolean(data?.muted);
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
  const [presetSyncError, setPresetSyncError] = useState("");
  const [muteSyncError, setMuteSyncError] = useState("");
  const [isMuted, setIsMuted] = useState(false);
  const [mutePending, setMutePending] = useState(false);
  const endRef = useRef(null);
  const transcriptRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const explanationShownAtRef = useRef(0);
  const PROVISIONAL_SETTLE_MS = 900;
  const preset = usePresetStore((state) => state.preset);
  const hydrated = usePresetStore((state) => state.hydrated);
  const setPreset = usePresetStore((state) => state.setPreset);
  const validPresetKeys = useMemo(() => new Set(Object.keys(PRESETS)), []);

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
            sourceId: msg.provisional ? sourceId : null,
            updatedAt: Date.now(),
          };
          return next;
        }
      }

      return [
        ...prev,
        {
          id: msg.id ?? sourceId ?? `line-${Date.now()}`,
          text: msg.text,
          flags: msg.flags ?? [],
          provisional: Boolean(msg.provisional),
          sourceId: msg.provisional ? sourceId : null,
          updatedAt: Date.now(),
        },
      ];
    });
  });

  useEffect(() => {
    let ws;
    let reconnectTimer;
    let heartbeatTimer;
    let cancelled = false;

    const clearHeartbeat = () => {
      window.clearInterval(heartbeatTimer);
      heartbeatTimer = undefined;
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setConnected(true);
        setConnectionError("");
        clearHeartbeat();
        heartbeatTimer = window.setInterval(() => {
          if (ws?.readyState !== WebSocket.OPEN) return;
          try {
            ws.send('{"type":"ping"}');
          } catch {
            ws.close();
          }
        }, WS_HEARTBEAT_MS);
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "pong") {
          return;
        }

        if (msg.type === "final") {
          upsertFinalLine(msg);
          setInterim("");
          return;
        }

        if (msg.type === "interim") {
          if (typeof msg.source_id === "string") {
            upsertFinalLine({ ...msg, provisional: true, id: msg.id ?? msg.source_id });
            setInterim("");
          } else {
            setInterim(msg.text);
          }
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
            if (prev.length === 0) {
              explanationShownAtRef.current = Date.now();
              setActiveExplanationIndex(0);
            }
            return next;
          });
        }
      };

      ws.onerror = () => {
        setConnectionError("WebSocket no disponible. Esperando backend…");
      };

      ws.onclose = () => {
        clearHeartbeat();
        setConnected(false);
        if (cancelled) return;
        setConnectionError("WebSocket no disponible. Esperando backend…");
        reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      cancelled = true;
      clearHeartbeat();
      window.clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  useEffect(() => {
    const node = transcriptRef.current;
    if (!node) return;

    const handleScroll = () => {
      const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
      stickToBottomRef.current = distanceFromBottom < 96;
    };

    handleScroll();
    node.addEventListener("scroll", handleScroll);
    return () => node.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 980);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (typeof preset !== "string" || !validPresetKeys.has(preset)) {
      setPreset("cafe");
      return;
    }
    if (!connected) {
      setPresetSyncError("");
      return;
    }
    let cancelled = false;
    pushPreset(preset)
      .then((serverPreset) => {
        if (!cancelled) {
          if (serverPreset !== preset && validPresetKeys.has(serverPreset)) {
            setPreset(serverPreset);
          }
          setPresetSyncError("");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPresetSyncError("No se pudo sincronizar el preset.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [connected, hydrated, preset, setPreset, validPresetKeys]);

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
        if (typeof data.pipeline?.audio_muted === "boolean") {
          setIsMuted(data.pipeline.audio_muted);
        }
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
        const next = prev.filter((line) => {
          if (!line.provisional) return true;
          return now - (line.updatedAt ?? now) < PROVISIONAL_SETTLE_MS * 5;
        });
        return next.length === prev.length ? prev : next;
      });
    }, 400);

    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (selectedFlag || explanations.length === 0) return;

    const newestIndex = explanations.length - 1;
    if (activeExplanationIndex >= newestIndex) return;

    const elapsed = Date.now() - explanationShownAtRef.current;
    const delay = Math.max(EXPLANATION_MIN_PRESENCE_MS - elapsed, 0);
    const timer = window.setTimeout(() => {
      explanationShownAtRef.current = Date.now();
      setActiveExplanationIndex((prev) => Math.min(prev + 1, explanations.length - 1));
    }, delay);

    return () => window.clearTimeout(timer);
  }, [activeExplanationIndex, explanations.length, selectedFlag]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional live scroll
  useEffect(() => {
    if (!stickToBottomRef.current) return;
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines, interim]);

  const time = startedAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const shownAudioLevel = isMuted ? 0 : displayAudioLevel;

  const activeExplanation = explanations[activeExplanationIndex] ?? null;
  const selectedExplanation =
    selectedFlag && selectedFlag.explanationIndex >= 0
      ? explanations[selectedFlag.explanationIndex]
      : null;
  const panelExplanation = selectedFlag ? selectedExplanation : activeExplanation;
  const bannerGeneration =
    selectedFlag?.flag.generation ?? panelExplanation?.target_generation ?? null;
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
      explanationShownAtRef.current = Date.now();
      setActiveExplanationIndex(explanationIndex);
    }
  };

  const toggleMute = async () => {
    if (mutePending) return;
    const nextMuted = !isMuted;
    setMutePending(true);
    setMuteSyncError("");
    setIsMuted(nextMuted);
    try {
      const confirmedMuted = await pushMute(nextMuted);
      setIsMuted(confirmedMuted);
    } catch {
      setIsMuted(!nextMuted);
      setMuteSyncError("No se pudo sincronizar el mute.");
    } finally {
      setMutePending(false);
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
          <div style={headCluster}>
            <div style={headLeft}>
              <span style={dot(getStatusColor(connected, pipelineError))} />
              <span style={timestamp}>{time}</span>
            </div>
            <div style={presetRail(compact)}>
              {Object.entries(PRESETS).map(([key, value]) => (
                <button
                  key={key}
                  type="button"
                  style={presetButton(preset === key)}
                  onClick={() => setPreset(key)}
                >
                  {value.label}
                </button>
              ))}
            </div>
          </div>
          <div style={statusRail}>
            <button
              type="button"
              style={audioMeter(isMuted, mutePending)}
              aria-label={
                isMuted
                  ? "microfono silenciado, click para activar"
                  : "microfono activo, click para silenciar"
              }
              title={
                isMuted ? "Mic muted · click para activar" : "Mic activo · click para silenciar"
              }
              onClick={toggleMute}
              disabled={mutePending}
            >
              {renderAudioBars(shownAudioLevel, isMuted)}
            </button>
            {!connected && <p style={statusText}>{connectionError}</p>}
          </div>
        </header>

        {pipelineError && <p style={errorText}>error del pipeline · {pipelineError}</p>}
        {presetSyncError && <p style={errorText}>{presetSyncError}</p>}
        {muteSyncError && <p style={errorText}>{muteSyncError}</p>}

        <div style={layout}>
          <section style={transcriptShell}>
            <div style={fadeTop} />
            <div ref={transcriptRef} style={transcriptPanel(compact)} className="angle-lads-scroll">
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
          </section>

          <aside style={sidePanel(compact)} className="angle-lads-scroll">
            <div style={sideRail(compact)}>
              <section style={heroCard(bannerGeneration)}>
                <CharacterPortrait
                  key={bannerGeneration || "waiting"}
                  generation={bannerGeneration}
                />
                {!selectedFlag && !panelExplanation && (
                  <div style={heroEmptyState}>
                    <p style={heroEmptyEyebrow}>Sin término activo</p>
                    <p style={heroEmptyCopy}>
                      El personaje aparecerá con generación y confianza cuando detectemos una
                      expresión que valga la pena explicar.
                    </p>
                  </div>
                )}
                {(selectedFlag || panelExplanation) && (
                  <div style={heroBody}>
                    {selectedFlag && !selectedExplanation && (
                      <>
                        <div style={heroMetaRow}>
                          <span style={termTag(selectedFlag.flag.generation)}>
                            {selectedFlag.flag.term}
                          </span>
                          <span style={entryMeta}>{selectedFlag.flag.generation}</span>
                        </div>
                        <p style={heroDefinition}>{selectedFlag.flag.definition}</p>
                      </>
                    )}
                    {panelExplanation && (
                      <>
                        <div style={heroMetaRow}>
                          <span style={termTag(panelExplanation.target_generation)}>
                            {panelExplanation.term}
                          </span>
                          <span style={entryMeta}>
                            {panelExplanation.target_generation} ·{" "}
                            {(panelExplanation.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                        <p style={heroDefinition}>{panelExplanation.definition}</p>
                      </>
                    )}
                  </div>
                )}
              </section>

              <section style={contextCard(compact)}>
                <div style={contextHead}>
                  <div>
                    <p style={panelTitle}>Contexto</p>
                  </div>
                  <div style={contextNav}>
                    <button
                      type="button"
                      style={navButton}
                      onClick={() => {
                        setSelectedFlag(null);
                        explanationShownAtRef.current = Date.now();
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
                        explanationShownAtRef.current = Date.now();
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
                {panelExplanation && (
                  <article style={entry}>
                    {!compact && <p style={why}>{panelExplanation.why_in_context}</p>}
                    {!compact && renderTiming(panelExplanation.timing_ms) && (
                      <p style={timing}>{renderTiming(panelExplanation.timing_ms)}</p>
                    )}
                  </article>
                )}
              </section>
            </div>
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
  marginBottom: 14,
};

const headCluster = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  flexWrap: "wrap",
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

const presetRail = (compact) => ({
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
  marginLeft: compact ? 0 : 6,
});

const presetButton = (active) => ({
  border: `1px solid ${active ? COLORS.text : COLORS.border}`,
  background: active ? COLORS.text : "transparent",
  color: active ? "#f8f6f2" : COLORS.muted,
  height: 26,
  padding: "0 9px",
  fontSize: 12,
  letterSpacing: TRACKING,
  cursor: "pointer",
  borderRadius: 999,
});

const statusText = {
  margin: 0,
  fontSize: 13,
  color: COLORS.muted,
  letterSpacing: TRACKING,
  textAlign: "right",
};

const audioMeter = (muted, pending) => ({
  display: "flex",
  alignItems: "flex-end",
  gap: 4,
  border: 0,
  background: muted ? "rgba(17,24,39,0.06)" : "transparent",
  borderRadius: 999,
  padding: "4px 8px",
  margin: 0,
  cursor: pending ? "wait" : "pointer",
  opacity: pending ? 0.7 : 1,
});

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
  height: "calc(100vh - 118px)",
  minHeight: 0,
};

const heroBody = {
  padding: "8px 18px 20px",
};

const heroEmptyState = {
  padding: "16px 18px 20px",
};

const heroEmptyEyebrow = {
  margin: 0,
  fontSize: 12,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const heroEmptyCopy = {
  margin: "8px 0 0 0",
  fontSize: 14,
  lineHeight: 1.55,
  color: COLORS.muted,
  letterSpacing: TRACKING,
};

const transcriptShell = {
  position: "relative",
  minWidth: 0,
  minHeight: 0,
  height: "100%",
};

const transcriptPanel = (compact) => ({
  minWidth: 0,
  height: "100%",
  overflowY: "auto",
  overscrollBehavior: "contain",
  paddingRight: compact ? 0 : 8,
  paddingBottom: compact ? 220 : 12,
});

const transcriptCopy = {
  fontSize: 16,
  lineHeight: 1.72,
  color: COLORS.text,
  letterSpacing: TRACKING,
  paddingTop: 20,
  paddingBottom: 40,
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
  overflow: "visible",
  paddingRight: compact ? 16 : 2,
  paddingLeft: compact ? 16 : 0,
  paddingBottom: compact ? 16 : 0,
  position: compact ? "fixed" : "relative",
  left: compact ? 0 : "auto",
  right: compact ? 0 : "auto",
  bottom: compact ? 0 : "auto",
  zIndex: compact ? 20 : "auto",
});

const sideRail = (compact) => ({
  position: compact ? "relative" : "sticky",
  top: compact ? "auto" : 0,
  display: "flex",
  flexDirection: "column",
  gap: 14,
});

const heroCard = (generation) => ({
  border: `1px solid ${COLORS.border}`,
  background: COLORS.panel,
  color: GEN_COLORS[generation] || COLORS.text,
  overflow: "hidden",
});

const portraitShell = (generation) => ({
  position: "relative",
  width: "100%",
  aspectRatio: "1.8 / 1",
  overflow: "hidden",
  borderBottom: `1px solid ${COLORS.border}`,
  background:
    generation && GEN_COLORS[generation]
      ? `linear-gradient(145deg, ${GEN_COLORS[generation]}22 0%, rgba(17,24,39,0.08) 100%)`
      : COLORS.background,
});

const portraitBackground = {
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  objectFit: "cover",
  filter: "saturate(0.92) contrast(1.02)",
  transform: "scale(1.02)",
};

const portraitShade = (generation) => ({
  position: "absolute",
  inset: 0,
  background:
    generation && GEN_COLORS[generation]
      ? `linear-gradient(180deg, rgba(248,246,242,0.06) 0%, ${GEN_COLORS[generation]}12 100%)`
      : "linear-gradient(180deg, rgba(248,246,242,0.06) 0%, rgba(17,24,39,0.08) 100%)",
});

const portraitImage = (framing) => ({
  position: "absolute",
  left: framing.left,
  right: framing.right,
  bottom: framing.bottom ?? 0,
  width: framing.width ?? "64%",
  height: framing.height ?? "94%",
  objectFit: framing.objectFit ?? "contain",
  objectPosition: framing.objectPosition ?? "center bottom",
  transform: framing.transform,
  filter: "drop-shadow(0 18px 30px rgba(17,24,39,0.18)) saturate(0.98) contrast(1.02)",
});

const portraitFallback = (generation, isWaiting) => ({
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  display: "grid",
  placeItems: "center",
  fontSize: 30,
  letterSpacing: TRACKING,
  color: GEN_COLORS[generation] || COLORS.text,
  background:
    generation && GEN_COLORS[generation]
      ? `linear-gradient(145deg, rgba(255,255,255,0.72) 0%, ${GEN_COLORS[generation]}18 100%)`
      : "linear-gradient(180deg, rgba(248,246,242,0.96) 0%, rgba(232,227,218,0.7) 100%)",
  opacity: isWaiting ? 0.65 : 1,
});

const heroMetaRow = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  flexWrap: "wrap",
  marginTop: 12,
};

const heroDefinition = {
  margin: "14px 0 0 0",
  fontSize: 20,
  lineHeight: 1.45,
  color: COLORS.text,
  letterSpacing: TRACKING,
};

const contextCard = (compact) => ({
  padding: compact ? "16px" : "16px 18px 18px",
  border: `1px solid ${COLORS.border}`,
  background: COLORS.panel,
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
