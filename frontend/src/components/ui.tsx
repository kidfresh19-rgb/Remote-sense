import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/format";

const focusable =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg";
const interactive =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ease-out transition-[transform,background-color,border-color,opacity] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";

type Variant = "primary" | "outline" | "ghost";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:opacity-90",
  outline: "border border-border bg-panel text-fg hover:bg-panel-2",
  ghost: "text-fg hover:bg-panel-2",
};

/** The exact class set the `Button` component renders, exposed so a non-button element (e.g. a
 *  router `Link` used as an action) can match a button pixel-for-pixel without nesting a real
 *  <button> inside an <a>. */
export function buttonClasses(variant: Variant = "outline", className?: string): string {
  return cn(interactive, focusable, variants[variant], "h-9 px-3", className);
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "outline", className, ...props }, ref) => (
    <button ref={ref} className={buttonClasses(variant, className)} {...props} />
  ),
);
Button.displayName = "Button";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, active, className, ...props }, ref) => (
    <button
      ref={ref}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        interactive,
        focusable,
        "size-9 rounded-md",
        active ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2 hover:text-fg",
        className,
      )}
      {...props}
    />
  ),
);
IconButton.displayName = "IconButton";

type Tone = "neutral" | "positive" | "caution" | "critical" | "accent";

const tones: Record<Tone, string> = {
  neutral: "bg-panel-2 text-muted",
  positive: "bg-positive/15 text-positive",
  caution: "bg-caution/15 text-caution",
  critical: "bg-critical/15 text-critical",
  accent: "bg-accent/15 text-accent",
};

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="inline-flex rounded-md border border-border bg-panel p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            role="tab"
            aria-selected={active}
            title={opt.title ?? opt.label}
            onClick={() => onChange(opt.value)}
            className={cn(
              "rounded-[5px] px-2.5 py-1 text-xs font-medium ease-out transition-colors duration-150",
              focusable,
              active ? "bg-accent text-accent-fg" : "text-muted hover:text-fg",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-panel-2", className)} />;
}
