import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { ErrorBox, Loading } from "./components/ui";
import { ApiError, get, onAuthProblem } from "./lib/api";
import { eventSocket } from "./lib/events";
import type { AuthState } from "./lib/types";
import { Appearance } from "./pages/Appearance";
import { Login } from "./pages/Login";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/Overview";
import { Providers } from "./pages/Providers";
import { Secrets } from "./pages/Secrets";
import { Settings } from "./pages/Settings";
import { TotpSetup } from "./pages/TotpSetup";
import { TraceDrawer } from "./pages/TraceDrawer";
import { Voice } from "./pages/Voice";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't retry client errors (auth, 404, validation); retry transient failures twice.
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
      staleTime: 2_000,
    },
  },
});

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Overview />, children: [{ path: "turns/:id", element: <TraceDrawer /> }] },
      { path: "/providers", element: <Providers /> },
      { path: "/voice", element: <Voice /> },
      { path: "/appearance", element: <Appearance /> },
      { path: "/secrets", element: <Secrets /> },
      { path: "/settings", element: <Settings /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

function AuthGate() {
  const qc = useQueryClient();
  const auth = useQuery({
    queryKey: ["auth"],
    queryFn: () => get<AuthState>("/api/auth/state"),
    staleTime: 60_000,
  });

  useEffect(() => {
    const recheck = () => {
      // Drop everything cached under the old session, then re-ask who we are.
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      void qc.invalidateQueries({ queryKey: ["auth"] });
    };
    eventSocket.onAuthRefused = recheck;
    const off = onAuthProblem(recheck);
    return () => {
      off();
      eventSocket.onAuthRefused = null;
    };
  }, [qc]);

  if (auth.isPending) return <Splash><Loading label="Connecting to JARVIS…" /></Splash>;
  if (auth.isError)
    return (
      <Splash>
        <ErrorBox error={auth.error} onRetry={() => void auth.refetch()} />
      </Splash>
    );
  if (!auth.data.authenticated) return <Login totpEnabled={auth.data.totp_enabled} />;
  if (auth.data.needs_totp_setup) return <TotpSetup />;
  return <RouterProvider router={router} />;
}

function Splash({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-6">{children}</div>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthGate />
      </ToastProvider>
    </QueryClientProvider>
  );
}
