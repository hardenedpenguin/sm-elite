const state = {
  boot: null,
  nodes: new Map(),
  stats: new Map(),
  voters: new Map(),
  heard: [],
  heardLive: new Map(),
  heardEnabled: false,
  favorites: [],
  favFile: "favorites.ini",
  favError: "",
  selected: [],
  target: "",
  local: "",
  permanent: false,
  dropFirst: true,
  sortKey: "file",
  sortDir: 1,
  socket: null,
};

const TX_LABEL = {
  local: "Transmit · local source",
  network: "Transmit · network source",
  telemetry: "Transmit · telemetry",
  playback: "Transmit · playback from a remote node",
  idle: "Idle",
};

function nodeAttr(id) {
  return String(id).replace(/[^0-9A-Za-z-]/g, "");
}

const $ = (id) => document.getElementById(id);

// Empty when served at "/", "/sm-elite" behind a reverse proxy at /sm-elite/.
const BASE = new URL(".", location.href).pathname.replace(/\/$/, "");

async function api(path, { method = "GET", body } = {}) {
  const upload = body instanceof FormData;
  const response = await fetch(BASE + path, {
    method,
    credentials: "same-origin",
    headers: body && !upload ? { "Content-Type": "application/json" } : undefined,
    body: upload ? body : body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `request failed (${response.status})`);
  }
  return data;
}

function operator() {
  const role = state.boot?.user?.role;
  return role === "operator" || role === "admin";
}

function canCommand(node) {
  if (!operator()) return false;
  const limit = state.boot?.user?.nodes;
  return !limit || limit.includes(node);
}

function clock(seconds) {
  if (seconds === null || seconds === undefined || Number(seconds) < 0) return "Never";
  const total = Math.floor(Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return [hours, minutes, secs].map((part) => String(part).padStart(2, "0")).join(":");
}

function uptimeLabel(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return "";
  const days = Math.floor(seconds / 86400);
  const rest = clock(seconds % 86400);
  return days ? `${days}d ${rest}` : rest;
}

function lengthLabel(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

function whenLabel(epoch) {
  const date = new Date(epoch * 1000);
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (date.toDateString() === new Date().toDateString()) return time;
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function agoLabel(epoch) {
  const seconds = Math.max(0, Date.now() / 1000 - epoch);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

function heatColor(percent) {
  const light = 16 + Math.min(percent, 100) * 0.2;
  return `hsl(6 78% ${light}%)`;
}

function linkedIds() {
  const ids = new Set();
  for (const id of state.selected) {
    const snap = state.nodes.get(id);
    for (const conn of snap?.conns || []) ids.add(String(conn.node));
  }
  return ids;
}

function showResult(title, body) {
  $("result-title").textContent = title;
  $("result-body").textContent = body || "";
  $("result-dialog").showModal();
}

async function sendCommand(action, extra = {}) {
  const node = state.local || state.selected[0];
  if (!node) return;
  if (!canCommand(node)) {
    if (operator()) showResult("Not allowed", `Your account cannot control node ${node}.`);
    else showResult("Login required", "Log in as an operator to control this node.");
    return;
  }
  if (["connect", "monitor", "local_monitor"].includes(action) && state.dropFirst) {
    const current = (state.nodes.get(node)?.conns || []).map((conn) => String(conn.node)).filter((id) => id !== state.target);
    if (current.length && !confirm(`Disconnect ${current.join(", ")} from ${node} before linking ${state.target}?`)) return;
  }
  try {
    const result = await api("/api/command", {
      method: "POST",
      body: {
        node,
        action,
        target: state.target,
        permanent: state.permanent,
        disconnect_first: state.dropFirst,
        ...extra,
      },
    });
    showResult(result.command || action, result.detail || "OK");
  } catch (error) {
    showResult("Command rejected", error.message);
  }
}

function chooseTarget(node, { connect = false } = {}) {
  state.target = String(node);
  const input = document.querySelector("#target");
  if (input) input.value = state.target;
  if (connect && operator()) sendCommand("connect");
}

function renderChrome() {
  const boot = state.boot;
  $("site-title").textContent = boot.title || "SM-Elite";
  const bits = [boot.callsign, boot.location].filter(Boolean);
  $("site-sub").textContent = bits.join(" · ");
  $("welcome").textContent = boot.welcome || "";
  document.title = boot.title || "SM-Elite";
  const login = $("login-btn");
  if (boot.user) {
    login.textContent = `Log out ${boot.user.name}`;
  } else {
    login.textContent = "Log in";
  }
  $("users-btn").hidden = boot.user?.role !== "admin";
  $("account-btn").hidden = !boot.user;
  $("admin-btn").hidden = boot.user?.role !== "admin" || !boot.admin_tools;
  $("system-btn").hidden = !["operator", "admin"].includes(boot.user?.role);
  $("setup-code-wrap").hidden = !boot.setup_code_file;
  $("setup-code").required = Boolean(boot.setup_code_file);
  $("setup-code-cmd").textContent = boot.setup_code_file ? `sudo cat ${boot.setup_code_file}` : "";
  const banner = $("setup-banner");
  banner.hidden = !boot.needs_setup;
  banner.replaceChildren();
  if (boot.needs_setup) {
    banner.append("The node is live. ");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "ghost";
    open.textContent = "Create admin";
    open.addEventListener("click", () => $("setup-dialog").showModal());
    banner.append(open);
    banner.append(" when you want to control it.");
  }
  renderNav();
  renderControl();
}

function renderNav() {
  const nav = $("node-nav");
  nav.replaceChildren();
  const buttons = [];
  for (const group of state.boot.groups || []) {
    buttons.push({ label: group.name, ids: group.nodes });
  }
  for (const node of state.boot.nodes || []) {
    buttons.push({ label: node.label || node.id, ids: [node.id] });
  }
  if (!buttons.length) {
    nav.append("No nodes configured");
    return;
  }
  for (const item of buttons) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = item.ids.every((id) => state.selected.includes(id)) && item.ids.length === state.selected.length ? "on" : "";
    button.textContent = item.label;
    button.addEventListener("click", () => selectNodes(item.ids));
    nav.append(button);
  }
}

function selectNodes(ids) {
  state.selected = ids.filter((id, index) => ids.indexOf(id) === index).sort(compareNodes);
  if (!state.selected.includes(state.local)) state.local = state.selected[0] || "";
  history.replaceState(null, "", `#${state.selected.join(",")}`);
  renderNav();
  renderControl();
  pruneCards();
  subscribe();
  loadHeard();
}

function renderControl() {
  const bar = $("control");
  bar.replaceChildren();
  const mine = state.selected.filter(canCommand);
  bar.hidden = !mine.length;
  if (!mine.length) return;
  if (!mine.includes(state.local)) state.local = mine[0];
  const local = field("Local node");
  const select = document.createElement("select");
  select.id = "local-node";
  for (const id of mine) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = id;
    option.selected = id === state.local;
    select.append(option);
  }
  select.addEventListener("change", () => { state.local = select.value; });
  local.append(select);

  const target = field("Target");
  const input = document.createElement("input");
  input.id = "target";
  input.value = state.target;
  input.inputMode = "tel";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.maxLength = 15;
  input.placeholder = "Node or DTMF";
  input.addEventListener("input", () => { state.target = input.value.trim(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendCommand("connect");
  });
  target.append(input);

  const options = document.createElement("div");
  options.className = "ctl-options";
  options.append(
    check("Permanent", state.permanent, (on) => { state.permanent = on; }),
    check("Disconnect first", state.dropFirst, (on) => { state.dropFirst = on; }),
  );
  const fields = document.createElement("div");
  fields.className = "ctl-fields";
  fields.append(local, target, options);
  const head = document.createElement("div");
  head.className = "ctl-row";
  head.append(fields);
  if (operator()) {
    const tools = document.createElement("div");
    tools.className = "ctl-tools";
    tools.append(
      actionButton("Announce", openSounds),
      actionButton("Who can connect", openAccess),
      actionButton("Schedule", openSchedule),
    );
    if (dvsNodes().length) tools.append(actionButton("DVSwitch", openDvswitch));
    head.append(tools);
  }

  const connect = actionButton("Connect", () => sendCommand("connect"));
  connect.className = "primary";
  const actions = document.createElement("div");
  actions.className = "ctl-actions";
  actions.append(
    buttonGroup("", connect,
      actionButton("Monitor", () => sendCommand("monitor")),
      actionButton("Local monitor", () => sendCommand("local_monitor"))),
    buttonGroup("danger",
      actionButton("Disconnect", () => sendCommand("disconnect")),
      actionButton("Disconnect all", () => sendCommand("disconnect_all"))),
    buttonGroup("", actionButton("DTMF", () => sendCommand("dtmf", { digits: state.target }))),
  );
  const custom = document.createElement("select");
  custom.setAttribute("aria-label", "More commands");
  const blank = document.createElement("option");
  blank.textContent = "More commands";
  blank.value = "";
  custom.append(blank);
  for (const item of state.boot.commands || []) {
    const option = document.createElement("option");
    option.value = item.command;
    option.textContent = item.label;
    custom.append(option);
  }
  custom.addEventListener("change", () => {
    if (!custom.value) return;
    const template = custom.value;
    custom.value = "";
    sendCommand("custom", { template });
  });
  actions.append(custom);
  bar.append(head, actions);
}

function field(text) {
  const label = document.createElement("label");
  label.className = "field";
  const caption = document.createElement("span");
  caption.className = "ctl-label";
  caption.textContent = text;
  label.append(caption);
  return label;
}

function buttonGroup(kind, ...buttons) {
  const group = document.createElement("div");
  group.className = kind ? `seg ${kind}` : "seg";
  group.setAttribute("role", "group");
  group.append(...buttons);
  return group;
}

function check(label, value, onChange) {
  const wrap = document.createElement("label");
  wrap.className = "check";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = value;
  input.addEventListener("change", () => onChange(input.checked));
  wrap.append(input, document.createTextNode(label));
  return wrap;
}

function actionButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function pruneCards() {
  const host = $("nodes");
  for (const card of [...host.children]) {
    if (!state.selected.includes(card.dataset.node)) card.remove();
  }
  for (const id of state.selected) {
    if (state.nodes.has(id)) renderSnapshot(state.nodes.get(id));
  }
  renderVoters();
}

function nodeRank(id) {
  const text = String(id);
  if (!/^\d+$/.test(text)) return [2, 0];
  const number = Number(text);
  return [number < 2000 ? 1 : 0, number];
}

function compareNodes(a, b) {
  const [groupA, numberA] = nodeRank(a);
  const [groupB, numberB] = nodeRank(b);
  return groupA - groupB || numberA - numberB || String(a).localeCompare(String(b));
}

function cardFor(id) {
  let card = document.querySelector(`.card[data-node="${nodeAttr(id)}"]`);
  if (card) return card;
  card = document.createElement("article");
  card.className = "card";
  card.dataset.node = id;
  card.innerHTML = `
    <header><h2 class="name"></h2><span class="meta muted"></span></header>
    <div class="tx idle"></div>
    <div class="status"></div>
    <table>
      <thead><tr>
        <th>Node</th><th>Info</th><th>Last recv</th><th>Connected</th><th>Dir</th><th>State</th><th>Mode</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <details><summary>Link map</summary><svg class="map" role="img" aria-label="Link map"></svg></details>`;
  const later = [...$("nodes").querySelectorAll(".card")].find((other) => compareNodes(id, other.dataset.node) < 0);
  $("nodes").insertBefore(card, later || null);
  return card;
}

function renderSnapshot(snap) {
  if (!state.selected.includes(snap.node)) return;
  const card = cardFor(snap.node);
  card.querySelector(".name").textContent = `${snap.node} · ${snap.desc || "Node"}`;
  const meta = [];
  if (!snap.online) meta.push(snap.error || "offline");
  else meta.push(uptimeLabel(snap.uptime));
  if (snap.direct || snap.linked) meta.push(`${snap.direct} direct · ${snap.linked} linked`);
  card.querySelector(".meta").textContent = meta.filter(Boolean).join(" · ");
  const tx = card.querySelector(".tx");
  const mode = snap.tx || "idle";
  tx.className = `tx ${mode}`;
  let label = TX_LABEL[mode] || "Idle";
  if (mode === "playback" && snap.remote_keyed_node) label += ` ${snap.remote_keyed_node}`;
  tx.textContent = label;
  renderStatus(card.querySelector(".status"), snap.status || {});
  const body = card.querySelector("tbody");
  const seen = new Set();
  for (const conn of snap.conns || []) {
    seen.add(String(conn.node));
    let row = body.querySelector(`tr[data-node="${nodeAttr(conn.node)}"]`);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.node = String(conn.node);
      row.innerHTML = "<td class='node'></td><td></td><td></td><td></td><td></td><td></td><td></td>";
      row.addEventListener("click", () => chooseTarget(conn.node));
      row.addEventListener("dblclick", () => {
        chooseTarget(conn.node);
        if (operator()) sendCommand("disconnect");
      });
      body.append(row);
    }
    const cells = row.children;
    cells[0].textContent = conn.node;
    cells[1].textContent = conn.desc || "";
    cells[2].textContent = clock(conn.since_unkey);
    cells[3].textContent = conn.elapsed || "";
    cells[4].textContent = conn.direction || "";
    cells[5].textContent = conn.state || "";
    cells[6].textContent = conn.mode || "";
    row.className = conn.ptt ? "keyed" : (conn.state === "CONNECTING" ? "connecting" : "");
    if (String(conn.node) === state.target) row.classList.add("selected");
  }
  for (const row of [...body.children]) {
    if (!seen.has(row.dataset.node)) row.remove();
  }
  if (!seen.size) {
    if (!body.querySelector(".empty-row")) {
      const row = document.createElement("tr");
      row.className = "empty-row";
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent = "No connections · repeat only";
      row.append(cell);
      body.append(row);
    }
  }
  drawMap(card.querySelector(".map"), snap);
  paintFavoriteLinks();
  paintTransmit();
}

function renderStatus(box, status) {
  box.replaceChildren();
  const line = (className, ...parts) => {
    const row = document.createElement("div");
    row.className = className;
    row.append(...parts);
    box.append(row);
    return row;
  };
  for (const alert of status.alert || []) {
    const row = line("alert", alert.text);
    if (alert.color) row.style.color = alert.color;
  }
  if (status.wx) line("wx", status.wx);
  if (status.temp || status.uptime) {
    const parts = ["CPU="];
    if (status.temp) {
      const badge = document.createElement("span");
      badge.className = `temp ${status.temp.level}`;
      badge.textContent = status.temp.text;
      parts.push(badge);
    }
    if (status.uptime) parts.push(` - ${status.uptime}`);
    line("cpu", ...parts);
  }
  if (status.load) line("load", status.load);
  if (status.disk) line("disk", status.disk);
  box.hidden = !box.children.length;
}

function drawMap(svg, snap) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const ns = "http://www.w3.org/2000/svg";
  const width = 420;
  const height = 180;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const peers = (snap.conns || []).slice(0, 12);
  const add = (name, attrs) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
    return node;
  };
  peers.forEach((conn, index) => {
    const angle = (-Math.PI / 2) + (index / Math.max(peers.length, 1)) * Math.PI * 2;
    const x = 210 + Math.cos(angle) * 150;
    const y = 90 + Math.sin(angle) * 68;
    add("line", { x1: 210, y1: 90, x2: x, y2: y });
    add("circle", { cx: x, cy: y, r: 8, class: "peer" });
    const label = add("text", { x: x + 10, y: y + 4 });
    label.textContent = conn.node;
  });
  add("circle", { cx: 210, cy: 90, r: 16, class: "hub" });
  const hub = add("text", { x: 228, y: 94 });
  hub.textContent = snap.node;
}

