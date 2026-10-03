// The eyes, on the page: the screen the user shares, and which of its frames
// are worth sending. The browser's own picker offers the whole screen, a
// window or a tab; the server reads one frame at a time (`eyes.py`), so the
// page sends a frame only when it differs from the last one *sent* — a still
// screen costs nothing.

export const THUMB_W = 64;
export const THUMB_H = 36;
export const MAX_WIDTH = 1280;
export const QUALITY = 0.7;
export const LEVEL = 24;  // a grey step this big is a change, not compression noise

// The share of pixels whose grey value moved by more than `level`, between two
// greyscale thumbnails of equal size. Pure, so node tests it.
export function changedFraction(previous, current, level = LEVEL) {
  if (!previous || previous.length !== current.length) return 1;
  let moved = 0;
  for (let i = 0; i < current.length; i++) {
    if (Math.abs(current[i] - previous[i]) > level) moved++;
  }
  return current.length ? moved / current.length : 0;
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
// surface)` repaints the button. `interval` (s) and `threshold` (0–1) come
// from the server's facts.
export function createEyes({ send, onChange, share, frame, interval = 1, threshold = 0.01 }) {
  let stream = null;
  let video = null;
  let timer = null;
  let sentThumb = null;
  const thumb = document.createElement("canvas");
  thumb.width = THUMB_W;
  thumb.height = THUMB_H;
  const thumbCtx = thumb.getContext("2d", { willReadFrequently: true });
  const full = document.createElement("canvas");

  function look() {
    if (!video || video.readyState < 2 || !video.videoWidth) return;
    thumbCtx.drawImage(video, 0, 0, THUMB_W, THUMB_H);
    const now = grey(thumbCtx.getImageData(0, 0, THUMB_W, THUMB_H).data);
    const changed = changedFraction(sentThumb, now);
    if (sentThumb && changed < threshold) return;
    const scale = Math.min(1, MAX_WIDTH / video.videoWidth);
    full.width = Math.round(video.videoWidth * scale);
    full.height = Math.round(video.videoHeight * scale);
    full.getContext("2d").drawImage(video, 0, 0, full.width, full.height);
    const jpeg = full.toDataURL("image/jpeg", QUALITY).split(",", 2)[1];
    if (!jpeg) return;
    sentThumb = now;
    send(frame(jpeg, Math.round(changed * 1000) / 1000));
  }

  function stop(tell = true) {
    clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    if (video) video.srcObject = null;
    video = null;
    sentThumb = null;
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
    timer = setInterval(look, Math.max(0.25, interval) * 1000);
    setTimeout(look, 300);  // the first frame at once, not after a whole interval
  }

  return {
    start,
    stop: () => { if (stream) stop(); },
    isSharing: () => stream !== null,
  };
}
