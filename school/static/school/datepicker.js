(function () {
  var MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  var WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function toISO(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function parseISO(value) {
    if (!value) return new Date();
    var parts = String(value).split("-");
    if (parts.length !== 3) return new Date();
    var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return isNaN(d.getTime()) ? new Date() : d;
  }

  function formatLabel(d) {
    return d.getDate() + " " + MONTHS[d.getMonth()].slice(0, 3) + " " + d.getFullYear();
  }

  function closeAll(except) {
    document.querySelectorAll(".jnv-datepicker.is-open").forEach(function (wrap) {
      if (except && wrap === except) return;
      wrap.classList.remove("is-open");
      var btn = wrap.querySelector(".jnv-datepicker-btn");
      var panel = wrap.querySelector(".jnv-datepicker-panel");
      if (btn) btn.setAttribute("aria-expanded", "false");
      if (panel) panel.hidden = true;
    });
  }

  function renderPanel(wrap) {
    var input = wrap.querySelector('input[name="date"]');
    var panel = wrap.querySelector(".jnv-datepicker-panel");
    var label = wrap.querySelector(".jnv-datepicker-label");
    var selected = parseISO(input.value);
    var view = wrap._view || new Date(selected.getFullYear(), selected.getMonth(), 1);
    wrap._view = view;

    var year = view.getFullYear();
    var month = view.getMonth();
    var first = new Date(year, month, 1);
    var startWeekday = first.getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var todayISO = toISO(new Date());
    var selectedISO = toISO(selected);

    var html = [];
    html.push('<div class="jnv-datepicker-head">');
    html.push('<button type="button" class="jnv-datepicker-nav" data-dir="-1" aria-label="Previous month">‹</button>');
    html.push('<p class="jnv-datepicker-month">' + MONTHS[month] + " " + year + "</p>");
    html.push('<button type="button" class="jnv-datepicker-nav" data-dir="1" aria-label="Next month">›</button>');
    html.push("</div>");
    html.push('<div class="jnv-datepicker-week">');
    WEEKDAYS.forEach(function (day) {
      html.push('<span>' + day + "</span>");
    });
    html.push("</div>");
    html.push('<div class="jnv-datepicker-grid">');
    var i;
    for (i = 0; i < startWeekday; i += 1) {
      html.push('<span class="jnv-datepicker-empty"></span>');
    }
    for (i = 1; i <= daysInMonth; i += 1) {
      var iso = year + "-" + pad(month + 1) + "-" + pad(i);
      var cls = "jnv-datepicker-day";
      if (iso === selectedISO) cls += " is-selected";
      if (iso === todayISO) cls += " is-today";
      html.push(
        '<button type="button" class="' + cls + '" data-date="' + iso + '">' + i + "</button>"
      );
    }
    html.push("</div>");
    panel.innerHTML = html.join("");
    if (label) label.textContent = formatLabel(selected);
  }

  function enhance(root) {
    (root || document).querySelectorAll("[data-jnv-datepicker]").forEach(function (wrap) {
      if (wrap.dataset.enhanced === "1") return;
      wrap.dataset.enhanced = "1";
      var input = wrap.querySelector('input[name="date"]');
      var btn = wrap.querySelector(".jnv-datepicker-btn");
      var panel = wrap.querySelector(".jnv-datepicker-panel");
      if (!input || !btn || !panel) return;

      wrap._view = parseISO(input.value);
      renderPanel(wrap);

      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var open = wrap.classList.contains("is-open");
        closeAll();
        if (!open) {
          wrap.classList.add("is-open");
          btn.setAttribute("aria-expanded", "true");
          panel.hidden = false;
        }
      });

      panel.addEventListener("click", function (event) {
        event.stopPropagation();
        var nav = event.target.closest(".jnv-datepicker-nav");
        if (nav) {
          var dir = Number(nav.getAttribute("data-dir"));
          wrap._view = new Date(wrap._view.getFullYear(), wrap._view.getMonth() + dir, 1);
          renderPanel(wrap);
          return;
        }
        var day = event.target.closest(".jnv-datepicker-day");
        if (!day) return;
        input.value = day.getAttribute("data-date");
        wrap._view = parseISO(input.value);
        renderPanel(wrap);
        closeAll();
        if (input.form) input.form.submit();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    enhance(document);
  });
  document.addEventListener("click", function () {
    closeAll();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeAll();
  });
})();
