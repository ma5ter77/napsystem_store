// NAP-109: playback engine for the session player.
//
// The track streams through an <audio> element (HTTP Range, so playback starts at once and seeking does not wait for
// the whole file) and is routed through Web Audio with the exact channel count and "discrete" interpretation:
// no downmix to mono, no channel swap, no crossfeed. Memory stays flat for a 120-minute file.
//
// In the background a full copy is saved to the browser's Cache Storage (written to disk) and its SHA-256 is checked
// against the server's value. Once verified, playback moves to the local copy at the same position (at the next pause,
// or at once when the connection drops), so a lost connection no longer stops the session. A copy that does not
// match is never played.

export type OutputInfo = { maxChannels: number };

/** How many output channels the browser offers right now (the system's output device, e.g. 2 for headphones). */
export function detectOutput(): OutputInfo {
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new Ctx();
    const n = ctx.destination.maxChannelCount || 2;
    ctx.close().catch(() => {});
    return { maxChannels: n };
  } catch {
    return { maxChannels: 2 };
  }
}

export type OfflineState = { state: "idle" | "saving" | "ready" | "too_large" | "unavailable" | "mismatch"; progress: number };

type Opts = {
  url: string;
  channels: number;
  sha256: string;
  sizeBytes: number;
  /** a fresh URL when the signed link has expired */
  renew: () => Promise<string>;
  onEnded: () => void;
  onOffline: (s: OfflineState) => void;
  onWaiting?: (waiting: boolean) => void;
  onFatal: (code: "decode" | "channels" | "network" | "checksum") => void;
};

// the offline copy is kept only while the session page is open; larger files play from the network
export const OFFLINE_LIMIT_BYTES = 2 * 1024 ** 3;
const CACHE = "nap-session-audio";

export class Playback {
  readonly el: HTMLAudioElement;
  private ctx: AudioContext;
  private node: MediaElementAudioSourceNode | null = null;
  private opts: Opts;
  private local: string | null = null;
  private usingLocal = false;
  private cacheKey: string;
  private renewing = false;
  private closed = false;
  private abort = new AbortController();
  offline: OfflineState = { state: "idle", progress: 0 };

  constructor(opts: Opts) {
    this.opts = opts;
    this.cacheKey = `/__nap-offline/${opts.sha256}`;
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    this.ctx = new Ctx({ sampleRate: 48000 });
    this.el = new Audio();
    this.el.preload = "auto";
    this.el.addEventListener("ended", () => this.opts.onEnded());
    this.el.addEventListener("waiting", () => { this.opts.onWaiting?.(true); if (!navigator.onLine) this.useLocal(); });
    this.el.addEventListener("pause", () => this.useLocal());
    window.addEventListener("offline", this.onNetLost);
    this.el.addEventListener("playing", () => this.opts.onWaiting?.(false));
    this.el.addEventListener("error", () => this.onError());
  }

  /** Loads metadata and wires the audio graph. Resolves with the duration in seconds. */
  async load(): Promise<number> {
    this.el.src = this.opts.url;
    await new Promise<void>((resolve, reject) => {
      const ok = () => { cleanup(); resolve(); };
      const bad = () => { cleanup(); reject(new Error("decode")); };
      const cleanup = () => { this.el.removeEventListener("loadedmetadata", ok); this.el.removeEventListener("error", bad); };
      this.el.addEventListener("loadedmetadata", ok);
      this.el.addEventListener("error", bad);
    });
    const max = this.ctx.destination.maxChannelCount || 2;
    if (this.opts.channels > max) { this.opts.onFatal("channels"); throw new Error("channels"); }
    const dst = this.ctx.destination;
    dst.channelCount = this.opts.channels;
    dst.channelCountMode = "explicit";
    dst.channelInterpretation = "discrete";   // no implicit up/down-mix
    this.node = this.ctx.createMediaElementSource(this.el);
    this.node.channelCountMode = "explicit";
    this.node.channelCount = this.opts.channels;
    this.node.channelInterpretation = "discrete";
    this.node.connect(dst);
    this.saveOffline();
    return this.el.duration;
  }

  get position() { return this.el.currentTime; }
  get duration() { return this.el.duration; }
  get playing() { return !this.el.paused && !this.el.ended; }
  get channels() { return this.opts.channels; }

  async play(from?: number) {
    if (from !== undefined) this.el.currentTime = from;
    await this.ctx.resume();
    await this.el.play();
  }

