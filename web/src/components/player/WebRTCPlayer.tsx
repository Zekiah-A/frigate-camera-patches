import { FrigateConfig } from "@/types/frigateConfig";
import useSWR from "swr";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { baseUrl } from "@/api/baseUrl";
import { LivePlayerError, PlayerStatsType } from "@/types/live";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type WebRtcPlayerProps = {
  className?: string;
  camera: string;
  playbackEnabled?: boolean;
  audioEnabled?: boolean;
  volume?: number;
  microphoneEnabled?: boolean;
  iOSCompatFullScreen?: boolean; // ios doesn't support fullscreen divs so we must support the video element
  pip?: boolean;
  getStats?: boolean;
  setStats?: (stats: PlayerStatsType) => void;
  onPlaying?: () => void;
  onError?: (error: LivePlayerError) => void;
};

export default function WebRtcPlayer({
  className,
  camera,
  playbackEnabled = true,
  audioEnabled = false,
  volume,
  microphoneEnabled = false,
  iOSCompatFullScreen = false,
  pip = false,
  getStats = false,
  setStats,
  onPlaying,
  onError,
}: WebRtcPlayerProps) {
  const { t } = useTranslation("views/live");

  const { data: config } = useSWR<FrigateConfig>("config");
  const iceServers = config?.go2rtc?.webrtc?.ice_servers;

  // metadata

  const wsURL = useMemo(() => {
    return `${baseUrl.replace(/^http/, "ws")}live/webrtc/api/ws?src=${camera}`;
  }, [camera]);

  // error handler
  const handleError = useCallback(
    (error: LivePlayerError, description: string = "Unknown error") => {
      // eslint-disable-next-line no-console
      console.error(
        `${camera} - WebRTC error '${error}': ${description} See the documentation: https://docs.frigate.video/configuration/live/#live-player-error-messages`,
      );
      onError?.(error);
    },
    [camera, onError],
  );

  // camera states

  const pcRef = useRef<RTCPeerConnection | undefined>(undefined);
  const wsRef = useRef<WebSocket | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [bufferTimeout, setBufferTimeout] = useState<NodeJS.Timeout>();
  const videoLoadTimeoutRef = useRef<NodeJS.Timeout>(undefined);

  const PeerConnection = useCallback(
    async (media: string, signal: AbortSignal) => {
      if (!videoRef.current) {
        return;
      }

      const pc = new RTCPeerConnection({
        bundlePolicy: "max-bundle",
        iceServers: iceServers ?? [{ urls: "stun:stun.l.google.com:19302" }],
      });

      const localTracks = [];

      if (/camera|microphone/.test(media)) {
        const tracks = await getMediaTracks("user", {
          video: media.indexOf("camera") >= 0,
          audio: media.indexOf("microphone") >= 0,
        });
        if (signal.aborted || !tracks.length) {
          tracks.forEach((track) => track.stop());
          pc.close();
          if (!signal.aborted) toast.error(t("twoWayTalk.captureError"));
          return;
        }
        tracks.forEach((track) => {
          pc.addTransceiver(track, { direction: "sendonly" });
          if (track.kind === "video") localTracks.push(track);
        });
      }

      if (media.indexOf("display") >= 0) {
        const tracks = await getMediaTracks("display", {
          video: true,
          audio: media.indexOf("speaker") >= 0,
        });
        tracks.forEach((track) => {
          pc.addTransceiver(track, { direction: "sendonly" });
          if (track.kind === "video") localTracks.push(track);
        });
      }

      if (/video|audio/.test(media)) {
        const tracks = ["video", "audio"]
          .filter((kind) => media.indexOf(kind) >= 0)
          .map(
            (kind) =>
              pc.addTransceiver(kind, { direction: "recvonly" }).receiver.track,
          );
        localTracks.push(...tracks);
      }

      videoRef.current.srcObject = new MediaStream(localTracks);
      return pc;
    },
    [videoRef, t, iceServers],
  );

  async function getMediaTracks(
    media: string,
    constraints: MediaStreamConstraints,
  ) {
    try {
      const stream =
        media === "user"
          ? await navigator.mediaDevices.getUserMedia(constraints)
          : await navigator.mediaDevices.getDisplayMedia(constraints);
      return stream.getTracks();
    } catch (e) {
      return [];
    }
  }

  const connect = useCallback(
    (pc: RTCPeerConnection) => {
      pcRef.current = pc;
      wsRef.current = new WebSocket(wsURL);
      const ws = wsRef.current;

      ws.addEventListener("open", () => {
        pc?.addEventListener("icecandidate", (ev) => {
          if (!ev.candidate) return;
          const msg = {
            type: "webrtc/candidate",
            value: ev.candidate.candidate,
          };
          ws.send(JSON.stringify(msg));
        });

        pcRef.current
          ?.createOffer()
          .then((offer) => pc?.setLocalDescription(offer))
          .then(() => {
            const msg = {
              type: "webrtc/offer",
              value: pc?.localDescription?.sdp,
            };
            ws.send(JSON.stringify(msg));
          });
      });

      ws.addEventListener("message", (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "webrtc/candidate") {
          pc?.addIceCandidate({ candidate: msg.value, sdpMid: "0" });
        } else if (msg.type === "webrtc/answer") {
          pc?.setRemoteDescription({
            type: "answer",
            sdp: msg.value,
          });
        }
      });
    },
    [wsURL],
  );

  useEffect(() => {
    if (!videoRef.current) {
      return;
    }

    if (!playbackEnabled) {
      return;
    }

    const abort = new AbortController();
    void PeerConnection(
      microphoneEnabled ? "video+audio+microphone" : "video+audio",
      abort.signal,
    ).then((pc) => {
      if (!pc) return;
      if (abort.signal.aborted) {
        pc.getSenders().forEach((sender) => sender.track?.stop());
        pc.close();
      } else {
        connect(pc);
      }
    });

    return () => {
      abort.abort();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (pcRef.current) {
        pcRef.current.getSenders().forEach((sender) => sender.track?.stop());
        pcRef.current.close();
        pcRef.current = undefined;
      }
    };
  }, [
    camera,
    connect,
    PeerConnection,
    pcRef,
    videoRef,
    playbackEnabled,
    microphoneEnabled,
  ]);

  // ios compat

  const [iOSCompatControls, setiOSCompatControls] = useState(false);

  // control pip

  useEffect(() => {
    if (!videoRef.current || !pip) {
      return;
    }

    videoRef.current.requestPictureInPicture();
  }, [pip, videoRef]);

  // control volume

  useEffect(() => {
    if (!videoRef.current || volume == undefined) {
      return;
    }

    videoRef.current.volume = volume;
  }, [volume, videoRef]);

  useEffect(() => {
    videoLoadTimeoutRef.current = setTimeout(() => {
      handleError("stalled", "WebRTC connection timed out.");
    }, 5000);

    return () => {
      if (videoLoadTimeoutRef.current) {
        clearTimeout(videoLoadTimeoutRef.current);
      }
    };
    // we know that these deps are correct
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLoadedData = () => {
    if (videoLoadTimeoutRef.current) {
      clearTimeout(videoLoadTimeoutRef.current);
    }
    onPlaying?.();
  };

  // stats

  useEffect(() => {
    if (!pcRef.current || !getStats) return;

    let lastBytesReceived = 0;
    let lastTimestamp = 0;

    const interval = setInterval(async () => {
      if (pcRef.current && videoRef.current && !videoRef.current.paused) {
        const report = await pcRef.current.getStats();
        let bytesReceived = 0;
        let timestamp = 0;
        let roundTripTime = 0;
        let framesReceived = 0;
        let framesDropped = 0;
        let framesDecoded = 0;

        report.forEach((stat) => {
          if (stat.type === "inbound-rtp" && stat.kind === "video") {
            bytesReceived = stat.bytesReceived;
            timestamp = stat.timestamp;
            framesReceived = stat.framesReceived;
            framesDropped = stat.framesDropped;
            framesDecoded = stat.framesDecoded;
          }
          if (stat.type === "candidate-pair" && stat.state === "succeeded") {
            roundTripTime = stat.currentRoundTripTime;
          }
        });

        const timeDiff = (timestamp - lastTimestamp) / 1000; // in seconds
        const bitrate =
          timeDiff > 0
            ? (bytesReceived - lastBytesReceived) / timeDiff / 1000
            : 0; // in kBps

        setStats?.({
          streamType: "WebRTC",
          bandwidth: Math.round(bitrate),
          latency: roundTripTime,
          totalFrames: framesReceived,
          droppedFrames: framesDropped,
          decodedFrames: framesDecoded,
          droppedFrameRate:
            framesReceived > 0 ? (framesDropped / framesReceived) * 100 : 0,
        });

        lastBytesReceived = bytesReceived;
        lastTimestamp = timestamp;
      }
    }, 1000);

    return () => {
      clearInterval(interval);
      setStats?.({
        streamType: "-",
        bandwidth: 0,
        latency: undefined,
        totalFrames: 0,
        droppedFrames: undefined,
        decodedFrames: 0,
        droppedFrameRate: 0,
      });
    };
    // we need to listen on the value of the ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pcRef, pcRef.current, getStats]);

  return (
    <video
      ref={videoRef}
      className={className}
      controls={iOSCompatControls}
      autoPlay
      playsInline
      muted={!audioEnabled}
      onLoadedData={handleLoadedData}
      onProgress={
        onError != undefined
          ? () => {
              if (videoRef.current?.paused) {
                return;
              }

              if (bufferTimeout) {
                clearTimeout(bufferTimeout);
                setBufferTimeout(undefined);
              }

              setBufferTimeout(
                setTimeout(() => {
                  if (
                    document.visibilityState === "visible" &&
                    pcRef.current != undefined
                  ) {
                    handleError(
                      "stalled",
                      "Media playback has stalled after 3 seconds due to insufficient buffering or a network interruption.",
                    );
                  }
                }, 3000),
              );
            }
          : undefined
      }
      onClick={
        iOSCompatFullScreen
          ? () => setiOSCompatControls(!iOSCompatControls)
          : undefined
      }
      onError={(e) => {
        if (
          // @ts-expect-error code does exist
          e.target.error.code == MediaError.MEDIA_ERR_NETWORK
        ) {
          handleError("startup", "Browser reported a network error.");
        }
      }}
    />
  );
}
