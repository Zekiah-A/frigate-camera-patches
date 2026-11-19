import { forwardRef } from "react";
import { LuCirclePlus } from "react-icons/lu";
import { cn } from "@/lib/utils";

type FrigatePlusIconProps = {
  className?: string;
  onClick?: () => void;
};

const FrigatePlusIcon = forwardRef<HTMLDivElement, FrigatePlusIconProps>(
  ({ className, onClick }, ref) => {
    return (
      <div
        ref={ref}
        className={cn("relative flex items-center", className)}
        onClick={onClick}
      >
        <LuCirclePlus className="size-full" aria-hidden />
      </div>
    );
  },
);

export default FrigatePlusIcon;
