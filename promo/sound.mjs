// The sound for a promo video, made from scratch so there is nothing to
// license: soft pops as words land, key clicks while the claim is typed, a
// rise while the check runs, a stamp when the verdict lands, a whoosh between
// scenes, a chime at the end. A trending song can still be laid on top in
// the Instagram or TikTok editor.
import fs from "node:fs";

const RATE = 44100;
const TAU = 2 * Math.PI;

// cues: [{ type, at, ...options }] with `at` in seconds of the finished video
export function soundtrack(cues, seconds, file) {
  const n = Math.ceil((seconds + 1) * RATE);
  const buf = new Float32Array(n);
  let seed = 11;
  const noise = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x3fffffff - 1; };
  const add = (at, dur, fn) => {
    const a = Math.max(0, Math.round(at * RATE)), b = Math.min(n, a + Math.round(dur * RATE));
    for (let i = a; i < b; i++) buf[i] += fn((i - a) / RATE);
  };

  const sounds = {
    pop({ at, k = 0 }) {
      const f0 = 620 * Math.pow(1.07, k % 5);
      let ph = 0;
      add(at, 0.09, (x) => {
        ph += TAU * f0 * (1 + 0.9 * Math.exp(-x / 0.01)) / RATE;
        return 0.15 * Math.min(1, x / 0.002) * Math.exp(-x / 0.028) * Math.sin(ph);
      });
    },
    click({ at }) {
      add(at, 0.014, (x) => 0.06 * Math.exp(-x / 0.0025) * noise()
        + 0.035 * Math.exp(-x / 0.003) * Math.sin(TAU * 2300 * x));
    },
    whoosh({ at, dur = 0.38, amp = 0.12 }) {
      let y = 0;
      add(at, dur, (x) => {
        const k = x / dur;
        y += (0.03 + 0.3 * k) * (noise() - y);
        return amp * 3 * Math.pow(Math.sin(Math.PI * k), 2) * y;
      });
    },
    riser({ at, dur }) {
      let ph = 0, y = 0;
      add(at, dur + 0.06, (x) => {
        const k = Math.min(1, x / dur);
        ph += TAU * 150 * Math.pow(4.5, k) / RATE;
        y += 0.12 * (noise() - y);
        const tail = x > dur ? Math.max(0, 1 - (x - dur) / 0.06) : 1;
        return k * k * tail * (0.07 * Math.sin(ph) + 0.06 * y);
      });
    },
    stamp({ at }) {
      let ph = 0;
      add(at, 0.5, (x) => {
        ph += TAU * (46 + 70 * Math.exp(-x / 0.025)) / RATE;
        return 0.55 * Math.exp(-x / 0.13) * Math.sin(ph) + 0.22 * Math.exp(-x / 0.006) * noise();
      });
    },
    chime({ at }) {
      add(at, 1.2, (x) => Math.exp(-x / 0.35) * Math.min(1, x / 0.004)
        * (0.07 * Math.sin(TAU * 880 * x) + 0.045 * Math.sin(TAU * 1318.5 * x) + 0.02 * Math.sin(TAU * 1760 * x)));
    },
  };
  for (const c of cues) sounds[c.type]?.(c);

  // 16-bit stereo WAV, softly limited so stacked sounds never clip
  const data = Buffer.alloc(n * 4);
  for (let i = 0; i < n; i++) {
    const v = Math.round(Math.tanh(buf[i] * 1.1) * 32000);
    data.writeInt16LE(v, i * 4);
    data.writeInt16LE(v, i * 4 + 2);
  }
  const head = Buffer.alloc(44);
  head.write("RIFF", 0); head.writeUInt32LE(36 + data.length, 4); head.write("WAVE", 8);
  head.write("fmt ", 12); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20); head.writeUInt16LE(2, 22);
  head.writeUInt32LE(RATE, 24); head.writeUInt32LE(RATE * 4, 28); head.writeUInt16LE(4, 32); head.writeUInt16LE(16, 34);
  head.write("data", 36); head.writeUInt32LE(data.length, 40);
  fs.writeFileSync(file, Buffer.concat([head, data]));
}
