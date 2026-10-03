// The Work Map as HTML: escaped, linked to its screenshots, honest about quotes.

import assert from "node:assert/strict";
import { test } from "node:test";

import { EXPECTED_S, frameUrl, progress, renderMap } from "../../src/voice_agent/web/workmap.js";

const drawn = {
  version: 2,
  ms: 23000,
  creator: "Claude Opus 5.5",
  map: {
    title: "Book <b>the</b> flight",
    summary: "Direct, on time.",
    steps: [
      { n: 1, title: "Filter to direct", decision: "Direct only", screen: "g3", frame: true, at: "0:12",
        judgment: true, reason: { quote: "he hates layovers", said: "m2", verified: true },
        guardrails: [{ rule: "Over €500: ask him", kind: "escalate", words: null }] },
      { n: 2, title: "Pick the 9:00", decision: "The 9:00", screen: "", frame: false, at: "",
        judgment: false, reason: { quote: "made up", said: "m2", verified: false }, guardrails: [] },
      { n: 3, title: "Pay", decision: "Company card", screen: "", frame: false, reason: null, guardrails: [] },
    ],
    gaps: ["What if no direct flight exists?"],
  },
};

test("everything a model wrote is escaped", () => {
  const html = renderMap(drawn, "k");
  assert.ok(html.includes("Book &#60;b&#62;the&#60;/b&#62; flight"));
  assert.ok(!html.includes("<b>the</b>"));
});

test("a step links to its screenshot, by the conversation's key", () => {
  const html = renderMap(drawn, "abc");
  assert.ok(html.includes(`src="${frameUrl("abc", "g3")}"`));
  assert.equal(frameUrl("abc", "g3"), "/c/abc/frames/g3.jpg");
  assert.ok(html.includes("No screenshot for this step."));
});

test("a quote not found in what was said is marked, a missing reason is a gap", () => {
  const html = renderMap(drawn, "k");
  assert.ok(html.includes(`<q class="unverified"`));
  assert.ok(html.includes("<q>he hates layovers</q>"));
  assert.ok(html.includes("not said yet"));
  assert.ok(html.includes("stop and ask"));
});

test("steps carry their number for a click", () => {
  const html = renderMap(drawn, "k");
  assert.deepEqual([...html.matchAll(/data-step="(\d+)"/g)].map((m) => m[1]), ["1", "2", "3"]);
});

test("the wait fills over the expected time, then says it is late", () => {
  assert.ok(progress("Opus", 0).includes("0 s of ~30 s"));
  assert.ok(progress("Opus", EXPECTED_S / 2).includes("▰".repeat(10)));
  assert.ok(progress("Opus", 45).includes("Taking longer"));
  assert.ok(progress("Opus", 1, 30, true).includes("redrawing"));
});

import { renderMastery } from "../../src/voice_agent/web/workmap.js";

test("the teaching report lists what was caught, in the expert's words, escaped", () => {
  const html = renderMastery({
    headline: "Nearly <b>ready</b>",
    mastered: [{ step: "1. Filter", note: "Direct only" }],
    caught: [{ step: "2. Pick", what: "Two stops", rule: "One stop at most", quote: "he hates layovers" }],
    practice: ["Check the time first"],
    creator: "Claude Opus 5.5",
    ms: 18000,
  });
  assert.ok(html.includes("Nearly &#60;b&#62;ready&#60;/b&#62;"));
  assert.ok(html.includes("<q>he hates layovers</q>"));
  assert.ok(html.includes("Caught by the tutor") && html.includes("Practise next"));
  assert.ok(html.includes("in 18 s"));
});

test("a report with nothing caught leaves that section out", () => {
  assert.ok(!renderMastery({ headline: "x", mastered: [], caught: [], practice: [] }).includes("Caught"));
});
