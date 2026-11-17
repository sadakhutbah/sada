import type Hls from "hls.js";
import { csrf } from "./api";

/** Plays one control room monitor: WebRTC (WHEP) first, HLS as fallback, both through the back
 *  office proxy (/api/v1/live/watch/<which>/…), which checks the session and the `live` right.
 *  Returns a stop function. `onState` reports "connecting" | "webrtc" | "hls" | "offline". */
export type MonitorState = "connecting" | "webrtc" | "hls" | "offline";

const RETRY_MS = 5000;
const ICE_WAIT_MS = 1500;

export function playMonitor(which: "source" | "program", video: HTMLVideoElement, onState: (s: MonitorState) => void) {
  const base = `/api/v1/live/watch/${which}`;
  let stopped = false;
  let pc: RTCPeerConnection | null = null;
  let hls: Hls | null = null;
  let session: string | null = null;
  let timer = 0;

  const cleanup = () => {
    window.clearTimeout(timer);
    if (pc) { pc.close(); pc = null; }
    if (session) {
      fetch(session, { method: "DELETE", headers: { "X-CSRF": csrf() }, credentials: "same-origin", keepalive: true }).catch(() => {});
      session = null;
    }
    if (hls) { hls.destroy(); hls = null; }
    video.srcObject = null;
    video.removeAttribute("src");
  };

  const later = () => {
    cleanup();
    if (stopped) return;
    onState("offline");
    timer = window.setTimeout(start, RETRY_MS);
  };

  const startHls = async () => {
    cleanup();
    if (stopped) return;
    const url = `${base}/hls/index.m3u8`;
    // hls.js is only fetched when WebRTC is not available (kept out of the main bundle)
    const { default: HlsJs } = await import("hls.js/light");
    if (stopped) return;
    if (HlsJs.isSupported()) {
      const player = new HlsJs({ liveSyncDurationCount: 2, lowLatencyMode: true, manifestLoadingMaxRetry: 1 });
      hls = player;
      player.on(HlsJs.Events.MANIFEST_PARSED, () => { onState("hls"); video.play().catch(() => {}); });
      player.on(HlsJs.Events.ERROR, (_evt, data) => { if (data.fatal) later(); });
      player.loadSource(url);
      player.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      video.onloadeddata = () => onState("hls");
      video.onerror = () => later();
      video.play().catch(() => {});
    } else {
      later();
    }
  };

  async function start() {
    if (stopped) return;
    onState("connecting");
    if (typeof RTCPeerConnection === "undefined") return void startHls();
    const conn = new RTCPeerConnection();
    pc = conn;
    conn.addTransceiver("video", { direction: "recvonly" });
    conn.addTransceiver("audio", { direction: "recvonly" });
    const stream = new MediaStream();
    conn.ontrack = (e) => {
      stream.addTrack(e.track);
      video.srcObject = stream;
      video.play().catch(() => {});
    };
    conn.onconnectionstatechange = () => {
      if (pc !== conn) return;
      if (conn.connectionState === "connected") onState("webrtc");
      // media blocked (UDP filtered, no public address announced): try HLS
      if (conn.connectionState === "failed") void startHls();
      if (conn.connectionState === "disconnected") later();
    };
    try {
      await conn.setLocalDescription(await conn.createOffer());
      await new Promise<void>((done) => {
        if (conn.iceGatheringState === "complete") return done();
        const t = window.setTimeout(done, ICE_WAIT_MS);
        conn.onicegatheringstatechange = () => {
          if (conn.iceGatheringState === "complete") { window.clearTimeout(t); done(); }
        };
      });
      const r = await fetch(`${base}/whep`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/sdp", "X-CSRF": csrf() },
        body: conn.localDescription?.sdp ?? "",
      });
      if (stopped || pc !== conn) return;
      if (r.status === 404) return later();
      if (r.status !== 201) return void startHls();
      session = r.headers.get("Location");
      await conn.setRemoteDescription({ type: "answer", sdp: await r.text() });
    } catch {
      if (!stopped && pc === conn) void startHls();
    }
  }

  start();
  return () => { stopped = true; cleanup(); };
}
