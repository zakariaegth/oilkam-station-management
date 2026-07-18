const currentPath = window.location.pathname;

const routeForPath = (path) => {
  if (path === "/" || path.startsWith("/dashboard")) return "home";
  if (path.startsWith("/tasks")) return "tasks";
  if (path.startsWith("/training")) return "training";
  if (path.startsWith("/attendance") || path.startsWith("/pointage")) return "attendance";
  if (path.startsWith("/losses")) return "losses";
  return "";
};

const activeRoute = routeForPath(currentPath);

document.querySelectorAll("[data-route]").forEach((link) => {
  if (link.dataset.route === activeRoute) {
    link.classList.add("is-active");
    link.setAttribute("aria-current", "page");
  }
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  });
}
