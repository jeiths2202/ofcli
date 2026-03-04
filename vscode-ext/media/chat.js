// @ts-check
/// <reference lib="dom" />

import { marked } from "marked";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import x86asm from "highlight.js/lib/languages/x86asm";
import sql from "highlight.js/lib/languages/sql";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import xml from "highlight.js/lib/languages/xml";
import ini from "highlight.js/lib/languages/ini";
import pgsql from "highlight.js/lib/languages/pgsql";

hljs.registerLanguage("jcl", x86asm);
hljs.registerLanguage("cobol", x86asm); // no native COBOL; x86asm as fallback
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("python", python);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("ini", ini);
hljs.registerLanguage("pgsql", pgsql);

// Configure marked with highlight.js
marked.setOptions({
  breaks: true,
  gfm: true,
  renderer: (() => {
    const renderer = new marked.Renderer();
    renderer.code = function ({ text, lang }) {
      const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      let highlighted;
      try {
        highlighted = hljs.highlight(text, { language }).value;
      } catch {
        highlighted = escapeHtml(text);
      }
      return `<pre><code class="hljs language-${escapeHtml(language)}">${highlighted}</code></pre>`;
    };
    return renderer;
  })(),
});

// DOMPurify config — allow safe HTML from markdown rendering
const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    "p", "br", "strong", "em", "b", "i", "u", "s", "del",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "pre", "code", "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "span", "div", "hr", "img",
  ],
  ALLOWED_ATTR: ["class", "href", "title", "alt", "src", "target", "rel"],
  ALLOW_DATA_ATTR: false,
};

/** Sanitize HTML string via DOMPurify */
function sanitize(html) {
  return DOMPurify.sanitize(html, PURIFY_CONFIG);
}

// ── VSCode API ──
// @ts-ignore
const vscode = acquireVsCodeApi();

// ── DOM refs ──
const $ = (/** @type {string} */ sel) =>
  /** @type {HTMLElement} */ (document.querySelector(sel));
const messagesEl = $("#messages");
const phasesEl = $("#phases");
const queryInput = /** @type {HTMLTextAreaElement} */ ($("#queryInput"));
const btnSend = /** @type {HTMLButtonElement} */ ($("#btnSend"));
const btnCancel = $("#btnCancel");
const btnClear = $("#btnClear");
const btnSettings = $("#btnSettings");
const healthDot = $("#healthDot");
const selProduct = /** @type {HTMLSelectElement} */ ($("#selProduct"));
const selLanguage = /** @type {HTMLSelectElement} */ ($("#selLanguage"));

let isStreaming = false;

// ── Fallback product list (always available even without API) ──
const DEFAULT_PRODUCTS = [
  { id: "mvs_openframe_7.1", name: "MVS OpenFrame 7.1" },
  { id: "openframe_hidb_7", name: "OpenFrame HiDB 7 (IMS)" },
  { id: "openframe_osc_7", name: "OpenFrame OSC 7 (CICS)" },
  { id: "tibero_7", name: "Tibero 7" },
  { id: "ofasm_4", name: "OFASM 4" },
  { id: "ofcobol_4", name: "OFCOBOL 4" },
  { id: "tmax_6", name: "Tmax 6" },
  { id: "jeus_8", name: "JEUS 8" },
  { id: "webtob_5", name: "WebtoB 5" },
  { id: "ofstudio_7", name: "OFStudio 7" },
  { id: "protrieve_7", name: "Protrieve 7" },
  { id: "xsp_openframe_7", name: "XSP OpenFrame 7 (Fujitsu)" },
];

// ── Phase name map ──
const PHASE_NAMES = {
  query_analysis: "Query Analysis",
  embedding_search: "Embedding Search",
  domain_knowledge: "Domain Knowledge",
  ofcode_web: "OFCode Web Docs",
  ofcode_parser: "OFCode Parser",
  fallback: "Fallback Search",
};

// ── Message handling ──

window.addEventListener("message", (event) => {
  const msg = event.data;

  switch (msg.type) {
    case "health":
      updateHealth(msg.data);
      break;
    case "products":
      updateProducts(msg.data);
      break;
    case "settings":
      applySettings(msg.data);
      break;
    case "streamPhase":
      showPhase(msg.data);
      break;
    case "streamAnswer":
      showAnswer(msg.data);
      break;
    case "streamDone":
      showDone(msg.data);
      break;
    case "error":
      showError(msg.message);
      endStream();
      break;
    case "clearChat":
      clearAll();
      break;
  }
});

