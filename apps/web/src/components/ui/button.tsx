import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "icon";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-white border border-accent/60 hover:bg-[#4b7df0] active:bg-[#426fe0] shadow-[0_1px_0_rgba(255,255,255,0.12)_inset]",
  secondary:
    "bg-elevated text-ink border border-hairline-strong hover:bg-[#1a1e24] hover:border-[#333840]",
  ghost: "bg-transparent text-muted border border-transparent hover:bg-white/[0.04] hover:text-ink",
  danger:
    "bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25 hover:text-[#ff7a63]",
  outline: "bg-transparent text-ink border border-hairline-strong hover:bg-white/[0.03]",
};

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8 px-3 text-sm gap-1.5",
  icon: "h-7 w-7 justify-center",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "secondary", size = "md", loading, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center rounded font-medium transition-colors duration-100",
        "disabled:pointer-events-none disabled:opacity-45",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="mr-1 h-3 w-3 animate-spin rounded-full border-[1.5px] border-current border-t-transparent" />
      ) : null}
      {children}
    </button>
  );
});
