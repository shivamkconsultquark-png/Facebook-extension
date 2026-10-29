// Standalone harness. In the main app, add the two routes next to the Twitter ones:
//   <Route path="/facebook" element={<FacebookInsights />} />
//   <Route path="/facebook/auto-scraper" element={<FacebookScraper />} />
// and a sidebar item { id: 'facebook-filter', label: 'Facebook Filter', icon: Facebook, path: '/facebook' }.
import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import axios from 'axios';
import { AuthProvider, useAuth } from './context/AuthContext';
import FacebookInsights from './pages/FacebookInsights';
import FacebookScraper from './pages/FacebookScraper';

function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try { await login(email.trim(), password, register); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gradient-to-br from-blue-50 to-zinc-100 p-8">
      <form onSubmit={submit} className="w-full max-w-sm p-8 rounded-2xl border-2 border-zinc-200 bg-white shadow-lg">
        <h1 className="text-3xl font-black text-zinc-900 mb-2 text-center">Facebook <span className="text-blue-500">Filter</span></h1>
        <p className="text-sm text-zinc-500 text-center mb-6">{register ? 'Create your account' : 'Sign in to continue'}</p>
        <input type="email" autoComplete="username" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required
          className="w-full text-sm border border-zinc-200 rounded-lg p-2.5 mb-3 focus:outline-none focus:ring-2 focus:ring-blue-400" />
        <input type="password" autoComplete={register ? 'new-password' : 'current-password'} placeholder="Password" value={password}
          onChange={(e) => setPassword(e.target.value)} required minLength={8}
          className="w-full text-sm border border-zinc-200 rounded-lg p-2.5 mb-3 focus:outline-none focus:ring-2 focus:ring-blue-400" />
        {error && <p className="text-xs text-red-600 font-bold mb-3">❌ {error}</p>}
        <button disabled={busy} className="w-full px-6 py-3 bg-zinc-900 text-white rounded-xl font-bold text-sm hover:bg-black transition-all disabled:opacity-50">
          {busy ? 'Please wait…' : register ? 'Create account' : 'Sign in'}
        </button>
        <button type="button" onClick={() => { setRegister(!register); setError(null); }} className="w-full mt-3 text-xs font-bold text-blue-600 hover:underline">
          {register ? 'Already have an account? Sign in' : 'New here? Create an account'}
        </button>
      </form>
    </div>
  );
}

function App() {
  const { token, logout } = useAuth();
  // A stored token the server no longer accepts sends the user back to sign-in.
  useEffect(() => {
    const id = axios.interceptors.response.use(undefined, (err) => {
      if (err.response?.status === 401) logout();
      return Promise.reject(err);
    });
    return () => axios.interceptors.response.eject(id);
  }, [logout]);

  if (!token) return <Login />;
  if (window.location.pathname.startsWith('/facebook/auto-scraper')) return <FacebookScraper />;
  return (
    <div className="min-h-screen bg-zinc-50">
      <FacebookInsights />
      <div className="text-center pb-8">
        <button onClick={logout} className="text-xs font-bold text-zinc-400 hover:text-zinc-700">Sign out</button>
      </div>
    </div>
  );
}

if (window.location.pathname === '/') window.history.replaceState(null, '', '/facebook');
createRoot(document.getElementById('root')).render(<AuthProvider><App /></AuthProvider>);