function paintTransmit() {
  const live = state.selected.some((id) => {
    const mode = state.nodes.get(id)?.tx;
    return mode && mode !== "idle";
  });
  $("tx-dot").classList.toggle("live", live);
  const title = state.boot?.title || "SM-Elite";
  document.title = live ? `TX · ${title}` : title;
}

function renderFavorites() {
  const host = $("favorites");
  host.replaceChildren();
  const head = document.createElement("div");
  head.className = "fav-head";
  const title = document.createElement("h2");
  title.textContent = "Favorites";
  head.append(title);
  const tools = document.createElement("div");
  tools.className = "fav-tools";
  const picker = document.createElement("select");
  for (const name of state.boot.favorites || []) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.selected = name === state.favFile;
    picker.append(option);
  }
  picker.addEventListener("change", () => loadFavorites(picker.value));
  tools.append(picker);
  if (operator()) {
    const node = document.createElement("input");
    node.placeholder = "Node";
    node.size = 6;
    node.id = "fav-node";
    const label = document.createElement("input");
    label.placeholder = "Name";
    label.id = "fav-label";
    const add = document.createElement("button");
    add.type = "button";
    add.textContent = "Add";
    add.addEventListener("click", async () => {
      try {
        await api("/api/favorites", { method: "POST", body: { file: state.favFile, node: node.value.trim(), label: label.value.trim() } });
        node.value = "";
        label.value = "";
        await loadFavorites(state.favFile);
      } catch (error) {
        showResult("Favorite", error.message);
      }
    });
    tools.append(node, label, add);
  }
  head.append(tools);
  host.append(head);
  const scan = document.createElement("p");
  scan.id = "scan-line";
  scan.className = "muted";
  if (state.favError === "favorites file was not found") {
    scan.textContent = operator() ? `No ${state.favFile} yet. Add a node above to start one.` : `No ${state.favFile} on this node yet.`;
  } else if (state.favError) {
    scan.textContent = `Favorites could not be loaded: ${state.favError}`;
  } else {
    scan.textContent = "Activity scan uses the AllStar stats service. Nodes are not connected just to see if they are busy.";
  }
  host.append(scan);
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const header = document.createElement("tr");
  for (const column of [["#", "tx"], ["Node", "node"], ["Name", "label"], ["Call", "call"], ["Where", "location"], ["Rx%", "busy"], ["Links", "links"]]) {
    const cell = document.createElement("th");
    cell.textContent = column[0];
    cell.addEventListener("click", () => {
      state.sortDir = state.sortKey === column[1] ? -state.sortDir : 1;
      state.sortKey = column[1];
      renderFavorites();
    });
    header.append(cell);
  }
  if (operator()) {
    const cell = document.createElement("th");
    cell.textContent = "";
    header.append(cell);
  }
  thead.append(header);
  table.append(thead);
  const body = document.createElement("tbody");
  const linked = linkedIds();
  const rows = state.favorites.filter((item) => !item.header);
  const headers = state.favorites.filter((item) => item.header);
  const sorted = [...rows].sort((a, b) => compareFav(a, b));
  for (const item of [...headers, ...sorted]) {
    if (item.header) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = operator() ? 8 : 7;
      cell.textContent = item.label;
      row.append(cell);
      body.append(row);
      continue;
    }
    const row = document.createElement("tr");
    row.dataset.node = item.node;
    const swatch = document.createElement("td");
    const mark = document.createElement("span");
    mark.className = "swatch";
    swatch.append(mark);
    const nodeCell = document.createElement("td");
    nodeCell.className = "node";
    nodeCell.textContent = item.node;
    if (linked.has(item.node)) nodeCell.classList.add("linked");
    nodeCell.addEventListener("click", () => chooseTarget(item.node));
    nodeCell.addEventListener("dblclick", () => chooseTarget(item.node, { connect: true }));
    const name = document.createElement("td");
    name.textContent = item.label || item.detail || "";
    if (item.only) {
      const only = document.createElement("span");
      only.className = "muted small";
      only.textContent = ` · ${item.only} only`;
      only.title = `Listed for local node ${item.only} only`;
      name.append(only);
    }
    const call = document.createElement("td");
    call.textContent = item.call || "";
    const where = document.createElement("td");
    where.textContent = item.location || "";
    const busy = document.createElement("td");
    const links = document.createElement("td");
    row.append(swatch, nodeCell, name, call, where, busy, links);
    if (operator()) {
      const remove = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = "Remove";
      button.addEventListener("click", async () => {
        if (!confirm(`Remove ${item.node} from favorites?`)) return;
        await api("/api/favorites", { method: "DELETE", body: { file: state.favFile, node: item.node } });
        await loadFavorites(state.favFile);
      });
      remove.append(button);
      row.append(remove);
    }
    applyStat(row, state.stats.get(item.node));
    body.append(row);
  }
  table.append(body);
  host.append(table);
}

function compareFav(a, b) {
  const key = state.sortKey;
  const statA = state.stats.get(a.node) || {};
  const statB = state.stats.get(b.node) || {};
  const value = (item, stat) => {
    if (key === "tx") return stat.tx_pct || 0;
    if (key === "busy") return stat.busy_pct || 0;
    if (key === "links") return stat.links || 0;
    if (key === "node") return Number(item.node) || 0;
    return (item[key] || "").toLowerCase();
  };
  const left = value(a, statA);
  const right = value(b, statB);
  if (left < right) return -1 * state.sortDir;
  if (left > right) return 1 * state.sortDir;
  return 0;
}

function applyStat(row, stat) {
  if (!row || !stat) return;
  const mark = row.querySelector(".swatch");
  const busy = row.children[5];
  const links = row.children[6];
  if (stat.tx_pct > 5) {
    mark.className = "swatch";
    mark.style.background = heatColor(stat.tx_pct);
    mark.style.color = "white";
    mark.textContent = String(stat.tx_pct);
  } else if (stat.active && stat.web) {
    mark.className = "swatch web";
    mark.style.background = "";
    mark.textContent = "W";
  } else if (stat.active) {
    mark.className = "swatch up";
    mark.style.background = "";
    mark.textContent = "●";
  } else {
    mark.className = "swatch down";
    mark.style.background = "";
    mark.textContent = "·";
  }
  busy.textContent = stat.busy_pct === null || stat.busy_pct === undefined ? "" : `${stat.busy_pct}%`;
  links.textContent = stat.links ? String(stat.links) : "";
}

function paintFavoriteLinks() {
  const linked = linkedIds();
  for (const cell of document.querySelectorAll("#favorites td.node")) {
    cell.classList.toggle("linked", linked.has(cell.textContent));
  }
}

function paintStats(stat) {
  state.stats.set(stat.node, stat);
  const row = document.querySelector(`#favorites tr[data-node="${nodeAttr(stat.node)}"]`);
  applyStat(row, stat);
  const line = $("scan-line");
  if (line && !stat.error) {
    const hot = stat.tx_pct > 5 ? ` · Tx ${stat.tx_pct}%` : "";
    line.textContent = `Last sample ${stat.node}${hot}`;
  }
}