// ── Health ──

function updateHealth(data) {
  healthDot.className = `health-dot ${data.status}`;
  const svcs = Object.entries(data.services || {});
  const okCount = svcs.filter(([, v]) => v.status === "ok").length;
  healthDot.title = `${data.status} — ${okCount}/${svcs.length} services (v${data.version})`;
}

// ── Products ──

function updateProducts(products) {
  while (selProduct.options.length > 1) {
    selProduct.remove(1);
  }
  for (const p of products) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    selProduct.appendChild(opt);
  }
}

// ── Settings ──

function applySettings(data) {
  if (data.language) selLanguage.value = data.language;
  if (data.product) selProduct.value = data.product;
}

// ── Chat actions ──

function sendQuery() {
  const query = queryInput.value.trim();
  if (!query || isStreaming) return;

  addMessage("user", query);
  queryInput.value = "";
  autoResize();

  phasesEl.textContent = "";
  phasesEl.classList.remove("hidden");

  startStream();

  vscode.postMessage({
    type: "query",
    query,
    language: selLanguage.value,
    product: selProduct.value,
  });
}

function startStream() {
  isStreaming = true;
  btnSend.classList.add("hidden");
  btnCancel.classList.remove("hidden");
  queryInput.disabled = true;
}

function endStream() {
  isStreaming = false;
  btnSend.classList.remove("hidden");
  btnCancel.classList.add("hidden");
  queryInput.disabled = false;
  queryInput.focus();
}

function cancelQuery() {
  vscode.postMessage({ type: "cancel" });
  endStream();
}

function clearAll() {
  messagesEl.textContent = "";
  phasesEl.textContent = "";
  phasesEl.classList.add("hidden");
}

// ── Message rendering ──

function addMessage(role, content) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "user") {
    div.textContent = content;
  } else if (role === "error") {
    div.textContent = content;
    div.classList.add("error");
  } else {
    // Assistant messages: sanitized HTML from marked
    div.innerHTML = sanitize(content);
  }
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ── Phase progress ──

function showPhase(data) {
  const item = document.createElement("div");
  item.className = "phase-item";

  const check = document.createElement("span");
  check.className = "phase-check";
  check.textContent = "\u2713";

  const label = document.createElement("span");
  const name = PHASE_NAMES[data.name] || data.name;
  label.textContent = `Phase ${data.phase}: ${name}`;

  const time = document.createElement("span");
  time.className = "phase-time";
  time.textContent = `${data.time_ms}ms`;

  item.appendChild(check);
  item.appendChild(label);
  item.appendChild(time);
  phasesEl.appendChild(item);
  scrollToBottom();
}

// ── Answer rendering ──

