import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "../api";

export type Role = "administrator" | "staff" | "consultant";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  must_change_password: boolean;
  mfa_enabled: boolean;
}

interface SessionBody {
  status: "signed_in";
  user: User;
  csrf_token: string;
  recovery_codes_left: number | null;
}

type LoginBody = SessionBody | { status: "mfa_required" | "mfa_enrolment_required"; challenge: string };

export interface EnrolInfo {
  secret: string;
  otpauth_uri: string;
  qr: string;
}

/** What comes after a correct password. */
export type LoginStep = { kind: "signed_in" } | { kind: "code"; challenge: string } | { kind: "enrol"; challenge: string };

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unavailable" }
  | { status: "authenticated"; user: User; recoveryCodesLeft: number | null };

interface AuthApi {
  state: AuthState;
  /** Shown on the sign-in page, e.g. after being signed out for inactivity. */
  notice: string | null;
  login: (email: string, password: string) => Promise<LoginStep>;
  verifyCode: (challenge: string, code: string) => Promise<void>;
  startEnrolment: (challenge: string) => Promise<EnrolInfo>;
  /** The server has already signed you in; call `complete` once the recovery codes have been saved. */
  confirmEnrolment: (challenge: string, code: string) => Promise<{ recoveryCodes: string[]; complete: () => void }>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  refreshSession: () => Promise<void>;
  retry: () => void;
}

/** Staff and Administrator are signed out after 20 minutes without activity (BRD NFR-SE-03). */
export const IDLE_SIGN_OUT_MS = 20 * 60 * 1000;
const ACTIVITY_EVENTS = ["pointerdown", "pointermove", "keydown", "scroll", "touchstart"] as const;
const MAX_ACTIVITY_THROTTLE_MS = 5_000;
const IDLE_NOTICE = "You were signed out after 20 minutes of inactivity. Sign in again.";

const AuthContext = createContext<AuthApi | null>(null);

/** `idleMs` exists so tests can use a short timeout; the product always uses the 20 minutes. */
export function AuthProvider({ children, idleMs = IDLE_SIGN_OUT_MS }: { children: ReactNode; idleMs?: number }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const adopt = useCallback((body: SessionBody) => {
    setCsrfToken(body.csrf_token);
    setNotice(null);
    setState({ status: "authenticated", user: body.user, recoveryCodesLeft: body.recovery_codes_left });
  }, []);

  const clear = useCallback((message?: string) => {
    setCsrfToken(null);
    if (message) setNotice(message);
    setState({ status: "anonymous" });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler((error) => {
      clear(error.code === "session_idle" ? error.message : undefined);
    });
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
        if (error instanceof ApiError && error.status === 401) clear(error.code === "session_idle" ? error.message : undefined);
        else setState({ status: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, adopt, clear]);

  // Sign out on screen too, so a forgotten tab does not keep showing client data.
  const watchedRole = state.status === "authenticated" ? state.user.role : null;
  useEffect(() => {
    if (watchedRole !== "administrator" && watchedRole !== "staff") return;
    let timer: ReturnType<typeof setTimeout>;
    let lastReset = Date.now();
    const throttle = Math.min(MAX_ACTIVITY_THROTTLE_MS, idleMs / 10);
    const onIdle = () => {
      api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
      clear(IDLE_NOTICE);
    };
    const reset = () => {
      const now = Date.now();
      if (now - lastReset < throttle) return;
      lastReset = now;
      clearTimeout(timer);
      timer = setTimeout(onIdle, idleMs);
    };
    timer = setTimeout(onIdle, idleMs);
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, reset, { passive: true });
    return () => {
      clearTimeout(timer);
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, reset);
    };
  }, [watchedRole, clear, idleMs]);

  const login = useCallback(
    async (email: string, password: string): Promise<LoginStep> => {
      const body = await api<LoginBody>("/api/auth/login", { method: "POST", json: { email, password } });
      if (body.status === "signed_in") {
        adopt(body);
        return { kind: "signed_in" };
      }
      return body.status === "mfa_required" ? { kind: "code", challenge: body.challenge } : { kind: "enrol", challenge: body.challenge };
    },
    [adopt],
  );

  const verifyCode = useCallback(
    async (challenge: string, code: string) => {
      adopt(await api<SessionBody>("/api/auth/mfa/verify", { method: "POST", json: { challenge, code } }));
    },
    [adopt],
  );

  const startEnrolment = useCallback(
    (challenge: string) => api<EnrolInfo>("/api/auth/mfa/enrol/start", { method: "POST", json: { challenge } }),
    [],
  );

  const confirmEnrolment = useCallback(
    async (challenge: string, code: string) => {
      const body = await api<SessionBody & { recovery_codes: string[] }>("/api/auth/mfa/enrol/confirm", {
        method: "POST",
        json: { challenge, code },
      });
      setCsrfToken(body.csrf_token);
      return { recoveryCodes: body.recovery_codes, complete: () => { adopt(body); } };
    },
    [adopt],
  );

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" });
    clear();
  }, [clear]);

  const refreshSession = useCallback(async () => {
    adopt(await api<SessionBody>("/api/auth/me"));
  }, [adopt]);

  const changePassword = useCallback(
    async (current: string, next: string) => {
      await api("/api/auth/change-password", { method: "POST", json: { current_password: current, new_password: next } });
      await refreshSession();
    },
    [refreshSession],
  );

  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo(
    () => ({ state, notice, login, verifyCode, startEnrolment, confirmEnrolment, logout, changePassword, refreshSession, retry }),
    [state, notice, login, verifyCode, startEnrolment, confirmEnrolment, logout, changePassword, refreshSession, retry],
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
