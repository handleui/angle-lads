import { useEffect, useRef, useState } from "react";

export function Dashboard() {
  const [lines, setLines] = useState([]);
  const [interim, setInterim] = useState("");
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
        setLines((prev) => [...prev, { id, text: msg.text }]);
        setInterim("");
      } else {
        setInterim(msg.text);
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
          <span key={line.id}>{line.text} </span>
        ))}
        {interim && <span style={ghost}>{interim}</span>}
        <div ref={endRef} />
      </div>
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
