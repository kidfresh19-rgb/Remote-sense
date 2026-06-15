import {
  CalendarBlank,
  ClipboardText,
  CloudArrowUp,
  House,
  MapTrifold,
  Moon,
  SignOut,
  Sun,
} from "@phosphor-icons/react";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { useCanPublish } from "@/auth/permissions";
import { useToken } from "@/auth/TokenProvider";
import { INDICES, type IndexKey } from "@/lib/indices";
import { useTheme } from "@/lib/theme";
import { useWorkspace } from "@/state/workspace";

import { ReviewQueue } from "./ReviewQueue";
import { GatewayPushModal } from "./GatewayPushModal";
import { IconButton, SegmentedControl } from "./ui";

export function Header() {
  const { token, clear } = useToken();
  const { theme, toggle } = useTheme();
  const { index, setIndex } = useWorkspace();
  const canPublish = useCanPublish();
  const navigate = useNavigate();
  const [queueOpen, setQueueOpen] = useState(false);
  const [pushOpen, setPushOpen] = useState(false);

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b border-border bg-panel px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MapTrifold size={20} weight="duotone" className="shrink-0 text-accent" />
        <span className="text-sm font-semibold tracking-tight">remote-sense</span>
        <span className="hidden text-xs text-muted sm:inline">analyst workspace</span>
      </div>

      <div className="flex items-center gap-2">
        <IconButton
          label="Dashboard overview"
          onClick={() => void navigate({ to: "/" })}
        >
          <House size={18} />
        </IconButton>
        <IconButton
          label="AOI Studio"
          onClick={() => void navigate({ to: "/aoi-studio" })}
        >
          <CalendarBlank size={18} />
        </IconButton>
        {token ? (
          <div className="hidden md:block">
            <SegmentedControl<IndexKey>
              ariaLabel="Active index"
              value={index}
              onChange={setIndex}
              options={INDICES.map((m) => ({ value: m.key, label: m.label, title: m.long }))}
            />
          </div>
        ) : null}
        {token && canPublish ? (
          <div className="flex items-center gap-2">
            <IconButton
              label="Push data to gateway"
              active={pushOpen}
              onClick={() => setPushOpen((o) => !o)}
            >
              <CloudArrowUp size={18} />
            </IconButton>
            <IconButton
              label="Review queue"
              active={queueOpen}
              onClick={() => setQueueOpen((open) => !open)}
            >
              <ClipboardText size={18} />
            </IconButton>
          </div>
        ) : null}
        <IconButton
          label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
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
      {queueOpen ? <ReviewQueue onClose={() => setQueueOpen(false)} /> : null}
      {pushOpen ? <GatewayPushModal onClose={() => setPushOpen(false)} /> : null}
    </header>
  );
}
