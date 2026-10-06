// SpicyAPI for ComfyUI: the settings panel entries and the price badge on every model node.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const KEY_SETTING = "SpicyAPI.ApiKey";
const COST_SETTING = "SpicyAPI.MaxCostPerRun";
const MASK = "\u2022".repeat(4);
const NODE_PREFIX = "SpicyAPI_";
const MODEL_DEFAULT = "model default";
const UNIT_SHORT = { second: "s", image: "image", run: "run", "1k characters": "1k chars" };

let prices = null;

function toast(severity, summary, detail, life = 6000) {
  try {
    app.extensionManager.toast.add({ severity, summary, detail, life });
  } catch {
    console[severity === "error" ? "error" : "log"](`[SpicyAPI] ${summary}: ${detail ?? ""}`);
  }
}

function setSetting(id, value) {
  try {
    return app.extensionManager.setting.set(id, value);
  } catch {
    return app.ui?.settings?.setSettingValue?.(id, value);
  }
}

function getSetting(id) {
  try {
    return app.extensionManager.setting.get(id);
  } catch {
    return app.ui?.settings?.getSettingValue?.(id);
  }
}

// The settings field only ever keeps a masked marker. The key itself lives in the ComfyUI user
// directory on the server, never in comfy.settings.json and never in a workflow.
function maskedLabel(status) {
  if (!status?.configured) return "";
  const where = status.keySource === "env" ? "from SPICY_API_KEY" : "saved";
  return `${MASK} ${status.keyHint.replace("...", "")} (${where})`;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function onKeyChange(value, previous) {
  // The settings store calls this once on load with no previous value; that is not an edit.
  if (previous === undefined || value === previous) return;
  const text = String(value ?? "").trim();
  if (text.startsWith(MASK)) return;
  if (!text) {
    const response = await api.fetchApi("/spicyapi/api-key", { method: "DELETE" });
    const status = await readJson(response);
    if (status.configured) {
      toast("warn", "SpicyAPI", "The key set through SPICY_API_KEY stays in use.");
      setSetting(KEY_SETTING, maskedLabel(status));
    } else {
      toast("info", "SpicyAPI", "API key removed.");
    }
    return;
  }
  const response = await api.fetchApi("/spicyapi/api-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey: text }),
  });
  const result = await readJson(response);
  if (!response.ok || !result.ok) {
    toast("error", "SpicyAPI key not saved", result.message || `HTTP ${response.status}`);
    // Whatever key was in use before is still in use; show that rather than an empty field.
    setSetting(KEY_SETTING, String(previous ?? "").startsWith(MASK) ? previous : "");
    return;
  }
  setSetting(KEY_SETTING, maskedLabel(result));
  const balance = result.balance?.available;
  toast(
    "success",
    "SpicyAPI connected",
    balance !== undefined ? `Available balance: $${balance}` : "Key saved. It could not be checked right now.",
  );
  const refresh = result.catalogRefresh;
  if (refresh?.restartNeeded) {
    const added = refresh.added?.length ?? 0;
    toast(
      "info",
      "SpicyAPI",
      added > 0
        ? `Restart ComfyUI to load the full model list (${added} more models).`
        : "Restart ComfyUI to load the latest model list.",
      12000,
    );
  }
}

async function onCostChange(value) {
  const amount = Number(value) || 0;
  const response = await api.fetchApi("/spicyapi/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ maxCostPerRun: amount < 0 ? 0 : amount }),
  });
  if (!response.ok) {
    const result = await readJson(response);
    toast("error", "SpicyAPI", result.message || "Could not save the spending limit.");
  }
}

// -- Price badge -----------------------------------------------------------

function formatUsd(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return `$${value}`;
  let text = String(value);
  if (!text.includes(".")) text = `${text}.00`;
  const [whole, fraction] = text.split(".");
  const trimmed = fraction.replace(/0+$/, "");
  return `$${whole}.${trimmed.padEnd(2, "0")}`;
}

function keyValue(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

// Go's url.QueryEscape, which the platform applies to every value in a multi-part key.
function queryEscape(value) {
  return encodeURIComponent(value)
    .replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)
    .replace(/%20/g, "+");
}

