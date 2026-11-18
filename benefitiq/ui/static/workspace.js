/* Browser-only case list, checklist ticks, and panel toggles.
   This file does not contact a server, a model, or any other host. */

(function () {
  var CASES_KEY = "benefitiq.myCases.v1";
  var CHECKS_KEY = "benefitiq.checks.v1";

  function readJson(key) {
    try {
      var raw = window.localStorage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (error) {
      return null;
    }
  }

  function writeJson(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      /* Private mode can refuse storage. The page still works. */
    }
  }

  function cases() {
    var stored = readJson(CASES_KEY);
    return Array.isArray(stored) ? stored : [];
  }

  function checks() {
    var stored = readJson(CHECKS_KEY);
    return stored && typeof stored === "object" ? stored : {};
  }

  function safeHttps(url) {
    return typeof url === "string" && url.indexOf("https://") === 0 ? url : "";
  }

  function fill(node, record) {
    var fields = node.querySelectorAll("[data-field]");
    for (var i = 0; i < fields.length; i += 1) {
      var field = fields[i];
      var name = field.getAttribute("data-field");
      var value = record[name] || "";
      if (name === "progress") {
        value = (record.checklist_checked || 0) + " of " + (record.checklist_total || 0);
      }
      if (name === "label") {
        value = "SYNTHETIC";
      }
      field.textContent = value;
      if (field.classList.contains("chip")) {
        field.classList.remove("chip-ready", "chip-docs", "chip-none");
        if (record.board_status === "Ready to bill") field.classList.add("chip-ready");
        else if (record.board_status === "Needs documentation") field.classList.add("chip-docs");
        else field.classList.add("chip-none");
      }
    }
    var link = node.querySelector("a");
    if (link && record.case_id) {
      link.setAttribute("href", "/cases/local#" + encodeURIComponent(record.case_id));
    }
  }

  function renderUserCases() {
    var stored = cases();
    var cardTemplate = document.getElementById("user-case-card");
    var rowTemplate = document.getElementById("user-case-row");
    var mounts = document.querySelectorAll("[data-user-cases]");
    for (var i = 0; i < mounts.length; i += 1) {
      var mount = mounts[i];
      var kind = mount.getAttribute("data-user-cases");
      while (mount.firstChild) mount.removeChild(mount.firstChild);
      for (var c = 0; c < stored.length; c += 1) {
        var record = stored[c];
        if (!record || !record.case_id) continue;
        if (kind !== "list" && record.board_status !== kind) continue;
        var template = kind === "list" ? rowTemplate : cardTemplate;
        if (!template) continue;
        var node = template.content.firstElementChild.cloneNode(true);
        fill(node, record);
        mount.appendChild(node);
      }
    }
  }

  function saveCase(record) {
    if (!record || !record.case_id || String(record.case_id).indexOf("SYN-") !== 0) return;
    var stored = cases().filter(function (item) {
      return item.case_id !== record.case_id;
    });
    stored.unshift(record);
    writeJson(CASES_KEY, stored);
  }

  function rememberSnapshot() {
    var node = document.getElementById("case-snapshot");
    if (!node) return;
    try {
      var record = JSON.parse(node.textContent);
      var savedChecks = checks();
      var picked = savedChecks[record.case_id];
      if (Array.isArray(picked)) record.checklist_checked = picked.length;
      saveCase(record);
    } catch (error) {
      /* Ignore a damaged snapshot. */
    }
  }

  function applyChecks() {
    var saved = checks();
    var boxes = document.querySelectorAll("input[data-case-id][data-check-index]");
    var seen = {};
    for (var i = 0; i < boxes.length; i += 1) {
      var box = boxes[i];
      var caseId = box.getAttribute("data-case-id");
      if (!Object.prototype.hasOwnProperty.call(saved, caseId)) continue;
      var index = String(box.getAttribute("data-check-index"));
      var selected = saved[caseId].map(String);
      box.checked = selected.indexOf(index) !== -1;
      seen[caseId] = true;
    }
    Object.keys(seen).forEach(function (caseId) {
      updateProgress(caseId);
    });
  }

  function updateProgress(caseId) {
    var boxes = document.querySelectorAll('input[data-case-id="' + caseId + '"]');
    var total = boxes.length;
    var checked = 0;
    for (var i = 0; i < boxes.length; i += 1) {
      if (boxes[i].checked) checked += 1;
    }
    var labels = document.querySelectorAll('[data-progress-for="' + caseId + '"]');
    for (var n = 0; n < labels.length; n += 1) {
      labels[n].textContent = checked + " of " + total;
    }
    var saved = checks();
    var picked = [];
    for (var b = 0; b < boxes.length; b += 1) {
      if (boxes[b].checked) picked.push(Number(boxes[b].getAttribute("data-check-index")));
    }
    saved[caseId] = picked;
    writeJson(CHECKS_KEY, saved);
    var stored = cases();
    var changed = false;
    for (var c = 0; c < stored.length; c += 1) {
      if (stored[c].case_id === caseId) {
        stored[c].checklist_checked = checked;
        stored[c].checklist_total = total;
        changed = true;
      }
    }
    if (changed) writeJson(CASES_KEY, stored);
  }

  function bindChecks() {
    var boxes = document.querySelectorAll("input[data-case-id][data-check-index]");
    for (var i = 0; i < boxes.length; i += 1) {
      boxes[i].addEventListener("change", function (event) {
        updateProgress(event.target.getAttribute("data-case-id"));
      });
    }
  }

  function bindClear() {
    var form = document.getElementById("clear-my-cases-form");
    if (!form) return;
    form.addEventListener("submit", function () {
      try {
        window.localStorage.removeItem(CASES_KEY);
      } catch (error) {
        /* The form still clears the signed session cookie. */
      }
    });
  }

  function bindMenu() {
    var button = document.querySelector("[data-menu]");
    var sidebar = document.getElementById("sidebar");
    var backdrop = document.querySelector("[data-nav-close]");
    if (!button || !sidebar) return;
    function setOpen(open) {
      sidebar.classList.toggle("is-open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
      if (backdrop) {
        if (open) backdrop.removeAttribute("hidden");
        else backdrop.setAttribute("hidden", "");
      }
    }
    button.addEventListener("click", function () {
      setOpen(!sidebar.classList.contains("is-open"));
    });
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        setOpen(false);
      });
    }
    var closers = document.querySelectorAll("[data-menu-close]");
    for (var i = 0; i < closers.length; i += 1) {
      closers[i].addEventListener("click", function () {
        setOpen(false);
      });
    }
  }

  function bindChat() {
    var button = document.querySelector("[data-chat-toggle]");
    if (!button) return;
    button.addEventListener("click", function () {
      var panel = button.closest("[data-testid='case-chat']");
      if (!panel) return;
      var collapsed = panel.classList.toggle("is-collapsed");
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      button.textContent = collapsed ? "Expand" : "Collapse";
    });
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function renderLocal() {
    var root = document.getElementById("local-case");
    if (!root || window.location.pathname.indexOf("/cases/local") !== 0) return;
    var caseId = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
    var record = null;
    var stored = cases();
    for (var i = 0; i < stored.length; i += 1) {
      if (stored[i].case_id === caseId) record = stored[i];
    }
    if (!record) return;
    var empty = root.querySelector("[data-local-empty]");
    if (empty) empty.remove();
    var title = root.querySelector("h1");
    if (title) title.textContent = record.result_label || "Saved case";
    var head = root.querySelector(".case-head div");
    if (head) {
      var idLine = element("p", "case-id", record.case_id || "");
      var sub = element("p", "case-sub", (record.procedure || "") + " · " + (record.date || ""));
      head.appendChild(idLine);
      head.appendChild(sub);
      var chip = element("span", "chip", record.board_status || "");
      if (record.board_status === "Ready to bill") chip.classList.add("chip-ready");
      else if (record.board_status === "Needs documentation") chip.classList.add("chip-docs");
      else chip.classList.add("chip-none");
      root.querySelector(".case-head").appendChild(chip);
    }
    var result = element("section", "panel card");
    result.appendChild(element("h2", "", "Result"));
    var reason = element("p", "reason", record.reason || "");
    result.appendChild(reason);
    root.appendChild(result);

    var codes = element("section", "panel card");
    codes.appendChild(element("h2", "", "Codes to consider"));
    (record.codes || []).forEach(function (code) {
      if (code.code) codes.appendChild(element("p", "code", "Code " + code.code));
      if (code.label) codes.appendChild(element("p", "label", code.label));
      codes.appendChild(element("p", "", code.note || ""));
    });
    root.appendChild(codes);

    var list = element("section", "panel card");
    list.appendChild(element("h2", "", "Documentation checklist"));
    var items = document.createElement("ul");
    items.className = "check-list";
    (record.checklist || []).forEach(function (item, index) {
      var li = document.createElement("li");
      var label = document.createElement("label");
      label.className = "check-row";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.setAttribute("data-case-id", record.case_id);
      box.setAttribute("data-check-index", String(index));
      label.appendChild(box);
      label.appendChild(element("span", "", item.text || ""));
      li.appendChild(label);
      var href = safeHttps(item.url);
      if (href) {
        var link = document.createElement("a");
        link.href = href;
        link.textContent = item.title || href;
        li.appendChild(link);
        if (item.effective_from) {
          li.appendChild(element("span", "", "Effective " + item.effective_from + (item.effective_to ? " to " + item.effective_to : "")));
        }
      }
      items.appendChild(li);
    });
    list.appendChild(items);
    root.appendChild(list);
  }

  rememberSnapshot();
  renderLocal();
  renderUserCases();
  applyChecks();
  bindChecks();
  bindClear();
  bindMenu();
  bindChat();
})();
