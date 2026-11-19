import { cn } from "@/lib/utils";
import { LuVideo } from "react-icons/lu";

type LogoProps = {
  className?: string;
};

export default function Logo({ className }: LogoProps) {
  return (
    <LuVideo
      className={cn("w-auto", className)}
      role="img"
      aria-label="Camera Patches"
    />
  );
}
