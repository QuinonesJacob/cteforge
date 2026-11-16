// Who is signed in. A token comes from sessionStorage; if GET /me works without a token,
// the server runs with CTEFORGE_AUTH=off (local single-user mode, ADR 0018).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { onUnauthorized } from "../api/client";
import { getMe } from "../api/endpoints";
import { clearToken, getToken, setToken } from "../api/token";
import type { User } from "../api/types";

export type AuthStatus = "loading" | "signedOut" | "signedIn";

interface AuthApi {
  status: AuthStatus;
  user: User | null;
  authOff: boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
  recheck: () => Promise<void>;
}

const AuthCtx = createContext<AuthApi | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [authOff, setAuthOff] = useState(false);

  const recheck = useCallback(async () => {
    setStatus("loading");
    if (getToken()) {
      try {
        setUser(await getMe());
        setAuthOff(false);
        setStatus("signedIn");
        return;
      } catch {
        clearToken();
      }
    }
    try {
      setUser(await getMe({ anonymous: true }));
      setAuthOff(true);
      setStatus("signedIn");
    } catch {
      setUser(null);
      setAuthOff(false);
      setStatus("signedOut");
    }
  }, []);

  useEffect(() => {
    void recheck();
  }, [recheck]);

  useEffect(() => {
    onUnauthorized(() => {
      clearToken();
      setUser(null);
      setStatus("signedOut");
    });
    return () => onUnauthorized(null);
  }, []);

  const signIn = useCallback(async (token: string) => {
    const me = await getMe({ token }); // throws ApiError(401) for a bad token
    setToken(token);
    setUser(me);
    setAuthOff(false);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
    setStatus("signedOut");
  }, []);

  const api = useMemo(
    () => ({ status, user, authOff, signIn, signOut, recheck }),
    [status, user, authOff, signIn, signOut, recheck],
  );
  return <AuthCtx.Provider value={api}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
