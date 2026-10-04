// Microphone capture: permission, the capture graph, PCM16 frames out.

// A real file rather than a Blob URL: the worklet is now parsed under test and
// served like every other module. Resolved against this module so the page
// does not hard-code where static files are mounted.
const WORKLET = new URL("./capture-worklet.js", import.meta.url);

// `onFrame` receives each Int16Array of PCM; whether to send it (the half-duplex
// gate) is the caller's decision, not the microphone's.
//
// Permission is asked here, when the microphone is actually wanted: the start
// click, before the greeting can play.
// Why the microphone could not be opened, in words that say what to do. The
// browser's own text ("not allowed by the user agent or the platform in the
// current context") names no setting and no way out.
export function micFailure(err) {
  switch (err?.name) {
    case "NotAllowedError":
      return "the microphone is blocked for this page. Allow it in the browser's settings for " +
        "this site — on an iPhone: the aA menu → Website Settings → Microphone, and Settings → " +
        "Apps → your browser → Microphone. Inside another app's own browser — Telegram, " +
        "Instagram, WhatsApp — open the link in Safari or Chrome instead. Then reload.";
    case "NotFoundError":
      return "no microphone was found on this device.";
    case "NotReadableError":
      return "the microphone is busy — another app, or a call, may be using it.";
    case "SecurityError":
      return "the microphone needs a secure page: https.";
    default:
      return err?.message || String(err);
  }
}

// What the microphone delivered over a span, for the server's trace: its level,
// how many frames and the longest wait between two. Chrome on macOS was seen to
// hand over loud audio that no listener could hear speech in while a screen was
// shared; this tells "garbled" (loud, no speech) from "starved" (gaps) from
// "silent". Pure, so node tests it.
export function createMeter() {
  let sumsq = 0, samples = 0, peak = 0, frames = 0, maxGap = 0, last = null;
  return {
    add(pcm, now) {
      for (let i = 0; i < pcm.length; i++) {
        const v = pcm[i] / 32768;
        sumsq += v * v;
        if (Math.abs(v) > peak) peak = Math.abs(v);
      }
      samples += pcm.length;
      frames += 1;
      if (last !== null) maxGap = Math.max(maxGap, now - last);
      last = now;
    },
    take() {
      const db = (x) => (x > 0 ? Math.round(20 * Math.log10(x) * 10) / 10 : -120);
      const out = {
        rms_dbfs: samples ? db(Math.sqrt(sumsq / samples)) : -120,
        peak_dbfs: db(peak),
        frames,
        max_gap_ms: Math.round(maxGap),
      };
      sumsq = 0; samples = 0; peak = 0; frames = 0; maxGap = 0;
      return out;
    },
  };
}

// Which kind of device a microphone is, from its label. The label itself can
// carry its owner's name ("Jane's iPhone Microphone"), so only this leaves the
// page; the name is shown to the user, never sent.
export function micKind(label) {
  const text = String(label ?? "");
  if (/iphone/i.test(text)) return "iphone";
  if (/airpods/i.test(text)) return "airpods";
  if (/macbook|built-in|internal/i.test(text)) return "built-in";
  if (/usb/i.test(text)) return "usb";
  return text ? "other" : "";
}

export const micLabel = (mic) => mic?.stream?.getAudioTracks?.()[0]?.label ?? "";

// The microphone's own account of itself: which kind of device, and what
// processing the browser actually applied (it may ignore what was asked).
export function trackFacts(mic) {
  const track = mic?.stream?.getAudioTracks?.()[0];
  if (!track) return {};
  const s = track.getSettings?.() ?? {};
  return {
    kind: micKind(track.label),
    muted: track.muted,
    ready: track.readyState,
    echo: s.echoCancellation,
    noise: s.noiseSuppression,
    gain: s.autoGainControl,
    track_rate: s.sampleRate,
    context: mic.context?.state,
  };
}

export async function buildMic(sampleRate, onFrame) {
  // Capture at the recognizer's own rate so nothing resamples anywhere.
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true,
             channelCount: 1, sampleRate },
  });
  const context = new AudioContext({ sampleRate });
  await context.audioWorklet.addModule(WORKLET);
  const node = new AudioWorkletNode(context, "capture");
  node.port.onmessage = (e) => onFrame(e.data);
  context.createMediaStreamSource(stream).connect(node);
  return { context, node, stream };
}
