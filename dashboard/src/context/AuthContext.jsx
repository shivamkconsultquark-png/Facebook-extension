// Harness stand-in for the main app's AuthContext: signs in against the Scam Shield
// backend's /auth endpoints and exposes { token, user } exactly like useAuth() does.
import React, { createContext, useContext, useState } from 'react';
import { API_MAIN } from '../config/api';

const AuthContext = createContext(null);
const KEY = 'fbshield_session';

function readSession() {
  try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(readSession);

  const authenticate = async (email, password, register = false) => {
    let res;
    try {
      res = await fetch(`${API_MAIN}/auth/${register ? 'register' : 'login'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, label: 'dashboard' }),
      });
    } catch {
      throw new Error(`Can’t reach the Scam Shield server at ${API_MAIN}. Is it running?`);
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = body.errors?.length ? 'Enter a valid email and a password of at least 8 characters.' : body.detail;
      throw new Error(detail || `Sign-in failed (HTTP ${res.status})`);
    }
    const s = { token: body.token, user: body.user };
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode: session-only */ }
    setSession(s);
  };

  const logout = () => {
    try { localStorage.removeItem(KEY); } catch { /* ignore */ }
    setSession(null);
  };

  return (
    <AuthContext.Provider value={{ token: session?.token, user: session?.user, login: authenticate, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
