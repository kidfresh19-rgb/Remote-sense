import { Planet } from "@phosphor-icons/react";
import { useState } from "react";

import { Button } from "@/components/ui";

import { useToken } from "./TokenProvider";

/** Stand-in sign-in until the gateway OIDC flow is wired (see TokenProvider). Every workspace read
 *  is RBAC-gated, so without a token there is nothing to show: this gate collects one. */
export function TokenGate() {
  const { setToken } = useToken();
  const [draft, setDraft] = useState("");

  return (
    <div className="grid min-h-0 place-items-center p-6">
      <div className="w-full max-w-md rounded-xl border border-border bg-panel p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-accent">
          <Planet size={22} weight="duotone" />
          <h1 className="text-base font-semibold text-fg">remote-sense workspace</h1>
        </div>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          Paste a bearer token to continue. In production this comes from the gateway sign-in; for
          local development use a token minted against your <code className="text-fg">jwt_secret</code>.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (draft.trim()) setToken(draft);
          }}
          className="flex flex-col gap-3"
        >
          <label htmlFor="token" className="text-xs font-medium text-muted">
            Bearer token
          </label>
          <textarea
            id="token"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            rows={4}
            placeholder="eyJhbGciOiJIUzI1NiIs..."
            className="w-full resize-none rounded-md border border-border bg-bg p-2 font-mono text-xs text-fg outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
          <Button type="submit" variant="primary" disabled={!draft.trim()} className="w-full">
            Open workspace
          </Button>
        </form>
      </div>
    </div>
  );
}
