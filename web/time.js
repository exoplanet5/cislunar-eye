// Page clock (ARCHITECTURE §6.1, §10, AM2): LIVE follows the wall clock inside the window; playback is capped at
// 0.1 s of wall time per frame; time always clamps to the data window (no extrapolation).
export const SPEEDS = [[1, '1×'], [60, '1 min / s'], [600, '10 min / s'], [3600, '1 h / s'], [21600, '6 h / s'], [86400, '1 d / s']];

export class Clock {
  constructor(t0, t1, now, wall = () => Date.now()) {
    Object.assign(this, { t0, t1, now, wall, t: now, rate: 3600, dir: 1, playing: false, live: false, lastLive: 0 });
  }

  get liveAvailable() { const n = this.wall(); return n >= this.t0 && n <= this.t1; }
  clamp(t) { return Math.max(this.t0, Math.min(this.t1, Number.isFinite(t) ? t : this.now)); }
  set(t) { this.live = false; this.t = this.clamp(t); }
  setLive(on) {
    this.live = on && this.liveAvailable;
    if (this.live) { this.playing = false; this.t = this.clamp(this.wall()); }
    return this.live;
  }

  // Advance by dt seconds of wall time; returns true when the epoch changed.
  tick(dt, frameMs = 0) {
    if (this.live) {
      if (frameMs - this.lastLive < 100) return false;            // LIVE redraws at 10 Hz
      this.lastLive = frameMs;
      if (!this.liveAvailable) { this.live = false; return false; }
      const t = this.clamp(this.wall()), changed = t !== this.t; this.t = t; return changed;
    }
    if (!this.playing || !dt) return false;
    const t = this.clamp(this.t + this.dir * this.rate * dt * 1000);
    if (t === this.t || t === this.t0 || t === this.t1) this.playing = false;
    const changed = t !== this.t; this.t = t; return changed;
  }
}
