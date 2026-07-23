// AutoJob — progressive enhancement: theme, toasts, and small form niceties.
// Everything degrades gracefully: forms work without JS, and a <noscript>
// fallback shows flash messages when JS is unavailable.
(function () {
  "use strict";

  var THEME_KEY = "autojob-theme";

  // ── Theme management ─────────────────────────────────────────
  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme"); // follow system
    }
  }

  function currentTheme() {
    var stored = null;
    try { stored = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (stored === "light" || stored === "dark") return stored;
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    return prefersDark ? "dark" : "light";
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
      applyTheme(next);
    });
  });

  // ── Toast notifications ──────────────────────────────────────
  var container = document.getElementById("toast-container");

  function toast(message, type, timeout) {
    if (!container || !message) return;
    type = type || "info";
    if (type === "message") type = "info";
    var el = document.createElement("div");
    el.className = "toast " + type;
    el.setAttribute("role", type === "error" ? "alert" : "status");

    var msg = document.createElement("span");
    msg.className = "toast-msg";
    msg.textContent = message;

    var close = document.createElement("button");
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = "&times;";

    el.appendChild(msg);
    el.appendChild(close);
    container.appendChild(el);
    requestAnimationFrame(function () { el.classList.add("show"); });

    var timer;
    function dismiss() {
      clearTimeout(timer);
      el.classList.remove("show");
      setTimeout(function () { el.remove(); }, 260);
    }
    close.addEventListener("click", dismiss);
    // Errors linger; others auto-dismiss.
    if (type !== "error") timer = setTimeout(dismiss, timeout || 4500);
  }

  // Promote server flashes (seeded hidden) into toasts.
  document.querySelectorAll(".toast-seed").forEach(function (seed) {
    toast(seed.textContent.trim(), seed.dataset.type);
  });

  // Expose for inline scripts (e.g. the run panel).
  window.AutoJob = window.AutoJob || {};
  window.AutoJob.toast = toast;

  // ── Password show/hide ───────────────────────────────────────
  document.querySelectorAll("[data-toggle='password']").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = btn.closest(".password-wrap").querySelector("input");
      if (!input) return;
      var toText = input.type === "password";
      input.type = toText ? "text" : "password";
      btn.textContent = toText ? "Hide" : "Show";
      btn.setAttribute("aria-label", toText ? "Hide password" : "Show password");
    });
  });

  // ── Prevent double-submit ────────────────────────────────────
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var submit = form.querySelector("button[type='submit']");
      if (submit && !submit.disabled) {
        submit.dataset.original = submit.textContent;
        submit.disabled = true;
        submit.textContent = "Please wait…";
        setTimeout(function () {
          submit.disabled = false;
          if (submit.dataset.original) submit.textContent = submit.dataset.original;
        }, 4000);
      }
    });
  });
})();
