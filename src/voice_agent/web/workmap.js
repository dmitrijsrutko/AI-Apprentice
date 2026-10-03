// The Work Map, as HTML: steps in order, each with the screen moment it
// happened at, the decision, the reason in the expert's words and its
// guardrails. Pure — a map in, a string out — so node runs it. Everything in a
// map was written by a model reading what a user said, so all of it is escaped.

const escape = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export const EXPECTED_S = 30;
const CELLS = 20;

const KINDS = {
  limit: ["🛡", "limit"],
  exception: ["↪", "exception"],
  escalate: ["🙋", "stop and ask"],
  never: ["⛔", "never"],
};

// The wait while the map is drawn, like the judge's.
export function progress(title, elapsed, expected = EXPECTED_S, redraw = false) {
  const seconds = Math.max(0, Math.floor(elapsed));
  const filled = Math.min(CELLS, Math.floor((seconds / expected) * CELLS));
  const bar = "▰".repeat(filled) + "▱".repeat(CELLS - filled);
  const late = seconds > expected;
  const line = late
    ? "Taking longer than usual — still drawing…"
    : `${escape(title)} is ${redraw ? "redrawing the map with your corrections" : "drawing the map"}…`;
  const count = late ? `${seconds} s` : `${seconds} s of ~${expected} s`;
  return `<p class="m-wait"><span class="m-icon">🗺</span> ${line}</p>` +
    `<p class="m-progress">${bar}  ${count}</p>`;
}

export const frameUrl = (key, screen) => `/c/${encodeURIComponent(key)}/frames/${encodeURIComponent(screen)}.jpg`;

function words(said) {
  if (!said) return "";
  const doubt = said.verified ? "" : ` class="unverified" title="Not found word for word in what you said"`;
  return `<q${doubt}>${escape(said.quote)}</q>`;
}

function rail(g) {
  const [icon, label] = KINDS[g.kind] ?? KINDS.limit;
  return `<li class="m-rail m-${escape(g.kind)}"><span class="m-kind">${icon} ${label}</span> ` +
    `${escape(g.rule)}${g.words ? ` ${words(g.words)}` : ""}</li>`;
}

function step(s, key) {
  const chips = [
    s.judgment ? `<span class="m-chip m-judgment">⚖ judgment call</span>` : "",
    s.guardrails?.length ? `<span class="m-chip">🛡 ${s.guardrails.length}</span>` : "",
    s.reason ? "" : `<span class="m-chip m-gap">? why</span>`,
    s.at ? `<span class="m-chip m-at">⏱ ${escape(s.at)}</span>` : "",
  ].join("");
  const thumb = s.frame
    ? `<img class="m-thumb" loading="lazy" alt="" src="${frameUrl(key, s.screen)}">`
    : "";
  const shot = s.frame
    ? `<a class="m-shot" href="${frameUrl(key, s.screen)}" target="_blank" rel="noopener">` +
      `<img alt="The screen at ${escape(s.at)}" src="${frameUrl(key, s.screen)}"></a>`
    : `<p class="m-noshot">No screenshot for this step.</p>`;
  const why = s.reason
    ? `<p class="m-why"><b>Why</b> ${words(s.reason)}</p>`
    : `<p class="m-why m-gap"><b>Why</b> not said yet</p>`;
  const rails = s.guardrails?.length ? `<ul class="m-rails">${s.guardrails.map(rail).join("")}</ul>` : "";
  return `<li class="m-step" data-step="${Number(s.n)}">` +
    `<details><summary><span class="m-n">${Number(s.n)}</span>` +
    `<span class="m-head"><b>${escape(s.title)}</b><span class="m-decision">${escape(s.decision)}</span>` +
    `<span class="m-chips">${chips}</span></span>${thumb}</summary>` +
    `<div class="m-body">${shot}<p class="m-did"><b>Decision</b> ${escape(s.decision)}</p>${why}${rails}</div>` +
    `</details></li>`;
}

// `drawn`: { map, version, ms, creator } as the server sends it.
export function renderMap(drawn, key) {
  const map = drawn?.map ?? {};
  const steps = (map.steps ?? []).map((s) => step(s, key)).join("");
  const gaps = map.gaps?.length
    ? `<div class="m-gaps"><b>Still unclear</b><ul>${map.gaps.map((g) => `<li>${escape(g)}</li>`).join("")}</ul></div>`
    : "";
  const by = [
    drawn?.creator ? `Drawn by ${escape(drawn.creator)}` : "",
    drawn?.ms ? `in ${Math.round(drawn.ms / 1000)} s` : "",
    drawn?.version ? `· version ${Number(drawn.version)}` : "",
  ].filter(Boolean).join(" ");
  return `<section class="workmap" id="workmap">` +
    `<header><span class="m-icon">🗺</span> <b>${escape(map.title ?? "Work Map")}</b></header>` +
    `${map.summary ? `<p class="m-summary">${escape(map.summary)}</p>` : ""}` +
    `<ol class="m-steps">${steps}</ol>${gaps}<footer>${by}</footer></section>`;
}
