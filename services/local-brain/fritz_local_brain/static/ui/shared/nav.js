// Fritz Local Brain — shared sidebar nav (#220).
//
// Renders the sidebar into #sidebar and highlights the active page. Each page
// sets <body data-page="overview|activity|agents|operations|settings|knowledge">
// so the matching link gets the .active class. Clean deep-linkable URLs
// (/ui/, /ui/activity, …) served by the FastAPI static mount.

const NAV_ITEMS = [
  { page: "overview",   href: "/ui/",           label: "Overview" },
  { page: "activity",   href: "/ui/activity",   label: "Activity" },
  { page: "agents",     href: "/ui/agents",     label: "Agents" },
  { page: "operations", href: "/ui/operations", label: "Operations" },
  { page: "settings",   href: "/ui/settings",   label: "Settings" },
  { page: "knowledge",  href: "/ui/knowledge",  label: "Knowledge" },
];

function renderNav() {
  const el = document.getElementById("sidebar");
  if (!el) return;
  const active = document.body.getAttribute("data-page") || "";
  el.innerHTML =
    '<div class="nav-brand">Fritz Brain</div>' +
    NAV_ITEMS.map(item => {
      const cls = item.page === active ? "nav-link active" : "nav-link";
      return `<a class="${cls}" href="${item.href}">${item.label}</a>`;
    }).join("");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderNav);
} else {
  renderNav();
}
