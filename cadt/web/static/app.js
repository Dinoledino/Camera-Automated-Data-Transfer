// Interface de tri : boutons du plein écran, balayage, rafraîchissement de l'état de la copie.
"use strict";

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CADT": "1" },
    body: JSON.stringify(body),
  }).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}

function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

// ---------------------------------------------------------------- plein écran

function setPressed(btn, on) {
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.photo;
  btn.disabled = true;
  if (btn.dataset.action === "favorite") {
    const want = !btn.classList.contains("on");
    post(`/api/photo/${id}/favorite`, { value: want })
      .then((r) => {
        setPressed(btn, r.favorite);
        btn.querySelector("span").textContent = r.favorite ? "Dans les favoris" : "Mettre en favori";
      })
      .catch(() => toast("Impossible d'enregistrer : réessaie."))
      .finally(() => { btn.disabled = false; });
  } else if (btn.dataset.action === "flag") {
    const flag = btn.classList.contains("on") ? "none" : btn.dataset.flag;
    post(`/api/photo/${id}/flag`, { flag })
      .then((r) => {
        document.querySelectorAll("button[data-action=flag]").forEach((b) => setPressed(b, b.dataset.flag === r.flag));
      })
      .catch(() => toast("Impossible d'enregistrer : réessaie."))
      .finally(() => { btn.disabled = false; });
  }
});

const viewer = document.querySelector(".viewer-page");
if (viewer) {
  const go = (url) => { if (url) location.replace(url); };
  let x0 = null, y0 = null;
  const stage = viewer.querySelector(".stage");
  stage.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  stage.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? viewer.dataset.next : viewer.dataset.prev);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") go(viewer.dataset.next);
    if (e.key === "ArrowLeft") go(viewer.dataset.prev);
  });
}

// ---------------------------------------------------------------- galerie : état de la copie

const status = document.getElementById("status");
if (status) {
  // Recharge la galerie quand de nouvelles photos sont à l'abri (sauf si l'utilisateur fait défiler).
  let last = null;
  setInterval(() => {
    fetch("/api/status").then((r) => r.json()).then((s) => {
      const now = `${s.saved}:${s.unsaved}:${s.camera}:${s.full}`;
      if (last !== null && now !== last && window.scrollY < 80) location.reload();
      last = now;
    }).catch(() => {});
  }, 4000);
}
