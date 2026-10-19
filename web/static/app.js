"use strict";

const $ = (sel) => document.querySelector(sel);
const form = $("#listing-form");
const submitBtn = $("#submit-btn");
const formError = $("#form-error");
const progressEl = $("#progress");
const resultEl = $("#result");
const emptyEl = $("#empty-state");
const copyAllBtn = $("#copy-all-btn");

let platforms = {};
let running = false;

const EXAMPLE = {
  platform: "amazon_us",
  product_name: "304不锈钢真空保温杯 带吸管和提手",
  brand: "FROSTLY",
  category: "厨房用品 / 水杯",
  description: "双层真空隔热，冰饮保冷约12小时，热饮保温约6小时。容量1.2升（40盎司），杯底直径7.5厘米，可以放进大部分车载杯架。杯盖带可旋转吸管口和翻盖饮水口两种喝法。",
  features: "内胆和外壳都是304不锈钢\n盖子是Tritan材质，不含BPA（有检测报告）\n带折叠提手，方便携带\n附送2根吸管和1把吸管刷",
  materials: "304不锈钢，Tritan盖子，硅胶密封圈",
  dimensions: "高度 28 厘米，重量 520 克",
  target_audience: "上班族、健身人群、学生",
  keywords: "保温杯, 大容量水杯, tumbler with handle",
};

// ---------- helpers ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const utf8Bytes = (s) => new TextEncoder().encode(s).length;
const lines = (s) => s.split(/\n/).map((x) => x.trim()).filter(Boolean);

function countBadge(length, max, unit = "chars", min = 0) {
  let cls = "count";
  if (max && length > max) cls += " over";
  else if (min && length < min) cls += " under";
  const text = max ? `${length}/${max} ${unit}` : `${length} ${unit}`;
  return el("span", { class: cls, text: min && length < min ? `${text} · ${min}+ recommended` : text });
}

function copyButton(getText) {
  const btn = el("button", { type: "button", class: "copy-btn", text: "Copy" });
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      btn.textContent = "Copied";
    } catch {
      btn.textContent = "Copy failed";
    }
    setTimeout(() => (btn.textContent = "Copy"), 1500);
  });
  return btn;
}

function collectInput() {
  const data = Object.fromEntries(new FormData(form).entries());
  const payload = { platform: data.platform, product_name: (data.product_name || "").trim() };
  for (const k of ["description", "brand", "category", "materials", "dimensions", "target_audience", "extra_notes"]) {
    const v = (data[k] || "").trim();
    if (v) payload[k] = v;
  }
  const features = lines(data.features || "");
  const certs = lines(data.certifications || "");
  const keywords = (data.keywords || "").split(/[,，\n]/).map((x) => x.trim()).filter(Boolean);
  if (features.length) payload.features = features;
  if (certs.length) payload.certifications = certs;
  if (keywords.length) payload.keywords = keywords;
  return payload;
}

// ---------- progress ----------

function resetProgress() {
  progressEl.replaceChildren();
  progressEl.hidden = false;
}

function addProgress(message, elapsed) {
  const prev = progressEl.querySelector("li.current");
  if (prev) prev.classList.replace("current", "done");
  progressEl.append(el("li", { class: "current" },
    el("span", { class: "dot", "aria-hidden": "true" }),
    el("span", { class: "t", text: `${Number(elapsed || 0).toFixed(1)}s` }),
    el("span", { text: message })));
}

function finishProgress() {
  const cur = progressEl.querySelector("li.current");
  if (cur) cur.classList.replace("current", "done");
}

// ---------- result rendering ----------

const STATUS = {
  success: ["success", "Listing ready"],
  needs_clarification: ["warn", "We need a bit more information"],
  incomplete: ["warn", "Draft ready, but some problems remain"],
  failed: ["fail", "Something went wrong"],
};

function renderField(label, text, badge, opts = {}) {
  return el("div", { class: "out-field" },
    el("div", { class: "out-head" }, el("span", { class: "out-label", text: label }), badge, copyButton(() => text)),
    el("p", { class: "out-text" + (opts.cls ? " " + opts.cls : ""), text: text || "(empty)" }));
}

function renderIssues(issues) {
  const wrap = el("div");
  wrap.append(el("h3", { class: "section-title", text: issues.length ? `Things to review (${issues.length})` : "Things to review" }));
  if (!issues.length) {
    wrap.append(el("p", { class: "issue-meta", text: "No issues found." }));
    return wrap;
  }
  const labels = { high: "Must fix", medium: "Should fix", low: "Nice to fix" };
  for (const sev of ["high", "medium", "low"]) {
    const group = issues.filter((i) => i.severity === sev);
    if (!group.length) continue;
    const g = el("div", { class: "issue-group" }, el("span", { class: `sev ${sev}`, text: `${labels[sev]} · ${group.length}` }));
    for (const i of group) {
      const body = el("div", { class: `issue ${sev}` },
        el("div", { class: "issue-meta", text: `${i.field.replace("_", " ")} · ${sourceLabel(i.source)}` }));
      const line = el("div");
      if (i.quote) line.append(el("q", { text: i.quote }), " ");
      line.append(i.reason);
      body.append(line);
      if (i.suggestion) body.append(el("div", { class: "fix", text: `Fix: ${i.suggestion}` }));
      g.append(body);
    }
    wrap.append(g);
  }
  return wrap;
}

function sourceLabel(source) {
  return ({ length: "length check", terms_list: "banned terms", llm_compliance: "AI compliance review", claim_check: "not in your facts", format: "format" })[source] || source;
}