function renderVoters() {
  const host = $("voters");
  host.replaceChildren();
  const voterNodes = [];
  for (const node of state.boot.nodes || []) {
    if (state.selected.includes(node.id)) voterNodes.push(...(node.voters || []));
  }
  if (!voterNodes.length && ![...state.voters.keys()].some((id) => state.selected.includes(id))) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const title = document.createElement("h2");
  title.textContent = "Voters";
  host.append(title);
  const shown = new Set(voterNodes.length ? voterNodes : state.selected);
  for (const [node, payload] of state.voters) {
    if (!shown.has(node) && !state.selected.includes(node)) continue;
    const block = document.createElement("div");
    block.className = "voter";
    const heading = document.createElement("h3");
    heading.textContent = payload.voted ? `${node} · voted ${payload.voted}` : node;
    block.append(heading);
    if (payload.error) {
      const error = document.createElement("p");
      error.className = "muted";
      error.textContent = payload.error;
      block.append(error);
    }
    for (const client of payload.clients || []) {
      const name = document.createElement("div");
      name.textContent = `${client.name} · ${client.rssi}`;
      const bar = document.createElement("div");
      bar.className = `bar ${client.role || ""}`;
      const fill = document.createElement("span");
      fill.style.width = `${client.percent || 0}%`;
      bar.append(fill);
      block.append(name, bar);
    }
    host.append(block);
  }
}

let finderTimer = null;
let finderSeq = 0;

function wireFinder() {
  const input = $("finder-q");
  input.addEventListener("input", () => {
    clearTimeout(finderTimer);
    finderTimer = setTimeout(() => runFinder(input.value.trim()), 250);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      runFinder("");
    }
  });
  runFinder("");
}

async function runFinder(query) {
  const seq = ++finderSeq;
  const note = $("finder-note");
  const host = $("finder-results");
  if (query.length < 2) {
    note.textContent = operator()
      ? "Searches the AllStar node directory. Click a node to put it in the target box."
      : "Searches the AllStar node directory.";
    host.replaceChildren();
    return;
  }
  let data;
  try {
    data = await api(`/api/directory?q=${encodeURIComponent(query)}&limit=25`);
  } catch (error) {
    if (seq === finderSeq) note.textContent = error.message;
    return;
  }
  if (seq !== finderSeq) return;
  const items = data.items || [];
  if (!items.length) {
    note.textContent = `No nodes match "${query}".`;
    host.replaceChildren();
    return;
  }
  note.textContent = data.total > items.length
    ? `Showing ${items.length} of ${data.total} matches. Add another word to narrow it down.`
    : `${data.total} ${data.total === 1 ? "match" : "matches"}.`;
  renderFinder(items);
}

function renderFinder(items) {
  const host = $("finder-results");
  host.replaceChildren();
  const linked = linkedIds();
  const saved = new Set(state.favorites.map((item) => item.node).filter(Boolean));
  const table = document.createElement("table");
  table.className = "finder";
  const head = document.createElement("tr");
  for (const label of ["Node", "Call", "Name", "Where", ...(operator() ? [""] : [])]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    head.append(cell);
  }
  const thead = document.createElement("thead");
  thead.append(head);
  const body = document.createElement("tbody");
  for (const item of items) {
    const row = document.createElement("tr");
    const node = document.createElement("td");
    node.className = "node";
    node.textContent = item.node;
    node.title = "Click to target, double-click to connect";
    if (linked.has(item.node)) node.classList.add("linked");
    node.addEventListener("click", () => chooseTarget(item.node));
    node.addEventListener("dblclick", () => chooseTarget(item.node, { connect: true }));
    const call = document.createElement("td");
    call.textContent = item.call || "";
    const name = document.createElement("td");
    name.textContent = item.desc || "";
    const where = document.createElement("td");
    where.textContent = item.location || "";
    row.append(node, call, name, where);
    if (operator()) {
      const actions = document.createElement("td");
      actions.className = "row-actions";
      actions.append(
        smallButton(linked.has(item.node) ? "Linked" : "Connect", () => {
          chooseTarget(item.node);
          sendCommand("connect");
        }, linked.has(item.node)),
        smallButton("Monitor", () => {
          chooseTarget(item.node);
          sendCommand("monitor");
        }, linked.has(item.node)),
        smallButton(saved.has(item.node) ? "Saved" : "+ Favorite", async () => {
          try {
            const label = [item.call, item.desc].filter(Boolean).join(" ");
            await api("/api/favorites", { method: "POST", body: { file: state.favFile, node: item.node, label } });
            await loadFavorites(state.favFile);
            renderFinder(items);
          } catch (error) {
            showResult("Favorite", error.message);
          }
        }, saved.has(item.node)),
      );
      row.append(actions);
    }
    body.append(row);
  }
  table.append(thead, body);
  host.append(table);
}

function smallButton(label, onClick, disabled = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost tiny";
  button.textContent = label;
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  return button;
}

function stampLive(entries) {
  const now = Date.now();
  return (entries || []).map((entry) => ({ ...entry, receivedAt: now }));
}

async function loadHeard() {
  if (!state.selected.length) return;
  try {
    const data = await api(`/api/heard?nodes=${encodeURIComponent(state.selected.join(","))}&limit=50`);
    state.heardEnabled = Boolean(data.enabled);
    state.heard = data.items || [];
    state.heardLive.clear();
    for (const entry of stampLive(data.live)) {
      alerts.seen.add(`${entry.node}:${entry.source}:${entry.started}`);
      const list = state.heardLive.get(entry.node) || [];
      list.push(entry);
      state.heardLive.set(entry.node, list);
    }
  } catch {
    state.heardEnabled = false;
  }
  renderHeard();
}

const ALERTS_KEY = "smelite-alerts";
const alerts = {
  settings: { on: false, who: "any", nodes: [], sound: true, desktop: false, quiet: 30 },
  seen: new Set(),
  last: new Map(),
  audio: null,
};

function loadAlertSettings() {
  try {
    Object.assign(alerts.settings, JSON.parse(localStorage.getItem(ALERTS_KEY) || "{}"));
  } catch {
    localStorage.removeItem(ALERTS_KEY);
  }
  $("alerts-btn").textContent = alerts.settings.on ? "Alerts on" : "Alerts";
}

function saveAlertSettings() {
  const s = alerts.settings;
  s.on = $("alerts-on").checked;
  s.who = document.querySelector("input[name=alerts-who]:checked").value;
  s.nodes = $("alerts-nodes").value.split(/[\s,]+/).map((part) => part.trim().toLowerCase()).filter(Boolean);
  s.sound = $("alerts-sound").checked;
  s.desktop = $("alerts-desktop").checked && desktopAlertsUsable();
  s.quiet = Math.max(0, Math.min(3600, Number($("alerts-quiet").value) || 0));
  localStorage.setItem(ALERTS_KEY, JSON.stringify(s));
  $("alerts-btn").textContent = s.on ? "Alerts on" : "Alerts";
}

function desktopAlertsUsable() {
  return window.isSecureContext && "Notification" in window && Notification.permission !== "denied";
}

function openAlerts() {
  const s = alerts.settings;
  $("alerts-on").checked = s.on;
  document.querySelector(`input[name=alerts-who][value=${s.who === "list" ? "list" : "any"}]`).checked = true;
  $("alerts-nodes").value = s.nodes.join(", ");
  $("alerts-sound").checked = s.sound;
  $("alerts-quiet").value = s.quiet;
  const usable = desktopAlertsUsable();
  $("alerts-desktop").disabled = !usable;
  $("alerts-desktop").checked = usable && s.desktop;
  const note = $("alerts-desktop-note");
  note.hidden = usable;
  if (!window.isSecureContext) note.textContent = "Desktop notifications need the page on HTTPS (or localhost). The chime and on-page pop-up still work.";
  else if (!("Notification" in window)) note.textContent = "This browser does not support desktop notifications.";
  else note.textContent = "Notifications are blocked for this site in the browser settings.";
  $("alerts-dialog").showModal();
}

function unlockAudio() {
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return null;
  if (!alerts.audio) alerts.audio = new Context();
  if (alerts.audio.state === "suspended") alerts.audio.resume();
  return alerts.audio;
}

function chime() {
  const ctx = unlockAudio();
  if (!ctx) return;
  const play = () => {
    const start = ctx.currentTime;
    for (const [offset, freq] of [[0, 880], [0.16, 1320]]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, start + offset);
      gain.gain.exponentialRampToValueAtTime(0.25, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start + offset);
      osc.stop(start + offset + 0.32);
    }
  };
  if (ctx.state === "running") play();
  else ctx.resume().then(() => { if (ctx.state === "running") play(); }).catch(() => {});
}

function toast(text) {
  const box = document.createElement("div");
  box.className = "toast";
  box.textContent = text;
  $("toasts").append(box);
  setTimeout(() => box.remove(), 6000);
}

function alertText(entry) {
  const who = entry.kind === "local" ? "Local radio" : `${entry.source}${entry.desc ? ` ${entry.desc}` : ""}`;
  return `${who} keyed up on ${entry.node}`;
}

function fireAlert(text) {
  const s = alerts.settings;
  toast(text);
  if (s.sound) chime();
  if (s.desktop && desktopAlertsUsable() && Notification.permission === "granted") {
    new Notification("SM-Elite", { body: text, tag: "smelite-keyup" });
  }
}

function checkAlerts(message) {
  const s = alerts.settings;
  const now = Date.now();
  for (const entry of message.live || []) {
    const key = `${entry.node}:${entry.source}:${entry.started}`;
    if (alerts.seen.has(key)) continue;
    alerts.seen.add(key);
    if (!s.on || entry.seconds > 5) continue;
    const source = String(entry.source).toLowerCase();
    if (s.who === "list" && !s.nodes.includes(source)) continue;
    const quietKey = `${entry.node}:${source}`;
    if (now - (alerts.last.get(quietKey) || 0) < s.quiet * 1000) continue;
    alerts.last.set(quietKey, now);
    fireAlert(alertText(entry));
  }
  if (alerts.seen.size > 2000) alerts.seen = new Set([...alerts.seen].slice(-500));
}

function wireAlerts() {
  loadAlertSettings();
  $("alerts-btn").addEventListener("click", openAlerts);
  $("alerts-close").addEventListener("click", () => {
    saveAlertSettings();
    $("alerts-dialog").close();
  });
  $("alerts-dialog").addEventListener("close", saveAlertSettings);
  $("alerts-on").addEventListener("change", unlockAudio);
  $("alerts-desktop").addEventListener("change", async (event) => {
    if (event.target.checked && Notification.permission === "default") {
      const answer = await Notification.requestPermission();
      if (answer !== "granted") event.target.checked = false;
    }
  });
  $("alerts-test").addEventListener("click", () => {
    saveAlertSettings();
    unlockAudio();
    setTimeout(() => fireAlert("Test: 2000 keyed up on your node"), 100);
  });
  document.addEventListener("pointerdown", () => {
    if (alerts.settings.on && alerts.settings.sound) unlockAudio();
  }, { once: true });
}

function onHeard(message) {
  checkAlerts(message);
  const known = new Set(state.heard.map((entry) => entry.id));
  const fresh = (message.done || []).filter((entry) => !known.has(entry.id));
  state.heard = [...fresh, ...state.heard].sort((a, b) => b.started - a.started).slice(0, 100);
  state.heardLive.set(message.node, stampLive(message.live));
  state.heardEnabled = true;
  renderHeard();
}

function liveSeconds(entry) {
  return entry.seconds + (Date.now() - entry.receivedAt) / 1000;
}

