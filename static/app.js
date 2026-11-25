// Mission Control page.
// Vendored: highlight.js 11.12.0 (highlight.min.js + styles/github-dark.min.css from @highlightjs/cdn-assets).
import { setRobotState, say } from "./robot.js";

const BUDGET_MS = 3000;
const ACTIVE_MS = 120000;
const MAX_NUDGE = 2000;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const $ = (id) => document.getElementById(id);

// Context lines show at once; only added lines type. Every edit finishes within budgetMs.
export function typingPlan(patchLines, budgetMs = BUDGET_MS) {
  const instant = [], typed = [];
  for (const l of patchLines) {
    if (l[0] === "+") typed.push(l.slice(1));
    else if (l[0] === " ") instant.push(l.slice(1));
  }
  const chars = typed.reduce((n, l) => n + l.length + 1, 0); // +1 for each newline
  return { instant, typed, msPerChar: chars ? Math.min(30, budgetMs / chars) : 30 };
}

window.MC = { typingPlan, setRobotState, say };

const S = {
  cfg: { subagent_nudges: false },
  sessions: [],
  tab: null, // {sid, agent}
  items: [], // edit_result and bash events for the tab, in order
  seen: new Set(),
  notes: new Map(), // tool_use_id -> {note, kind}
  bubbles: new Map(), // tool_use_id -> bubble
  firstTs: 0,
  lastTs: 0,
  cursor: -1,
  target: -1,
  live: true,
  liveSeen: -1,
  replaying: false,
  token: 0,
  playing: 0,
  mode: "typing",
  loading: false,
  buffer: [],
  activity: new Map(), // "sid|agent" -> ms of last live event
  browse: null, // {repo, path} while browsing a project, else null
};

// ---------- helpers ----------

