// Progressive enhancement only — the forms work without JS.
(function () {
  "use strict";

  // Show/hide password toggles.
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

  // Disable submit buttons on submit to prevent double-posting.
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var submit = form.querySelector("button[type='submit']");
      if (submit) {
        submit.disabled = true;
        submit.dataset.original = submit.textContent;
        submit.textContent = "Please wait…";
        // Re-enable shortly in case validation blocks the navigation.
        setTimeout(function () {
          submit.disabled = false;
          if (submit.dataset.original) submit.textContent = submit.dataset.original;
        }, 4000);
      }
    });
  });
})();
