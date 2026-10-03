// The page's eyes: which frames of a shared screen are worth sending.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MIN_CELLS, THUMB_H, THUMB_W, changedCells, grey, isNew,
} from "../../src/voice_agent/web/eyes.js";

const blank = (value = 200) => new Uint8Array(THUMB_W * THUMB_H).fill(value);
const at = (x, y) => y * THUMB_W + x;
const with_ = (cells, value = 40) => {
  const t = blank();
  for (const [x, y] of cells) t[at(x, y)] = value;
  return t;
};

test("an unchanged screen has no changed cells", () => {
  assert.equal(changedCells(blank(), blank()), 0);
});

test("the first frame counts as wholly changed", () => {
  assert.equal(changedCells(null, blank()), THUMB_W * THUMB_H);
});

test("compression noise below the level is not a change", () => {
  const noisy = blank().map((v, i) => v + (i % 2 ? 8 : -8));
  assert.equal(isNew([blank()], noisy), false);
});

test("one typed character — two cells — is new", () => {
  const typed = with_([[100, 50], [100, 51]]);
  assert.ok(MIN_CELLS <= 2);
  assert.equal(isNew([blank()], typed), true);
});

test("a blinking caret is sent twice, then never again", () => {
  const on = with_([[30, 70], [30, 71]]);
  const off = blank();
  assert.equal(isNew([off], on), true, "the caret's first appearance is new");
  const recent = [off, on];
  assert.equal(isNew(recent, off), false, "blinking off matches a frame already sent");
  assert.equal(isNew(recent, on), false, "and blinking on again does too");
  const typed = with_([[30, 70], [30, 71], [31, 70], [31, 71]]);
  assert.equal(isNew(recent, typed), true, "a character typed beside it is new");
});

test("a word changed and changed back is sent every time", () => {
  const a = blank();
  const b = with_([[100, 50], [101, 50], [102, 50], [103, 50], [104, 50], [100, 51], [104, 51]]);
  assert.equal(isNew([a], b), true, "24 h → 30 h");
  assert.equal(isNew([a, b], a), true, "30 h → 24 h again: a revert, not a blink");
});

test("a deleted character is seen as deleted", () => {
  const typed = with_([[60, 20], [61, 20], [60, 21], [61, 21], [62, 21]]);
  assert.equal(isNew([blank(), typed], blank()), true);
});

test("RGBA pixels become one grey byte each", () => {
  const rgba = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]);
  assert.deepEqual([...grey(rgba)], [255, 0]);
});