function renderHeard() {
  const host = $("heard");
  host.hidden = !state.heardEnabled;
  if (!state.heardEnabled) return;
  host.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = "Last heard";
  const note = document.createElement("p");
  note.className = "muted small";
  note.textContent = "Talkers behind a hub are listed as the link their audio came in on.";
  host.append(title, note);

  const many = state.selected.length > 1;
  const live = state.selected.flatMap((id) => state.heardLive.get(id) || []);
  live.sort((a, b) => a.started - b.started);
  const done = state.heard.filter((entry) => state.selected.includes(entry.node));
  if (!live.length && !done.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Nobody has keyed up since the log started.";
    host.append(empty);
    return;
  }

  const table = document.createElement("table");
  table.className = "heard";
  const head = document.createElement("tr");
  for (const label of ["When", ...(many ? ["On"] : []), "Node", "Who", "Length"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    head.append(cell);
  }
  const thead = document.createElement("thead");
  thead.append(head);
  const body = document.createElement("tbody");
  for (const entry of [...live, ...done].slice(0, display.heardRows)) {
    const row = document.createElement("tr");
    if (entry.live) row.className = "keyed";
    const when = document.createElement("td");
    when.textContent = entry.live ? "Now" : whenLabel(entry.started);
    when.title = entry.live ? "Keyed right now" : agoLabel(entry.started);
    row.append(when);
    if (many) {
      const on = document.createElement("td");
      on.textContent = entry.node;
      row.append(on);
    }
    const node = document.createElement("td");
    if (entry.kind === "local") {
      node.textContent = "RF";
      node.className = "muted";
    } else {
      node.textContent = entry.source;
      node.className = "node";
      node.addEventListener("click", () => chooseTarget(entry.source));
    }
    const who = document.createElement("td");
    who.textContent = entry.desc || "";
    const length = document.createElement("td");
    length.textContent = entry.live ? lengthLabel(liveSeconds(entry)) : lengthLabel(entry.seconds);
    row.append(node, who, length);
    body.append(row);
  }
  table.append(thead, body);
  host.append(table);
}

const DISPLAY_KEY = "smelite-display";
const DISPLAY_PARTS = [
  ["Page", [
    ["control", "Node control bar"],
    ["finder", "Find a node"],
    ["heard", "Last heard"],
    ["favorites", "Favorites"],
    ["voters", "Voters"],
    ["legend", "Color key"],
  ]],
  ["Node cards", [
    ["tx", "Transmit status"],
    ["alerts", "Alerts and weather"],
    ["cpu", "CPU temperature and uptime"],
    ["load", "Load average"],
    ["disk", "Disk"],
    ["links", "Connection table"],
    ["map", "Link map"],
  ]],
  ["Connection table columns", [
    ["col-info", "Info"],
    ["col-recv", "Last recv"],
    ["col-connected", "Connected"],
    ["col-dir", "Dir"],
    ["col-state", "State"],
    ["col-mode", "Mode"],
  ]],
];
const HEARD_ROWS = [5, 10, 25, 50];
const display = { hidden: new Set(), heardRows: 5 };

function loadDisplay() {
  try {
    const saved = JSON.parse(localStorage.getItem(DISPLAY_KEY) || "{}");
    display.hidden = new Set(Array.isArray(saved.hidden) ? saved.hidden : []);
    if (HEARD_ROWS.includes(saved.heardRows)) display.heardRows = saved.heardRows;
  } catch {
    localStorage.removeItem(DISPLAY_KEY);
  }
  applyDisplay();
}

function saveDisplay() {
  localStorage.setItem(DISPLAY_KEY, JSON.stringify({ hidden: [...display.hidden], heardRows: display.heardRows }));
  applyDisplay();
  renderHeard();
}

function applyDisplay() {
  for (const [, parts] of DISPLAY_PARTS) {
    for (const [key] of parts) document.body.classList.toggle(`hide-${key}`, display.hidden.has(key));
  }
}

function openDisplay() {
  const host = $("display-groups");
  host.replaceChildren();
  for (const [group, parts] of DISPLAY_PARTS) {
    const set = document.createElement("fieldset");
    set.className = "scope";
    const legend = document.createElement("legend");
    legend.textContent = group;
    set.append(legend);
    for (const [key, label] of parts) {
      const wrap = document.createElement("label");
      wrap.className = "check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !display.hidden.has(key);
      box.addEventListener("change", () => {
        if (box.checked) display.hidden.delete(key);
        else display.hidden.add(key);
        saveDisplay();
      });
      wrap.append(box, document.createTextNode(` ${label}`));
      set.append(wrap);
    }
    host.append(set);
  }
  $("display-heard-rows").value = String(display.heardRows);
  $("display-dialog").showModal();
}

function wireDisplay() {
  $("display-btn").addEventListener("click", openDisplay);
  $("display-close").addEventListener("click", () => $("display-dialog").close());
  $("display-heard-rows").addEventListener("change", (event) => {
    display.heardRows = Number(event.target.value) || 5;
    saveDisplay();
  });
  $("display-reset").addEventListener("click", () => {
    display.hidden.clear();
    saveDisplay();
    openDisplay();
  });
}

async function loadFavorites(file) {
  state.favFile = file;
  try {
    await api("/api/favorites/watch", { method: "POST", body: { file } });
    const data = await api(`/api/favorites?file=${encodeURIComponent(file)}`);
    state.favorites = data.items || [];
    state.favError = "";
    for (const [node, stat] of Object.entries(data.stats || {})) {
      if (stat) state.stats.set(node, stat);
    }
  } catch (error) {
    state.favorites = [];
    state.favError = error.message;
  }
  renderFavorites();
}

function subscribe() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify({ action: "subscribe", nodes: state.selected }));
}

function connectSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}${BASE}/ws`);
  state.socket = socket;
  socket.addEventListener("open", subscribe);
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "snapshot") {
      state.nodes.set(message.data.node, message.data);
      renderSnapshot(message.data);
    } else if (message.type === "stats") {
      paintStats(message.data);
    } else if (message.type === "voter") {
      state.voters.set(message.node, message.data);
      renderVoters();
    } else if (message.type === "heard") {
      onHeard(message);
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket === socket) setTimeout(connectSocket, 2000);
  });
}

async function refreshHealth() {
  try {
    const health = await api("/api/health");
    const host = health.host || {};
    const bits = [];
    if (health.demo) bits.push("demo");
    if (host.temp_c !== undefined) bits.push(`${host.temp_c}°C`);
    if (host.load) bits.push(`load ${host.load[0]}`);
    if (host.disk_pct !== undefined && host.disk_pct !== null) bits.push(`disk ${host.disk_pct}%`);
    $("health").textContent = bits.join(" · ") || "online";
  } catch {
    $("health").textContent = "offline";
  }
}

function wireDialogs() {
  $("login-btn").addEventListener("click", async () => {
    if (state.boot.user) {
      await api("/api/logout", { method: "POST", body: {} });
      location.reload();
      return;
    }
    $("login-error").textContent = "";
    $("login-dialog").showModal();
  });
  $("login-cancel").addEventListener("click", () => $("login-dialog").close());
  $("setup-later").addEventListener("click", () => $("setup-dialog").close());
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      await api("/api/login", { method: "POST", body: { username: form.get("username"), password: form.get("password") } });
      await reloadIfSessionKept("login-error");
    } catch (error) {
      $("login-error").textContent = error.message;
    }
  });
  $("setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      await api("/api/setup", {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password"), code: form.get("code") || "" },
      });
      await reloadIfSessionKept("setup-error");
    } catch (error) {
      $("setup-error").textContent = error.message;
    }
  });
  $("touch-btn").addEventListener("click", () => {
    document.body.classList.toggle("touch");
    localStorage.setItem("smelite-touch", document.body.classList.contains("touch") ? "1" : "0");
  });
  $("users-btn").addEventListener("click", openUsers);
  $("users-close").addEventListener("click", () => $("users-dialog").close());
  $("users-cancel-edit").addEventListener("click", () => editUser(null));
  $("users-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const editing = users_state.editing;
    try {
      if (editing) {
        const password = form.get("password");
        if (password && !confirm(`Set a new password for ${editing}? They are logged out everywhere.`)) return;
        await api(`/api/users/${encodeURIComponent(editing)}`, {
          method: "POST",
          body: { role: form.get("role"), nodes: form.get("nodes"), password },
        });
      } else {
        await api("/api/users", {
          method: "POST",
          body: {
            username: form.get("username"),
            password: form.get("password"),
            role: form.get("role"),
            nodes: form.get("nodes"),
          },
        });
      }
      editUser(null);
      await openUsers();
      if (editing === state.boot.user?.name) {
        state.boot = await api("/api/bootstrap");
        renderChrome();
      }
    } catch (error) {
      $("users-error").textContent = error.message;
    }
  });
  $("account-btn").addEventListener("click", () => {
    $("account-form").reset();
    $("account-error").textContent = "";
    $("account-title").textContent = `Account · ${state.boot.user.name} (${state.boot.user.role})`;
    $("account-dialog").showModal();
  });
  $("account-close").addEventListener("click", () => $("account-dialog").close());
  $("account-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    if (form.password.value !== form.again.value) {
      $("account-error").textContent = "The two new passwords do not match.";
      return;
    }
    try {
      await api("/api/account/password", { method: "POST", body: { current: form.current.value, password: form.password.value } });
      form.reset();
      $("account-dialog").close();
      showResult("Account", "Your password was changed.");
    } catch (error) {
      $("account-error").textContent = error.message;
    }
  });
}

const users_state = { editing: null, users: [] };

function editUser(user) {
  users_state.editing = user ? user.name : null;
  const form = $("users-form");
  form.reset();
  form.username.disabled = Boolean(user);
  form.password.required = !user;
  form.password.placeholder = user ? "leave blank to keep the current one" : "";
  if (user) {
    form.username.value = user.name;
    form.role.value = user.role;
    form.nodes.value = (user.nodes || []).join(", ");
  }
  $("users-form-title").textContent = user ? `Edit ${user.name}` : "Add a user";
  $("users-submit").textContent = user ? "Save changes" : "Add user";
  $("users-cancel-edit").hidden = !user;
  $("users-error").textContent = "";
  if (user) form.role.focus();
}

function admin() {
  return state.boot?.user?.role === "admin";
}

async function openAccess() {
  const node = state.local || state.selected[0];
  if (!node) return;
  try {
    state.access = await api(`/api/access?node=${encodeURIComponent(node)}`);
  } catch (error) {
    showResult("Who can connect", error.message);
    return;
  }
  const form = $("access-form");
  form.reset();
  if (/^[0-9]{1,8}$/.test(state.target) && state.target !== node) form.target.value = state.target;
  $("access-error").textContent = "";
  renderAccess();
  if (!$("access-dialog").open) $("access-dialog").showModal();
}

function renderAccess() {
  const { node, allow, deny } = state.access;
  $("access-title").textContent = `Who can connect to ${node}`;
  let mode;
  if (allow.length) {
    mode = `Allow-list mode: only the ${allow.length} ${allow.length === 1 ? "node" : "nodes"} on the allow list can connect in to ${node}. The deny list is not checked while the allow list has entries.`;
  } else if (deny.length) {
    mode = `Open with ${deny.length} blocked: any node can connect in to ${node} except the ones on the deny list.`;
  } else {
    mode = `Open: any node can connect in to ${node}. Both lists are empty.`;
  }
  $("access-mode").textContent = `${mode} Links you start from ${node} are never blocked.`;
  for (const [hostId, rows, which] of [["access-allow", allow, "allow"], ["access-deny", deny, "deny"]]) {
    const host = $(hostId);
    host.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "Empty";
      host.append(empty);
      continue;
    }
    for (const row of rows) {
      const line = document.createElement("div");
      line.className = "user-row";
      const label = document.createElement("span");
      label.textContent = [row.node, row.desc, row.comment && `“${row.comment}”`].filter(Boolean).join(" · ");
      line.append(label);
      if (admin()) {
        line.append(smallButton("Remove", () => changeAccess({ list: which, target: row.node, action: "remove" })));
      }
      host.append(line);
    }
  }
  $("access-form").hidden = !admin();
  $("access-readonly").hidden = admin();
}

async function changeAccess(body) {
  const { node, allow } = state.access;
  if (body.action === "remove" && body.list === "allow" && allow.length === 1) {
    if (!confirm(`Removing the last allow-list entry opens ${node} to every node again (except the deny list). Continue?`)) return false;
  }
  try {
    state.access = await api("/api/access", { method: "POST", body: { node, ...body } });
    $("access-error").textContent = "";
    renderAccess();
    if (state.access.detail) showResult("Disconnected", state.access.detail);
    return true;
  } catch (error) {
    $("access-error").textContent = error.message;
    return false;
  }
}

function wireAccess() {
  $("access-close").addEventListener("click", () => $("access-dialog").close());
  $("access-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const { node, allow } = state.access;
    const target = form.target.value.trim();
    const list = form.list.value;
    const extra = [];
    if (list === "allow" && !allow.length) {
      if (!confirm(`This turns on allow-list mode for ${node}: every node not on the allow list will be refused when it tries to connect in. Continue?`)) return;
      const linked = (state.nodes.get(node)?.conns || []).map((conn) => String(conn.node)).filter((id) => id !== target && /^[0-9]+$/.test(id) && !/^3[0-9]{6}$/.test(id));
      if (linked.length && confirm(`Also allow your current links (${linked.join(", ")}) so they can reconnect later?`)) extra.push(...linked);
    }
    const ok = await changeAccess({ action: "add", list, target, comment: form.comment.value.trim(), disconnect: form.disconnect.checked });
    if (!ok) return;
    for (const id of extra) {
      if (!await changeAccess({ action: "add", list: "allow", target: id, comment: "current link" })) return;
    }
    form.reset();
  });
}

const sounds_state = { node: "", info: null };

function soundsStatus(text, isError = false) {
  const line = $("sounds-status");
  line.textContent = text;
  line.classList.toggle("form-error", isError);
}

function soundScope() {
  return document.querySelector("input[name=sound-scope]:checked").value;
}

function confirmScope(what) {
  if (soundScope() !== "links") return true;
  return confirm(`Play ${what} on ${sounds_state.node} and every node linked to it?`);
}

async function openSounds() {
  const node = state.local || state.selected[0];
  if (!node) return;
  try {
    sounds_state.info = await api("/api/sounds");
  } catch (error) {
    showResult("Announce", error.message);
    return;
  }
  sounds_state.node = node;
  const info = sounds_state.info;
  $("sounds-title").textContent = `Announce on ${node}`;
  $("tts").hidden = !info.tts;
  $("tts-off").hidden = info.tts;
  $("tts-save").hidden = !info.can_save;
  const voice = $("tts-voice");
  const kept = voice.value;
  voice.replaceChildren(...info.voices.map((name) => new Option(name.replace(/^[a-z]{2}_[A-Z]{2}-/, "").replace(/-/g, " "), name)));
  if (info.voices.includes(kept)) voice.value = kept;
  $("tts-text").maxLength = info.max_chars;
  updateTtsCount();
  renderSoundList();
  soundsStatus("");
  $("sounds-dialog").showModal();
}

function updateTtsCount() {
  const max = sounds_state.info?.max_chars || 300;
  $("tts-count").textContent = `${$("tts-text").value.length} / ${max} characters`;
}

function renderSoundList() {
  const list = $("sound-list");
  const items = sounds_state.info?.library || [];
  if (!items.length) {
    list.replaceChildren(Object.assign(document.createElement("p"), { className: "muted small", textContent: "No sound files found." }));
    return;
  }
  const table = document.createElement("table");
  table.className = "heard";
  for (const item of items) {
    const row = table.insertRow();
    row.insertCell().textContent = item.name;
    row.insertCell().textContent = item.seconds == null ? "" : `${item.seconds}s`;
    const actions = row.insertCell();
    actions.append(smallButton("Play", () => playSound(item.name)));
    if (item.saved && sounds_state.info.can_save) actions.append(smallButton("Delete", () => deleteSound(item.name)));
  }
  list.replaceChildren(table);
}

async function speakText() {
  const text = $("tts-text").value.trim();
  if (!text) {
    soundsStatus("Type something to say.", true);
    return;
  }
  if (!confirmScope("this announcement")) return;
  soundsStatus("Preparing speech…");
  $("tts-speak").disabled = true;
  try {
    const result = await api("/api/sounds/speak", {
      method: "POST",
      body: { node: sounds_state.node, text, voice: $("tts-voice").value, scope: soundScope() },
    });
    soundsStatus(`Playing on ${sounds_state.node} (${result.seconds}s).`);
  } catch (error) {
    soundsStatus(error.message, true);
  } finally {
    $("tts-speak").disabled = false;
  }
}

async function playSound(name) {
  if (!confirmScope(name)) return;
  try {
    await api("/api/sounds/play", { method: "POST", body: { node: sounds_state.node, name, scope: soundScope() } });
    soundsStatus(`Playing ${name} on ${sounds_state.node}.`);
  } catch (error) {
    soundsStatus(error.message, true);
  }
}

async function saveSound() {
  const name = $("tts-name").value.trim().toLowerCase();
  const text = $("tts-text").value.trim();
  if (!name || !text) {
    soundsStatus("Type the text and a name first.", true);
    return;
  }
  const exists = sounds_state.info.library.some((item) => item.name === `announcements/${name}`);
  if (exists && !confirm(`Replace the saved announcement "${name}"?`)) return;
  soundsStatus("Saving…");
  try {
    const result = await api("/api/sounds/save", { method: "POST", body: { name, text, voice: $("tts-voice").value } });
    sounds_state.info.library = result.library;
    renderSoundList();
    soundsStatus(`Saved ${result.saved.name}. It can be played from the list below.`);
  } catch (error) {
    soundsStatus(error.message, true);
  }
}

async function deleteSound(name) {
  if (!confirm(`Delete ${name}?`)) return;
  try {
    const result = await api("/api/sounds/delete", { method: "POST", body: { name } });
    sounds_state.info.library = result.library;
    renderSoundList();
    soundsStatus(`Deleted ${name}.`);
  } catch (error) {
    soundsStatus(error.message, true);
  }
}

function wireSounds() {
  $("sounds-close").addEventListener("click", () => $("sounds-dialog").close());
  $("tts-text").addEventListener("input", updateTtsCount);
  $("tts-speak").addEventListener("click", speakText);
  $("tts-save-btn").addEventListener("click", saveSound);
}

const schedule_state = { info: null, editing: null };
const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SCHEDULE_FIELDS = {
  connect: ["target", "permanent"],
  monitor: ["target", "permanent"],
  local_monitor: ["target", "permanent"],
  disconnect: ["target"],
  disconnect_all: [],
  play: ["sound", "scope"],
  command: ["command"],
};

function scheduleStatus(text, isError = false) {
  const line = $("schedule-status");
  line.textContent = text;
  line.classList.toggle("form-error", isError);
}

async function loadSchedule() {
  try {
    schedule_state.info = await api("/api/schedule");
  } catch (error) {
    if ($("schedule-dialog").open) scheduleStatus(error.message, true);
    else showResult("Schedule", error.message);
    return false;
  }
  const info = schedule_state.info;
  const zone = info.timezone ? ` (${info.timezone})` : "";
  $("schedule-clock").textContent = `Times use the node's clock${zone}. It is ${DAY_LABELS[info.weekday]} ${info.now} there now. An action repeats every week on the days you pick, or runs once on a date.`;
  renderScheduleList();
  return true;
}

function scheduleWhat(job) {
  if (job.action === "play") {
    return `Play ${job.sound} on ${job.scope === "links" ? "the node and its links" : "this node"}`;
  }
  if (job.action === "command") {
    const known = (schedule_state.info?.commands || []).find((item) => item.command === job.template);
    return [known ? known.label : job.template, job.target].filter(Boolean).join(" ");
  }
  const text = [job.action_label, job.target].filter(Boolean).join(" ");
  return job.permanent ? `${text} (permanent)` : text;
}

function renderScheduleList() {
  const list = $("schedule-list");
  const jobs = schedule_state.info?.jobs || [];
  if (!jobs.length) {
    list.replaceChildren(Object.assign(document.createElement("p"), { className: "muted small", textContent: "Nothing is scheduled yet." }));
    return;
  }
  const table = document.createElement("table");
  table.className = "heard";
  const head = table.createTHead().insertRow();
  for (const label of ["When", "Node", "Action", "Note", "Added by", "Last run", ""]) {
    head.append(Object.assign(document.createElement("th"), { textContent: label }));
  }
  const body = table.createTBody();
  for (const job of jobs) {
    const row = body.insertRow();
    if (!job.enabled) row.className = "paused";
    row.insertCell().textContent = `${job.days_label} ${job.at}${job.enabled ? "" : " (paused)"}`;
    row.insertCell().textContent = job.node;
    row.insertCell().textContent = scheduleWhat(job);
    row.insertCell().textContent = job.note;
    row.insertCell().textContent = job.owner;
    row.insertCell().textContent = job.last_run ? `${whenLabel(job.last_run)} · ${job.last_result}` : "Not yet";
    const actions = row.insertCell();
    actions.className = "row-actions";
    actions.append(
      smallButton("Edit", () => editJob(job)),
      smallButton(job.enabled ? "Pause" : "Resume", () => toggleJob(job)),
      smallButton("Run now", () => runJob(job)),
      smallButton("Remove", () => removeJob(job)),
    );
  }
  list.replaceChildren(table);
}

async function toggleJob(job) {
  try {
    await api(`/api/schedule/${job.id}`, { method: "POST", body: { enabled: !job.enabled } });
    await loadSchedule();
    scheduleStatus(job.enabled ? `Paused ${scheduleWhat(job)}.` : `Resumed ${scheduleWhat(job)}.`);
  } catch (error) {
    scheduleStatus(error.message, true);
  }
}

async function runJob(job) {
  if (!confirm(`${scheduleWhat(job)} on ${job.node} now?`)) return;
  scheduleStatus("Running…");
  try {
    const result = await api(`/api/schedule/${job.id}/run`, { method: "POST", body: {} });
    await loadSchedule();
    scheduleStatus(result.result, result.result.startsWith("failed") || result.result.startsWith("skipped"));
  } catch (error) {
    scheduleStatus(error.message, true);
  }
}

async function removeJob(job) {
  if (!confirm(`Remove the scheduled action "${scheduleWhat(job)}" on ${job.node} (${job.days_label} ${job.at})?`)) return;
  try {
    await api(`/api/schedule/${job.id}`, { method: "DELETE" });
    if (schedule_state.editing === job.id) editJob(null);
    await loadSchedule();
    scheduleStatus("Removed.");
  } catch (error) {
    scheduleStatus(error.message, true);
  }
}

