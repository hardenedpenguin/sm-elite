// Themes: a set of colors written into the page's CSS variables.
// Loaded in <head> so the saved theme is on before the page paints; the dialog is wired by app.js.

const THEME_COLORS = [
  { key: "bg", label: "Page background", group: "page" },
  { key: "bg-raise", label: "Panels and dialogs", group: "page" },
  { key: "line", label: "Borders", group: "page" },
  { key: "text", label: "Text", group: "page" },
  { key: "muted", label: "Secondary text", group: "page" },
  { key: "accent", label: "Buttons and links", group: "page" },
  { key: "accent-ink", label: "Button text", group: "page" },
  { key: "local", label: "Local", group: "status" },
  { key: "network", label: "Network", group: "status" },
  { key: "telemetry", label: "Telemetry", group: "status" },
  { key: "playback", label: "Playback", group: "status" },
  { key: "linked", label: "Connected", group: "status" },
  { key: "heat", label: "Favorite activity", group: "status" },
  { key: "idle", label: "Idle", group: "status" },
  { key: "danger", label: "Warnings and errors", group: "status" },
];

const STOCK_STATUS = {
  local: "#e39b2b", network: "#3db7e8", telemetry: "#b79af0", playback: "#ef7aa8",
  linked: "#3dbe86", heat: "#c4473a", idle: "#6d8294", danger: "#e15d5d",
};

const BUILT_IN_THEMES = [
  {
    id: "dark", label: "Dark",
    colors: {
      bg: "#10161c", "bg-raise": "#18222c", line: "#2c3c4c", text: "#e7eef4", muted: "#8ea3b5",
      accent: "#7dcec4", "accent-ink": "#08312e", ...STOCK_STATUS,
    },
  },
  {
    id: "light", label: "Light",
    colors: {
      bg: "#f4f7f8", "bg-raise": "#ffffff", line: "#d5e0e6", text: "#1c2a33", muted: "#5d7382",
      accent: "#0f766e", "accent-ink": "#f3fffd", ...STOCK_STATUS,
    },
  },
  {
    id: "blue", label: "Blue",
    colors: {
      bg: "#172b6b", "bg-raise": "#1e3a8a", line: "#3b5fc4", text: "#ffffff", muted: "#bfd0f5",
      accent: "#60a5fa", "accent-ink": "#0b1b4a", local: "#f59e0b", network: "#22d3ee", telemetry: "#c4b5fd",
      playback: "#f9a8d4", linked: "#34d399", heat: "#fb7185", idle: "#93a5c8", danger: "#f87171",
    },
  },
  {
    id: "green", label: "Green",
    colors: {
      bg: "#053b2d", "bg-raise": "#065f46", line: "#0b8a68", text: "#ffffff", muted: "#a7e8cf",
      accent: "#34d399", "accent-ink": "#022c22", local: "#f59e0b", network: "#38bdf8", telemetry: "#c4b5fd",
      playback: "#f9a8d4", linked: "#bef264", heat: "#fb923c", idle: "#8fb8a8", danger: "#f87171",
    },
  },
  {
    id: "seafoam", label: "Seafoam",
    colors: {
      bg: "#f5f5dc", "bg-raise": "#e8f4f0", line: "#7fb3a3", text: "#2d5a4a", muted: "#557a6c",
      accent: "#2d6e5c", "accent-ink": "#f5f5dc", local: "#b86200", network: "#1d6fa0", telemetry: "#7d3c98",
      playback: "#b0306a", linked: "#1e7f46", heat: "#c0392b", idle: "#7f9a90", danger: "#a93226",
    },
  },
  {
    id: "neon-purple", label: "Neon purple",
    colors: {
      bg: "#000000", "bg-raise": "#1a0a2e", line: "#8a2be2", text: "#e0b0ff", muted: "#a884c8",
      accent: "#00ffff", "accent-ink": "#120020", local: "#ffaa00", network: "#00d4ff", telemetry: "#ff66ff",
      playback: "#ff77cc", linked: "#00ff88", heat: "#ff5500", idle: "#6b5a80", danger: "#ff0066",
    },
  },
];

