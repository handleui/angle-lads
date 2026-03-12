import { createRoot } from "react-dom/client";
import { Dashboard } from "./Dashboard";

const FAVICONS = ["/favicon-circle.svg", "/favicon-triangle.svg", "/favicon-rhombus.svg"];

document.documentElement.style.margin = "0";
document.documentElement.style.height = "100%";
document.documentElement.style.overflow = "hidden";
document.documentElement.style.overscrollBehavior = "none";
document.body.style.margin = "0";
document.body.style.height = "100%";
document.body.style.overflow = "hidden";
document.body.style.overscrollBehavior = "none";

const favicon = document.querySelector("link[rel='icon']");
if (favicon instanceof HTMLLinkElement) {
  favicon.href = FAVICONS[Math.floor(Math.random() * FAVICONS.length)];
}

createRoot(document.getElementById("root")).render(<Dashboard />);
