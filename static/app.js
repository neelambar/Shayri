(function () {
  "use strict";
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} },
  };
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // Dark / light toggle
  const toggle = $(".theme-toggle");
  if (toggle) toggle.addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("shayri-theme", root.dataset.theme);
  });

  // Hindi / English letters (both are in the page; CSS shows one)
  const scriptToggle = $(".script-toggle");
  const isEnglish = () => document.documentElement.dataset.script === "en";
  if (scriptToggle) scriptToggle.addEventListener("click", () => {
    const root = document.documentElement;
    if (isEnglish()) delete root.dataset.script; else root.dataset.script = "en";
    store.set("shayri-script", isEnglish() ? "en" : null);
    if (window.setTitle) window.setTitle();
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (typing) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.form && e.target.form.id === "poem-form") {
        e.preventDefault();
        e.target.form.requestSubmit();
      }
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); $("#topsearch").focus(); }
    else if (e.key === "n") location.href = "/add";
    else if (e.key === "r") location.href = "/random";
    else if (e.key === "e" && scriptToggle) scriptToggle.click();
    else if (e.key === "ArrowLeft" && $("#prev-link")) location.href = $("#prev-link").href;
    else if (e.key === "ArrowRight" && $("#next-link")) location.href = $("#next-link").href;
  });

  // Confirm before destructive actions
  $$("form[data-confirm]").forEach((f) => f.addEventListener("submit", (e) => {
    if (!confirm(f.dataset.confirm)) e.preventDefault();
  }));

  // Favourite toggle without page reload
  $$(".favform").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $(".star", f);
    try {
      const res = await fetch(f.action, { method: "POST", headers: { "X-Requested-With": "fetch" } });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      btn.classList.toggle("on", data.favorite);
      btn.setAttribute("aria-pressed", data.favorite);
    } catch (err) { f.submit(); }
  }));

  // Poem page: font size, copy, share
  const body = $("#poem-body");
  if (body) {
    const root = document.documentElement;
    let size = parseFloat(store.get("shayri-font")) || 1.25;
    const apply = () => root.style.setProperty("--poem-size", size + "rem");
    apply();
    $$("[data-font]").forEach((b) => b.addEventListener("click", () => {
      size = Math.min(2.6, Math.max(0.9, size + 0.15 * Number(b.dataset.font)));
      store.set("shayri-font", String(size));
      apply();
    }));

    const both = JSON.parse($("#poem-data").textContent);
    const textOf = () => {
      const data = both[isEnglish() ? "en" : "hi"];
      return [data.title, data.body, data.poet ? "— " + data.poet : ""].filter(Boolean).join("\n\n");
    };
    const copyBtn = $("#copy-btn");
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(textOf());
      } catch (e) {
        const ta = document.createElement("textarea");
        ta.value = textOf(); document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); ta.remove();
      }
      const old = copyBtn.textContent;
      copyBtn.textContent = "✓ Copied";
      setTimeout(() => (copyBtn.textContent = old), 1500);
    });

    const shareBtn = $("#share-btn");
    if (navigator.share) {
      shareBtn.hidden = false;
      shareBtn.addEventListener("click", () => navigator.share({ text: textOf() }).catch(() => {}));
    }
  }

  // Add form: tag suggestions + draft autosave
  const tagsInput = $("#tags-input");
  $$("[data-tag]").forEach((b) => b.addEventListener("click", () => {
    const cur = tagsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!cur.includes(b.dataset.tag)) cur.push(b.dataset.tag);
    tagsInput.value = cur.join(", ");
    tagsInput.dispatchEvent(new Event("input", { bubbles: true }));
  }));

  const form = $("form[data-draft]");
  if (form) {
    const key = form.dataset.draft;
    const status = $(".draft-status", form);
    const fields = $$("input[name], textarea[name]", form).filter((el) => el.type !== "checkbox" && el.type !== "hidden");
    const isEmpty = () => fields.every((el) => !el.value.trim() || el.name === "poet");
    // Restore a draft only into an empty form (not after a server-side re-render)
    const saved = store.get(key);
    if (saved && isEmpty() && !$(".flash", document)) {
      try {
        const d = JSON.parse(saved);
        fields.forEach((el) => { if (d[el.name]) el.value = d[el.name]; });
        if (status) status.textContent = "Unsaved draft restored.";
      } catch (e) {}
    }
    let timer;
    form.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const d = {};
        fields.forEach((el) => (d[el.name] = el.value));
        store.set(key, JSON.stringify(d));
        if (status) status.textContent = "Draft saved on this device.";
      }, 500);
    });
    form.addEventListener("submit", () => store.set(key, null));
  }

  // Poets page filter
  const pf = $("#poet-filter");
  if (pf) pf.addEventListener("input", () => {
    const v = pf.value.trim().toLowerCase().normalize("NFD").replace(/़/g, "");
    $$(".poet-list li").forEach((li) => {
      const name = li.dataset.name.normalize("NFD").replace(/़/g, "");
      li.hidden = v && !name.includes(v);
    });
    $$(".poet-group").forEach((g) => (g.hidden = !$$("li", g).some((li) => !li.hidden)));
  });
})();
