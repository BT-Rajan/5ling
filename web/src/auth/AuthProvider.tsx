import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "../api";

export type Role = "administrator" | "staff" | "consultant";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  must_change_password: boolean;
}

interface SessionBody {
  user: User;
  csrf_token: string;
}

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unavailable" }
  | { status: "authenticated"; user: User };

interface AuthApi {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthApi | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  const adopt = useCallback((body: SessionBody) => {
    setCsrfToken(body.csrf_token);
    setState({ status: "authenticated", user: body.user });
  }, []);

  const clear = useCallback(() => {
    setCsrfToken(null);
    setState({ status: "anonymous" });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(clear);
    return () => {
      setUnauthorizedHandler(null);
    };
  }, [clear]);

  useEffect(() => {
    let cancelled = false;
    api<SessionBody>("/api/auth/me")
      .then((body) => {
        if (!cancelled) adopt(body);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) clear();
        else setState({ status: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, adopt, clear]);

  const login = useCallback(
    async (email: string, password: string) => {
      adopt(await api<SessionBody>("/api/auth/login", { method: "POST", json: { email, password } }));
    },
    [adopt],
  );

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" });
    clear();
  }, [clear]);

  const changePassword = useCallback(
    async (current: string, next: string) => {
      await api("/api/auth/change-password", {
        method: "POST",
        json: { current_password: current, new_password: next },
      });
      adopt(await api<SessionBody>("/api/auth/me"));
    },
    [adopt],
  );

  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo(
    () => ({ state, login, logout, changePassword, retry }),
    [state, login, logout, changePassword, retry],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** The signed-in person. Only call from screens that render when someone is signed in. */
export function useUser(): User {
  const { state } = useAuth();
  if (state.status !== "authenticated") throw new Error("No signed-in user");
  return state.user;
}