  pause() { this.el.pause(); }

  private onNetLost = () => { this.useLocal(); };

  /** Moves playback to the verified local copy, if there is one and it is not in use yet. */
  private useLocal() {
    if (!this.local || this.usingLocal || this.closed) return false;
    this.usingLocal = true;
    this.swap(this.local).catch(() => this.opts.onFatal("decode"));
    return true;
  }

  seek(to: number) { this.el.currentTime = Math.max(0, Math.min(to, (this.el.duration || 0) - 0.25)); }

  close() {
    this.closed = true;
    this.abort.abort();
    window.removeEventListener("offline", this.onNetLost);
    this.el.pause();
    this.el.removeAttribute("src");
    this.el.load();
    this.ctx.close().catch(() => {});
    if (this.local) URL.revokeObjectURL(this.local);
    caches?.open(CACHE).then((c) => c.delete(this.cacheKey)).catch(() => {});
  }

  /** Switch source keeping position and play state (signed link renewed, or the local copy became ready). */
  private async swap(src: string) {
    const at = this.el.currentTime, wasPlaying = this.playing;
    this.el.src = src;
    await new Promise<void>((r) => this.el.addEventListener("loadedmetadata", () => r(), { once: true }));
    this.el.currentTime = at;
    if (wasPlaying) await this.el.play().catch(() => {});
  }

  private async onError() {
    if (this.closed || !this.node) return;           // errors before load() are handled there
    const code = this.el.error?.code;
    if (code === MediaError.MEDIA_ERR_DECODE || code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED) {
      if (this.usingLocal || this.renewing) { this.opts.onFatal("decode"); return; }
    }
    if (this.usingLocal) { this.opts.onFatal("decode"); return; }
    if (this.useLocal()) return;                     // a verified copy is on disk: continue from it
    if (this.renewing) return;
    // most likely the 5-minute signed link expired during a Range request: get a new one and continue
    this.renewing = true;
    try {
      if (!navigator.onLine) { this.opts.onFatal("network"); return; }
      await this.swap(await this.opts.renew());
    } catch {
      this.opts.onFatal("network");
    } finally {
      this.renewing = false;
    }
  }

  private setOffline(s: OfflineState) { this.offline = s; this.opts.onOffline(s); }

  private async saveOffline() {
    if (this.opts.sizeBytes > OFFLINE_LIMIT_BYTES) { this.setOffline({ state: "too_large", progress: 0 }); return; }
    if (typeof caches === "undefined") { this.setOffline({ state: "unavailable", progress: 0 }); return; }
    try {
      const cache = await caches.open(CACHE);
      let res = await fetch(this.opts.url, { signal: this.abort.signal, credentials: "include" });
      if (res.status === 410 || res.status === 403) res = await fetch(await this.opts.renew(), { signal: this.abort.signal, credentials: "include" });
      if (!res.ok || !res.body) throw new Error("fetch");
      const { createSHA256 } = await import("hash-wasm");
      const h = await createSHA256();
      h.init();
      let got = 0;
      const total = this.opts.sizeBytes || Number(res.headers.get("content-length")) || 0;
      this.setOffline({ state: "saving", progress: 0 });
      // one stream: hashed as it goes and written to disk by the cache, never held whole in memory
      const counted = res.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
        transform: (chunk, ctl) => {
          h.update(chunk); got += chunk.byteLength;
          if (total) this.setOffline({ state: "saving", progress: Math.min(1, got / total) });
          ctl.enqueue(chunk);
        },
      }));
      await cache.put(this.cacheKey, new Response(counted, { headers: { "Content-Type": res.headers.get("content-type") || "audio/flac" } }));
      if (h.digest("hex") !== this.opts.sha256) {
        await cache.delete(this.cacheKey);
        this.setOffline({ state: "mismatch", progress: 0 });
        this.opts.onFatal("checksum");
        return;
      }
      const blob = await (await cache.match(this.cacheKey))!.blob();
      if (this.closed) return;
      this.local = URL.createObjectURL(blob);
      this.setOffline({ state: "ready", progress: 1 });
      // switch now if nothing is playing; while playing, switch at the next pause or when the network drops
      if (!this.playing || !navigator.onLine) this.useLocal();
    } catch {
      if (!this.closed) this.setOffline({ state: "unavailable", progress: 0 });
    }
  }
}