const THEME_KEY = "smelite-theme";
const CUSTOM_THEMES_KEY = "smelite-custom-themes";
const SITE_THEME_KEY = "smelite-site-theme";
const HEX_COLOR = /^#[0-9a-f]{6}$/;

function validColors(colors) {
  if (!colors || typeof colors !== "object") return null;
  const clean = {};
  for (const { key } of THEME_COLORS) {
    const value = String(colors[key] || "").toLowerCase();
    if (!HEX_COLOR.test(value)) return null;
    clean[key] = value;
  }
  return clean;
}

function readJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function customThemes() {
  const list = readJson(CUSTOM_THEMES_KEY, []);
  if (!Array.isArray(list)) return [];
  return list
    .map((item) => ({ id: String(item?.id || ""), label: String(item?.label || "Custom"), colors: validColors(item?.colors), custom: true }))
    .filter((item) => item.id && item.colors);
}

function saveCustomThemes(list) {
  localStorage.setItem(CUSTOM_THEMES_KEY, JSON.stringify(list.map(({ id, label, colors }) => ({ id, label, colors }))));
}

function siteTheme(site = readJson(SITE_THEME_KEY, null)) {
  if (site?.id === "custom") {
    const colors = validColors(site.colors);
    if (colors) return { id: "site", label: site.label || "Custom", colors };
  }
  return BUILT_IN_THEMES.find((theme) => theme.id === site?.id) || BUILT_IN_THEMES[0];
}

function themeById(id) {
  if (!id) return siteTheme();
  return BUILT_IN_THEMES.find((theme) => theme.id === id) || customThemes().find((theme) => theme.id === id) || null;
}

