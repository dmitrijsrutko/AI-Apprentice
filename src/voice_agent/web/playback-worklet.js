// Runs on the audio thread: one continuous output fed from a queue of PCM.
//
// Replaces one AudioBufferSourceNode per chunk. Every scheduled source node is
// work on *every* render quantum until it plays, so a 100-second reply (~4,600
// chunks, all scheduled within two seconds) cost the audio thread ~780 µs per
// 128-frame quantum on average — measured by offline rendering — and missed
// real-time deadlines: clicks and pitch wobble that eased as the nodes played
// out. A queue costs the same per quantum however long the reply is.
//
// The logic is exported so node can execute it; the processor class at the
// bottom exists only inside an AudioWorkletGlobalScope.

// How many samples a dropout is faded over, on each side (2 ms at 24 kHz): a
// step from speech to silence, or back, is a click; a ramp this short is not
// heard as one. Only edges of a dropout are touched; a stream that never runs
// dry comes out bit for bit.
export const FADE_SAMPLES = 48;

// How much audio a stream holds before it starts playing. v4 Turbo's first
// chunk is 32 ms: started on it alone, a next chunk a few ms late on the
// network stuttered the first syllable. The rest of its first piece follows in
// a burst within ~40 ms, so this waits for audio already on its way, not for
// the clock (measured on 22 replies: +17 ms p50, +35 ms p90, +71 ms at most).
export const START_MS = 150;

// Float samples in arrival order, read out in fixed-size quanta with no seams.
// `startSamples`: how many must be queued before the first is played.
export class PcmQueue {
  constructor(startSamples = 0) {
    this.startSamples = startSamples;
    this.queued = 0;        // samples pushed and not yet read
    this.chunks = [];
    this.head = 0;          // index of the chunk being read
    this.offset = 0;        // samples already read from that chunk
    this.started = false;   // has any sample been played
    this.ended = false;     // has the server closed the stream
    this.dry = false;       // is the queue currently starved
    this.gaps = 0;
    this.gapFrames = 0;
    this.read = 0;          // real samples read out so far
    this.firstGapAt = null; // samples read when the first gap began
  }

  push(samples) {
    if (samples.length) {
      this.chunks.push(samples);
      this.queued += samples.length;
    }
  }

  end() {
    this.ended = true;
  }

  get drained() {
    return this.ended && this.head === this.chunks.length;
  }

  // Fill `out` from the queue; any shortfall is silence. A shortfall between
  // the first sample and the end of the stream is a gap: the network fell
  // behind playback. Returns how many samples were real audio.
  pull(out) {
    if (!this.started && !this.ended && this.queued < this.startSamples) {
      out.fill(0);  // waiting to start, which is not a gap
      return 0;
    }
    let written = 0;
    while (written < out.length && this.head < this.chunks.length) {
      const chunk = this.chunks[this.head];
      const n = Math.min(out.length - written, chunk.length - this.offset);
      // A plain loop, not `out.set(chunk.subarray(...))`: `subarray` allocates
      // a view every quantum, and garbage collection on the audio thread is a
      // glitch of its own. This path allocates nothing.
      for (let i = 0; i < n; i++) out[written + i] = chunk[this.offset + i];
      written += n;
      this.offset += n;
      if (this.offset === chunk.length) {
        this.chunks[this.head++] = null;  // release it; compacted below
        this.offset = 0;
      }
    }
    // Drop consumed slots only once they are at least half the array. Audio
    // arrives far faster than it plays, so nearly the whole reply is queued: a
    // compaction every fixed number of chunks copied everything still waiting,
    // O(queued) each time. Waiting until the consumed part dominates means a
    // compaction copies fewer slots than were consumed since the last one —
    // amortized O(1) per chunk however long the reply.
    if (this.head > 256 && this.head * 2 > this.chunks.length) {
      this.chunks = this.chunks.slice(this.head);
      this.head = 0;
    }
    out.fill(0, written);

    // The fades see a dry spell that begins partway through a quantum. One that
    // began exactly on a quantum boundary would step unfaded; measured, none of
    // 242 pauses in a voice's arrival ended on one, so it is left.
    const missing = out.length - written;
    const resuming = written > 0 && this.dry;
    const runningDry = missing > 0 && written > 0 && !this.ended;
    if (resuming) {
      // Back from a dropout: ramp up from silence rather than step into speech.
      const n = Math.min(written, FADE_SAMPLES);
      for (let i = 0; i < n; i++) out[i] *= (i + 1) / (n + 1);
    }
    if (runningDry) {
      // About to fall silent: ramp the last of the audio down to it.
      const n = Math.min(written, FADE_SAMPLES);
      for (let i = 0; i < n; i++) out[written - n + i] *= (n - i) / (n + 1);
    }

    if (written > 0) {
      this.started = true;
      this.dry = false;
    }
    this.read += written;
    this.queued -= written;
    if (missing && this.started && !this.ended) {
      if (!this.dry) {
        this.gaps += 1;
        if (this.firstGapAt === null) this.firstGapAt = this.read;
      }
      this.dry = true;
      this.gapFrames += missing;
    }
    return written;
  }
}

