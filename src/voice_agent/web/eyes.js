// The eyes, on the page: the screen the user shares, and which of its frames
// are worth sending. The browser's own picker offers the whole screen, a
// window or a tab; the server reads them as fast as it can (`eyes.py`), so the
// page sends a frame only when the screen shows something it has not sent
// lately — a still screen, or a blinking caret, costs nothing.
//
// The test is on a 256x144 grey thumbnail: a cell is ~7.5 px of a 1080p screen
// (one character covers one or two), and a frame is new when at least
// `minCells` cells differ by more than LEVEL from the last frame sent.
// Counting cells, not a share of the screen: one changed price is as worth
// reading as a new page. One exception: a change no bigger than a caret that
// brings back a frame sent lately is a blink, and is not sent again. A bigger
// change is always sent, a change back included — a value set, changed and
// set back must be seen as set back.

export const THUMB_W = 256;
export const THUMB_H = 144;
export const MAX_WIDTH = 1024;  // ~790 image tokens: read faster than 1280's ~1,230
export const QUALITY = 0.6;
export const LEVEL = 16;  // a grey step this big is a change, not compression noise
export const MIN_CELLS = 2;
export const RECENT = 4;  // a caret's two states, and room for a toggle's
export const CARET_W = 2;  // cells: a caret is a thin bar, at most this wide…
export const CARET_H = 4;  // …and this tall

// How many cells' grey values moved by more than `level` between two
// thumbnails of equal size; every cell when there is nothing to compare with.
// Pure, so node tests it.
export function changedCells(previous, current, level = LEVEL) {
  if (!previous || previous.length !== current.length) return current.length;
  let moved = 0;
  for (let i = 0; i < current.length; i++) {
    if (Math.abs(current[i] - previous[i]) > level) moved++;
  }
  return moved;
}

// The box around the cells that differ, as [width, height] in cells; null when
// none differ.
export function changedBox(previous, current, level = LEVEL) {
  let left = THUMB_W, right = -1, top = THUMB_H, bottom = -1;
  for (let i = 0; i < current.length; i++) {
    if (Math.abs(current[i] - previous[i]) <= level) continue;
    const x = i % THUMB_W, y = (i - x) / THUMB_W;
    if (x < left) left = x;
    if (x > right) right = x;
    if (y < top) top = y;
    if (y > bottom) bottom = y;
  }
  return right < 0 ? null : [right - left + 1, bottom - top + 1];
}

// Whether `current` is worth sending, given the `recent` frames sent (newest
// last). A blinking caret alternates between two states: a caret-sized change
// back to a state sent lately is skipped, so a blink costs two frames once.
export function isNew(recent, current, minCells = MIN_CELLS, level = LEVEL) {
  const last = recent.at(-1);
  if (!last) return true;
  if (changedCells(last, current, level) < minCells) return false;
  const box = changedBox(last, current, level);
  const caretSized = box && box[0] <= CARET_W && box[1] <= CARET_H;
  if (!caretSized) return true;
  return recent.slice(0, -1).every((sent) => changedCells(sent, current, level) >= minCells);
}

// RGBA pixels as one grey byte each (Rec. 601 luma).
export function grey(rgba) {
  const out = new Uint8Array(rgba.length / 4);
  for (let i = 0, j = 0; j < out.length; i += 4, j++) {
    out[j] = (rgba[i] * 299 + rgba[i + 1] * 587 + rgba[i + 2] * 114) / 1000;
  }
  return out;
}

export const canShare = () => Boolean(globalThis.navigator?.mediaDevices?.getDisplayMedia);

// `send(message)` puts a protocol message on the socket; `onChange(sharing,
// surface)` repaints the button. `interval` (s) and `minCells` come from the
// server's facts.
export function createEyes({ send, onChange, share, frame, interval = 1, minCells = MIN_CELLS }) {
  let stream = null;
  let video = null;
  let timer = null;
  let sent = [];  // the thumbnails of the last RECENT frames sent, newest last
  const thumb = document.createElement("canvas");
  thumb.width = THUMB_W;
  thumb.height = THUMB_H;
  const thumbCtx = thumb.getContext("2d", { willReadFrequently: true });
  // Averaged rather than sampled: a character must move its cell's grey.
  thumbCtx.imageSmoothingEnabled = true;
  thumbCtx.imageSmoothingQuality = "high";
  const full = document.createElement("canvas");

  // `forced`: the server asked for the screen as it is now (the user started
  // speaking), so any change at all is worth sending; an identical frame is not.
  function look(forced = false) {
    if (!video || video.readyState < 2 || !video.videoWidth) return;
    thumbCtx.drawImage(video, 0, 0, THUMB_W, THUMB_H);
    const now = grey(thumbCtx.getImageData(0, 0, THUMB_W, THUMB_H).data);
    const last = sent.at(-1) ?? null;
    const changed = changedCells(last, now);
    if (last && (forced ? changed === 0 : !isNew(sent, now, minCells))) return;
    const scale = Math.min(1, MAX_WIDTH / video.videoWidth);
    full.width = Math.round(video.videoWidth * scale);
    full.height = Math.round(video.videoHeight * scale);
    full.getContext("2d").drawImage(video, 0, 0, full.width, full.height);
    const jpeg = full.toDataURL("image/jpeg", QUALITY).split(",", 2)[1];
    if (!jpeg) return;
    sent = [...sent, now].slice(-RECENT);
    send(frame(jpeg, changed));
  }

  function stop(tell = true) {
    clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    if (video) video.srcObject = null;
    video = null;
    sent = [];
    if (tell) send(share(false));
    onChange(false, "");
  }

  async function start() {
    // Asked inside the click: the browser shows its picker only for a gesture.
    const next = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
    if (stream) stop(false);  // a new pick replaces the old one, as one switch
    stream = next;
    const [track] = stream.getVideoTracks();
    const surface = track.getSettings?.().displaySurface ?? "";
    // The browser's own "Stop sharing" bar ends the track, not our button.
    track.addEventListener("ended", () => { if (stream === next) stop(); });
    video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play().catch(() => {});
    send(share(true, surface, track.label));
    onChange(true, track.label || surface);
    timer = setInterval(() => look(), Math.max(0.25, interval) * 1000);
    setTimeout(() => look(), 300);  // the first frame at once, not after a whole interval
  }

  return {
    start,
    stop: () => { if (stream) stop(); },
    isSharing: () => stream !== null,
    lookNow: () => look(true),
  };
}
