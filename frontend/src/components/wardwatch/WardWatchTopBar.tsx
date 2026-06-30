import { ArrowLeft, Moon, SignOut, Sun } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { useToken } from "@/auth/TokenProvider";
import { IconButton } from "@/components/ui";
import { useTheme } from "@/lib/theme";

/** The shared Ward Watch header: a back link, the screen title/subtitle, and the theme + sign-out
 *  controls. `backTo` is one of the registered Ward Watch routes so navigation stays typed. */
export function WardWatchTopBar({
  title,
  subtitle,
  backTo,
  backLabel,
}: {
  title: string;
  subtitle: string;
  backTo: "/" | "/ward-watch";
  backLabel: string;
}) {
  const { token, clear } = useToken();
  const { theme, toggle } = useTheme();
  return (
    <header className="flex h-[72px] shrink-0 items-center justify-between gap-4 border-b border-border bg-panel px-6">
      <div className="flex min-w-0 items-center gap-3">
        <Link to={backTo} aria-label={backLabel}>
          <IconButton label={backLabel}>
            <ArrowLeft size={18} />
          </IconButton>
        </Link>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold leading-none tracking-tight text-fg">
            {title}
          </p>
          <p className="mt-1 truncate text-xs leading-none text-muted">{subtitle}</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <IconButton
          label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={toggle}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </IconButton>
        {token ? (
          <IconButton label="Sign out" onClick={clear}>
            <SignOut size={18} />
          </IconButton>
        ) : null}
      </div>
    </header>
  );
}