async function getJSON(url, opts) {
  const r = await fetch(url, { credentials: "same-origin", ...opts });
  if (!r.ok) {
    const err = new Error(String(r.status));
    err.status = r.status;
    throw err;
  }
  return r.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function splitLines(s) {
  const out = s.replace(/\r\n/g, "\n").split("\n");
  if (out.length && out[out.length - 1] === "") out.pop();
  return out;
}

function langOf(path) {
  const ext = (path.split("/").pop() || "").split(".").pop().toLowerCase();
  return window.hljs && ext && window.hljs.getLanguage(ext) ? ext : null;
}

function highlight(node, text, lang) {
  if (!lang || !text) { node.textContent = text; return; }
  try {
    // hljs escapes the source text, so this never injects markup from the edit.
    node.innerHTML = window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
  } catch {
    node.textContent = text;
  }
}

function rel(path) {
  const s = S.sessions.find((x) => x.id === S.tab?.sid);
  const cwd = s?.cwd ? s.cwd.replace(/\/$/, "") + "/" : "";
  return cwd && path.startsWith(cwd) ? path.slice(cwd.length) : path;
}

function sessionName(s) {
  return (s.cwd || "").split("/").filter(Boolean).pop() || s.project || s.id.slice(0, 8);
}

function ms(ts) {
  const t = Date.parse(ts);
  return Number.isNaN(t) ? 0 : t;
}

function duration(msDiff) {
  const m = Math.max(0, Math.round(msDiff / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function message(text) {
  const code = $("code");
  code.replaceChildren(el("p", "msg", text));
}

// ---------- lines of an edit ----------

function buildLines(it) {
  const out = [];
  const hunks = it.patch || [];
  const push = (raw, n) => {
    const p = raw[0], s = raw.slice(1);
    if (p === "-") out.push({ t: "-", s });
    else if (p === "+") out.push({ t: "+", s, n: n.v++ });
    else if (p === " ") out.push({ t: " ", s, n: n.v++ });
    return p;
  };
  if (typeof it.original === "string") {
    const orig = splitLines(it.original);
    let o = 0;
    const n = { v: 1 };
    for (const h of hunks) {
      const start = h.oldLines === 0 ? h.oldStart : h.oldStart - 1;
      while (o < start && o < orig.length) out.push({ t: " ", s: orig[o++], n: n.v++ });
      for (const raw of h.lines) {
        const p = push(raw, n);
        if (p === "-" || p === " ") o++;
      }
    }
    while (o < orig.length) out.push({ t: " ", s: orig[o++], n: n.v++ });
  } else {
    hunks.forEach((h, i) => {
      if (i > 0 || h.oldStart > 1) out.push({ t: "gap" });
      const n = { v: h.newStart || 1 };
      for (const raw of h.lines) push(raw, n);
    });
  }
  return out;
}

// ---------- rendering an edit ----------

function lineRow(l, diff) {
  const row = el("div", "ln");
  if (l.t === "gap") {
    row.classList.add("gap");
    row.append(el("span", "no", ""), el("span", "src", "⋯"));
    return { row, src: null };
  }
  if (diff && l.t === "+") row.classList.add("t-add");
  if (diff && l.t === "-") row.classList.add("t-del");
  const src = el("span", "src");
  row.append(el("span", "no", l.n != null ? String(l.n) : ""));
  if (diff) row.append(el("span", "sg", l.t === " " ? " " : l.t));
  row.append(src);
  return { row, src };
}

function follow(row) {
  const c = $("code");
  const top = row.offsetTop;
  if (top < c.scrollTop || top + row.offsetHeight > c.scrollTop + c.clientHeight) {
    c.scrollTop = Math.max(0, top - c.clientHeight / 2);
  }
}

function highlightLater(rows, lang, tok) {
  if (!lang) return;
  let i = 0;
  const step = () => {
    if (S.token !== tok) return;
    for (const end = Math.min(rows.length, i + 300); i < end; i++) highlight(rows[i].src, rows[i].s, lang);
    if (i < rows.length) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function typeRows(rows, msPerChar, lang, tok) {
  return new Promise((done) => {
    const caret = el("span", "caret");
    let i = 0, pos = 0, last = performance.now();
    const frame = (now) => {
      if (S.token !== tok) return done();
      let chunk = Math.ceil((now - last) / msPerChar);
      last = now;
      while (chunk > 0 && i < rows.length) {
        const r = rows[i];
        const take = Math.min(chunk, r.s.length + 1 - pos);
        pos += take;
        chunk -= take;
        if (pos > r.s.length) {
          highlight(r.src, r.s, lang); // once per finished line
          i++;
          pos = 0;
        } else {
          r.src.textContent = r.s.slice(0, pos);
          r.src.append(caret);
        }
      }
      if (i < rows.length) {
        if (!rows[i].src.contains(caret)) rows[i].src.append(caret);
        follow(rows[i].row);
        requestAnimationFrame(frame);
      } else {
        done();
      }
    };
    requestAnimationFrame(frame);
  });
}

function renderEdit(it, animate, tok) {
  const diff = S.mode === "diff";
  const lang = langOf(it.file);
  const lines = buildLines(it).filter((l) => diff || l.t !== "-");
  const frag = document.createDocumentFragment();
  const instant = [], typed = [];
  let firstChange = null;
  const typing = animate && !diff && !reduceMotion.matches;
  for (const l of lines) {
    const { row, src } = lineRow(l, diff);
    frag.append(row);
    if (!src) continue;
    if (l.t !== " " && !firstChange) firstChange = row;
    if (typing && l.t === "+") typed.push({ row, src, s: l.s });
    else {
      src.textContent = l.s;
      instant.push({ src, s: l.s });
    }
  }
  const code = $("code");
  const maxNo = lines.reduce((m, l) => Math.max(m, l.n || 0), 0);
  code.style.setProperty("--nw", `${String(maxNo).length + 1.5}ch`);
  code.replaceChildren(frag);
  if (!lines.length) code.append(el("p", "msg", "This edit has no line changes."));
  highlightLater(instant, lang, tok);
  if (!typed.length) {
    if (firstChange) follow(firstChange);
    return Promise.resolve();
  }
  const plan = typingPlan(lines.filter((l) => l.t !== "gap").map((l) => l.t + l.s));
  return typeRows(typed, plan.msPerChar, lang, tok);
}

function renderBash(it) {
  const row = el("div", "ln ran");
  row.append(el("span", "no", ""), el("span", "src", it.command));
  $("code").replaceChildren(row);
}

async function show(i, animate, tok) {
  const it = S.items[i];
  if (!it) return;
  $("note").textContent = it.note ? `✎ "${it.note}"` : "";
  $("file").textContent = it.type === "edit" ? `${it.kind} · ${rel(it.file)}` : "Bash";
  renderTree();
  renderTimeline();
  const b = S.bubbles.get(it.tid);
  if (b) say(b.text);
  if (it.type === "bash") return renderBash(it);
  await renderEdit(it, animate, tok);
}

// ---------- playback ----------

async function pump() {
  const tok = S.token;
  if (S.playing === tok) return;
  S.playing = tok;
  renderControls();
  while (S.token === tok) {
    if (S.cursor < S.target) {
      S.cursor++;
      await show(S.cursor, true, tok);
      continue;
    }
    if (S.replaying) { // replay caught up: back to live, play whatever queued meanwhile
      S.replaying = false;
      S.live = true;
      S.target = S.items.length - 1;
      S.liveSeen = S.target;
      continue;
    }
    break;
  }
  if (S.playing === tok) S.playing = 0;
  renderControls();
}

function cancel() {
  S.token++;
  S.playing = 0;
}

function jump(i) {
  if (!S.items.length) return;
  i = Math.max(0, Math.min(S.items.length - 1, i));
  cancel();
  S.live = false;
  S.replaying = false;
  S.cursor = S.target = i;
  show(i, false, S.token);
  renderControls();
}

function replay() {
  if (!S.items.length) return;
  if (S.playing && S.replaying) { // pause
    cancel();
    S.live = false;
    S.replaying = false;
    S.target = S.cursor;
    renderControls();
    return;
  }
  cancel();
  S.live = false;
  S.replaying = true;
  S.cursor = Math.max(-1, S.cursor - 1); // replay the current edit too
  S.target = S.items.length - 1;
  pump();
}

function goLive() {
  cancel();
  S.replaying = false;
  S.live = true;
  S.target = S.items.length - 1;
  if (S.liveSeen >= S.target) {
    S.cursor = S.target;
    show(S.cursor, false, S.token);
  } else {
    S.cursor = S.liveSeen;
  }
  S.liveSeen = S.target;
  pump();
}

// ---------- ingest ----------

function ingest(ev, live) {
  const tid = ev.tool_use_id || "";
  if (ev.type === "bubble") {
    if (tid) S.bubbles.set(tid, ev);
    if (live) {
      const idx = S.items.findIndex((x) => x.tid === tid);
      if (idx >= 0 && idx <= S.cursor && (S.live || idx === S.cursor)) say(ev.text);
    }
    return false;
  }
  if (ev.agent_id !== S.tab.agent) return false;
  const t = ms(ev.ts);
  if (t) {
    S.firstTs = S.firstTs ? Math.min(S.firstTs, t) : t;
    S.lastTs = Math.max(S.lastTs, t);
  }
  if (ev.type === "edit") {
    S.notes.set(tid, { note: ev.note, kind: ev.kind });
    return false;
  }
  if (ev.type === "error") {
    if (live) setRobotState("squint");
    return false;
  }
  if (ev.type !== "edit_result" && ev.type !== "bash") return false;
  const key = ev.type + ":" + tid;
  if (tid && S.seen.has(key)) return false;
  S.seen.add(key);
  if (ev.type === "bash") {
    S.items.push({ type: "bash", tid, ts: t, note: ev.note, command: ev.command });
  } else {
    const n = S.notes.get(tid) || {};
    S.items.push({
      type: "edit", tid, ts: t, note: n.note || "", kind: n.kind || "Edit",
      file: ev.file_path, patch: ev.patch, original: ev.original_file,
      added: ev.added || 0, removed: ev.removed || 0,
    });
  }
  return true;
}

function onLive(ev) {
  if (!ingest(ev, true)) return;
  renderStat();
  renderTimeline();
  if (S.live) {
    S.target = S.items.length - 1;
    S.liveSeen = S.target;
    pump();
  } else {
    renderControls();
  }
}

// ---------- chrome ----------

function tabActive(s, agent) {
  const seen = S.activity.get(s.id + "|" + agent) || 0;
  if (Date.now() - seen < ACTIVE_MS) return true;
  return agent === "main" && s.active;
}

function renderTabs() {
  const nav = $("tabs");
  const focused = nav.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  nav.replaceChildren();
  for (const s of S.sessions) {
    for (const agent of s.agents) {
      const b = el("button", "tab");
      b.type = "button";
      const on = !S.browse && S.tab && S.tab.sid === s.id && S.tab.agent === agent;
      b.setAttribute("aria-pressed", String(on));
      if (s.title) b.title = s.title;
      const active = tabActive(s, agent);
      const dot = el("span", active ? "dot" : "dot idle");
      dot.setAttribute("aria-hidden", "true");
      const label = agent === "main" ? `${sessionName(s)} · main` : `${sessionName(s)} · subagent ${agent.slice(0, 8)}`;
      b.append(dot, el("span", null, label), el("span", "sr", active ? " (working)" : " (idle)"));
      b.dataset.key = s.id + "|" + agent;
      b.addEventListener("click", () => selectTab(s.id, agent));
      nav.append(b);
      if (b.dataset.key === focused) b.focus();
    }
  }
}

function renderStat() {
  const edits = S.items.filter((x) => x.type === "edit");
  const add = edits.reduce((n, x) => n + x.added, 0);
  const del = edits.reduce((n, x) => n + x.removed, 0);
  const s = S.sessions.find((x) => x.id === S.tab?.sid);
  const end = s?.active ? Date.now() : S.lastTs;
  const stat = $("stat");
  stat.replaceChildren(
    el("span", "add", `+${add}`), " ", el("span", "del", `−${del}`),
    ` · ${edits.length} ${edits.length === 1 ? "edit" : "edits"} · ${S.firstTs ? duration(end - S.firstTs) : "0 min"}`,
  );
}

function renderTree() {
  const ul = $("tree");
  ul.replaceChildren();
  const cur = S.items[S.cursor];
  const hot = cur?.type === "edit" ? rel(cur.file) : null;
  const dirs = new Map();
  for (const it of S.items) {
    if (it.type !== "edit") continue;
    const p = rel(it.file);
    const cut = p.lastIndexOf("/");
    const dir = cut >= 0 ? p.slice(0, cut + 1) : "";
    if (!dirs.has(dir)) dirs.set(dir, new Set());
    dirs.get(dir).add(p);
  }
  for (const dir of [...dirs.keys()].sort()) {
    if (dir) ul.append(el("li", "d", dir));
    for (const p of [...dirs.get(dir)].sort()) {
      const li = el("li", dir ? "f" : "", p.slice(dir.length) + (p === hot ? " ●" : ""));
      if (p === hot) {
        li.classList.add("hot");
        li.setAttribute("aria-current", "true");
      }
      ul.append(li);
    }
  }
  ul.querySelector(".hot")?.scrollIntoView?.({ block: "nearest" });
}

function tickPos(i) {
  const n = S.items.length;
  const t0 = S.items[0]?.ts, t1 = S.items[n - 1]?.ts;
  if (n < 2) return 50;
  if (t0 && t1 && t1 > t0 && S.items[i].ts) return 2 + 96 * (S.items[i].ts - t0) / (t1 - t0);
  return 2 + 96 * i / (n - 1);
}

function renderTimeline() {
  const ticks = $("ticks");
  if (ticks.childElementCount !== S.items.length) {
    ticks.replaceChildren();
    S.items.forEach((it, i) => {
      const t = el("div", "tick" + (it.type === "bash" ? " b" : it.kind === "Write" ? " w" : ""));
      t.style.left = tickPos(i) + "%";
      ticks.append(t);
    });
  }
  const n = S.items.length;
  const bar = $("bar");
  bar.setAttribute("aria-valuemax", String(Math.max(1, n)));
  bar.setAttribute("aria-valuenow", String(Math.max(1, S.cursor + 1)));
  const cur = S.items[S.cursor];
  bar.setAttribute("aria-valuetext", cur ? `edit ${S.cursor + 1} of ${n}, ${cur.type === "bash" ? "ran a command" : rel(cur.file)}` : "no edits");
  const ph = $("playhead");
  ph.hidden = S.cursor < 0;
  if (S.cursor >= 0) ph.style.left = tickPos(S.cursor) + "%";
  $("pos").textContent = `edit ${S.cursor + 1} / ${n}`;
}

function renderControls() {
  const play = $("play");
  play.textContent = S.playing && S.replaying ? "⏸" : "▶";
  play.setAttribute("aria-label", S.playing && S.replaying ? "Pause replay" : "Replay from here");
  const live = $("live");
  const fresh = S.items.length - 1 - S.liveSeen;
  live.hidden = S.live;
  live.textContent = fresh > 0 ? `${fresh} new · jump to live` : "back to live";
}

function renderNudge() {
  clearTimeout(pollTimer);
  nudgeGen++; // late POST or poll results from the previous tab are dropped
  nudgeBusy = false;
  $("nudge").value = "";
  updateLeft();
  const s = S.sessions.find((x) => x.id === S.tab?.sid);
  const blocked = !s || (S.tab.agent !== "main" && !S.cfg.subagent_nudges);
  $("nudge").disabled = $("nudge-send").disabled = blocked;
  const chip = $("nudge-chip");
  chip.dataset.s = "";
  chip.textContent = s && blocked ? "Nudges are off for subagents." : "";
}

// ---------- sessions ----------

async function refreshSessions() {
  S.sessions = await getJSON("/api/sessions");
  renderTabs();
  renderStat();
  if (!S.tab && !S.browse && S.sessions.length) selectTab(initialSession().id, "main");
}

// Auto-open links point at the session that started editing: /#session=<id>. Falls back to the newest.
function initialSession() {
  const m = location.hash.match(/^#session=([A-Za-z0-9_-]{1,128})$/);
  if (m) history.replaceState(null, "", location.pathname);
  return (m && S.sessions.find((s) => s.id === m[1])) || S.sessions[0];
}

let refreshTimer = 0;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refreshSessions().catch(() => {}), 500);
}

async function selectTab(sid, agent) {
  cancel();
  if (S.browse) leaveBrowse();
  Object.assign(S, {
    tab: { sid, agent }, items: [], seen: new Set(), notes: new Map(), bubbles: new Map(),
    firstTs: 0, lastTs: 0, cursor: -1, target: -1, live: true, liveSeen: -1, replaying: false,
    loading: true, buffer: [],
  });
  renderTabs();
  renderNudge();
  renderTimeline();
  renderTree();
  $("note").textContent = "";
  $("file").textContent = "";
  message("Loading this session…");
  let hist;
  try {
    hist = await getJSON(`/api/sessions/${encodeURIComponent(sid)}/history`);
  } catch {
    if (!S.browse && S.tab.sid === sid && S.tab.agent === agent) {
      S.loading = false;
      message("Couldn't load this session's history. Pick the tab again to retry.");
    }
    return;
  }
  if (S.browse || S.tab.sid !== sid || S.tab.agent !== agent) return; // switched away (or to a project) meanwhile
  for (const b of hist.bubbles || []) ingest(b, false);
  for (const ev of hist.events || []) ingest(ev, false);
  S.loading = false;
  const n = S.items.length;
  S.cursor = S.target = S.liveSeen = n - 1;
  renderStat();
  renderControls();
  if (n) show(n - 1, false, S.token);
  else {
    renderTimeline();
    message("No edits in this session yet. They type in here as they happen.");
  }
  for (const ev of S.buffer.splice(0)) onLive(ev);
}

function openStream() {
  const es = new EventSource("/api/stream", { withCredentials: true });
  let dropped = false;
  es.onerror = () => { dropped = true; };
  es.onopen = () => {
    if (dropped && S.tab && !S.browse) { // fill any gap left while disconnected
      dropped = false;
      selectTab(S.tab.sid, S.tab.agent);
    }
  };
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    if (!ev || !ev.session_id) return;
    const s = S.sessions.find((x) => x.id === ev.session_id);
    if (ev.type !== "bubble") {
      S.activity.set(ev.session_id + "|" + ev.agent_id, Date.now());
      if (!s || !s.agents.includes(ev.agent_id)) refreshSoon();
      else renderTabs();
    }
    if (S.browse || !S.tab || ev.session_id !== S.tab.sid) return;
    if (S.loading) S.buffer.push(ev);
    else onLive(ev);
  };
}

// ---------- nudge ----------

let pollTimer = 0;
let nudgeGen = 0;
let nudgeBusy = false;
const POLL_FAILS_MAX = 5;

function setChip(state, text) {
  const chip = $("nudge-chip");
  chip.dataset.s = state;
  chip.textContent = text;
}

async function sendNudge(e) {
  e.preventDefault();
  const input = $("nudge");
  const text = input.value.trim();
  if (!text || !S.tab || input.disabled || nudgeBusy) return;
  clearTimeout(pollTimer);
  const gen = ++nudgeGen;
  const { sid, agent } = S.tab;
  nudgeBusy = true;
  $("nudge-send").disabled = true;
  try {
    const r = await getJSON("/api/nudge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, agent_id: agent, text }),
    });
    if (gen !== nudgeGen) return;
    if (r.status === "idle") {
      setChip("idle", "idle, type in the chat instead");
      return;
    }
    input.value = "";
    updateLeft();
    setChip("queued", "queued");
    let fails = 0;
    const poll = async () => {
      try {
        const st = await getJSON(`/api/nudge/${encodeURIComponent(r.id)}?session_id=${encodeURIComponent(sid)}`);
        if (gen !== nudgeGen) return;
        fails = 0;
        setChip(st.status, st.status);
        if (st.status === "queued") pollTimer = setTimeout(poll, 2000);
      } catch {
        if (gen !== nudgeGen) return;
        if (++fails >= POLL_FAILS_MAX) setChip("error", "Lost track of this nudge. Is the server still running?");
        else pollTimer = setTimeout(poll, 2000);
      }
    };
    pollTimer = setTimeout(poll, 2000);
  } catch (err) {
    if (gen === nudgeGen) {
      setChip("error", err.status === 422 ? "Couldn't send: keep it under 2,000 characters." : "Couldn't send. Is the server still running?");
    }
  } finally {
    if (gen === nudgeGen) {
      nudgeBusy = false;
      $("nudge-send").disabled = $("nudge").disabled;
    }
  }
}

function updateLeft() {
  $("nudge-left").textContent = `${MAX_NUDGE - $("nudge").value.length} left`;
}

// ---------- projects (read-only browsing) ----------

const projBtn = $("proj-btn");
const projList = $("proj-list");

function closeProjects(focusBtn) {
  projList.hidden = true;
  projBtn.setAttribute("aria-expanded", "false");
  if (focusBtn) projBtn.focus();
}

async function openProjects() {
  projList.hidden = false;
  projBtn.setAttribute("aria-expanded", "true");
  projList.replaceChildren(el("li", "msg", "Loading projects…"));
  let repos;
  try {
    repos = await getJSON("/api/repos");
  } catch {
    projList.replaceChildren(el("li", "msg", "Couldn't load projects. Try again."));
    return;
  }
  if (!repos.length) {
    projList.replaceChildren(el("li", "msg", "No git repos found in the code folder (set MC_CODE_ROOT)."));
    return;
  }
  projList.replaceChildren(...repos.map((r) => {
    const li = el("li");
    const b = el("button", "proj-item", r.id);
    b.type = "button";
    if (S.browse?.repo.id === r.id) b.setAttribute("aria-current", "true");
    b.addEventListener("click", () => { closeProjects(false); selectRepo(r); });
    li.append(b);
    return li;
  }));
  projList.querySelector("button")?.focus();
}

function touchedIn(repoPath) {
  const s = S.sessions.find((x) => x.id === S.tab?.sid);
  if (!s || (s.cwd || "").replace(/\/$/, "") !== repoPath) return new Set();
  return new Set(S.items.filter((it) => it.type === "edit").map((it) => rel(it.file)));
}

function leaveBrowse() {
  S.browse = null;
  document.body.classList.remove("browsing");
  projBtn.setAttribute("aria-pressed", "false");
  $("tree-box").setAttribute("aria-label", "Files touched");
}

async function selectRepo(r) {
  cancel();
  const touched = touchedIn(r.path);
  S.browse = { repo: r, path: null, touched };
  document.body.classList.add("browsing");
  projBtn.setAttribute("aria-pressed", "true");
  renderTabs();
  $("tree-box").setAttribute("aria-label", `Files in ${r.id}`);
  $("note").textContent = `Browsing ${r.id} · read-only`;
  $("file").textContent = "";
  $("tree").replaceChildren();
  message("Loading files…");
  let files;
  try {
    ({ files } = await getJSON(`/api/repos/${encodeURIComponent(r.id)}/files`));
  } catch {
    if (S.browse?.repo.id === r.id) message("Couldn't load this project's files. Pick it again to retry.");
    return;
  }
  if (S.browse?.repo.id !== r.id) return;
  renderRepoTree(files, touched);
  message(files.length ? "Pick a file on the left." : "This repo has no tracked files yet.");
}

function renderRepoTree(files, touched) {
  const root = { dirs: new Map(), files: [] };
  for (const f of files) {
    const parts = f.split("/");
    let node = root;
    for (const d of parts.slice(0, -1)) {
      if (!node.dirs.has(d)) node.dirs.set(d, { dirs: new Map(), files: [] });
      node = node.dirs.get(d);
    }
    node.files.push(f);
  }
  const build = (node, depth) => {
    const ul = el("ul");
    for (const [name, child] of [...node.dirs].sort(([a], [b]) => a.localeCompare(b))) {
      const li = el("li", "rd");
      const det = el("details");
      det.open = depth === 0 && node.dirs.size <= 8;
      det.append(el("summary", null, name + "/"), build(child, depth + 1));
      li.append(det);
      ul.append(li);
    }
    for (const f of node.files.sort()) {
      const li = el("li", "rf");
      const b = el("button", "rf-btn", f.split("/").pop() + (touched.has(f) ? " ●" : ""));
      b.type = "button";
      b.dataset.path = f;
      if (touched.has(f)) b.classList.add("touched");
      b.addEventListener("click", () => openFile(f));
      li.append(b);
      ul.append(li);
    }
    return ul;
  };
  $("tree").replaceChildren(...build(root, 0).children);
}

async function openFile(path) {
  const r = S.browse?.repo;
  if (!r) return;
  cancel();
  S.browse.path = path;
  for (const b of $("tree").querySelectorAll(".rf-btn")) {
    if (b.dataset.path === path) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
  $("file").textContent = path;
  message("Loading file…");
  let out;
  try {
    out = await getJSON(`/api/repos/${encodeURIComponent(r.id)}/file?path=${encodeURIComponent(path)}`);
  } catch {
    if (S.browse?.path === path) message("Couldn't open this file. It may have been deleted or moved.");
    return;
  }
  if (S.browse?.path !== path) return;
  if (out.note) return message(`Not shown: ${out.note}.`);
  const lines = splitLines(out.text);
  if (!lines.length) return message("This file is empty.");
  const code = $("code");
  code.style.setProperty("--nw", `${String(lines.length).length + 1.5}ch`);
  const frag = document.createDocumentFragment();
  const rows = lines.map((s, i) => {
    const { row, src } = lineRow({ t: " ", n: i + 1, s }, false);
    src.textContent = s;
    frag.append(row);
    return { src, s };
  });
  code.replaceChildren(frag);
  code.scrollTop = 0;
  highlightLater(rows, langOf(path), S.token);
}

projBtn.addEventListener("click", () => (projList.hidden ? openProjects() : closeProjects(false)));
projList.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); closeProjects(true); }
});
document.addEventListener("click", (e) => {
  if (!projList.hidden && !e.target.closest(".proj")) closeProjects(false);
});