// Mirrors pricing.variant_key in Python, which mirrors the platform's own rule.
function variantKey(spec, node) {
  const widgets = Object.fromEntries((node.widgets ?? []).map((w) => [w.name, w.value]));
  const omit = spec.omitDefaults ?? {};
  const parts = [];
  for (const name of [...(spec.fields ?? [])].sort()) {
    let value;
    const target = spec.presence?.[name];
    if (target !== undefined) {
      const connected = (node.inputs ?? []).some(
        (input) => input.link != null && (input.name === target || input.name.startsWith(`${target}.`)),
      );
      value = keyValue(connected);
    } else if (name in widgets && widgets[name] !== MODEL_DEFAULT) {
      const shown = keyValue(widgets[name]);
      value = spec.labels?.[name]?.[shown] ?? shown;
    } else if (spec.defaults && name in spec.defaults) {
      value = spec.defaults[name];
    } else {
      continue;
    }
    if (name in omit && omit[name] === value) continue;
    parts.push([name, value]);
  }
  if (parts.length === 1 && !(parts[0][0] in omit)) return parts[0][1];
  return parts.map(([name, value]) => `${name}=${queryEscape(value)}`).join(";");
}

function priceText(node) {
  const spec = prices?.[node.comfyClass ?? node.type];
  if (!spec) return "";
  const unit = UNIT_SHORT[spec.unit] ?? spec.unit;
  const price = spec.table?.[variantKey(spec, node)];
  if (price !== undefined) return `${spec.approximate ? "~" : ""}${formatUsd(price)}/${unit}`;
  return `from ${formatUsd(spec.from)}/${unit}`;
}

function attachBadge(node) {
  const type = node.comfyClass ?? node.type ?? "";
  if (!type.startsWith(NODE_PREFIX) || node.__spicyBadge) return;
  const Badge = window.LGraphBadge;
  if (!Badge || !Array.isArray(node.badges)) return;
  node.__spicyBadge = true;
  node.badges.push(
    () => new Badge({ text: priceText(node), fgColor: "#E9FF70", bgColor: "#26291c" }),
  );
}

app.registerExtension({
  name: "SpicyAPI.Comfy",
  settings: [
    {
      id: KEY_SETTING,
      category: ["SpicyAPI", "Account", "API key"],
      name: "API key",
      tooltip:
        "Paste a key from spicyapi.ai/console/keys. It is stored in the ComfyUI user folder on " +
        "this machine, never in workflows or image metadata. Clear the field to remove it.",
      type: "text",
      defaultValue: "",
      attrs: { placeholder: "sk-spicy-..." },
      onChange: (value, previous) => {
        onKeyChange(value, previous).catch((error) => toast("error", "SpicyAPI", String(error)));
      },
    },
    {
      id: COST_SETTING,
      category: ["SpicyAPI", "Spending", "Max cost per run"],
      name: "Max cost per run (USD, 0 = no limit)",
      tooltip:
        "A node whose quoted price is above this stops before anything is charged. The quote is " +
        "the most a run can cost.",
      type: "number",
      defaultValue: 0,
      attrs: { min: 0, step: 0.5 },
      onChange: (value) => {
        onCostChange(value).catch((error) => toast("error", "SpicyAPI", String(error)));
      },
    },
  ],

  async setup() {
    try {
      const response = await api.fetchApi("/spicyapi/prices");
      prices = await readJson(response);
      app.graph?.setDirtyCanvas?.(true, true);
    } catch (error) {
      console.warn("[SpicyAPI] price list unavailable", error);
    }
    try {
      const status = await readJson(await api.fetchApi("/spicyapi/status"));
      const shown = String(getSetting(KEY_SETTING) ?? "");
      const expected = maskedLabel(status);
      if (shown !== expected && (status.configured || shown.startsWith(MASK))) {
        await setSetting(KEY_SETTING, expected);
      }
    } catch (error) {
      console.warn("[SpicyAPI] status unavailable", error);
    }
  },

  nodeCreated(node) {
    attachBadge(node);
  },

  loadedGraphNode(node) {
    attachBadge(node);
  },
});