function chosenTheme() {
  return themeById(localStorage.getItem(THEME_KEY) || "") || siteTheme();
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((at) => {
    const channel = parseInt(hex.slice(at, at + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

function applyTheme(theme) {
  const root = document.documentElement;
  for (const { key } of THEME_COLORS) root.style.setProperty(`--${key}`, theme.colors[key]);
  const scheme = luminance(theme.colors.bg) > 0.4 ? "light" : "dark";
  root.dataset.theme = scheme;
  root.style.setProperty("color-scheme", scheme);
  root.style.setProperty("--shadow", scheme === "light" ? "0 8px 24px rgba(28, 42, 51, 0.08)" : "0 10px 30px rgba(0, 0, 0, 0.25)");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme.colors.bg);
}

applyTheme(chosenTheme());

// The Theme dialog. Uses $, api, and state from app.js.

const theme_state = { editing: null, draft: null };

function rememberSiteTheme(site) {
  if (site) localStorage.setItem(SITE_THEME_KEY, JSON.stringify(site));
  else localStorage.removeItem(SITE_THEME_KEY);
  if (!localStorage.getItem(THEME_KEY)) applyTheme(siteTheme(site));
}

function themeStatus(message, error = false) {
  const line = $("theme-status");
  line.textContent = message;
  line.classList.toggle("form-error", error);
}

function themePreview(colors) {
  const preview = document.createElement("span");
  preview.className = "theme-preview";
  preview.setAttribute("aria-hidden", "true");
  preview.style.background = colors.bg;
  preview.style.borderColor = colors.line;
  const panel = document.createElement("span");
  panel.className = "theme-preview-panel";
  panel.style.background = colors["bg-raise"];
  panel.style.color = colors.text;
  panel.textContent = "Aa";
  const dots = document.createElement("span");
  dots.className = "theme-preview-dots";
  for (const key of ["accent", "local", "network", "linked"]) {
    const dot = document.createElement("i");
    dot.style.background = colors[key];
    dots.append(dot);
  }
  preview.append(panel, dots);
  return preview;
}

function themeCard(theme, choice, label) {
  const card = document.createElement("div");
  card.className = "theme-card";
  card.classList.toggle("on", (localStorage.getItem(THEME_KEY) || "") === choice);
  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "theme-pick";
  const name = document.createElement("span");
  name.className = "theme-name";
  name.textContent = label;
  pick.append(themePreview(theme.colors), name);
  pick.addEventListener("click", () => {
    if (choice) localStorage.setItem(THEME_KEY, choice);
    else localStorage.removeItem(THEME_KEY);
    applyTheme(theme);
    renderThemes();
    themeStatus(choice ? `Using ${label} in this browser.` : "This browser follows the site default.");
  });
  card.append(pick);
  if (theme.custom) {
    const tools = document.createElement("span");
    tools.className = "row-actions";
    tools.append(
      smallButton("Edit", () => editTheme(theme)),
      smallButton("Export", () => exportTheme(theme)),
      smallButton("Delete", () => deleteTheme(theme)),
    );
    card.append(tools);
  }
  return card;
}

function renderThemes() {
  const site = siteTheme();
  const list = $("theme-list");
  list.replaceChildren(
    themeCard(site, "", `Site default (${site.label})`),
    ...BUILT_IN_THEMES.map((theme) => themeCard(theme, theme.id, theme.label)),
    ...customThemes().map((theme) => themeCard(theme, theme.id, theme.label)),
  );
  const admin = state.boot?.user?.role === "admin";
  $("theme-site").hidden = !admin;
  if (admin) {
    const current = state.boot.site_theme;
    $("theme-site-now").textContent = `Everyone who has not picked a theme sees ${current ? siteTheme(current).label : "Dark (the stock theme)"}.`;
    $("theme-site-clear").disabled = !current;
  }
}

function themeInputs(group) {
  return THEME_COLORS.filter((item) => item.group === group).map((item) => {
    const label = document.createElement("label");
    label.className = "theme-color";
    const input = document.createElement("input");
    input.type = "color";
    input.name = item.key;
    input.addEventListener("input", () => {
      theme_state.draft[item.key] = input.value;
      applyTheme({ colors: theme_state.draft });
      checkContrast();
    });
    label.append(input, ` ${item.label}`);
    return label;
  });
}

function checkContrast() {
  const colors = theme_state.draft;
  const pairs = [
    ["text", "bg", 4.5, "Text on the page background"],
    ["text", "bg-raise", 4.5, "Text on panels"],
    ["muted", "bg-raise", 3, "Secondary text on panels"],
    ["accent-ink", "accent", 4.5, "Button text on buttons"],
    ["accent", "bg-raise", 3, "Buttons and links on panels"],
  ];
  const weak = pairs.filter(([a, b, need]) => contrast(colors[a], colors[b]) < need).map(([, , , label]) => label);
  $("theme-contrast").textContent = weak.length ? `Hard to read: ${weak.join(", ").toLowerCase()}.` : "";
}

function editTheme(theme) {
  const form = $("theme-editor");
  theme_state.editing = theme?.custom ? theme.id : null;
  theme_state.draft = { ...(theme || chosenTheme()).colors };
  form.label.value = theme?.custom ? theme.label : `${(theme || chosenTheme()).label} copy`;
  for (const { key } of THEME_COLORS) form.elements[key].value = theme_state.draft[key];
  $("theme-editor-title").textContent = theme?.custom ? `Edit ${theme.label}` : "New custom theme";
  form.hidden = false;
  applyTheme({ colors: theme_state.draft });
  checkContrast();
  themeStatus("");
  form.scrollIntoView({ block: "nearest" });
}

function closeEditor() {
  $("theme-editor").hidden = true;
  theme_state.editing = null;
  theme_state.draft = null;
  applyTheme(chosenTheme());
}

function addCustomTheme(label, colors, id = null) {
  const list = customThemes();
  const theme = { id: id || `custom-${Date.now().toString(36)}`, label: label.trim().slice(0, 40) || "Custom", colors, custom: true };
  const at = list.findIndex((item) => item.id === theme.id);
  if (at >= 0) list[at] = theme;
  else list.push(theme);
  saveCustomThemes(list);
  localStorage.setItem(THEME_KEY, theme.id);
  return theme;
}

function saveTheme(event) {
  event.preventDefault();
  const theme = addCustomTheme(event.target.label.value, { ...theme_state.draft }, theme_state.editing);
  closeEditor();
  renderThemes();
  themeStatus(`Saved ${theme.label} and switched to it.`);
}

function deleteTheme(theme) {
  if (!confirm(`Delete the theme ${theme.label}?`)) return;
  saveCustomThemes(customThemes().filter((item) => item.id !== theme.id));
  if (localStorage.getItem(THEME_KEY) === theme.id) localStorage.removeItem(THEME_KEY);
  if (theme_state.editing === theme.id) closeEditor();
  applyTheme(chosenTheme());
  renderThemes();
  themeStatus(`Deleted ${theme.label}.`);
}

function exportTheme(theme) {
  const blob = new Blob([JSON.stringify({ format: "sm-elite-theme", version: 1, label: theme.label, colors: theme.colors }, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${theme.label.replace(/[^A-Za-z0-9_-]+/g, "-").toLowerCase() || "theme"}.sm-elite-theme.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

async function importTheme(file) {
  try {
    const data = JSON.parse(await file.text());
    const colors = validColors(data?.colors);
    if (!colors) throw new Error("bad colors");
    const theme = addCustomTheme(String(data.label || file.name.replace(/\..*$/, "")), colors);
    applyTheme(theme);
    renderThemes();
    themeStatus(`Imported ${theme.label} and switched to it.`);
  } catch {
    themeStatus("That file is not an SM-Elite theme.", true);
  }
}

async function setSiteTheme(theme) {
  let body = null;
  if (theme) body = theme.custom || theme.id === "site" ? { id: "custom", label: theme.label, colors: theme.colors } : { id: theme.id };
  try {
    const result = await api("/api/site/theme", { method: "POST", body: { theme: body } });
    state.boot.site_theme = result.site_theme;
    rememberSiteTheme(result.site_theme);
    renderThemes();
    themeStatus(result.site_theme ? `${siteTheme(result.site_theme).label} is now the site default.` : "The site default is the stock dark theme again.");
  } catch (error) {
    themeStatus(error.message, true);
  }
}

function openThemes() {
  closeEditor();
  renderThemes();
  themeStatus("");
  $("theme-dialog").showModal();
}

function wireThemes() {
  rememberSiteTheme(state.boot?.site_theme || null);
  $("theme-colors-page").replaceChildren(...themeInputs("page"));
  $("theme-colors-status").replaceChildren(...themeInputs("status"));
  $("theme-btn").addEventListener("click", openThemes);
  $("theme-close").addEventListener("click", () => {
    closeEditor();
    $("theme-dialog").close();
  });
  $("theme-dialog").addEventListener("close", () => {
    if (theme_state.draft) closeEditor();
  });
  $("theme-new").addEventListener("click", () => editTheme(null));
  $("theme-editor").addEventListener("submit", saveTheme);
  $("theme-editor-cancel").addEventListener("click", closeEditor);
  $("theme-import").addEventListener("click", () => $("theme-import-file").click());
  $("theme-import-file").addEventListener("change", (event) => {
    const [file] = event.target.files;
    event.target.value = "";
    if (file) importTheme(file);
  });
  $("theme-site-set").addEventListener("click", () => setSiteTheme(chosenTheme()));
  $("theme-site-clear").addEventListener("click", () => {
    if (confirm("Go back to the stock dark theme as the site default?")) setSiteTheme(null);
  });
}
