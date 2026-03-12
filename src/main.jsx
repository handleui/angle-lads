import { createRoot } from "react-dom/client";
import { Dashboard } from "./Dashboard";

const FAVICONS = ["/favicon-circle.svg", "/favicon-triangle.svg", "/favicon-rhombus.svg"];

const favicon = document.querySelector("link[rel='icon']");
if (favicon instanceof HTMLLinkElement) {
  favicon.href = FAVICONS[Math.floor(Math.random() * FAVICONS.length)];
}

createRoot(document.getElementById("root")).render(<Dashboard />);
