import {
  ArrowRight,
  CalendarBlank,
  MapTrifold,
  Moon,
  SignOut,
  Sun,
  UsersThree,
} from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { useCanAccessWardWatch } from "@/auth/permissions";
import { useToken } from "@/auth/TokenProvider";
import { IconButton } from "@/components/ui";
import { useTheme } from "@/lib/theme";

export function DashboardHeader() {
  const { token, clear } = useToken();
  const { theme, toggle } = useTheme();
  const canWardWatch = useCanAccessWardWatch();

  return (
    <header className="flex h-[72px] shrink-0 items-center justify-between gap-4 border-b border-border bg-panel px-6">
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent/10">
          <MapTrifold size={20} weight="duotone" className="text-accent" />
        </div>
        <div>
          <p className="text-sm font-semibold leading-none tracking-tight text-fg">remote-sense</p>
          <p className="mt-1 text-xs leading-none text-muted">analyst workspace</p>
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
        {canWardWatch ? (
          <Link to="/ward-watch">
            <IconButton label="Ward Watch">
              <UsersThree size={18} />
            </IconButton>
          </Link>
        ) : null}
        <Link to="/aoi-studio">
          <IconButton label="AOI Studio">
            <CalendarBlank size={18} />
          </IconButton>
        </Link>
        <Link to="/workspace">
          <span className="inline-flex cursor-pointer select-none items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg transition-[opacity,transform] duration-150 hover:opacity-90 active:scale-[0.97]">
            Enter Workspace
            <ArrowRight size={15} weight="bold" />
          </span>
        </Link>
      </div>
    </header>
  );
}
