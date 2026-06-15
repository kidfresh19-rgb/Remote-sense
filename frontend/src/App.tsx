import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import { TokenProvider } from "@/auth/TokenProvider";
import { router } from "@/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 60_000, refetchOnWindowFocus: false },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TokenProvider>
        <RouterProvider router={router} />
      </TokenProvider>
    </QueryClientProvider>
  );
}
