import { useWsUpdate } from "@/api/ws";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { useTranslation } from "react-i18next";

const REFRESH_INTERVAL = 180;
const DEAD_ZONE = 0.17;
const ARROWS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
let nextGesture = 0;

export default function PtzJoystick({ camera }: { camera: string }) {
  const { t } = useTranslation(["views/live"]);
  const instructionsId = useId();
  const send = useWsUpdate();
  const gesture = useRef<number | null>(null);
  const input = useRef<number | "keyboard" | null>(null);
  const keys = useRef(new Set<string>());
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const vector = useRef({ x: 0, y: 0 });
  const [position, setPosition] = useState(vector.current);

  const command = useCallback(
    (phase: "start" | "move" | "stop") => {
      if (gesture.current === null) return;
      send(
        {
          topic: `${camera}/ptz_joystick`,
          payload: { phase, gesture: gesture.current, ...vector.current },
          retain: false,
        },
        // A queued start could be replayed after the operator let go.
        false,
      );
    },
    [camera, send],
  );

  const start = (source: number | "keyboard") => {
    input.current = source;
    gesture.current = nextGesture++;
    command("start");
    timer.current = setInterval(() => command("move"), REFRESH_INTERVAL);
  };

  const stop = useCallback(() => {
    if (gesture.current === null) return;
    if (timer.current !== null) clearInterval(timer.current);
    timer.current = null;
    command("stop");
    gesture.current = null;
    input.current = null;
    keys.current.clear();
    vector.current = { x: 0, y: 0 };
    setPosition(vector.current);
  }, [command]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden) stop();
    };

    window.addEventListener("blur", stop);
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("blur", stop);
      window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      stop();
    };
  }, [stop]);

  const setVector = (x: number, y: number) => {
    const magnitude = Math.hypot(x, y);
    const scale = magnitude < DEAD_ZONE ? 0 : 1 / Math.max(magnitude, 1);
    vector.current = { x: x * scale, y: y * scale };
    setPosition(vector.current);
  };

  const updatePointer = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    setVector(
      (event.clientX - bounds.left - bounds.width / 2) / (bounds.width * 0.36),
      (bounds.top + bounds.height / 2 - event.clientY) / (bounds.height * 0.36),
    );
  };

  const updateKeyboard = () => {
    const held = keys.current;
    setVector(
      Number(held.has("ArrowRight")) - Number(held.has("ArrowLeft")),
      Number(held.has("ArrowUp")) - Number(held.has("ArrowDown")),
    );
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && input.current !== null) {
      event.preventDefault();
      event.stopPropagation();
      stop();
      return;
    }
    if (
      !ARROWS.includes(event.key) ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat || typeof input.current === "number") return;
    keys.current.add(event.key);
    updateKeyboard();
    if (input.current === null) start("keyboard");
  };

  const onKeyUp = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!ARROWS.includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (input.current !== "keyboard") return;
    keys.current.delete(event.key);
    if (keys.current.size) {
      updateKeyboard();
    } else {
      stop();
    }
  };

  const stopPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerId === input.current) stop();
  };

  return (
    <div
      role="group"
      aria-label={t("ptz.joystick.label")}
      aria-describedby={instructionsId}
      title={t("ptz.joystick.instructions")}
      tabIndex={0}
      className="relative size-20 shrink-0 touch-none rounded-full border border-border bg-background/90 text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
      onPointerDown={(event) => {
        if (input.current !== null || event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        updatePointer(event);
        start(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (event.pointerId === input.current) updatePointer(event);
      }}
      onPointerUp={stopPointer}
      onPointerCancel={stopPointer}
      onLostPointerCapture={stopPointer}
      onKeyDown={onKeyDown}
      onKeyUp={onKeyUp}
      onBlur={stop}
    >
      <span id={instructionsId} className="sr-only">
        {t("ptz.joystick.instructions")}
      </span>
      <span aria-hidden className="pointer-events-none absolute inset-0">
        <span className="absolute left-1/2 top-0 -translate-x-1/2">↑</span>
        <span className="absolute bottom-0 left-1/2 -translate-x-1/2">↓</span>
        <span className="absolute left-1 top-1/2 -translate-y-1/2">←</span>
        <span className="absolute right-1 top-1/2 -translate-y-1/2">→</span>
        <span
          className="absolute left-1/2 top-1/2 -ml-3 -mt-3 size-6 rounded-full border border-primary bg-primary/30"
          style={{
            transform: `translate(${position.x * 23}px, ${-position.y * 23}px)`,
          }}
        />
      </span>
    </div>
  );
}