function updateScheduleForm() {
  const form = $("schedule-form");
  const fields = [...(SCHEDULE_FIELDS[form.kind.value] || [])];
  if (form.kind.value === "command" && form.template.value.includes("%")) fields.push("target");
  for (const element of form.querySelectorAll("[data-when]")) {
    element.hidden = !fields.includes(element.dataset.when);
  }
  const once = form.querySelector("input[name=repeat]:checked").value === "once";
  for (const box of form.querySelectorAll("input[name=day]")) box.disabled = once;
  form.date.disabled = !once;
}

function resetScheduleForm() {
  const info = schedule_state.info;
  const form = $("schedule-form");
  form.reset();
  form.node.replaceChildren(...info.nodes.map((id) => new Option(id, id, false, id === state.local)));
  form.kind.replaceChildren(...info.actions.map((item) => new Option(item.label, item.id)));
  form.sound.replaceChildren(...info.sounds.map((name) => new Option(name, name)));
  form.template.replaceChildren(...info.commands.map((item) => new Option(item.label, item.command)));
  if (/^[0-9]{1,8}$/.test(state.target)) form.target.value = state.target;
  for (const box of form.querySelectorAll("input[name=day]")) box.checked = Number(box.value) === info.weekday;
  form.date.min = info.today;
  form.date.value = info.today;
}

function editJob(job) {
  schedule_state.editing = job ? job.id : null;
  resetScheduleForm();
  const form = $("schedule-form");
  if (job) {
    if (![...form.node.options].some((option) => option.value === job.node)) form.node.append(new Option(job.node, job.node));
    form.node.value = job.node;
    form.kind.value = job.action;
    form.target.value = job.target;
    form.permanent.checked = job.permanent;
    if (job.sound) form.sound.value = job.sound;
    if (job.template) form.template.value = job.template;
    form.querySelector(`input[name=scope][value=${job.scope === "links" ? "links" : "local"}]`).checked = true;
    form.at.value = job.at;
    form.note.value = job.note;
    form.querySelector(`input[name=repeat][value=${job.date ? "once" : "weekly"}]`).checked = true;
    if (job.date) form.date.value = job.date;
    for (const box of form.querySelectorAll("input[name=day]")) box.checked = job.days.includes(Number(box.value));
  }
  $("schedule-form-title").textContent = job ? `Edit: ${scheduleWhat(job)} on ${job.node}` : "Add a scheduled action";
  $("schedule-submit").textContent = job ? "Save changes" : "Add";
  $("schedule-cancel-edit").hidden = !job;
  $("schedule-error").textContent = "";
  updateScheduleForm();
  if (job) form.scrollIntoView({ block: "nearest" });
}

async function openSchedule() {
  if (!(await loadSchedule())) return;
  editJob(null);
  scheduleStatus("");
  $("schedule-dialog").showModal();
}

async function addJob(event) {
  event.preventDefault();
  const form = event.target;
  const once = form.querySelector("input[name=repeat]:checked").value === "once";
  const days = once ? [] : [...form.querySelectorAll("input[name=day]:checked")].map((box) => Number(box.value));
  const editing = schedule_state.editing;
  try {
    const result = await api(editing ? `/api/schedule/${editing}` : "/api/schedule", {
      method: editing ? "PUT" : "POST",
      body: {
        node: form.node.value,
        action: form.kind.value,
        target: form.target.value.trim(),
        permanent: form.permanent.checked,
        sound: form.sound.value,
        scope: form.querySelector("input[name=scope]:checked").value,
        template: form.template.value,
        at: form.at.value,
        days,
        date: once ? form.date.value : "",
        note: form.note.value.trim(),
      },
    });
    await loadSchedule();
    editJob(null);
    scheduleStatus(`${editing ? "Saved" : "Added"}: ${scheduleWhat(result.job)} on ${result.job.node}, ${result.job.days_label} at ${result.job.at}.`);
  } catch (error) {
    $("schedule-error").textContent = error.message;
  }
}

function wireSchedule() {
  const days = $("schedule-days");
  days.replaceChildren(...DAY_LABELS.map((label, index) => {
    const wrap = document.createElement("label");
    wrap.className = "check";
    const box = Object.assign(document.createElement("input"), { type: "checkbox", name: "day", value: String(index) });
    wrap.append(box, document.createTextNode(label));
    return wrap;
  }));
  const form = $("schedule-form");
  form.kind.addEventListener("change", updateScheduleForm);
  form.template.addEventListener("change", updateScheduleForm);
  for (const radio of form.querySelectorAll("input[name=repeat]")) radio.addEventListener("change", updateScheduleForm);
  form.addEventListener("submit", addJob);
  $("schedule-cancel-edit").addEventListener("click", () => editJob(null));
  $("schedule-close").addEventListener("click", () => $("schedule-dialog").close());
}

const dvs_state = { node: "", info: null, mode: "", network: "" };

function dvsNodes() {
  const bridged = new Set(state.boot?.dvswitch || []);
  return state.selected.filter((id) => bridged.has(id));
}

function dvsStatus(text, isError = false) {
  const line = $("dvs-status");
  line.textContent = text;
  line.classList.toggle("form-error", isError);
}

async function openDvswitch() {
  const nodes = dvsNodes();
  const node = nodes.includes(state.local) ? state.local : nodes[0];
  if (!node) return;
  dvs_state.node = node;
  dvs_state.mode = "";
  dvsStatus("");
  $("dvs-title").textContent = `DVSwitch on ${node}`;
  $("dvs-custom").value = "";
  if (!(await loadDvswitch())) return;
  $("dvs-dialog").showModal();
}

async function loadDvswitch() {
  try {
    dvs_state.info = await api(`/api/dvswitch?node=${encodeURIComponent(dvs_state.node)}`);
  } catch (error) {
    if ($("dvs-dialog").open) dvsStatus(error.message, true);
    else showResult("DVSwitch", error.message);
    return false;
  }
  const modes = dvs_state.info.modes.map((mode) => mode.name);
  const live = dvs_state.info.status.mode;
  if (!modes.includes(dvs_state.mode)) dvs_state.mode = modes.includes(live) ? live : modes[0] || "";
  $("dvs-restart").hidden = !dvs_state.info.can_restart;
  renderDvswitch();
  return true;
}

function renderDvsNow() {
  const status = dvs_state.info.status;
  const line = $("dvs-now");
  line.classList.toggle("form-error", !status.online);
  if (!status.online) {
    line.textContent = status.error || "The bridge is not answering.";
    return;
  }
  const parts = [`Mode ${status.mode || "unknown"}`, status.tune ? `tuned to ${status.tune}` : "not tuned"];
  if (status.updated) parts.push(`updated ${new Date(status.updated * 1000).toLocaleTimeString()}`);
  line.textContent = parts.join(" · ");
}

function renderDvswitch() {
  const info = dvs_state.info;
  renderDvsNow();
  const tabs = $("dvs-modes");
  tabs.replaceChildren(...info.modes.map((mode) => {
    const button = actionButton(mode.name === info.status.mode ? `${mode.name} (live)` : mode.name, () => {
      dvs_state.mode = mode.name;
      dvs_state.network = "";
      renderDvswitch();
    });
    button.classList.toggle("on", mode.name === dvs_state.mode);
    return button;
  }));
  const mode = info.modes.find((item) => item.name === dvs_state.mode);
  const switchBtn = $("dvs-switch");
  switchBtn.hidden = !mode || mode.name === info.status.mode;
  switchBtn.textContent = mode ? `Switch to ${mode.name} without tuning` : "Switch mode";
  const networks = [...new Set((mode?.talkgroups || []).map((tg) => tg.network).filter(Boolean))];
  $("dvs-net-wrap").hidden = networks.length < 2;
  if (!networks.includes(dvs_state.network)) dvs_state.network = "";
  $("dvs-net").replaceChildren(new Option("All", ""), ...networks.map((name) => new Option(name, name)));
  $("dvs-net").value = dvs_state.network;
  renderDvsList(mode);
}

function renderDvsList(mode) {
  const list = $("dvs-list");
  if (!dvs_state.info.modes.length) {
    list.replaceChildren(Object.assign(document.createElement("p"), {
      className: "muted small",
      textContent: "No talkgroups are set up. Add [mode ...] sections to dvswitch.ini.",
    }));
    return;
  }
  const rows = (mode?.talkgroups || []).filter((tg) => !dvs_state.network || tg.network === dvs_state.network);
  if (!rows.length) {
    list.replaceChildren(Object.assign(document.createElement("p"), { className: "muted small", textContent: `No ${mode.name} talkgroups listed.` }));
    return;
  }
  const status = dvs_state.info.status;
  const table = document.createElement("table");
  table.className = "heard";
  for (const tg of rows) {
    const row = table.insertRow();
    const label = tg.network ? `${tg.alias} (${tg.network})` : tg.alias;
    if (mode.name === status.mode && (status.tune === label || (tg.target && status.tune === tg.target))) row.className = "dvs-current";
    row.insertCell().textContent = tg.alias;
    row.insertCell().textContent = tg.network || tg.target;
    row.insertCell().append(smallButton("Tune", () => dvsTune({ id: tg.id }, tg.alias)));
  }
  list.replaceChildren(table);
}

async function dvsTune(choice, label) {
  const mode = dvs_state.mode;
  const switching = mode !== dvs_state.info.status.mode;
  dvsStatus(switching ? `Switching to ${mode} and tuning ${label}…` : `Tuning ${label}…`);
  try {
    const result = await api("/api/dvswitch/tune", { method: "POST", body: { node: dvs_state.node, mode, ...choice } });
    dvs_state.info.status = result.status;
    renderDvswitch();
    dvsStatus(`Sent ${label} to the bridge.`);
  } catch (error) {
    dvsStatus(error.message, true);
  }
}

async function dvsSwitchMode() {
  const mode = dvs_state.mode;
  dvsStatus(`Switching to ${mode}…`);
  try {
    const result = await api("/api/dvswitch/mode", { method: "POST", body: { node: dvs_state.node, mode } });
    dvs_state.info.status = result.status;
    renderDvswitch();
    dvsStatus(`Switched to ${mode}.`);
  } catch (error) {
    dvsStatus(error.message, true);
  }
}

async function dvsRestart() {
  if (!confirm("Restart MMDVM_Bridge and Analog_Bridge? Digital audio drops for a few seconds.")) return;
  dvsStatus("Restarting the bridge services…");
  try {
    const result = await api("/api/dvswitch/restart", { method: "POST", body: { node: dvs_state.node } });
    dvsStatus(result.detail);
    setTimeout(loadDvswitch, 3000);
  } catch (error) {
    dvsStatus(error.message, true);
  }
}

function wireDvswitch() {
  $("dvs-close").addEventListener("click", () => $("dvs-dialog").close());
  $("dvs-refresh").addEventListener("click", async () => {
    if (await loadDvswitch()) dvsStatus("");
  });
  $("dvs-switch").addEventListener("click", dvsSwitchMode);
  $("dvs-restart").addEventListener("click", dvsRestart);
  $("dvs-net").addEventListener("change", (event) => {
    dvs_state.network = event.target.value;
    renderDvsList(dvs_state.info.modes.find((item) => item.name === dvs_state.mode));
  });
  const custom = () => {
    const value = $("dvs-custom").value.trim();
    if (!value) {
      dvsStatus("Type a talkgroup or reflector first.", true);
      return;
    }
    dvsTune({ custom: value }, value);
  };
  $("dvs-custom-btn").addEventListener("click", custom);
  $("dvs-custom").addEventListener("keydown", (event) => {
    if (event.key === "Enter") custom();
  });
}

const system_state = { tab: "server", rows: [] };

