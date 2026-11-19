import { createContext } from "react";

export type Update = {
  topic: string;
  payload: unknown;
  retain: boolean;
};

export type WsSend = (update: Update, buffer?: boolean) => void;

export const WsSendContext = createContext<WsSend | null>(null);
