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

// ---------------------------------------------------------------- sessions

function slug(name) {
  return name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

const newSession = document.getElementById("new-session");
if (newSession) {
  const input = newSession.querySelector("input");
  const hint = document.getElementById("folder-hint");
  input.addEventListener("input", () => {
    hint.textContent = `Les photos prises ensuite iront dans ${input.dataset.today}_${slug(input.value) || "Nom-de-la-session"}/`;
  });
  newSession.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const btn = newSession.querySelector("button");
    btn.disabled = true;
    post("/api/sessions", { name: input.value })
      .then(() => location.reload())
      .catch(() => { toast("Impossible de créer la session : réessaie."); btn.disabled = false; });
  });
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-session-active]");
  if (!btn) return;
  btn.disabled = true;
  const id = btn.dataset.sessionActive;
  post("/api/sessions/active", { id: id === "" ? null : Number(id) })
    .then(() => location.reload())
    .catch(() => { toast("Impossible de changer de session : réessaie."); btn.disabled = false; });
});

// ---------------------------------------------------------------- rejet et corbeille

const putTrash = document.getElementById("put-trash");
if (putTrash) {
  putTrash.addEventListener("click", () => {
    putTrash.disabled = true;
    const ids = putTrash.dataset.ids.split(",").map(Number);
    post("/api/corbeille/mettre", { ids })
      .then(() => location.replace("/corbeille"))
      .catch(() => { toast("Impossible de mettre à la corbeille : réessaie."); putTrash.disabled = false; });
  });
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-restore]");
  if (!btn) return;
  btn.disabled = true;
  post("/api/corbeille/recuperer", { id: Number(btn.dataset.restore) })
    .then(() => location.replace("/corbeille?r=1"))
    .catch(() => { toast("Impossible de récupérer la photo : réessaie."); btn.disabled = false; });
});

const confirmEmpty = document.getElementById("confirm-empty");
if (confirmEmpty) {
  document.getElementById("ask-empty").addEventListener("click", () => { confirmEmpty.hidden = false; });
  document.getElementById("cancel-empty").addEventListener("click", () => { confirmEmpty.hidden = true; });
  const doEmpty = document.getElementById("do-empty");
  doEmpty.addEventListener("click", () => {
    doEmpty.disabled = true;
    post("/api/corbeille/vider", {})
      .then(() => location.replace("/corbeille"))
      .catch(() => { toast("Impossible de vider la corbeille : réessaie."); doEmpty.disabled = false; });
  });
}

// ---------------------------------------------------------------- galerie : choix des sessions affichées

const scopePicker = document.getElementById("scope-picker");
if (scopePicker) {
  document.getElementById("open-scope").addEventListener("click", () => { scopePicker.hidden = false; });
  document.getElementById("close-scope").addEventListener("click", () => { scopePicker.hidden = true; });
  scopePicker.addEventListener("click", (ev) => { if (ev.target === scopePicker) scopePicker.hidden = true; });
  // Rien de coché : « Afficher la sélection » montre toutes les photos.
  scopePicker.querySelector("form").addEventListener("submit", (ev) => {
    if (!scopePicker.querySelector("input[name=s]:checked")) {
      ev.preventDefault();
      location.href = `/?f=${scopePicker.querySelector("input[name=f]").value}&s=toutes`;
    }
  });
}