// ---------- wire up ----------

function setMode(mode) {
  if (S.mode === mode) return;
  S.mode = mode;
  $("mode-typing").setAttribute("aria-pressed", String(mode === "typing"));
  $("mode-diff").setAttribute("aria-pressed", String(mode === "diff"));
  if (S.cursor < 0) return;
  const wasPlaying = S.playing;
  cancel();
  show(S.cursor, false, S.token);
  if (wasPlaying || S.cursor < S.target) pump();
}

$("mode-typing").addEventListener("click", () => setMode("typing"));
$("mode-diff").addEventListener("click", () => setMode("diff"));
$("first").addEventListener("click", () => jump(0));
$("play").addEventListener("click", replay);
$("live").addEventListener("click", goLive);
$("talk").addEventListener("submit", sendNudge);
$("nudge").addEventListener("input", updateLeft);

const bar = $("bar");
bar.addEventListener("click", (e) => {
  if (!S.items.length) return;
  const r = bar.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width * 100;
  let best = 0;
  S.items.forEach((_, i) => { if (Math.abs(tickPos(i) - x) < Math.abs(tickPos(best) - x)) best = i; });
  jump(best);
});
bar.addEventListener("keydown", (e) => {
  const k = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[e.key];
  if (k) jump(S.cursor + k);
  else if (e.key === "Home") jump(0);
  else if (e.key === "End") jump(S.items.length - 1);
  else return;
  e.preventDefault();
});

async function init() {
  openStream();
  try {
    S.cfg = await getJSON("/api/config");
    await refreshSessions();
  } catch (err) {
    message(err.status === 403
      ? "This page needs its key. Open the link mission-control printed in the terminal."
      : "Couldn't reach the server. Check that mission-control is running, then reload.");
    return;
  }
  if (!S.sessions.length) message("No Claude Code sessions from the last 2 days. Start one and it shows up here.");
  setInterval(() => refreshSessions().catch(() => {}), 10000);
  renderNudge();
}

init();