function bytesLabel(value) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function durationLabel(seconds) {
  if (seconds == null) return "";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function sysCard(title, ...children) {
  const card = document.createElement("div");
  card.className = "sys-card";
  const heading = document.createElement("h3");
  heading.textContent = title;
  card.append(heading, ...children);
  return card;
}

function sysRows(pairs) {
  const list = document.createElement("dl");
  for (const [label, value] of pairs) {
    if (value === "" || value == null) continue;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  }
  return list;
}

function usageBar(label, used, total) {
  const wrap = document.createElement("div");
  wrap.className = "usage";
  const pct = total ? Math.round((used / total) * 100) : 0;
  const text = document.createElement("div");
  text.className = "usage-text";
  text.textContent = `${label}: ${bytesLabel(used)} of ${bytesLabel(total)} (${pct}%)`;
  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("span");
  fill.style.width = `${pct}%`;
  fill.className = pct >= 90 ? "hot" : pct >= 75 ? "warm" : "";
  bar.append(fill);
  wrap.append(text, bar);
  return wrap;
}

function renderSystem(info) {
  const grid = $("system-grid");
  const temp = info.temp_c == null ? "" : `${info.temp_c} °C / ${Math.round(info.temp_c * 9 / 5 + 32)} °F`;
  const host = sysCard("Host", sysRows([
    ["Name", info.hostname],
    ["OS", info.os],
    ["Kernel", `${info.kernel} (${info.arch})`],
    ["CPU", `${info.cpu} · ${info.cores} cores`],
    ["CPU temp", temp],
    ["Uptime", durationLabel(info.uptime)],
    ["Load", info.load.join(", ")],
  ]));
  const storage = sysCard("Memory and disk", usageBar("Memory", info.memory.used, info.memory.total));
  if (info.memory.swap_total) storage.append(usageBar("Swap", info.memory.swap_used, info.memory.swap_total));
  for (const disk of info.disks) storage.append(usageBar(disk.mount, disk.used, disk.total));
  const services = sysCard("Services");
  const serviceList = document.createElement("ul");
  serviceList.className = "services";
  for (const service of info.services) {
    const item = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `svc ${service.state === "active" ? "up" : "down"}`;
    item.append(dot, `${service.name} `, Object.assign(document.createElement("span"), { className: "muted", textContent: service.state }));
    serviceList.append(item);
  }
  services.append(serviceList);
  const network = sysCard("Network", sysRows(info.addresses.map((link) => [`${link.name} (${link.state.toLowerCase()})`, link.addrs.join(", ") || "no address"])));
  const software = sysCard("Software", sysRows(info.packages.map((pkg) => [pkg.name, pkg.version])));
  const asterisk = sysCard("Asterisk");
  const pre = document.createElement("pre");
  pre.className = "sys-pre";
  pre.textContent = [info.asterisk.version, info.asterisk.uptime].filter(Boolean).join("\n\n");
  asterisk.append(pre);
  grid.replaceChildren(host, storage, services, network, software, asterisk);
}

async function loadSystem() {
  $("system-status").textContent = "Loading…";
  try {
    const info = await api("/api/system");
    renderSystem(info);
    $("system-title").textContent = `System · ${info.hostname}`;
    const nodes = $("db-node");
    if (!nodes.options.length) nodes.replaceChildren(...info.nodes.map((id) => new Option(id, id, false, id === state.local)));
    $("system-status").textContent = `Updated ${new Date().toLocaleTimeString()}.`;
  } catch (error) {
    $("system-status").textContent = error.message;
  }
}

async function loadDatabase() {
  const node = $("db-node").value;
  if (!node) return;
  $("db-count").textContent = "Loading…";
  try {
    const data = await api(`/api/system/database?node=${encodeURIComponent(node)}`);
    system_state.rows = data.rows;
    renderDatabase();
  } catch (error) {
    $("db-count").textContent = error.message;
  }
}

function renderDatabase() {
  const words = $("db-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = system_state.rows.filter((row) => {
    const hay = `${row.key} ${row.value}`.toLowerCase();
    return words.every((word) => hay.includes(word));
  });
  $("db-count").textContent = `${rows.length} of ${system_state.rows.length} entries. Read-only.`;
  const table = document.createElement("table");
  table.className = "heard";
  const head = table.createTHead().insertRow();
  for (const label of ["Key", "Value"]) head.append(Object.assign(document.createElement("th"), { textContent: label }));
  const body = table.createTBody();
  for (const row of rows.slice(0, 1000)) {
    const line = body.insertRow();
    line.insertCell().textContent = row.key;
    line.insertCell().textContent = row.value;
  }
  $("db-table").replaceChildren(table);
}

const actions_state = { items: [], more: false, keepDays: 0 };
const ACTIONS_PAGE = 200;

async function loadActions(older = false) {
  const before = older && actions_state.items.length ? actions_state.items[actions_state.items.length - 1].at : null;
  $("actions-count").textContent = "Loading…";
  try {
    const data = await api(`/api/actions?limit=${ACTIONS_PAGE}${before ? `&before=${before}` : ""}`);
    actions_state.items = older ? actions_state.items.concat(data.items) : data.items;
    actions_state.more = data.items.length === ACTIONS_PAGE;
    actions_state.keepDays = data.keep_days;
    renderActions();
  } catch (error) {
    $("actions-count").textContent = error.message;
  }
}

function renderActions() {
  const words = $("actions-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = actions_state.items.filter((row) => {
    const hay = `${row.user} ${row.action} ${row.node} ${row.target} ${row.detail} ${row.peer}`.toLowerCase();
    return words.every((word) => hay.includes(word));
  });
  const shown = `${rows.length} of ${actions_state.items.length} loaded entries.`;
  $("actions-count").textContent = `${shown} Entries are kept for ${actions_state.keepDays} days.`;
  $("actions-older").hidden = !actions_state.more;
  const table = document.createElement("table");
  table.className = "heard";
  const head = table.createTHead().insertRow();
  for (const label of ["When", "User", "Action", "Node", "Target", "Detail", "From"]) {
    head.append(Object.assign(document.createElement("th"), { textContent: label }));
  }
  const body = table.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    line.insertCell().textContent = whenLabel(row.at);
    line.insertCell().textContent = row.user || "—";
    line.insertCell().textContent = row.action;
    line.insertCell().textContent = row.node;
    line.insertCell().textContent = row.target;
    line.insertCell().textContent = row.detail;
    line.insertCell().textContent = row.peer;
  }
  $("actions-table").replaceChildren(table);
}

function systemTab(name) {
  system_state.tab = name;
  for (const button of document.querySelectorAll("#system-dialog [data-systab]")) {
    button.classList.toggle("on", button.dataset.systab === name);
  }
  for (const panel of document.querySelectorAll("#system-dialog [data-syspanel]")) {
    panel.hidden = panel.dataset.syspanel !== name;
  }
  if (name === "database") loadDatabase();
  if (name === "actions") loadActions();
}

function openSystem() {
  $("system-db-tab").hidden = !admin();
  $("system-actions-tab").hidden = !admin();
  systemTab("server");
  $("system-dialog").showModal();
  loadSystem();
}

function wireSystem() {
  $("system-btn").addEventListener("click", openSystem);
  $("system-close").addEventListener("click", () => $("system-dialog").close());
  $("system-refresh").addEventListener("click", () => {
    if (system_state.tab === "database") loadDatabase();
    else if (system_state.tab === "actions") loadActions();
    else loadSystem();
  });
  $("actions-filter").addEventListener("input", renderActions);
  $("actions-older").addEventListener("click", () => loadActions(true));
  for (const button of document.querySelectorAll("#system-dialog [data-systab]")) {
    button.addEventListener("click", () => systemTab(button.dataset.systab));
  }
  $("db-node").addEventListener("change", loadDatabase);
  $("db-filter").addEventListener("input", renderDatabase);
}

const ADMIN_CONFIRM = {
  reload: "Reload the Asterisk configuration?",
  rpt_restart: "Restart app_rpt? Every link drops for a few seconds.",
  asterisk_restart: "Restart Asterisk? Links will be down for about 20 seconds.",
  reboot: "Reboot the server? The node and this dashboard go offline for a minute or two.",
  poweroff: "Shut the server down? It will stay off until someone turns it back on by hand.",
};

const admin_state = { file: null };

const FILE_HINTS = {
  "rpt.conf": "Changes take effect after Restart app_rpt.",
  "iax.conf": "Changes take effect after Reload Asterisk config.",
  "extensions.conf": "Changes take effect after Reload Asterisk config.",
  "asdb.txt": "Names for private nodes and nodes missing from the AllStar directory, one per line: node|callsign|description|location. They apply as soon as you save.",
  "node_info.ini": "Settings for the node status block: the nodes to update, weather code and location, temperature unit, and the alert source. A status update runs as soon as you save.",
};

function nextStep(name, applied) {
  if (applied) return applied;
  const action = name === "rpt.conf" ? "Restart app_rpt" : "Reload Asterisk config";
  return `Use "${action}" on the Node & server tab to apply it.`;
}

function adminStatus(text, isError = false) {
  const line = $("admin-status");
  line.textContent = text;
  line.classList.toggle("form-error", isError);
}

function adminPassword() {
  const value = $("admin-password").value;
  if (!value) {
    adminStatus("Enter your password first.", true);
    $("admin-password").focus();
  }
  return value;
}

