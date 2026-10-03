// The page's eyes: which frames of a shared screen are worth sending.

import assert from "node:assert/strict";
import { test } from "node:test";

import { THUMB_H, THUMB_W, changedFraction, grey } from "../../src/voice_agent/web/eyes.js";

const blank = (value = 128) => new Uint8Array(THUMB_W * THUMB_H).fill(value);

test("an unchanged screen has changed by nothing", () => {
  assert.equal(changedFraction(blank(), blank()), 0);
});

test("the first frame always counts as changed", () => {
  assert.equal(changedFraction(null, blank()), 1);
});

test("compression noise below the level is not a change", () => {
  const noisy = blank().map((v, i) => v + (i % 2 ? 10 : -10));
  assert.equal(changedFraction(blank(), noisy), 0);
});

test("a line of new text passes the 1% threshold, a cursor does not", () => {
  const line = blank();
  for (let x = 0; x < 30; x++) line[10 * THUMB_W + x] = 0;  // 30 pixels: ~1.3%
  assert.ok(changedFraction(blank(), line) >= 0.01);
  const cursor = blank();
  cursor[5] = 0;
  cursor[6] = 0;
  assert.ok(changedFraction(blank(), cursor) < 0.01);
});

test("RGBA pixels become one grey byte each", () => {
  const rgba = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]);
  assert.deepEqual([...grey(rgba)], [255, 0]);
});