// How often a playing stream reports how far it has got: every 8 render quanta,
// ~43 ms at 24 kHz. Often enough for words to light up as they are said; each
// report is one small message across threads.
export const POSITION_QUANTA = 8;

// The processor's behaviour, independent of AudioWorkletProcessor. `post` sends
// a message back to the page; `rate` is the context's sample rate.
//
// Page -> here:  start {stream} · chunk {stream, samples} · end {stream} · drop {stream}
//                stop {stream}
// Here -> page:  playing {stream} once audio is audible · finished {stream, gaps, gapMs}
//                stopped {stream, played} — samples played, or null if it had already finished
//                position {stream, played} — samples played so far, while playing
export function createPlayback(post, rate, startMs = START_MS) {
  const startSamples = Math.round((startMs * rate) / 1000);
  let stream = null;  // { id, queue, playing, played }

  return {
    message(msg) {
      if (msg.type === "start") {
        // A new stream replaces whatever was playing, immediately.
        stream = { id: msg.stream, queue: new PcmQueue(startSamples), playing: false, played: 0, quanta: 0 };
        return;
      }
      if (msg.type === "stop") {
        // Always answered, even for a stream this thread has already finished:
        // the page is waiting on it to say how much of the reply was heard, and
        // "all of it" is an answer too.
        const current = stream && msg.stream === stream.id;
        post({ type: "stopped", stream: msg.stream, played: current ? stream.played : null });
        if (current) stream = null;
        return;
      }
      if (!stream || msg.stream !== stream.id) return;  // for a stream already replaced
      if (msg.type === "chunk") stream.queue.push(msg.samples);
      else if (msg.type === "end") stream.queue.end();
      else if (msg.type === "drop") stream = null;
    },

    process(out) {
      if (!stream) {
        out.fill(0);
        return;
      }
      const s = stream;
      const written = s.queue.pull(out);
      // Counted here, at the moment samples leave for the speaker — the only
      // place that knows how much of a reply was actually played.
      s.played += written;
      if (written > 0 && !s.playing) {
        s.playing = true;
        post({ type: "playing", stream: s.id });
      }
      // Counted in samples played, not time passed: a stream that ran dry
      // mid-reply has not moved on, and the words must not either.
      if (written > 0 && ++s.quanta % POSITION_QUANTA === 0) {
        post({ type: "position", stream: s.id, played: s.played });
      }
      if (s.queue.drained) {
        stream = null;
        post({
          type: "finished",
          stream: s.id,
          gaps: s.queue.gaps,
          gapMs: Math.round((s.queue.gapFrames * 1000) / rate),
          // Where in the reply it first ran dry: about 1.2 s in is a join between
          // the voice's pieces, anywhere else a network stall.
          firstGapMs:
            s.queue.firstGapAt === null ? null : Math.round((s.queue.firstGapAt * 1000) / rate),
        });
      }
    },
  };
}

if (typeof registerProcessor === "function") {
  class Playback extends AudioWorkletProcessor {
    constructor() {
      super();
      this.playback = createPlayback((msg) => this.port.postMessage(msg), sampleRate);
      this.port.onmessage = (event) => this.playback.message(event.data);
    }

    process(inputs, outputs) {
      this.playback.process(outputs[0][0]);
      return true;
    }
  }
  registerProcessor("playback", Playback);
}
