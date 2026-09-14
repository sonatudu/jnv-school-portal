(function () {
  function bindSearch(form) {
    if (!form || form.dataset.jnvSearch === "1") return;
    form.dataset.jnvSearch = "1";
    var input = form.querySelector("input[type='search'], input[name='q'], #searchbar");
    var btn = form.querySelector(".jnv-class-search-toggle");
    if (!input || !btn) return;
    var hasQuery = form.getAttribute("data-has-query") === "1" || Boolean((input.value || "").trim());

    function openField() {
      form.classList.add("is-open");
      input.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      input.focus();
    }

    function closeField() {
      form.classList.remove("is-open");
      input.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }

    if (hasQuery) openField();

    btn.addEventListener("click", function () {
      if (!form.classList.contains("is-open")) {
        openField();
        return;
      }
      if ((input.value || "").trim() || hasQuery) {
        form.submit();
        return;
      }
      closeField();
    });
  }

  function enhanceAll() {
    document.querySelectorAll("form.jnv-class-search").forEach(bindSearch);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceAll);
  } else {
    enhanceAll();
  }
})();
