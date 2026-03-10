// frontend/src/components/LoginPage.js
import React, { useState } from 'react';
import './AuthStyles.css';

// Login form component: handles credential input, submission and error states

function LoginPage({ onLogin, onSwitchToSignup }) {
  // Controlled inputs
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginStage, setLoginStage] = useState('login'); // 'login' | 'forgot' | 'change'
  const [forgotUsername, setForgotUsername] = useState('');
  const [tempPassword, setTempPassword] = useState('');
  const [enteredTemp, setEnteredTemp] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [showTempPopup, setShowTempPopup] = useState(false);
  // UI state
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    console.log('Attempting login with:', { username, password: '***' });

    const storedPasswords = JSON.parse(localStorage.getItem('userPasswords') || '{}');
    if (storedPasswords[username]) {
      if (storedPasswords[username] === password) {
        onLogin('local-token', { username });
        setLoading(false);
        return;
      } else {
        setError('Invalid username/password (local store)');
        setLoading(false);
        return;
      }
    }

    try {
      const response = await fetch('http://localhost:5000/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (response.ok) {
        onLogin(data.token, data.user);
      } else {
        setError(data.message || 'Login failed');
      }
    } catch (err) {
      setError('An error occurred. Please try again.');
      console.error('Login error:', err);
    } finally {
      setLoading(false);
    }
  };

  const generateRandomPassword = () => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';
    return Array.from({ length: 10 }, () => chars.charAt(Math.floor(Math.random() * chars.length))).join('');
  };

  const handleForgotPassword = () => {
    if (!forgotUsername) {
      setError('Please enter your username for password recovery.');
      return;
    }

    const temp = generateRandomPassword();
    setTempPassword(temp);
    localStorage.setItem(`tempPass_${forgotUsername}`, temp);
    setShowTempPopup(true);
  };

  const handleChangePasswordSubmit = (e) => {
    e.preventDefault();
    setError('');

    const savedTemp = localStorage.getItem(`tempPass_${forgotUsername}`);
    if (!savedTemp || savedTemp !== enteredTemp) {
      setError('Temporary password invalid or expired.');
      return;
    }

    if (newPassword.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }

    if (newPassword !== confirmNewPassword) {
      setError('New passwords do not match.');
      return;
    }

    const storedPasswords = JSON.parse(localStorage.getItem('userPasswords') || '{}');
    storedPasswords[forgotUsername] = newPassword;
    localStorage.setItem('userPasswords', JSON.stringify(storedPasswords));
    localStorage.removeItem(`tempPass_${forgotUsername}`);

    setError('Password changed successfully. Please login with your new password.');
    setLoginStage('login');
    setUsername(forgotUsername);
    setPassword('');
    setForgotUsername('');
    setEnteredTemp('');
    setNewPassword('');
    setConfirmNewPassword('');
  };

  // Image is served from `public/login.png` (no import required)
  return (
    <div className="auth-container">
      <div className="auth-card">
        <img src="/login.png" alt="Login" className="auth-logo" /> 
        {error && <div className="error-message">{error}</div>}
        
        {loginStage === 'login' && (
          <>
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

            <p style={{ textAlign: 'right', marginTop: '10px' }}>
              <button className="link-button" type="button" onClick={() => setLoginStage('forgot')}>Forgot Password?</button>
            </p>

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
          </>
        )}

        {loginStage === 'forgot' && (
          <div>
            <h3>Password Recovery</h3>
            <div className="form-group">
              <label htmlFor="forgotUsername">Username</label>
              <input
                id="forgotUsername"
                type="text"
                placeholder="Enter your username"
                value={forgotUsername}
                onChange={(e) => setForgotUsername(e.target.value)}
                required
              />
            </div>
            <button type="button" className="auth-button" onClick={handleForgotPassword}>
              Generate Temporary Password
            </button>
            <p>
              <button className="link-button" type="button" onClick={() => { setLoginStage('login'); setError(''); }}>
                Back to login
              </button>
            </p>
          </div>
        )}

        {loginStage === 'change' && (
          <div>
            <h3>Change Password</h3>
            <form onSubmit={handleChangePasswordSubmit}>
              <div className="form-group">
                <label htmlFor="forgotUsername">Username</label>
                <input
                  id="forgotUsername"
                  type="text"
                  placeholder="Enter your username"
                  value={forgotUsername}
                  onChange={(e) => setForgotUsername(e.target.value)}
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="enteredTemp">Temporary Password</label>
                <input
                  id="enteredTemp"
                  type="text"
                  placeholder="Enter temporary password"
                  value={enteredTemp}
                  onChange={(e) => setEnteredTemp(e.target.value)}
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="newPassword">New Password</label>
                <input
                  id="newPassword"
                  type="password"
                  placeholder="Enter new password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="confirmNewPassword">Confirm New Password</label>
                <input
                  id="confirmNewPassword"
                  type="password"
                  placeholder="Confirm new password"
                  value={confirmNewPassword}
                  onChange={(e) => setConfirmNewPassword(e.target.value)}
                  required
                />
              </div>
              <button type="submit" className="auth-button">Change Password</button>
            </form>
            <p>
              <button className="link-button" type="button" onClick={() => { setLoginStage('login'); setError(''); }}>
                Back to login
              </button>
            </p>
          </div>
        )}

        {showTempPopup && (
          <div className="tutorial-overlay" onClick={() => setShowTempPopup(false)}>
            <div className="tutorial-box">
              <div className="tutorial-close" onClick={() => setShowTempPopup(false)}>✕</div>
              <h3>Temporary Password</h3>
              <p>Your temporary password is: <strong>{tempPassword}</strong></p>
              <p>Please use this temporary password to set a new password now.</p>
              <button type="button" className="auth-button" onClick={() => {
                setLoginStage('change');
                setShowTempPopup(false);
                setUsername(forgotUsername);
              }}>Proceed to Change Password</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default LoginPage;