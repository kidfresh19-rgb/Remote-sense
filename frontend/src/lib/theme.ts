import { useCallback, useState } from "react";

type Theme = "light" | "dark";

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/** Light/dark toggle. The class is set pre-paint by the inline script in index.html; this only
 *  flips it and remembers the explicit choice. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => current());
  const toggle = useCallback(() => {
    const next: Theme = current() === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("rs-theme", next);
    } catch {
      /* ignore */
    }
    setTheme(next);
  }, []);
  return { theme, toggle };
}
