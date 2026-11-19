import { Button } from "@/components/ui/button";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { FaMicrophone, FaMicrophoneSlash } from "react-icons/fa";

export default function HoldToTalk({
  active,
  disabled,
  setActive,
}: {
  active: boolean;
  disabled: boolean;
  setActive: (value: boolean) => void;
}) {
  const { t } = useTranslation("views/live");
  useEffect(() => {
    const stop = () => setActive(false);
    const visibility = () => {
      if (document.hidden) stop();
    };
    if (disabled) stop();
    window.addEventListener("blur", stop);
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("blur", stop);
      window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", visibility);
      stop();
    };
  }, [disabled, setActive]);
  return (
    <Button
      type="button"
      size="icon"
      className="touch-none select-none"
      title={t("twoWayTalk.hold")}
      aria-label={t("twoWayTalk.hold")}
      aria-pressed={active}
      disabled={disabled}
      onPointerDown={(event) => {
        if (!event.isPrimary || event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setActive(true);
      }}
      onPointerUp={() => setActive(false)}
      onLostPointerCapture={() => setActive(false)}
      onPointerCancel={() => setActive(false)}
      onBlur={() => setActive(false)}
      onContextMenu={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          if (!event.repeat) setActive(true);
        }
      }}
      onKeyUp={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          setActive(false);
        }
      }}
    >
      {active ? <FaMicrophone /> : <FaMicrophoneSlash />}
    </Button>
  );
}
