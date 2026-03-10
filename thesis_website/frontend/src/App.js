// frontend/src/App.js
import React, { useState, useEffect } from 'react';
import LoginPage from './components/LoginPage';
import SignupPage from './components/SignupPage';
import Dashboard from './components/Dashboard';
import './App.css';

function App() {
  const [currentPage, setCurrentPage] = useState('login');
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const verifyToken = async () => {
      try {
        const response = await fetch('http://localhost:5000/api/auth/me', {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });
        
        if (response.ok) {
          const userData = await response.json();
          setUser(userData);
        } else {
          localStorage.removeItem('token');
          setToken(null);
        }
      } catch (err) {
        console.error('Error verifying token:', err);
        localStorage.removeItem('token');
        setToken(null);
      } finally {
        setLoading(false);
      }
    };

    if (!token) {
      setLoading(false);
      return;
    }

    if (token === 'local-token') {
      // Local password flow; no remote token check required
      setLoading(false);
      return;
    }

    verifyToken();
  }, [token]);

  const handleLogin = (loginToken, userData) => {
    localStorage.setItem('token', loginToken);
    setToken(loginToken);
    setUser(userData);
  };

  const handleSignup = (signupToken, userData) => {
    localStorage.setItem('token', signupToken);
    setToken(signupToken);
    setUser(userData);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setToken(null);
    setUser(null);
    setCurrentPage('login');
  };

  if (loading) {
    return <div className="loading">Loading...</div>;
  }

  if (user && token) {
    return <Dashboard user={user} onLogout={handleLogout} />;
  }

  return (
    <div className="App">
      {currentPage === 'login' ? (
        <LoginPage 
          onLogin={handleLogin}
          onSwitchToSignup={() => setCurrentPage('signup')}
        />
      ) : (
        <SignupPage 
          onSignup={handleSignup}
          onSwitchToLogin={() => setCurrentPage('login')}
        />
      )}
    </div>
  );
}

export default App;