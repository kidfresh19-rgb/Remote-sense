import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { config } from "@/lib/config";

const STORAGE_KEY = "rs-token";

function readToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? config.devToken;
  } catch {
    return config.devToken;
  }
}

interface TokenContextValue {
  token: string | null;
  setToken: (token: string) => void;
  clear: () => void;
}

const TokenContext = createContext<TokenContextValue | null>(null);

// ⚑ CONFIRM: the production workspace gets its bearer token from the gateway IdP via a real OIDC
// flow (PKCE redirect). That contract is parked, so for now the token is supplied directly (dev
// env var or the token gate) and only stored client-side. Swap this provider's internals for the
// OIDC client once the contract lands; the rest of the app only depends on `useToken().token`.
export function TokenProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => readToken() || null);

  const setToken = useCallback((next: string) => {
    const trimmed = next.trim();
    try {
      if (trimmed) localStorage.setItem(STORAGE_KEY, trimmed);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage disabled: keep it in memory for this session */
    }
    setTokenState(trimmed || null);
  }, []);

  const clear = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setTokenState(null);
  }, []);

  const value = useMemo<TokenContextValue>(
    () => ({ token, setToken, clear }),
    [token, setToken, clear],
  );
  return <TokenContext.Provider value={value}>{children}</TokenContext.Provider>;
}

export function useToken(): TokenContextValue {
  const value = useContext(TokenContext);
  if (!value) throw new Error("useToken must be used within a TokenProvider");
  return value;
}
