// frontend/src/components/LoginPage.js
import React, { useState } from 'react';
import './AuthStyles.css';

// Login form component: handles credential input, submission and error states

function LoginPage({ onLogin, onSwitchToSignup }) {
  // Controlled inputs
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // UI state
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    // Log attempt (mask password) — keep for debug, remove in production
    console.log('Attempting login with:', { username, password: '***' });

    // Send credentials to backend auth endpoint
    try {
      const response = await fetch('http://localhost:5000/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();
      console.log('Response status:', response.status);
      console.log('Response data:', data);

      if (response.ok) {
        // Successful login: lift token and user to parent
        onLogin(data.token, data.user);
      } else {
        // Show server-provided message or generic error
        setError(data.message || 'Login failed');
      }
    } catch (err) {
      setError('An error occurred. Please try again.');
      console.error('Login error:', err);
    } finally {
      setLoading(false);
    }
  };

  // Image is served from `public/login.png` (no import required)
  return (
    <div className="auth-container">
      <div className="auth-card">
        <img src="/login.png" alt="Login" className="auth-logo" /> 
        {error && <div className="error-message">{error}</div>}
        
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              placeholder="Enter your username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <button type="submit" className="auth-button" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <p className="auth-switch">
          Don't have an account?{' '}
          <button 
            type="button"
            className="link-button"
            onClick={onSwitchToSignup}
          >
            Create Account
          </button>
        </p>
      </div>
    </div>
  );
}

export default LoginPage;