function showAnswer(data) {
  // Render markdown → sanitize
  const rawHtml = marked.parse(data.answer || "");
  const safeHtml = sanitize(rawHtml);

  // Build meta bar using DOM
  const container = document.createElement("div");
  container.innerHTML = safeHtml;

  // Meta bar
  const metaBar = document.createElement("div");
  metaBar.className = "meta-bar";

  const confidence = data.confidence || 0;
  const pct = Math.round(confidence * 100);
  const level = pct >= 70 ? "high" : pct >= 40 ? "medium" : "low";

  // Confidence row
  const confRow = document.createElement("div");
  confRow.className = "confidence-row";

  const confLabel = document.createElement("span");
  confLabel.textContent = `Confidence: ${pct}%`;

  const confTrack = document.createElement("div");
  confTrack.className = "confidence-track";

  const confFill = document.createElement("div");
  confFill.className = `confidence-fill ${level}`;
  confFill.style.width = `${pct}%`;
  confTrack.appendChild(confFill);

  confRow.appendChild(confLabel);
  confRow.appendChild(confTrack);
  metaBar.appendChild(confRow);

  // Meta tags
  const metaTags = document.createElement("div");
  metaTags.className = "meta-tags";
  for (const [key, val] of [
    ["Intent", data.intent],
    ["Product", data.product],
    ["Lang", data.language],
  ]) {
    const tag = document.createElement("span");
    tag.className = "meta-tag";
    tag.textContent = `${key}: ${val || "?"}`;
    metaTags.appendChild(tag);
  }
  metaBar.appendChild(metaTags);
  container.appendChild(metaBar);

  // Sources
  if (data.sources && data.sources.length > 0) {
    const sourcesDiv = document.createElement("div");
    sourcesDiv.className = "sources";

    const srcTitle = document.createElement("div");
    srcTitle.className = "sources-title";
    srcTitle.textContent = `Sources (${data.sources.length})`;
    sourcesDiv.appendChild(srcTitle);

    for (const s of data.sources) {
      const card = document.createElement("div");
      card.className = "source-card";

      const nameSpan = document.createElement("span");
      nameSpan.className = "source-name";
      nameSpan.textContent = s.document;
      nameSpan.title = s.document;

      const pageSpan = document.createElement("span");
      pageSpan.className = "source-page";
      pageSpan.textContent = s.page != null ? `p.${s.page}` : "";

      const badge = document.createElement("span");
      const typeClass = s.type || "vector";
      badge.className = `source-badge ${typeClass}`;
      badge.textContent = `${(s.score * 100).toFixed(1)}%`;

      card.appendChild(nameSpan);
      card.appendChild(pageSpan);
      card.appendChild(badge);
      sourcesDiv.appendChild(card);
    }
    container.appendChild(sourcesDiv);
  }

  // Add as assistant message
  const msgDiv = document.createElement("div");
  msgDiv.className = "message assistant";
  while (container.firstChild) {
    msgDiv.appendChild(container.firstChild);
  }
  linkifyImsIds(msgDiv);
  messagesEl.appendChild(msgDiv);
  scrollToBottom();
}

// ── Done ──

function showDone(data) {
  const totalSec = (data.total_time_ms / 1000).toFixed(1);
  const timeDiv = document.createElement("div");
  timeDiv.className = "total-time";
  timeDiv.textContent = `Total: ${totalSec}s`;
  phasesEl.appendChild(timeDiv);

  endStream();
  scrollToBottom();
}

// ── Error ──

function showError(message) {
  addMessage("error", message);
}

// ── IMS link helper ──

const IMS_PATTERN = /ims_issue_(\d+)/g;

/**
 * Replace ims_issue_XXXXX text nodes in a DOM tree with clickable links
 * that open the TmaxSoft IMS issue tracker.
 */
function linkifyImsIds(container) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodesToReplace = [];

  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (IMS_PATTERN.test(node.textContent)) {
      nodesToReplace.push(node);
    }
    IMS_PATTERN.lastIndex = 0;
  }

  for (const textNode of nodesToReplace) {
    const frag = document.createDocumentFragment();
    const text = textNode.textContent;
    let lastIdx = 0;
    let match;

    IMS_PATTERN.lastIndex = 0;
    while ((match = IMS_PATTERN.exec(text)) !== null) {
      if (match.index > lastIdx) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
      }
      const link = document.createElement("a");
      link.className = "ims-link";
      link.textContent = match[0];
      link.title = "Open IMS #" + match[1];
      link.href = "#";
      const issueId = match[1];
      link.addEventListener("click", function (e) {
        e.preventDefault();
        var url = "https://ims.tmaxsoft.com/tody/ims/issue/issueView.do?issueId=" + issueId + "&menuCode=issue_list";
        vscode.postMessage({ type: "openExternal", url: url });
      });
      frag.appendChild(link);
      lastIdx = IMS_PATTERN.lastIndex;
    }

    if (lastIdx < text.length) {
      frag.appendChild(document.createTextNode(text.slice(lastIdx)));
    }

    textNode.parentNode.replaceChild(frag, textNode);
  }
}

// ── Utilities ──

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function autoResize() {
  queryInput.style.height = "auto";
  queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + "px";
}

// ── Event listeners ──

btnSend.addEventListener("click", sendQuery);
btnCancel.addEventListener("click", cancelQuery);
btnClear.addEventListener("click", clearAll);
btnSettings.addEventListener("click", () => {
  vscode.postMessage({ type: "openSettings" });
});

queryInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendQuery();
  }
});

queryInput.addEventListener("input", autoResize);

// ── Init: load fallback products immediately, then tell extension we're ready ──
updateProducts(DEFAULT_PRODUCTS);
vscode.postMessage({ type: "ready" });
