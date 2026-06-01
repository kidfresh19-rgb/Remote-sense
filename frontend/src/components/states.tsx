import { Warning } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";

import { Button, Skeleton } from "./ui";

export function LoadingRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-3">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon ? <div className="text-muted">{icon}</div> : null}
      <p className="text-sm font-medium text-fg">{title}</p>
      {hint ? <p className="max-w-[42ch] text-xs leading-relaxed text-muted">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message =
    error instanceof ApiError
      ? error.isAuth
        ? "Your session is not authorised for this view."
        : error.message
      : error instanceof Error
        ? error.message
        : "Something went wrong.";
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <Warning size={22} className="text-critical" />
      <p className="text-sm font-medium text-fg">Could not load</p>
      <p className="max-w-[44ch] text-xs leading-relaxed text-muted">{message}</p>
      {onRetry ? (
        <Button variant="outline" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
