// Executed tests for what the page says when the microphone cannot be opened.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createMeter, micFailure, micKind, trackFacts } from "../../src/voice_agent/web/mic.js";

const failure = (name, message = "raw browser text") => Object.assign(new Error(message), { name });

test("a blocked microphone says where to allow it, and names in-app browsers", () => {
  const text = micFailure(failure("NotAllowedError"));
  assert.match(text, /blocked for this page/);
  assert.match(text, /Website Settings → Microphone/);
  assert.match(text, /open the link in Safari or Chrome/);
});

test("a busy, missing or insecure microphone each says which", () => {
  assert.match(micFailure(failure("NotReadableError")), /busy/);
  assert.match(micFailure(failure("NotFoundError")), /no microphone/);
  assert.match(micFailure(failure("SecurityError")), /https/);
});

test("anything else keeps the browser's own words", () => {
  assert.equal(micFailure(failure("AbortError", "something odd")), "something odd");
});

test("the meter tells a loud span from a silent one, and counts the longest wait", () => {
  const meter = createMeter();
  const loud = new Int16Array(320).fill(16384);  // half of full scale: about -6 dBFS
  meter.add(loud, 0);
  meter.add(loud, 20);
  meter.add(loud, 320);  // a 300 ms wait: a starved capture

  const stats = meter.take();

  assert.equal(stats.frames, 3);
  assert.equal(stats.max_gap_ms, 300);
  assert.ok(stats.rms_dbfs > -7 && stats.rms_dbfs < -5);
  assert.deepEqual(meter.take(), { rms_dbfs: -120, peak_dbfs: -120, frames: 0, max_gap_ms: 0 }, "starts afresh");
});

test("a microphone describes its device and the processing applied", () => {
  const track = {
    label: "MacBook Pro Microphone", muted: false, readyState: "live",
    getSettings: () => ({ echoCancellation: true, noiseSuppression: true, autoGainControl: false, sampleRate: 16000 }),
  };
  const facts = trackFacts({ stream: { getAudioTracks: () => [track] }, context: { state: "running" } });

  assert.equal(facts.kind, "built-in");
  assert.ok(!("label" in facts), "the device's name stays on the page");
  assert.equal(facts.gain, false);
  assert.equal(facts.context, "running");
  assert.deepEqual(trackFacts(null), {});
});

test("a microphone's kind is sent, never a name that may be in its label", () => {
  assert.equal(micKind("Jane Doe iPhone Microphone"), "iphone");
  assert.equal(micKind("Jane's AirPods Pro"), "airpods");
  assert.equal(micKind("MacBook Pro Microphone"), "built-in");
  assert.equal(micKind("USB Audio Device"), "usb");
  assert.equal(micKind("Studio Mic"), "other");
  assert.equal(micKind(""), "");
});