function adminTab(name) {
  for (const button of document.querySelectorAll("#admin-dialog .tabs button")) {
    button.classList.toggle("on", button.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll("#admin-dialog [data-panel]")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  adminStatus("");
  if (name === "log") loadAdminLog();
  if (name === "files" && !admin_state.file) loadAdminFile($("admin-file").value);
}

async function openAdmin() {
  let info;
  try {
    info = await api("/api/admin");
  } catch (error) {
    showResult("Admin", error.message);
    return;
  }
  const helper = $("admin-helper");
  helper.hidden = info.helper_ok;
  helper.textContent = "The root helper is not installed, so saving files, reboot, and shutdown will fail. See deploy/smelite-admin.";
  const nodes = $("admin-node");
  nodes.replaceChildren(...info.nodes.map((id) => new Option(id, id, false, id === state.local)));
  const files = $("admin-file");
  files.replaceChildren();
  for (const [group, label] of [["asterisk", "Asterisk"], ["sm-elite", "SM-Elite"]]) {
    const members = info.files.filter((file) => (file.group || "asterisk") === group);
    if (!members.length) continue;
    const optgroup = document.createElement("optgroup");
    optgroup.label = label;
    optgroup.append(...members.map((file) => new Option(file.name, file.name)));
    files.append(optgroup);
  }
  admin_state.file = null;
  $("settings-file").value = "";
  $("settings-preview").hidden = true;
  adminStatus("");
  adminTab("system");
  $("admin-dialog").showModal();
}

async function runAdminAction(action) {
  const password = adminPassword();
  if (!password || !confirm(ADMIN_CONFIRM[action])) return;
  adminStatus("Working…");
  try {
    const result = await api("/api/admin/action", { method: "POST", body: { action, password, node: $("admin-node").value } });
    adminStatus(result.detail || "Done.");
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function loadAdminFile(name) {
  if (!name) return;
  if (admin_state.file && admin_state.dirty && !confirm("Discard your unsaved changes?")) {
    $("admin-file").value = admin_state.file.name;
    return;
  }
  try {
    admin_state.file = await api(`/api/admin/file?name=${encodeURIComponent(name)}`);
    admin_state.dirty = false;
    $("admin-text").value = admin_state.file.text;
    $("admin-file-hint").textContent = `${FILE_HINTS[name] || ""} Saving keeps a backup of the previous version in /var/backups/smelite.`.trim();
    if (!$("admin-backups").hidden) loadBackups();
    adminStatus(`Loaded ${name}.`);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function saveAdminFile() {
  const file = admin_state.file;
  if (!file) return;
  const text = $("admin-text").value;
  if (text === file.text) {
    adminStatus("No changes to save.");
    return;
  }
  const password = adminPassword();
  if (!password || !confirm(`Save ${file.name}? The current version is backed up first.`)) return;
  try {
    const saved = await api("/api/admin/file", {
      method: "POST",
      body: { name: file.name, text, sha256: file.sha256, password },
    });
    admin_state.file = saved;
    admin_state.dirty = false;
    $("admin-text").value = saved.text;
    if (!$("admin-backups").hidden) loadBackups();
    adminStatus(`Saved ${file.name}. The old version was backed up to ${saved.backup}. ${nextStep(file.name, saved.applied)}`);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function toggleBackups() {
  const panel = $("admin-backups");
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  await loadBackups();
}

async function loadBackups() {
  const file = admin_state.file;
  if (!file) return;
  $("admin-diff").hidden = true;
  try {
    const data = await api(`/api/admin/backups?name=${encodeURIComponent(file.name)}`);
    renderBackups(data.backups);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

function renderBackups(backups) {
  const list = $("admin-backup-list");
  const file = admin_state.file;
  if (!backups.length) {
    list.replaceChildren(Object.assign(document.createElement("p"), { className: "hint", textContent: `No backups of ${file.name} yet. One is made every time it is saved here.` }));
    return;
  }
  const table = document.createElement("table");
  table.className = "heard";
  for (const backup of backups) {
    const row = table.insertRow();
    row.insertCell().textContent = new Date(backup.when).toLocaleString();
    row.insertCell().textContent = bytesLabel(backup.size);
    const actions = row.insertCell();
    actions.className = "row-actions";
    actions.append(
      smallButton("Compare", () => compareBackup(backup)),
      smallButton("Restore", () => restoreBackup(backup)),
    );
  }
  list.replaceChildren(table);
}

async function compareBackup(backup) {
  const file = admin_state.file;
  try {
    const data = await api(`/api/admin/backup?name=${encodeURIComponent(file.name)}&stamp=${encodeURIComponent(backup.stamp)}`);
    const pre = $("admin-diff");
    pre.hidden = false;
    if (data.same) {
      pre.textContent = "This backup is identical to the file as it is now.";
      return;
    }
    pre.replaceChildren(...data.diff.split("\n").map((line) => {
      const span = document.createElement("span");
      span.textContent = `${line}\n`;
      if (line.startsWith("+") && !line.startsWith("+++")) span.className = "add";
      else if (line.startsWith("-") && !line.startsWith("---")) span.className = "del";
      else if (line.startsWith("@@")) span.className = "hunk";
      return span;
    }));
    adminStatus(`Lines marked + are in the backup from ${new Date(backup.when).toLocaleString()}; lines marked - are in the file now.`);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function restoreBackup(backup) {
  const file = admin_state.file;
  if (admin_state.dirty) {
    adminStatus("Save or reload your edits before restoring a backup.", true);
    return;
  }
  const password = adminPassword();
  const when = new Date(backup.when).toLocaleString();
  if (!password || !confirm(`Restore ${file.name} to the version from ${when}? The current version is backed up first.`)) return;
  try {
    const restored = await api("/api/admin/restore", {
      method: "POST",
      body: { name: file.name, stamp: backup.stamp, sha256: file.sha256, password },
    });
    admin_state.file = restored;
    admin_state.dirty = false;
    $("admin-text").value = restored.text;
    $("admin-diff").hidden = true;
    renderBackups(restored.backups);
    adminStatus(`Restored ${file.name} from ${when}. ${nextStep(file.name, restored.applied)}`);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function loadAdminLog() {
  try {
    const data = await api("/api/admin/log?lines=300");
    $("admin-log-file").textContent = `Last 300 lines of ${data.file}`;
    const pre = $("admin-log");
    pre.textContent = data.text;
    pre.scrollTop = pre.scrollHeight;
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function downloadSettings() {
  const password = adminPassword();
  if (!password) return;
  adminStatus("Building the backup…");
  try {
    const response = await fetch(BASE + "/api/admin/settings-backup", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `request failed (${response.status})`);
    }
    const blob = await response.blob();
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "sm-elite-settings.tar.gz";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    adminStatus(`Downloaded ${name}. It holds passwords, so keep it private.`);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

function backupForm(fields) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  form.append("file", $("settings-file").files[0]);
  return form;
}

function backupPartDetail(info, id) {
  if (id === "config") return info.config.join(", ");
  if (id === "accounts") return `${info.users} users (${info.admins} admin), ${info.jobs} scheduled actions`;
  return info.data.filter((name) => name === "history.db" || name === "actions.db").join(", ");
}

async function inspectSettings() {
  $("settings-preview").hidden = true;
  if (!$("settings-file").files.length) return;
  adminStatus("Reading the backup…");
  try {
    const info = await api("/api/admin/settings-inspect", { method: "POST", body: backupForm({}) });
    const when = info.created ? new Date(info.created).toLocaleString() : "an unknown date";
    $("settings-summary").textContent = `Made on ${info.host || "an unknown host"} by SM-Elite ${info.version || "(unknown version)"}, ${when}.`;
    $("settings-parts").replaceChildren(...info.groups.map((group) => {
      const label = document.createElement("label");
      label.className = "check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = group.id;
      box.checked = group.id !== "history";
      box.dataset.label = group.label;
      const text = document.createElement("span");
      const detail = document.createElement("span");
      detail.className = "muted";
      detail.textContent = ` (${backupPartDetail(info, group.id)})`;
      text.append(group.label, detail);
      label.append(box, text);
      return label;
    }));
    $("settings-preview").hidden = false;
    adminStatus("");
  } catch (error) {
    adminStatus(error.message, true);
  }
}

async function waitForRestart() {
  await new Promise((resolve) => setTimeout(resolve, 5000));
  for (let tries = 0; tries < 60; tries += 1) {
    try {
      if ((await fetch(BASE + "/api/health", { cache: "no-store" })).ok) break;
    } catch {
      // still restarting
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  location.reload();
}

async function restoreSettings() {
  const boxes = [...document.querySelectorAll("#settings-parts input:checked")];
  if (!boxes.length) {
    adminStatus("Pick what to restore.", true);
    return;
  }
  const password = adminPassword();
  const labels = boxes.map((box) => box.dataset.label.toLowerCase());
  if (!password || !confirm(`Replace the ${labels.join(", ")} on this node with the copies from the backup?`)) return;
  adminStatus("Restoring…");
  try {
    const result = await api("/api/admin/settings-restore", {
      method: "POST",
      body: backupForm({ password, parts: boxes.map((box) => box.value).join(",") }),
    });
    let message = `Restored ${result.restored.join(", ")}.`;
    if (result.restart) message += ` ${result.restart}. This page reloads when SM-Elite is back.`;
    else if (result.logged_out) message += " Everyone was logged out; log in with an account from the backup.";
    adminStatus(message);
    $("admin-password").value = "";
    if (result.restart) waitForRestart();
    else if (result.logged_out) setTimeout(() => location.reload(), 4000);
  } catch (error) {
    adminStatus(error.message, true);
  }
}

function wireAdmin() {
  $("admin-btn").addEventListener("click", openAdmin);
  $("settings-download").addEventListener("click", downloadSettings);
  $("settings-file").addEventListener("change", inspectSettings);
  $("settings-restore").addEventListener("click", restoreSettings);
  $("admin-close").addEventListener("click", () => {
    if (admin_state.dirty && !confirm("Close and discard your unsaved changes?")) return;
    $("admin-password").value = "";
    $("admin-dialog").close();
  });
  for (const button of document.querySelectorAll("#admin-dialog .tabs button")) {
    button.addEventListener("click", () => adminTab(button.dataset.tab));
  }
  for (const button of document.querySelectorAll("#admin-dialog [data-action]")) {
    button.addEventListener("click", () => runAdminAction(button.dataset.action));
  }
  $("admin-file").addEventListener("change", (event) => loadAdminFile(event.target.value));
  $("admin-file-reload").addEventListener("click", () => {
    admin_state.dirty = false;
    loadAdminFile($("admin-file").value);
  });
  $("admin-file-save").addEventListener("click", saveAdminFile);
  $("admin-file-backups").addEventListener("click", toggleBackups);
  $("admin-text").addEventListener("input", () => { admin_state.dirty = true; });
  $("admin-log-refresh").addEventListener("click", loadAdminLog);
}

async function reloadIfSessionKept(errorId) {
  const check = await api("/api/bootstrap");
  if (check.user) {
    location.reload();
    return;
  }
  $(errorId).textContent = `The password was accepted, but this browser did not keep the login cookie. Allow cookies for ${location.host} (in Brave, turn Shields off for this site) and try again.`;
}

async function openUsers() {
  const data = await api("/api/users");
  const list = $("user-list");
  list.replaceChildren();
  users_state.users = data.users;
  for (const user of data.users) {
    const row = document.createElement("div");
    row.className = "user-row";
    const label = document.createElement("span");
    const limit = user.nodes ? user.nodes.join(", ") : "all nodes";
    label.textContent = `${user.name} · ${user.role} · ${limit}`;
    const buttons = document.createElement("span");
    buttons.className = "row-actions";
    buttons.append(
      smallButton("Edit", () => editUser(user)),
      smallButton("Remove", async () => {
        if (!confirm(`Remove ${user.name}? Their scheduled actions stop running.`)) return;
        try {
          await api(`/api/users/${encodeURIComponent(user.name)}`, { method: "DELETE" });
          if (users_state.editing === user.name) editUser(null);
          await openUsers();
        } catch (error) {
          $("users-error").textContent = error.message;
        }
      }),
    );
    row.append(label, buttons);
    list.append(row);
  }
  if (!$("users-dialog").open) {
    editUser(null);
    $("users-dialog").showModal();
  }
}

async function boot() {
  if (localStorage.getItem("smelite-touch") === "1") document.body.classList.add("touch");
  loadDisplay();
  state.boot = await api("/api/bootstrap");
  const hash = location.hash.replace("#", "");
  const known = new Set((state.boot.nodes || []).map((node) => node.id));
  const fromHash = hash.split(",").map((part) => part.trim()).filter((id) => known.has(id));
  state.selected = (fromHash.length ? fromHash : (state.boot.nodes || []).map((node) => node.id)).sort(compareNodes);
  state.local = state.selected[0] || "";
  state.favFile = (state.boot.favorites || [])[0] || "favorites.ini";
  renderChrome();
  wireDialogs();
  wireDisplay();
  wireThemes();
  wireFinder();
  wireAccess();
  wireAdmin();
  wireSounds();
  wireSchedule();
  wireDvswitch();
  wireSystem();
  wireAlerts();
  if (state.boot.needs_setup) $("setup-dialog").showModal();
  connectSocket();
  if (state.favFile) await loadFavorites(state.favFile);
  await loadHeard();
  refreshHealth();
  setInterval(refreshHealth, 15000);
  setInterval(() => {
    if (state.selected.some((id) => (state.heardLive.get(id) || []).length)) renderHeard();
  }, 1000);
}

boot().catch((error) => {
  $("welcome").textContent = error.message;
});
