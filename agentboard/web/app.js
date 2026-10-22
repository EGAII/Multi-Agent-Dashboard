"use strict";
// AgentBoard page. Pulls a JSON snapshot from Python (window.pywebview.api) and patches the DOM in place,
// keyed by agent type and agent process, so bars animate between values instead of redrawing.
// The window keeps its width; its height follows the content, so opening an agent type's details makes it taller.
// Each page has its own column count (3, 2 or 1), set with the small buttons in the toolbar; one column also
// narrows the window and tightens the spacing.
(() => {
  const $ = (id) => document.getElementById(id);
  const ICON = (name) => (window.ICONS && window.ICONS[name]) || "";
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: keep defaults */ } },
  };
  // open: which agent type's details are shown ("all", a type id, or null). Closed on every start.
  const S = { api: null, snap: null, lastTs: 0, page: store.get("ab.page", "usage"), open: null, maximized: false, layout: "", addOpen: false,
              cols: { usage: Number(store.get("ab.cols.usage", 2)) || 2, agents: Number(store.get("ab.cols.agents", 2)) || 2 } };

  // ------------------------------------------------------------------ formatting
  const fmt = (n) => {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k";
    return String(Math.round(n));
  };
  const until = (ts) => {
    const m = Math.max(0, Math.round((ts - Date.now() / 1000) / 60));
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
  };
  const pct = (p) => `${Math.round(p)}%`;
  const level = (p) => (p >= 95 ? "crit" : p >= 80 ? "warn" : "");
  const clamp = (p) => Math.max(0, Math.min(100, Number(p) || 0));

  function paintIcons(root = document) {
    root.querySelectorAll("[data-icon]").forEach((el) => {
      if (el.dataset.painted === el.dataset.icon) return;
      const svg = ICON(el.dataset.icon);
      el.innerHTML = el.classList.contains("icon") ? svg : `<span class="icon">${svg}</span>`;
      el.dataset.painted = el.dataset.icon;
    });
  }
  const setIcon = (el, name) => { el.dataset.icon = name; paintIcons(el.parentNode || document); };

  // ------------------------------------------------------------------ usage page: a monitor (card) per agent type,
  // with a bar per window (5h, 7d, Fable, ...). Agent types in use show by default; you can add others and remove any.
  const cards = new Map();
  const WIN_NAME = { "5h": "5-hour", "7d": "7-day", "1d": "Today" };
  // hidden: monitors you removed; added: monitors you added. Kept in this window's storage.
  const MON = (() => {
    try {
      const m = JSON.parse(store.get("ab.monitors", "{}"));
      return { hidden: new Set(m.hidden || []), added: Array.isArray(m.added) ? m.added : [] };
    } catch (e) {
      return { hidden: new Set(), added: [] };
    }
  })();
  const saveMon = () => store.set("ab.monitors", JSON.stringify({ hidden: [...MON.hidden], added: MON.added }));

  function shownTypes(types) {
    const out = types.filter((t) => t.visible && !MON.hidden.has(t.id));
    for (const id of MON.added) {
      const t = types.find((x) => x.id === id);
      if (t && !out.includes(t)) out.push(t);
    }
    return out;
  }
  function addMonitor(id) {
    MON.hidden.delete(id);
    if (!MON.added.includes(id)) MON.added.push(id);
    saveMon();
    S.addOpen = false;
    if (S.snap) renderUsage(S.snap);
    checkLayout();
  }
  function removeMonitor(id) {
    MON.hidden.add(id);
    MON.added = MON.added.filter((x) => x !== id);
    saveMon();
    const t = (S.snap && S.snap.types || []).find((x) => x.id === id);
    if (S.snap) renderUsage(S.snap);
    checkLayout();
    toast(`Removed ${t ? t.name : "the monitor"}. Add monitor brings it back.`);
  }

  function makeCard(id) {
    const el = document.createElement("article");
    el.className = "card";
    el.dataset.id = id;
    el.innerHTML = `
      <div class="head"><i class="tdot"></i><h3></h3><span class="run"></span></div>
      <div class="bars"></div>
      <div class="note"></div>
      <div class="foot"><div class="meta"></div>
        <button class="plan-chip" aria-haspopup="dialog" aria-expanded="false"><span class="plan-name">Auto</span><span class="icon" data-icon="caret-right"></span></button>
        <button class="remove"><span class="icon" data-icon="x"></span>Remove</button></div>`;
    el.querySelector(".remove").addEventListener("click", () => removeMonitor(id));
    el.querySelector(".plan-chip").addEventListener("click", (e) => {
      if (S.planFor === id) closePlan(); else openPlan(id, e.currentTarget);
    });
    paintIcons(el);
    return el;
  }

  // ------------------------------------------------------------------ plans: an estimated % where the agent reports none
  // Pick a plan per monitor: its published limits, your own (Custom), or Auto, which uses limits learned from the
  // official readings you have had (tokens at 100%). The provider's own % always wins when it is reported.
  const loadObj = (k) => {
    try { const v = JSON.parse(store.get(k, "{}")); return v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch (e) { return {}; }
  };
  const PLAN = loadObj("ab.plans");     // type id -> {choice: "auto" | plan id | "custom", custom: {5h|7d|1d: {unit, value}}}
  const LEARN = loadObj("ab.learned");  // "type:window" -> [{v: tokens at 100%, at: time}]
  const UNIT = { tokens: "tokens", requests: "requests", prompts: "messages" };
  const savePlans = () => store.set("ab.plans", JSON.stringify(PLAN));
  const presetsOf = (t) => (t.plans && t.plans.plans) || [];
  const planOf = (t) => PLAN[t.id] || { choice: "auto" };

  function learn(t) {  // each time a window has an official %, remember what 100% came to in tokens
    let changed = false;
    for (const w of t.windows || []) {
      if (w.basis !== "official" || !(w.pct >= 5) || !w.tokens) continue;
      const k = `${t.id}:${w.key}`, list = LEARN[k] || (LEARN[k] = []), full = Math.round(w.tokens * 100 / w.pct);
      const last = list[list.length - 1];
      if (last && Math.abs(last.v - full) / full < 0.03 && Date.now() - last.at < 15 * 60e3) continue;  // nothing new
      list.push({ v: full, at: Date.now() });
      if (list.length > 12) list.shift();
      changed = true;
    }
    if (changed) store.set("ab.learned", JSON.stringify(LEARN));
  }
  function learned(id, key) {  // the middle of the recent readings, so one odd reading does not swing it
    const v = (LEARN[`${id}:${key}`] || []).map((x) => x.v).sort((a, b) => a - b);
    return v.length ? v[Math.floor(v.length / 2)] : null;
  }
  function planLabel(t) {
    const p = planOf(t);
    if (p.choice === "custom") return "Custom";
    const preset = presetsOf(t).find((x) => x.id === p.choice);
    return preset ? preset.short || preset.label : "Auto";
  }
  function limitFor(t, key) {  // your custom limit, else the chosen plan's, else one learned from official readings
    const p = planOf(t);
    if (p.choice === "custom") {
      const c = p.custom && p.custom[key];
      if (c && c.value > 0) return { ...c, src: "custom" };
    } else if (p.choice !== "auto") {
      const preset = presetsOf(t).find((x) => x.id === p.choice);
      const l = preset && preset.limits[key];
      if (l) return { ...l, src: "plan", plan: preset.label };
    }
    const v = key === "1d" ? null : learned(t.id, key);
    return v ? { unit: "tokens", value: v, src: "learned" } : null;
  }
  function estimate(t, w) {  // the window with an estimated % (basis "est"), or as it was when there is nothing to go on
    if (w.basis === "official") return w;
    const lim = limitFor(t, w.key);
    if (!lim) return w;
    if (lim.none) return { ...w, basis: "nolimit", pct: null, lim };
    const count = lim.unit === "tokens" ? w.tokens : lim.unit === "requests" ? w.requests : w.prompts;
    if (count == null) return w;
    if (lim.range) {  // a published range: the bar runs to the top of it, amber from the bottom of it
      const [lo, hi] = lim.range;
      return { ...w, basis: "est", count, lim, pct: 100 * count / hi, lvl: count >= hi ? "crit" : count >= lo ? "warn" : "" };
    }
    const p = 100 * count / lim.value;
    return { ...w, basis: "est", count, lim, pct: p, lvl: level(p) };
  }
  function windowsOf(t) {  // what the card shows: 5h, 7d, model windows, and Today when the plan has a daily limit
    const out = (t.windows || []).map((w) => estimate(t, w));
    if (t.daily && limitFor(t, "1d")) out.push(estimate(t, { key: "1d", label: "Today", basis: null, pct: null, ...t.daily }));
    return out;
  }

  // the floating plan panel, anchored to a monitor's plan chip
  const parseAmount = (s) => {  // "3M", "500k", "1,500" -> number
    const m = String(s).trim().replace(/,/g, "").match(/^(\d+(?:\.\d+)?)\s*([kKmMbB]?)$/);
    if (!m) return 0;
    return Math.round(Number(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[m[2].toLowerCase()] || 1));
  };
  function openPlan(id, chip) {
    S.planFor = id;
    S.planChip = chip;
    renderPlan();
    $("plan-panel").hidden = false;
    placePlan();
    checkLayout();
  }
  function closePlan() {
    if (!S.planFor) return;
    S.planFor = null;
    $("plan-panel").hidden = true;
    document.querySelectorAll(".plan-chip[aria-expanded='true']").forEach((c) => c.setAttribute("aria-expanded", "false"));
    checkLayout();
  }
  function placePlan() {
    const pop = $("plan-panel"), chip = S.planChip;
    if (!chip || !chip.isConnected) return closePlan();
    chip.setAttribute("aria-expanded", "true");
    const r = chip.getBoundingClientRect(), h = pop.scrollHeight;
    pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, window.innerWidth - pop.offsetWidth - 8))}px`;
    // open downwards when it fits, else upwards when that fits, so the window rarely has to grow
    const below = r.bottom + 6 + h <= window.innerHeight - 8, above = r.top - 6 - h >= 8;
    pop.style.top = `${below || !above ? r.bottom + 6 : r.top - 6 - h}px`;
  }
  function renderPlan() {
    const t = S.snap && (S.snap.types || []).find((x) => x.id === S.planFor);
    if (!t) return;
    const p = planOf(t), info = t.plans || { plans: [], note: "", as_of: "" };
    $("plan-title").textContent = `${t.name} plan`;
    const learnedText = ["5h", "7d"].map((k) => [k, learned(t.id, k)]).filter(([, v]) => v).map(([k, v]) => `${k} ≈ ${fmt(v)} tokens`);
    const options = [
      { id: "auto", label: "Auto", desc: learnedText.length ? `Learned: ${learnedText.join(", ")}` : "Official when reported, else learned from official readings" },
      ...presetsOf(t),
      { id: "custom", label: "Custom", desc: "Your own limits" },
    ];
    const list = $("plan-list");
    list.innerHTML = "";
    for (const o of options) {
      const b = document.createElement("button");
      b.className = "plan-opt"; b.setAttribute("role", "radio"); b.setAttribute("aria-checked", String(p.choice === o.id));
      b.innerHTML = `<i class="radio"></i><span><b></b><em></em></span>`;
      b.querySelector("b").textContent = o.label;
      b.querySelector("em").textContent = o.desc || "";
      b.addEventListener("click", () => {
        PLAN[t.id] = { ...(PLAN[t.id] || {}), choice: o.id };
        savePlans();
        renderPlan();
        if (S.snap) renderUsage(S.snap);
        placePlan();
        checkLayout();
        if (o.id === "custom") { const f = $("plan-custom").querySelector("input"); if (f) f.focus(); }
      });
      list.appendChild(b);
    }
    const form = $("plan-custom");
    form.hidden = p.choice !== "custom";
    if (!form.hidden) {
      const c = (PLAN[t.id] && PLAN[t.id].custom) || {};
      form.innerHTML = [["5h", "5h"], ["7d", "7d"], ["1d", "Today"]].map(([k, label]) => {
        const v = c[k] || {}, unit = v.unit || "tokens";
        return `<label class="plan-row" data-k="${k}"><span>${label}</span>` +
          `<input inputmode="decimal" placeholder="no limit" value="${v.value ? (unit === "tokens" ? fmt(v.value) : v.value) : ""}">` +
          `<select>${Object.entries(UNIT).map(([u, name]) => `<option value="${u}"${u === unit ? " selected" : ""}>${name}</option>`).join("")}</select></label>`;
      }).join("");
      let timer = 0;
      const save = () => {
        const custom = {};
        form.querySelectorAll(".plan-row").forEach((row) => {
          const value = parseAmount(row.querySelector("input").value), unit = row.querySelector("select").value;
          if (value > 0) custom[row.dataset.k] = { unit, value };
        });
        PLAN[t.id] = { ...(PLAN[t.id] || {}), choice: "custom", custom };
        savePlans();
        if (S.snap) renderUsage(S.snap);
        checkLayout();
      };
      form.querySelectorAll("input, select").forEach((el) => el.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(save, 300); }));
    }
    $("plan-note").textContent = [info.note, info.as_of && presetsOf(t).length ? `Plan figures as of ${info.as_of}.` : ""].filter(Boolean).join(" ");
  }

  // the small + in the toolbar opens a floating panel, anchored under it, with the agent types that have no monitor yet
  let addKey = "";
  function placeAddPanel() {
    const pop = $("add-panel"), r = $("btn-add").getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, window.innerWidth - pop.offsetWidth - 8))}px`;
    pop.style.top = `${r.bottom + 6}px`;
  }
  function updateAddPanel(types, shown) {
    const addable = types.filter((t) => !shown.includes(t));
    if (!addable.length) S.addOpen = false;
    const btn = $("btn-add"), list = $("add-list");
    btn.disabled = !addable.length;
    btn.title = addable.length ? "Add monitor" : "Every agent type has a monitor";
    btn.setAttribute("aria-expanded", String(S.addOpen));
    $("add-panel").hidden = !S.addOpen;
    if (S.addOpen) placeAddPanel();
    const key = S.addOpen ? addable.map((t) => `${t.id}:${t.running}:${t.week}`).join("|") : "";
    if (key === addKey) return;
    addKey = key;
    list.innerHTML = "";
    if (!S.addOpen) return;
    for (const t of addable) {
      const b = document.createElement("button");
      b.className = "add-item"; b.dataset.add = t.id; b.setAttribute("role", "listitem");
      b.style.setProperty("--c", t.color);
      b.innerHTML = `<i class="tdot"></i><span></span><em></em>`;
      b.querySelector("span").textContent = t.name;
      b.querySelector("em").textContent = t.running ? `${t.running} running` : t.week ? `7d ${fmt(t.week)}` : "no usage yet";
      list.appendChild(b);
    }
  }

  function makeBar() {
    const row = document.createElement("div");
    row.className = "brow";
    row.innerHTML = `<div class="btop"><span class="k"></span><b class="tok mono"></b><span class="pct"></span></div>` +
                    `<div class="bar"><i></i></div>`;
    return row;
  }

  // What one window line says, and its longer hover text.
  function describeWin(w, local) {
    const name = WIN_NAME[w.key] || `${w.label || w.key} weekly`, tok = `${fmt(w.tokens)} tokens`;
    const ends = w.end ? `, resets in ${until(w.end)}` : "";
    if (w.basis === "nolimit") return { text: "no limit", cls: "muted", tip: `${name}: ${tok}; ${w.lim.plan} has no ${name} limit` };
    if (w.basis === "est") {
      const L = w.lim, unit = UNIT[L.unit] || L.unit;
      const used = L.unit === "tokens" ? fmt(w.count) : w.count.toLocaleString("en-US");
      const of = L.range ? `${L.range[0]}–${L.range[1]}` : L.unit === "tokens" ? fmt(L.value) : Number(L.value).toLocaleString("en-US");
      const src = L.src === "learned" ? "learned from your official readings" : L.src === "custom" ? "your own limit" : `${L.plan} plan`;
      return { text: L.range ? `${used} of ${of}` : `${pct(w.pct)} est.`, cls: w.lvl || "",
               tip: `${name} estimate (${src}): ${used} of ${L.range ? "" : "about "}${of} ${unit}${ends}` };
    }
    if (w.basis === "official") {
      return { text: `${pct(w.pct)} used`, cls: level(w.pct),
               tip: `${name} limit: ${pct(w.pct)} used (official), ${tok}${ends}` };
    }
    if (w.basis === "limit") {
      return { text: `${pct(w.pct)} of ${fmt(w.ref)}`, cls: level(w.pct),
               tip: `${name}: ${tok} of your ${fmt(w.ref)} limit (${pct(w.pct)})${ends}` };
    }
    if (w.basis === "peak") {
      const busiest = w.pct >= 99.5;
      return { text: busiest ? "busiest" : `${pct(w.pct)} of peak`, cls: "",
               tip: busiest ? `${name}: ${tok}, your busiest ${name} window in 4 weeks${ends}`
                            : `${name}: ${tok}, ${pct(w.pct)} of your busiest (${fmt(w.ref)})${ends}` };
    }
    if (!w.tokens) return { text: "not used", cls: "muted", tip: `${name}: no tokens in this window` };
    return { text: local ? "no limit" : "", cls: "", tip: `${name}: ${tok}` };
  }

  function updateCard(el, t) {
    el.style.setProperty("--c", t.color);
    el.querySelector("h3").textContent = t.name;
    el.querySelector(".run").textContent = t.running ? `${t.running} running` : t.local ? "local" : "not running";
    el.querySelector(".plan-name").textContent = planLabel(t);
    const wins = windowsOf(t);
    const box = el.querySelector(".bars");
    while (box.children.length < wins.length) box.appendChild(makeBar());
    while (box.children.length > wins.length) box.lastChild.remove();
    wins.forEach((w, i) => {
      const row = box.children[i], d = describeWin(w, t.quota_kind === "unlimited");
      row.querySelector(".k").textContent = w.label || w.key;
      row.querySelector(".tok").textContent = fmt(w.tokens);
      const p = row.querySelector(".pct");
      p.textContent = d.text; p.className = `pct ${d.cls}`;
      const bar = row.querySelector(".bar");
      bar.className = `bar ${w.basis === "peak" ? "peak" : w.basis === "est" ? `est ${d.cls}` : d.cls === "muted" ? "" : d.cls}`;
      bar.firstChild.style.width = `${clamp(w.pct)}%`;
      row.title = d.tip;
    });
    // when the windows reset: the official reset times, or when the current 5-hour block ends
    const w5 = wins.find((w) => w.key === "5h") || {}, day = wins.find((w) => w.key === "1d");
    const resets = wins.filter((w) => w.end && w.basis === "official").map((w) => `${w.label || w.key} ${until(w.end)}`);
    if (day && day.end) resets.push(`today ${until(day.end)}`);
    el.querySelector(".note").textContent = resets.length ? "resets " + resets.join(" · ")
      : w5.end ? `5h block ends in ${until(w5.end)}` : "";
    const meta = [`today <b class="mono">${fmt(t.today)}</b>`];
    if (t.today_cache) meta.push(`cache <b class="mono">${fmt(t.today_cache)}</b>`);
    if (t.requests) meta.push(`req <b class="mono">${t.requests.used}${t.requests.limit ? "/" + t.requests.limit : ""}</b>`);
    if (t.loaded && t.loaded.length) meta.push(`loaded <b>${t.loaded.length}</b>`);
    el.querySelector(".meta").innerHTML = meta.join(" · ");
    el.title = t.models && t.models.length ? `Models today: ${t.models.join(", ")}` : "";
  }

  function renderUsage(snap) {
    const box = $("cards");
    const types = snap.types || [];
    if (box.querySelector(".sk-card, .empty")) box.innerHTML = "";
    types.forEach(learn);
    const shown = shownTypes(types);
    const seen = new Set();
    shown.forEach((t, i) => {
      let el = cards.get(t.id);
      if (!el) { el = makeCard(t.id); cards.set(t.id, el); }
      updateCard(el, t);
      if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null);
      seen.add(t.id);
    });
    for (const [id, el] of cards) if (!seen.has(id)) { el.remove(); cards.delete(id); }
    if (!shown.length && !box.querySelector(".empty")) {
      box.innerHTML = emptyHtml("plus", "No monitors", "Use + at the top right to add one for any agent.");
      paintIcons(box);
    }
    updateAddPanel(types, shown);
  }

  // ------------------------------------------------------------------ agents page: a tile per type, its agents below
  const SURFACE_ICON = { app: "app-window", ide: "code", cli: "terminal-window" };
  const COUNTED = [["approval", "approve"], ["your_turn", "your turn"], ["working", "working"]];
  const tiles = new Map();     // tile id -> button
  const rows = new Map();      // agent key -> row button in the open type's list
  const pinRows = new Map();   // agent key -> row button in the Pinned list

  // Pins and highlight colours for the sessions you care about, by session id (sid), kept in this window's storage.
  const HL = { red: "#e5484d", orange: "#f2811d", yellow: "#d9a400", green: "#2f9e5b", blue: "#3b82f6", purple: "#8b5cf6" };
  const MARK = (() => {
    try {
      const m = JSON.parse(store.get("ab.marks", "{}"));
      return { pins: Array.isArray(m.pins) ? m.pins : [], hl: m.hl && typeof m.hl === "object" ? m.hl : {} };
    } catch (e) {
      return { pins: [], hl: {} };
    }
  })();
  function saveMarks() {
    MARK.pins = MARK.pins.slice(-100);  // sessions come and go; keep the list from growing forever
    const keys = Object.keys(MARK.hl);
    for (const k of keys.slice(0, Math.max(0, keys.length - 100))) delete MARK.hl[k];
    store.set("ab.marks", JSON.stringify(MARK));
  }
  const isPinned = (a) => MARK.pins.includes(a.sid);
  const pinnedOf = (list) => list.filter(isPinned).sort((x, y) => MARK.pins.indexOf(x.sid) - MARK.pins.indexOf(y.sid));

  function makeTile(id) {
    const b = document.createElement("button");
    b.className = "type";
    b.dataset.tile = id;
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="icon caret" data-icon="caret-right"></span><span class="tname"><i class="tdot"></i><span></span></span>` +
                  `<b class="total mono"></b><span class="counts"></span>`;
    b.addEventListener("click", () => {
      if (b._dragged) { b._dragged = false; return; }  // the end of a drag is not a click
      S.open = S.open === id ? null : id;
      if (S.snap) renderAgents(S.snap);
      fitSoon();
    });
    enableTileDrag(b);
    paintIcons(b);
    return b;
  }

  // ------------------------------------------------------------------ reorder tiles and agent rows: drag, or Alt+arrow keys
  // The dragged item takes the slot under the pointer as it moves, and the others slide out of the way.
  const loadList = (k) => { try { const v = JSON.parse(store.get(k, "[]")); return Array.isArray(v) ? v : []; } catch (e) { return []; } };
  S.tileOrder = loadList("ab.tileOrder");
  S.rowOrder = loadList("ab.rowOrder");  // session ids, in the order you put them
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

  function slide(box, change) {  // move tiles in the DOM, animating each from where it was (FLIP)
    const before = new Map([...box.children].map((el) => [el, el.getBoundingClientRect()]));
    change();
    if (reduceMotion.matches) return;
    for (const el of box.children) {
      const a = before.get(el), z = el.getBoundingClientRect();
      if (!a || el.classList.contains("dragging") || (a.left === z.left && a.top === z.top)) continue;
      el.animate([{ transform: `translate(${a.left - z.left}px, ${a.top - z.top}px)` }, { transform: "none" }],
                 { duration: 180, easing: "cubic-bezier(0.22, 1, 0.36, 1)" });
    }
  }
  function saveTileOrder() {
    const now = [...$("types").children].map((el) => el.dataset.tile).filter(Boolean);
    // keep the place of types that are not running right now
    S.tileOrder = [...now, ...S.tileOrder.filter((id) => !now.includes(id))];
    store.set("ab.tileOrder", JSON.stringify(S.tileOrder));
  }
  const enableTileDrag = (b) => makeSortable(b, { selector: ".type", done: saveTileOrder });

  // Make an item draggable among its siblings. canSwap(other) limits where it may go; done() saves the new order.
  function makeSortable(item, { selector, canSwap = () => true, done }) {
    item.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const box = item.parentNode, x0 = e.clientX, y0 = e.clientY;
      let dragging = false;
      const move = (ev) => {
        if (!dragging) {
          if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;  // still a click
          dragging = true;
          S.dragging = true;
          item.classList.add("dragging");
          try { item.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer */ }
        }
        // a list that scrolls (the five-row agent list) scrolls when you drag near its top or bottom edge
        if (box.scrollHeight > box.clientHeight) {
          const r = box.getBoundingClientRect();
          if (ev.clientY < r.top + 24) box.scrollTop -= 12;
          else if (ev.clientY > r.bottom - 24) box.scrollTop += 12;
        }
        // find the slot under the pointer from each item's place in the layout, not from what is drawn: items that
        // are still sliding into place are drawn where they were, and hit-testing them makes the order flip back
        const r = box.getBoundingClientRect();
        const px = ev.clientX - r.left - box.clientLeft + box.scrollLeft, py = ev.clientY - r.top - box.clientTop + box.scrollTop;
        const target = [...box.children].find((el) => el !== item && el.matches(selector) &&
          px >= el.offsetLeft && px < el.offsetLeft + el.offsetWidth && py >= el.offsetTop && py < el.offsetTop + el.offsetHeight);
        if (!target || !canSwap(target)) return;
        const kids = [...box.children];
        slide(box, () => box.insertBefore(item, kids.indexOf(target) > kids.indexOf(item) ? target.nextSibling : target));
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        if (!dragging) return;
        item.classList.remove("dragging");
        item._dragged = true;
        setTimeout(() => { item._dragged = false; }, 0);  // swallow only the click that ends this drag
        S.dragging = false;
        done(item);
        checkLayout();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });
    item.addEventListener("keydown", (e) => {
      if (!e.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
      e.preventDefault();
      const box = item.parentNode, back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      const sib = back ? item.previousElementSibling : item.nextElementSibling;
      if (!sib || !canSwap(sib)) return;
      slide(box, () => box.insertBefore(item, back ? sib : sib.nextSibling));
      done(item);
      item.focus();
    });
  }

  // Agent rows: pinned and unpinned rows each keep to their own part of the list.
  function saveRowOrder(item) {
    const grid = item.parentNode;
    const sids = (pinned) => [...grid.children].filter((el) => el.classList.contains("is-pinned") === pinned).map((el) => el.dataset.sid);
    if (item.classList.contains("is-pinned")) {
      const now = sids(true);
      MARK.pins = [...now, ...MARK.pins.filter((s) => !now.includes(s))];
      saveMarks();
    } else {
      const now = sids(false);
      S.rowOrder = [...now, ...S.rowOrder.filter((s) => !now.includes(s))].slice(0, 300);
      store.set("ab.rowOrder", JSON.stringify(S.rowOrder));
    }
    if (S.snap) renderAgents(S.snap);  // the other list (Pinned, or the open type) follows the new order
  }
  const ordered = (list) => {  // pinned first in your pin order, then your order, then the rest as they come
    const rest = list.filter((a) => !isPinned(a));
    const rank = (a, i) => (S.rowOrder.includes(a.sid) ? S.rowOrder.indexOf(a.sid) : 1000 + i);
    return [...pinnedOf(list), ...rest.map((a, i) => [rank(a, i), a]).sort((p, q) => p[0] - q[0]).map((p) => p[1])];
  };

  function makeRow() {
    const b = document.createElement("button");
    b.className = "agent";
    b.innerHTML = `
      <span class="mark"><span class="mono-letter"></span><span class="status-dot"></span></span>
      <span class="body"><span class="name"><span class="icon pin-ic" data-icon="push-pin"></span><span class="ntext"></span></span>
        <span class="surface"><span class="icon"></span><span class="surface-label"><b></b><span></span></span></span></span>
      <span class="side"><span class="tok mono"></span><span class="since"></span></span>
      <span class="go"><span class="icon" data-icon="arrow-up-right"></span></span>`;
    b.addEventListener("click", () => { if (b._dragged) { b._dragged = false; return; } jump(b); });
    b.addEventListener("contextmenu", (e) => { e.preventDefault(); openCtx(b, e); });
    makeSortable(b, { selector: ".agent", done: saveRowOrder,
                      canSwap: (other) => other.classList.contains("is-pinned") === b.classList.contains("is-pinned") });
    paintIcons(b);
    return b;
  }

  function monogram(name) {
    const w = (name || "?").replace(/[^\p{L}\p{N} ]/gu, "").trim();
    return (w.slice(0, 1).toUpperCase() + w.slice(1, 2).toLowerCase()) || "?";
  }

  function updateRow(b, a) {
    b.dataset.key = a.key;
    b.dataset.sid = a.sid;
    const hl = HL[MARK.hl[a.sid]];
    b.className = `agent s-${a.status}${b.classList.contains("busy") ? " busy" : ""}${isPinned(a) ? " is-pinned" : ""}${hl ? " hl" : ""}`;
    b.style.setProperty("--c", a.color);
    if (hl) b.style.setProperty("--hl", hl); else b.style.removeProperty("--hl");
    b.querySelector(".mono-letter").textContent = monogram(a.type_name);
    b.querySelector(".ntext").textContent = a.name;
    const ic = b.querySelector(".surface .icon"), want = SURFACE_ICON[a.surface_kind] || "terminal-window";
    if (ic.dataset.icon !== want) { ic.dataset.icon = want; ic.dataset.painted = ""; paintIcons(b); }
    // "Claude Code CLI · Windows Terminal": what the agent is, then where it lives
    const [what, ...where] = (a.surface || "").split(" · ");
    const lbl = b.querySelector(".surface-label");
    lbl.firstChild.textContent = what;
    lbl.lastChild.textContent = where.length ? ` · ${where.join(" · ")}` : "";
    b.querySelector(".tok").textContent = fmt(a.tokens);
    const since = b.querySelector(".since");
    since.textContent = a.status === "approval" ? "approve?" : a.status === "working" ? "working" : a.since || "";
    since.className = `since${a.status === "approval" ? " approval" : ""}`;
    const extra = [a.model, a.folder && `folder ${a.folder}`, `pid ${a.pid}`].filter(Boolean).join(" · ");
    b.title = `${a.name}\n${a.surface}\n${extra}`;  // information only, no how-to hints
    b.setAttribute("aria-label", `${a.name}, ${a.status.replace("_", " ")}, ${a.surface}. Open it`);
  }

  function renderAgents(snap) {
    const all = snap.agents || [];
    const box = $("types"), detail = $("detail");
    if (!all.length) {
      tiles.clear(); rows.clear(); pinRows.clear(); S.open = null;
      detail.hidden = true; detail.innerHTML = "";
      $("pinned").hidden = true;
      box.innerHTML = emptyHtml("terminal-window", "No agents running",
        "Start Claude Code, Codex, Gemini CLI, Ollama or any agent in your settings. It shows up here within a few seconds.");
      paintIcons(box);
      return;
    }
    if (box.querySelector(".empty")) box.innerHTML = "";
    // tiles: All, then each agent type in the order the Usage page uses
    const order = (snap.types || []).map((t) => t.id);
    const types = [...new Set(all.map((a) => a.type_id))].sort((x, y) => (order.indexOf(x) + 1 || 99) - (order.indexOf(y) + 1 || 99));
    const list = [{ id: "all", name: "All agents", color: null, agents: all }].concat(types.map((id) => {
      const ag = all.filter((a) => a.type_id === id);
      return { id, name: ag[0].type_name, color: ag[0].color, agents: ag };
    }));
    if (S.open && !list.some((f) => f.id === S.open)) S.open = null;
    // your order first (drag a tile to change it), then any type you have not placed yet
    const rank = (id, i) => (S.tileOrder.includes(id) ? S.tileOrder.indexOf(id) : 1000 + i);
    const ranked = list.map((f, i) => [rank(f.id, i), f]).sort((p, q) => p[0] - q[0]).map((p) => p[1]);
    list.length = 0;
    list.push(...ranked);

    const seen = new Set();
    list.forEach((f, i) => {
      let b = tiles.get(f.id);
      if (!b) { b = makeTile(f.id); tiles.set(f.id, b); }
      b.setAttribute("aria-pressed", String(S.open === f.id));
      b.querySelector(".tdot").hidden = !f.color;
      if (f.color) b.style.setProperty("--c", f.color);
      b.querySelector(".tname span").textContent = f.name;
      b.querySelector(".total").textContent = f.agents.length;
      const counts = COUNTED.map(([s, label]) => {
        const n = f.agents.filter((a) => a.status === s).length;
        return n ? `<span class="cnt s-${s}">${n} ${label}</span>` : "";
      }).join("");
      b.querySelector(".counts").innerHTML = counts || `<span class="cnt none">all idle</span>`;
      if (S.dragging) { if (!b.parentNode) box.appendChild(b); }  // leave the order alone mid-drag
      else if (box.children[i] !== b) box.insertBefore(b, box.children[i] || null);
      seen.add(f.id);
    });
    for (const [id, b] of tiles) if (!seen.has(id)) { b.remove(); tiles.delete(id); }

    // pinned sessions: always listed under the tiles, whichever type is open
    const pinned = pinnedOf(all);
    $("pinned").hidden = !pinned.length;
    syncRows($("pinned-grid"), pinned, pinRows);

    const open = list.find((f) => f.id === S.open);
    detail.hidden = !open;
    if (!open) { detail.innerHTML = ""; rows.clear(); return; }
    let grid = detail.querySelector(".detail-grid");
    if (!grid) { grid = document.createElement("div"); grid.className = "detail-grid"; detail.appendChild(grid); }
    syncRows(grid, ordered(open.agents), rows);
  }

  // Put one row per agent into a grid, reusing row elements (keyed by agent) so focus and hover survive refreshes.
  function syncRows(grid, agents, map) {
    const keep = new Set();
    const want = agents.map((a) => {
      let b = map.get(a.key);
      if (!b) { b = makeRow(); map.set(a.key, b); }
      updateRow(b, a);
      keep.add(a.key);
      return b;
    });
    const same = want.length === grid.children.length && want.every((b, j) => grid.children[j] === b);
    if (!same && !S.dragging) { grid.innerHTML = ""; want.forEach((b) => grid.appendChild(b)); }  // not mid-drag
    for (const [k, b] of map) if (!keep.has(k)) { b.remove(); map.delete(k); }
  }

  // ------------------------------------------------------------------ right-click menu on an agent: pin, highlight, open
  let ctxRow = null;
  function openCtx(row, e) {
    ctxRow = row;
    const sid = row.dataset.sid, pinned = MARK.pins.includes(sid), cur = MARK.hl[sid] || "";
    $("ctx-pin-label").textContent = pinned ? "Unpin" : "Pin to top";
    setIcon($("ctx-pin").querySelector(".icon"), pinned ? "push-pin-slash" : "push-pin");
    $("ctx-sw").querySelectorAll(".sw").forEach((s) => s.setAttribute("aria-pressed", String(s.dataset.hl === cur)));
    const m = $("ctx");
    m.hidden = false;
    const r = row.getBoundingClientRect();
    const x = e.clientX || r.left + 24, y = e.clientY || r.bottom;  // a keyboard-opened menu has no pointer position
    m.style.left = `${Math.max(6, Math.min(x, window.innerWidth - m.offsetWidth - 6))}px`;
    m.style.top = `${y + m.offsetHeight > window.innerHeight - 6 ? Math.max(6, y - m.offsetHeight) : y}px`;
    $("ctx-pin").focus();
  }
  function closeCtx() {
    $("ctx").hidden = true;
    ctxRow = null;
  }
  function applyMark(change) {
    if (!ctxRow) return;
    change(ctxRow.dataset.sid);
    saveMarks();
    closeCtx();
    if (S.snap) renderAgents(S.snap);
    checkLayout();
  }
  function buildCtx() {
    $("ctx-sw").innerHTML = `<button class="sw none" data-hl="" role="menuitemradio" title="No highlight" aria-label="No highlight"><span class="icon" data-icon="x"></span></button>` +
      Object.entries(HL).map(([k, c]) => `<button class="sw" data-hl="${k}" role="menuitemradio" title="${k}" aria-label="Highlight ${k}" style="--hl:${c}"></button>`).join("");
    paintIcons($("ctx"));
    $("ctx-pin").addEventListener("click", () => applyMark((sid) => {
      MARK.pins = MARK.pins.includes(sid) ? MARK.pins.filter((s) => s !== sid) : [...MARK.pins, sid];
    }));
    $("ctx-sw").addEventListener("click", (e) => {
      const s = e.target.closest(".sw");
      if (s) applyMark((sid) => { if (s.dataset.hl) MARK.hl[sid] = s.dataset.hl; else delete MARK.hl[sid]; });
    });
    $("ctx-open").addEventListener("click", () => { const r = ctxRow; closeCtx(); if (r) jump(r); });
    document.addEventListener("mousedown", (e) => {
      if (!$("ctx").hidden && !e.target.closest("#ctx")) closeCtx();
      if (S.addOpen && !e.target.closest("#add-panel, #btn-add")) openAdd(false);  // a click elsewhere closes the add panel
      if (S.planFor && !e.target.closest("#plan-panel, .plan-chip")) closePlan();  // and the plan panel
    });
    window.addEventListener("resize", () => { if (S.addOpen) placeAddPanel(); if (S.planFor) placePlan(); });
    $("plan-close").addEventListener("click", closePlan);
    window.addEventListener("blur", closeCtx);
    $("main").addEventListener("scroll", closeCtx, { passive: true });
  }

  async function jump(b) {
    if (!S.api || b.classList.contains("busy")) return;
    b.classList.add("busy");
    try {
      const r = await S.api.jump(b.dataset.key);
      toast(r.message, r.ok);
    } catch (e) {
      toast(`Could not switch: ${e}`, false);
    } finally {
      b.classList.remove("busy");
    }
  }

  // ------------------------------------------------------------------ window height follows the content
  let fitTimer = 0;
  function fitSoon() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(() => requestAnimationFrame(fit), 30);
  }
  function contentHeight() {
    const page = $(`page-${S.page}`), banner = $("banner");
    const bottom = parseFloat(getComputedStyle($("main")).paddingBottom) || 0;
    const h = $("titlebar").offsetHeight + $("toolbar").offsetHeight + (banner.hidden ? 0 : banner.offsetHeight + 12) +
              page.offsetHeight + bottom + 2;
    // a short window grows while a floating panel (add, plan) is open, so all of it fits; it shrinks back afterwards
    const popBottom = Math.max(0, ...[$("add-panel"), $("plan-panel")].filter((p) => !p.hidden)
      .map((p) => p.getBoundingClientRect().top + p.scrollHeight + 10));
    return Math.ceil(Math.max(h, popBottom));
  }
  // page width: the landscape panel, or a narrow one in single-column mode
  const WIDE = 704, NARROW = 384;
  const nextResize = () => new Promise((done) => {
    const t = setTimeout(done, 400);
    window.addEventListener("resize", () => { clearTimeout(t); requestAnimationFrame(() => requestAnimationFrame(done)); }, { once: true });
  });
  async function fit(again = true) {
    if (!S.api || S.maximized) return;
    const wantW = S.cols[S.page] === 1 ? NARROW : WIDE;
    if (Math.abs(window.innerWidth - wantW) > 2) {  // width first, so the height is measured at the final width
      try { await S.api.set_width(wantW); } catch (e) { /* keep the current width */ }
      await nextResize();
    }
    try { await S.api.fit(contentHeight(), window.innerHeight); } catch (e) { return; /* keep the current size */ }
    // check once more after the window has settled, in case the layout moved meanwhile
    if (again) setTimeout(() => { if (Math.abs(window.innerHeight - contentHeight()) > 3) fit(false); }, 250);
  }
  // Refit only when something that changes the height changed, not on every refresh.
  function checkLayout() {
    const sig = [S.page, S.open, S.cols[S.page], cards.size, S.addOpen, addKey, S.planFor, $("plan-custom").hidden,
                 $("cards").querySelectorAll(".brow").length, tiles.size,
                 $("detail").querySelectorAll(".agent").length, pinRows.size, $("banner").hidden].join("|");
    if (sig !== S.layout) { S.layout = sig; fitSoon(); }
  }

  // ------------------------------------------------------------------ shared bits
  function emptyHtml(icon, title, text) {
    const div = document.createElement("div");
    div.className = "empty";
    div.innerHTML = `<span class="icon" data-icon="${icon}"></span><b></b><p></p>`;
    div.querySelector("b").textContent = title;
    div.querySelector("p").textContent = text;
    return div.outerHTML;
  }

  function renderSkeleton() {
    $("cards").innerHTML = [0, 1].map(() =>
      `<div class="card sk-card"><div class="sk sk-line" style="width:45%"></div>` +
      `<div class="sk sk-bar"></div><div class="sk sk-bar"></div><div class="sk sk-line" style="width:70%"></div></div>`).join("");
  }

  let toastTimer = 0;
  function toast(text, ok = true) {
    const t = $("toast");
    $("toast-text").textContent = text;
    const ic = $("toast-icon");
    ic.dataset.icon = ok ? "arrow-up-right" : "warning-circle"; ic.dataset.painted = ""; paintIcons(t);
    t.classList.toggle("bad", !ok);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), ok ? 2600 : 4200);
  }

  function renderTotals(snap) {
    const t = snap.totals || {};
    $("t-today").textContent = fmt(t.today);
    $("t-agents").textContent = t.agents || 0;
    $("t-working").textContent = t.working || 0;
    $("t-waiting").textContent = t.waiting || 0;
    $("t-waiting-wrap").classList.toggle("attn", (t.waiting || 0) > 0);
    $("agent-count").textContent = t.agents || 0;
    const err = snap.error;
    $("banner").hidden = !err;
    $("banner-text").textContent = err ? `Last refresh failed: ${err}` : "";
  }

  function tickClock() {
    if (!S.snap || !S.snap.ts) return;
    const age = Math.max(0, Math.round(Date.now() / 1000 - S.snap.ts));
    $("live").title = age < 5 ? "Live" : `Updated ${age}s ago`;
    $("live").classList.toggle("stale", age > 15);
  }

  function render(snap) {
    S.snap = snap;
    renderTotals(snap);
    renderUsage(snap);
    renderAgents(snap);
    tickClock();
    checkLayout();
  }

  function showPage(page) {
    S.page = page; store.set("ab.page", page);
    for (const p of ["usage", "agents"]) {
      $(`tab-${p}`).setAttribute("aria-selected", String(p === page));
      $(`page-${p}`).hidden = p !== page;
    }
    $("btn-add").hidden = page !== "usage";  // monitors live on the Usage page
    if (page !== "usage" && S.addOpen) { S.addOpen = false; $("add-panel").hidden = true; }
    if (page !== "usage") closePlan();
    syncCols();
    checkLayout();
  }

  // ------------------------------------------------------------------ column switch (per page)
  function syncCols() {
    for (const p of ["usage", "agents"]) {
      $(`page-${p}`).style.setProperty("--cols", S.cols[p]);
      $(`page-${p}`).dataset.cols = S.cols[p];
    }
    document.querySelectorAll("#layout .lbtn").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.cols) === S.cols[S.page])));
    document.body.classList.toggle("compact", S.cols[S.page] === 1);  // single column: the narrow, compact panel
  }
  function setCols(n, page = S.page) {
    S.cols[page] = n; store.set(`ab.cols.${page}`, n);
    syncCols();
    checkLayout();
  }

  // ------------------------------------------------------------------ wiring
  function effectiveTheme() {
    return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function syncThemeIcon() { setIcon($("btn-theme"), effectiveTheme() === "dark" ? "sun" : "moon"); }
  function setTheme(t) {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
    store.set("ab.theme", t || "");
    syncThemeIcon();
  }
  function openFold(id) {
    S.open = id;
    if (S.snap) renderAgents(S.snap);
    checkLayout();
  }
  function syncMaxIcon() {
    const b = $("btn-max");
    b.setAttribute("aria-pressed", String(S.maximized));
    b.title = S.maximized ? "Restore" : "Maximise";
    b.setAttribute("aria-label", b.title);
    setIcon(b, S.maximized ? "corners-in" : "corners-out");
    document.body.classList.toggle("maximized", S.maximized);
  }
  async function toggleMax() {
    if (!S.api) return;
    S.maximized = await S.api.toggle_maximize();
    syncMaxIcon();
    if (!S.maximized) fitSoon();
  }
  function openAdd(open) {
    S.addOpen = !!open && S.page === "usage";
    if (S.snap) renderUsage(S.snap);
    checkLayout();
    if (S.addOpen) { const first = $("add-list").querySelector(".add-item"); if (first) first.focus(); }
    else if (document.activeElement && document.activeElement.closest("#add-panel")) $("btn-add").focus();
  }
  // handy from devtools and the screenshot script
  function markSession(sid, { pin, hl } = {}) {  // for the screenshot script: what the right-click menu does
    if (pin !== undefined) MARK.pins = pin ? [...MARK.pins.filter((s) => s !== sid), sid] : MARK.pins.filter((s) => s !== sid);
    if (hl !== undefined) { if (hl) MARK.hl[sid] = hl; else delete MARK.hl[sid]; }
    saveMarks();
    if (S.snap) renderAgents(S.snap);
    checkLayout();
  }
  function rightClick(n) {  // for the screenshot script: open the menu on the n-th visible agent
    const b = document.querySelectorAll("#page-agents .agent")[n];
    if (b) { const r = b.getBoundingClientRect(); openCtx(b, { clientX: r.left + 60, clientY: r.top + 20 }); }
  }
  function setPlan(id, choice, custom) {  // for the screenshot script: what choosing in the plan panel does
    PLAN[id] = { ...(PLAN[id] || {}), choice, ...(custom ? { custom } : {}) };
    savePlans();
    if (S.snap) renderUsage(S.snap);
    checkLayout();
  }
  const openPlanFor = (id) => { const c = document.querySelector(`#cards .card[data-id="${id}"] .plan-chip`); if (c) openPlan(id, c); };
  window.agentboard = { showPage, setTheme, openFold, toggleMax, setCols, openAdd, addMonitor, removeMonitor, markSession, rightClick,
                        setPlan, openPlanFor, closePlan };

  function wire() {
    paintIcons();
    renderSkeleton();
    showPage(S.page);
    syncThemeIcon();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncThemeIcon);

    document.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));
    $("t-waiting-wrap").addEventListener("click", () => { showPage("agents"); openFold("all"); });
    $("btn-theme").addEventListener("click", () => setTheme(effectiveTheme() === "dark" ? "light" : "dark"));
    $("btn-pin").addEventListener("click", async () => {
      if (!S.api) return;
      const on = await S.api.toggle_pin();
      $("btn-pin").setAttribute("aria-pressed", String(on));
      setIcon($("btn-pin"), on ? "push-pin" : "push-pin-slash");
      toast(on ? "Kept on top of other windows" : "No longer kept on top");
    });
    $("btn-settings").addEventListener("click", async () => { if (S.api) toast(await S.api.open_settings()); });
    $("btn-min").addEventListener("click", () => S.api && S.api.minimize());
    $("btn-max").addEventListener("click", toggleMax);
    buildCtx();
    $("btn-add").addEventListener("click", () => openAdd(!S.addOpen));
    $("add-close").addEventListener("click", () => openAdd(false));
    $("add-list").addEventListener("click", (e) => {
      const b = e.target.closest("[data-add]");
      if (b) addMonitor(b.dataset.add);
    });
    document.querySelectorAll("#layout .lbtn").forEach((b) => b.addEventListener("click", () => setCols(Number(b.dataset.cols))));
    $("btn-close").addEventListener("click", () => S.api && S.api.close());
    $("titlebar").addEventListener("dblclick", (e) => { if (!e.target.closest("button")) toggleMax(); });
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey && (e.key === "1" || e.key === "2")) showPage(e.key === "1" ? "usage" : "agents");
      if (e.key === "Escape" && !$("ctx").hidden) closeCtx();
      else if (e.key === "Escape" && S.addOpen) openAdd(false);
      else if (e.key === "Escape" && S.planFor) closePlan();
    });
    if (document.fonts) document.fonts.ready.then(() => { S.layout = ""; checkLayout(); });
    setInterval(tickClock, 1000);
  }

  async function poll() {
    try {
      const snap = await S.api.snapshot();
      if (snap && snap.ready && snap.ts !== S.lastTs) { S.lastTs = snap.ts; render(snap); }
    } catch (e) {
      $("banner").hidden = false;
      $("banner-text").textContent = `Lost contact with the AgentBoard process: ${e}`;
    }
  }

  async function connect() {
    if (S.api) return;
    S.api = window.pywebview.api;
    try {
      const st = await S.api.state();
      $("btn-pin").setAttribute("aria-pressed", String(st.pinned));
      setIcon($("btn-pin"), st.pinned ? "push-pin" : "push-pin-slash");
      S.maximized = !!st.maximized;
      syncMaxIcon();
    } catch (e) { /* keep defaults */ }
    S.layout = "";
    checkLayout();
    poll();
    setInterval(poll, 1500);
  }

  wire();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.snapshot) connect();
  else window.addEventListener("pywebviewready", connect);
})();
