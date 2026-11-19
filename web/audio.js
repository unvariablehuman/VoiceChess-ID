/*
 * VoiceChess-ID - pengolahan audio di browser.
 * Endpointer = terjemahan langsung dari Endpointer di app/asr.py (aplikasi desktop),
 * supaya versi web memotong perintah dengan aturan yang sama.
 */
(function (root) {
  const SR = 16000;
  const BLOCK = 480;              // 30 ms
  const START_MS = 150;
  const END_SILENCE_MS = 700;
  const MAX_UTTER_S = 8.0;
  const PRE_ROLL_MS = 300;
  const MIN_UTTER_S = 0.35;
  const HISTORY_BLOCKS = Math.floor(5000 / 30);
  const DIGITAL_SILENCE_DB = -85;

  function blockDb(block) {
    let s = 0;
    for (let i = 0; i < block.length; i++) s += block[i] * block[i];
    return 10 * Math.log10(s / block.length + 1e-10);
  }

  function percentile(arr, p) {
    const a = arr.slice().sort((x, y) => x - y);
    const idx = (a.length - 1) * p / 100;
    const lo = Math.floor(idx), hi = Math.ceil(idx);
    return a[lo] + (a[hi] - a[lo]) * (idx - lo);   // sama dengan numpy (linear)
  }

  class Endpointer {
    constructor(marginDb = 10) {
      this.marginDb = marginDb;
      this.hist = [];
      this.speaking = false;
      this.voicedRun = 0;
      this.silenceRun = 0;
      this.buf = [];
      this.pre = [];
      this.preMax = Math.max(1, Math.floor(PRE_ROLL_MS * SR / 1000 / BLOCK));
    }
    get noiseDb() { return this.hist.length ? percentile(this.hist, 10) : null; }
    _pushPre(b) { this.pre.push(b); if (this.pre.length > this.preMax) this.pre.shift(); }

    /** blok Float32Array(480) -> null | "start" | {audio: Float32Array} */
    feed(block) {
      const db = blockDb(block);
      if (db > DIGITAL_SILENCE_DB) {
        this.hist.push(db);
        if (this.hist.length > HISTORY_BLOCKS) this.hist.shift();
      }
      if (this.hist.length < 10) { this._pushPre(block); return null; }
      const voiced = db > this.noiseDb + this.marginDb;
      if (!this.speaking) {
        this._pushPre(block);
        this.voicedRun = voiced ? this.voicedRun + 1 : 0;
        if (Math.floor(this.voicedRun * BLOCK * 1000 / SR) >= START_MS) {
          this.speaking = true; this.silenceRun = 0;
          this.buf = this.pre.slice();
          return "start";
        }
        return null;
      }
      this.buf.push(block);
      this.silenceRun = voiced ? 0 : this.silenceRun + 1;
      const dur = this.buf.length * BLOCK / SR;
      if (Math.floor(this.silenceRun * BLOCK * 1000 / SR) >= END_SILENCE_MS || dur >= MAX_UTTER_S) {
        const audio = concat(this.buf);
        this.speaking = false; this.voicedRun = 0; this.buf = []; this.pre = [];
        return { audio };
      }
      return null;
    }
  }

  function concat(blocks) {
    let n = 0; for (const b of blocks) n += b.length;
    const out = new Float32Array(n); let o = 0;
    for (const b of blocks) { out.set(b, o); o += b.length; }
    return out;
  }

  /** Ubah laju sampel (mis. 48000 -> 16000) dengan rata-rata per jendela. Menyimpan sisa. */
  class Resampler {
    constructor(inRate, outRate = SR) { this.ratio = inRate / outRate; this.pos = 0; this.carry = []; }
    process(input) {
      const data = this.carry.length ? concat([Float32Array.from(this.carry), input]) : input;
      const out = [];
      let pos = this.pos;
      while (pos + this.ratio <= data.length) {
        const a = Math.floor(pos), b = Math.floor(pos + this.ratio);
        let s = 0; for (let i = a; i < b; i++) s += data[i];
        out.push(b > a ? s / (b - a) : data[a]);
        pos += this.ratio;
      }
      const keepFrom = Math.floor(pos);
      this.carry = Array.from(data.subarray(keepFrom));
      this.pos = pos - keepFrom;
      return Float32Array.from(out);
    }
  }

  /** Float32 mono 16 kHz -> WAV PCM 16-bit (Uint8Array) */
  function encodeWav(samples, rate = SR) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const x = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Uint8Array(buf);
  }

  const api = { SR, BLOCK, MIN_UTTER_S, Endpointer, Resampler, encodeWav, concat };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VCAudio = api;
})(typeof window !== "undefined" ? window : globalThis);