function renderClarification(questions) {
  const box = el("div");
  box.append(el("ul", { class: "questions" }, ...questions.map((q) => el("li", { text: q }))));
  const ta = el("textarea", { rows: "4", id: "answers", placeholder: "Write your answers here (Chinese or English is fine)" });
  const btn = el("button", { type: "button", class: "secondary-btn", text: "Add answers and try again" });
  btn.addEventListener("click", () => {
    const answers = ta.value.trim();
    if (!answers) { ta.focus(); return; }
    const notes = $("#extra_notes");
    notes.value = [notes.value.trim(), answers].filter(Boolean).join("\n");
    form.requestSubmit();
  });
  box.append(el("div", { class: "field" }, el("label", { for: "answers", text: "Your answers" }), ta), btn);
  return box;
}

function listingAsText(listing, p) {
  const parts = [`TITLE\n${listing.title}`];
  if (p.highlights_enabled && listing.item_highlights) parts.push(`ITEM HIGHLIGHTS\n${listing.item_highlights}`);
  parts.push("BULLETS\n" + listing.bullets.map((b) => `- ${b}`).join("\n"));
  parts.push(`DESCRIPTION\n${listing.description}`);
  parts.push(`SEARCH TERMS\n${listing.search_terms}`);
  return parts.join("\n\n");
}

function renderResult(r) {
  resultEl.replaceChildren();
  resultEl.hidden = false;
  const [cls, heading] = STATUS[r.status] || STATUS.failed;
  resultEl.append(el("div", { class: `banner ${cls}` }, el("strong", { text: heading }), el("p", { text: r.message || "" })));

  if (r.status === "needs_clarification" && r.clarifying_questions?.length) {
    resultEl.append(renderClarification(r.clarifying_questions));
  }

  const listing = r.listing;
  const p = platforms[r.platform] || {};
  if (listing) {
    resultEl.append(renderField("Title", listing.title, countBadge(listing.title.length, p.title_max), { cls: "title" }));
    if (p.highlights_enabled) {
      resultEl.append(renderField("Item highlights", listing.item_highlights, countBadge(listing.item_highlights.length, p.highlights_max)));
    }
    const bulletsBox = el("div", { class: "out-field" },
      el("div", { class: "out-head" }, el("span", { class: "out-label", text: "Bullet points" }),
        copyButton(() => listing.bullets.join("\n"))),
      el("ol", { class: "bullets" }, ...listing.bullets.map((b) => el("li", {}, b, countBadge(b.length, p.bullet_max)))));
    resultEl.append(bulletsBox);
    resultEl.append(renderField("Description", listing.description,
      countBadge(listing.description.length, p.description_max, "chars", p.description_min)));
    resultEl.append(renderField("Search terms", listing.search_terms,
      countBadge(utf8Bytes(listing.search_terms), p.search_terms_max_bytes, "bytes"), { cls: "mono" }));
    copyAllBtn.hidden = false;
    copyAllBtn.onclick = async () => {
      try { await navigator.clipboard.writeText(listingAsText(listing, p)); copyAllBtn.textContent = "Copied"; }
      catch { copyAllBtn.textContent = "Copy failed"; }
      setTimeout(() => (copyAllBtn.textContent = "Copy all"), 1500);
    };
    resultEl.append(renderIssues(r.issues || []));
  }

  if (r.usage && (r.usage.llm_calls || r.elapsed_s)) {
    const note = r.note ? ` · ${r.note}` : "";
    resultEl.append(el("p", { class: "usage", text: `Done in ${Number(r.elapsed_s).toFixed(1)}s · ${r.usage.llm_calls} AI calls · ${r.usage.input_tokens + r.usage.output_tokens} tokens${note}` }));
  }
}

// ---------- submit ----------

async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

function setRunning(on) {
  running = on;
  submitBtn.disabled = on;
  submitBtn.textContent = on ? "Working on it…" : "Generate listing";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (running) return;
  formError.hidden = true;
  const payload = collectInput();
  if (!payload.product_name) {
    formError.textContent = "Please enter a product name.";
    formError.hidden = false;
    $("#product_name").focus();
    return;
  }

  setRunning(true);
  emptyEl.hidden = true;
  resultEl.hidden = true;
  copyAllBtn.hidden = true;
  resetProgress();
  addProgress("Sending your product", 0);
  if (window.matchMedia("(max-width: 900px)").matches) progressEl.scrollIntoView({ behavior: "smooth", block: "start" });

  let gotResult = false;
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let msg = "The server could not handle your request. Please try again.";
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    await readSSE(res, (event, data) => {
      if (event === "progress") addProgress(data.message, data.elapsed_s);
      else if (event === "result") { gotResult = true; finishProgress(); renderResult(data); }
    });
    if (!gotResult) throw new Error("The connection closed before the listing was finished. Please try again.");
  } catch (err) {
    finishProgress();
    renderResult({ status: "failed", message: err.message || "Network error. Please check your connection and try again." });
  } finally {
    setRunning(false);
  }
});

$("#example-btn").addEventListener("click", () => {
  form.reset();
  for (const [k, v] of Object.entries(EXAMPLE)) {
    const input = form.elements[k];
    if (k === "platform") form.querySelector(`input[name=platform][value="${v}"]`).checked = true;
    else if (input) input.value = v;
  }
});

fetch("/api/platforms").then((r) => r.json()).then((p) => (platforms = p)).catch(() => {});
