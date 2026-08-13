import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiClientError, resetCsrfToken } from "@/api/client";
import type { User } from "@/types";

interface RegisterInput {
  name: string;
  email: string;
  password: string;
  confirm: string;
  accept_terms: boolean;
}

interface AuthContextValue {
  user: User | null;
  /** Undefined while the initial session check is in flight. */
  isLoading: boolean;
  login: (email: string, password: string, remember: boolean) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ user: User | null }>("/api/auth/me")
      .then((data) => setUser(data.user))
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string, remember: boolean) => {
    const data = await api.post<{ user: User }>("/api/auth/login", { email, password, remember });
    resetCsrfToken();
    setUser(data.user);
  }, []);

  const register = useCallback(async (input: RegisterInput) => {
    const data = await api.post<{ user: User }>("/api/auth/register", input);
    resetCsrfToken();
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      resetCsrfToken();
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

export { ApiClientError };
