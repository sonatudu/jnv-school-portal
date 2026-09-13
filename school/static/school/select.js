(function () {
  function isPlainSelect(el) {
    if (!el || el.tagName !== "SELECT") return false;
    if (el.dataset.jnvEnhanced === "1") return false;
    if (el.multiple) return false;
    if (el.size && Number(el.size) > 1) return false;
    if (el.classList.contains("admin-autocomplete")) return false;
    if (el.closest(".select2")) return false;
    return true;
  }

  function optionLabel(select) {
    var opt = select.options[select.selectedIndex];
    if (!opt) return "Select";
    var text = (opt.textContent || "").trim();
    return text || "Select";
  }

  function closeAll(except) {
    document.querySelectorAll(".jnv-select.is-open").forEach(function (wrap) {
      if (except && wrap === except) return;
      wrap.classList.remove("is-open");
      var btn = wrap.querySelector(".jnv-select-btn");
      var menu = wrap.querySelector(".jnv-select-menu");
      if (btn) btn.setAttribute("aria-expanded", "false");
      if (menu) menu.hidden = true;
    });
  }

  function fillMenu(select, menu, btn) {
    menu.replaceChildren();
    Array.prototype.forEach.call(select.options, function (opt, index) {
      if (opt.hidden) return;
      var item = document.createElement("div");
      item.className = "jnv-select-option";
      item.setAttribute("role", "option");
      item.dataset.index = String(index);
      item.textContent = (opt.textContent || "").trim() || "\u00a0";
      if (opt.disabled) item.setAttribute("aria-disabled", "true");
      if (opt.selected) item.setAttribute("aria-selected", "true");
      menu.appendChild(item);
    });
    var label = btn.querySelector(".jnv-select-label");
    if (label) label.textContent = optionLabel(select);
  }

  function enhance(select) {
    if (!isPlainSelect(select)) return;
    select.dataset.jnvEnhanced = "1";

    var wrap = document.createElement("div");
    wrap.className = "jnv-select";
    if (select.disabled) wrap.classList.add("is-disabled");

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "jnv-select-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    if (select.disabled) btn.disabled = true;
    if (select.id) {
      var forLabel = document.querySelector('label[for="' + select.id + '"]');
      if (forLabel) {
        if (!forLabel.id) forLabel.id = select.id + "-caption";
        btn.setAttribute("aria-labelledby", forLabel.id);
      }
    }

    var label = document.createElement("span");
    label.className = "jnv-select-label";
    btn.appendChild(label);

    var menu = document.createElement("div");
    menu.className = "jnv-select-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    var parent = select.parentNode;
    if (parent && parent.tagName === "LABEL") {
      var stack = document.createElement("div");
      stack.className = "jnv-select-field";
      parent.parentNode.insertBefore(stack, parent);
      stack.appendChild(parent);
      stack.appendChild(wrap);
      if (!parent.id) {
        parent.id = "jnv-select-caption-" + (select.name || "field") + "-" + String(Math.random()).slice(2, 8);
      }
      btn.setAttribute("aria-labelledby", parent.id);
    } else {
      parent.insertBefore(wrap, select);
    }
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    wrap.appendChild(select);
    select.classList.add("jnv-select-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.style.display = "none";

    fillMenu(select, menu, btn);

    function open() {
      if (select.disabled) return;
      closeAll(wrap);
      fillMenu(select, menu, btn);
      wrap.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
      menu.hidden = false;
      var current = menu.querySelector('[aria-selected="true"]');
      if (current) current.scrollIntoView({ block: "nearest" });
    }

    function close() {
      wrap.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
      menu.hidden = true;
    }

    function choose(index) {
      var opt = select.options[index];
      if (!opt || opt.disabled) return;
      select.selectedIndex = index;
      fillMenu(select, menu, btn);
      close();
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    btn.addEventListener("click", function (event) {
      event.preventDefault();
      if (wrap.classList.contains("is-open")) close();
      else open();
    });

    menu.addEventListener("click", function (event) {
      var item = event.target.closest(".jnv-select-option");
      if (!item || item.getAttribute("aria-disabled") === "true") return;
      choose(Number(item.dataset.index));
    });

    btn.addEventListener("keydown", function (event) {
      var key = event.key;
      if (key === "ArrowDown" || key === "Enter" || key === " ") {
        event.preventDefault();
        if (!wrap.classList.contains("is-open")) open();
      } else if (key === "Escape") {
        close();
      }
    });

    select.addEventListener("change", function () {
      fillMenu(select, menu, btn);
    });
    select.addEventListener("jnv-options-changed", function () {
      fillMenu(select, menu, btn);
    });
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll("select").forEach(enhance);
  }

  document.addEventListener("DOMContentLoaded", function () {
    enhanceAll(document);
  });
  document.addEventListener("click", function (event) {
    if (!event.target.closest(".jnv-select")) closeAll();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeAll();
  });
})();
