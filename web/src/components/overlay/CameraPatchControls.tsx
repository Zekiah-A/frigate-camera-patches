import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useIsAdmin } from "@/hooks/use-is-admin";
import axios from "axios";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import useSWR from "swr";

type Control = {
  id: string;
  label: string;
  kind: "toggle" | "select" | "button";
  options: { label: string; value: string }[];
  duration?: { minimum: number; maximum: number; default: number } | null;
  duration_values?: string[];
  state_key?: string | null;
  readback: "device" | "ack" | "none";
  requires_admin: boolean;
};

type PatchState = {
  id: string;
  controls: Control[];
  state: Record<string, string | boolean | null>;
  available: boolean;
  error?: string | null;
};

function ControlRow({
  control,
  state,
  busy,
  submit,
}: {
  control: Control;
  state: PatchState["state"];
  busy: boolean;
  submit: (value: string | boolean | null, duration?: number) => Promise<void>;
}) {
  const { t } = useTranslation("views/live");
  const isAdmin = useIsAdmin();
  const [duration, setDuration] = useState(
    String(control.duration?.default ?? ""),
  );
  const value = state[control.state_key ?? control.id];
  const useDuration = (choice: string | boolean | null) =>
    !!control.duration &&
    (!control.duration_values?.length ||
      control.duration_values.includes(String(choice)));
  const validDuration =
    !control.duration ||
    (Number.isInteger(Number(duration)) &&
      Number(duration) >= control.duration.minimum &&
      Number(duration) <= control.duration.maximum);
  const disabled = busy || (control.requires_admin && !isAdmin);
  const send = (choice: string | boolean | null) =>
    submit(choice, useDuration(choice) ? Number(duration) : undefined);
  const status =
    typeof value === "boolean"
      ? t(value ? "patches.on" : "patches.off")
      : value;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-secondary p-2">
      {control.kind !== "button" && (
        <span className="font-medium">{control.label}</span>
      )}
      {control.readback !== "none" && (
        <span className="text-muted-foreground">
          {status ?? t("patches.unknown")}
        </span>
      )}
      {control.duration && (
        <label className="flex items-center gap-1 text-xs">
          {t("patches.seconds")}
          <Input
            type="number"
            className="h-8 w-20"
            min={control.duration.minimum}
            max={control.duration.maximum}
            step={1}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />
        </label>
      )}
      {control.kind === "select" ? (
        control.options.map((option) => (
          <Button
            key={option.value}
            size="sm"
            aria-pressed={value === option.value}
            disabled={disabled || (useDuration(option.value) && !validDuration)}
            onClick={() => void send(option.value)}
          >
            {option.label}
          </Button>
        ))
      ) : (
        <Button
          size="sm"
          disabled={
            disabled ||
            !validDuration ||
            (control.kind === "toggle" && typeof value !== "boolean")
          }
          onClick={() => void send(control.kind === "toggle" ? !value : null)}
        >
          {control.kind === "toggle"
            ? t(value ? "patches.turnOff" : "patches.turnOn")
            : control.label}
        </Button>
      )}
    </div>
  );
}

export default function CameraPatchControls({ camera }: { camera: string }) {
  const { t } = useTranslation("views/live");
  const path = encodeURIComponent(camera) + "/patches";
  const { data, error, mutate } = useSWR<{ patches: PatchState[] }>(path, {
    refreshInterval: 2500,
  });
  const [busy, setBusy] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const submit = async (
    patch: PatchState,
    control: Control,
    value: string | boolean | null,
    duration?: number,
  ) => {
    setBusy((previous) => [...previous, control.id]);
    setMessage("");
    try {
      await axios.post(
        path +
          "/" +
          encodeURIComponent(patch.id) +
          "/" +
          encodeURIComponent(control.id),
        { value, duration },
      );
      setMessage(t("patches.acknowledged", { control: control.label }));
    } catch {
      setMessage(t("patches.failed", { control: control.label }));
    } finally {
      setBusy((previous) => previous.filter((id) => id !== control.id));
      await mutate();
    }
  };
  return (
    <section
      aria-label={t("patches.controls")}
      className="max-h-[32dvh] overflow-y-auto p-2"
    >
      {(!data || error) && (
        <p role="status">
          {t(error ? "patches.unavailable" : "patches.loading")}
        </p>
      )}
      {data?.patches.map((patch) => (
        <div
          key={patch.id}
          className="grid gap-2 md:grid-cols-2 xl:grid-cols-3"
        >
          {(!patch.available || patch.error) && (
            <p role="status">{t("patches.unavailable")}</p>
          )}
          {patch.controls.map((control) => (
            <ControlRow
              key={control.id}
              control={control}
              state={patch.state}
              busy={!patch.available || busy.includes(control.id)}
              submit={(value, duration) =>
                submit(patch, control, value, duration)
              }
            />
          ))}
        </div>
      ))}
      <p role="status" className="text-sm">
        {message}
      </p>
    </section>
  );
}
