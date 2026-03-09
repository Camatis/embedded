// frontend/src/components/Dashboard.js
import React, { useState, useEffect, useRef } from 'react';
import './Dashboard.css';

function Dashboard({ user, onLogout }) {
  // Main view state: 'dashboard' | 'sensor-status' | 'batch-history'
  const [currentView, setCurrentView] = useState('dashboard');
  // Sensor states: detection flag and human-readable status
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });
  // Last detected mango size (SMALL, MEDIUM, LARGE, DEFECTIVE, NONE)
  const [detectedSize, setDetectedSize] = useState('NONE');
  const [sortingStats, setSortingStats] = useState({
    small: 0,
    medium: 0,
    large: 0,
    total: 0,
    defective: 0
  });
  // Flag for the most recent item being defective
  const [isDefective, setIsDefective] = useState(false);
  const [sortingHistory, setSortingHistory] = useState([]);
  // Session and history management
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingName, setEditingName] = useState('');
  const videoRef = useRef(null);
  const [cameraError, setCameraError] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showTutorial, setShowTutorial] = useState(false);
  const tutorialSteps = [
    {
      title: 'Start a New Batch',
      body: 'Ready to begin? Click the Start New Batch button located under the System Controls panel to kick off the sorting process.'
    },
    {
      title: 'Stop the Batch',
      body: 'Whenever you need to finish or halt the current run, simply click the Stop button, also found in the System Controls.'
    },
    {
      title: 'Current Batch Statistics',
      body: 'Keep an eye on your numbers here! This section shows your live data, including total mangoes processed, size breakdowns, and the count of defective mangoes.'
    },
    {
      title: 'Open the Menu Bar',
      body: 'To access more system options, click the three horizontal lines (the hamburger icon) in the upper left corner. To close the menu, simply click anywhere outside of it.'
    },
    {
      title: 'Batch History & Renaming',
      body: 'Inside the menu bar, open the Batch History tab to review past runs. Here, you can view the total counts for previous batches and rename them for better organization.'
    },
    {
      title: 'Sensor Status',
      body: 'Ensure your hardware is running smoothly. Click the Sensor Status tab in the menu bar to verify that every single sensor is online and working correctly.'
    }
  ];
  const openTutorial = () => setShowTutorial(true);
  const overlayRef = useRef(null);
  const menuToggleRef = useRef(null);
  // Track counts with ref to ensure we always save current values (not stale state)
  const countsRef = useRef({ small: 0, medium: 0, large: 0, defective: 0, total: 0 });

  // Process incoming sensor payload and update UI state/counters
  // Input: { small, medium, large, defective, detectedSize }
  const handleSensorData = (data) => {
    try {
      // Update sensor states
      setSensorStates(prev => ({
        small: { ...prev.small, detecting: data.small },
        medium: { ...prev.medium, detecting: data.medium },
        large: { ...prev.large, detecting: data.large }
      }));

      // Check if defective
      if (data.defective) {
        setIsDefective(true);
        setDetectedSize('DEFECTIVE');
        console.log('🚨 Defective detected! Adding to count.');
        setSortingStats(prev => {
          const updated = { ...prev, defective: prev.defective + 1, total: prev.total + 1 };
          countsRef.current = updated;  // Keep ref in sync
          return updated;
        });
      } else {
        setIsDefective(false);
        // Update detected size based on hardware data
        switch(data.detectedSize) {
          case 1:
            setDetectedSize('SMALL');
            console.log('📦 Small detected! Count:', sortingStats.small + 1);
            setSortingStats(prev => {
              const updated = { ...prev, small: prev.small + 1, total: prev.total + 1 };
              countsRef.current = updated;
              return updated;
            });
            break;
          case 2:
            setDetectedSize('MEDIUM');
            console.log('📦 Medium detected! Count:', sortingStats.medium + 1);
            setSortingStats(prev => {
              const updated = { ...prev, medium: prev.medium + 1, total: prev.total + 1 };
              countsRef.current = updated;
              return updated;
            });
            break;
          case 3:
            setDetectedSize('LARGE');
            console.log('📦 Large detected! Count:', sortingStats.large + 1);
            setSortingStats(prev => {
              const updated = { ...prev, large: prev.large + 1, total: prev.total + 1 };
              countsRef.current = updated;
              return updated;
            });
            break;
          default:
            setDetectedSize('NONE');
        }
      }

      // Add to sorting history with timestamp
      if (data.detectedSize >= 1 && data.detectedSize <= 3) {
        const timestamp = new Date().toLocaleTimeString();
        setSortingHistory(prev => {
          const newHistory = [...prev, {
            timestamp,
            size: data.detectedSize === 1 ? 'SMALL' : data.detectedSize === 2 ? 'MEDIUM' : 'LARGE'
          }];
          // Keep only last 100 entries
          return newHistory.slice(-100);
        });
      }
    } catch (error) {
      console.error('Error processing sensor data:', error);
    }
  };

  // Poll backend endpoint for latest sensor readings and feed into handler
  // Runs on mount and polls every 300ms; cleans up on unmount
  useEffect(() => {
    // mark sensors active
    setSensorStates(prev => ({
      small: { ...prev.small, status: 'Active' },
      medium: { ...prev.medium, status: 'Active' },
      large: { ...prev.large, status: 'Active' }
    }));

    let mounted = true;
    const pollSensorData = async () => {
      try {
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sensor-data`;
        const res = await fetch(apiUrl);
        if (!res.ok) return;
        const data = await res.json();
        
        // Check if we actually got data
        if (!data || Object.keys(data).length === 0) return;

        // normalize detectedSize (accepts numeric 1/2/3 or string 'Small')
        let detectedSize = data.detectedSize;
        if (typeof detectedSize === 'string') {
          const s = detectedSize.toLowerCase();
          if (s.startsWith('s')) detectedSize = 1;
          else if (s.startsWith('m')) detectedSize = 2;
          else if (s.startsWith('l')) detectedSize = 3;
          else detectedSize = null;
        }

        const normalized = {
          small: !!data.small || detectedSize === 1,
          medium: !!data.medium || detectedSize === 2,
          large: !!data.large || detectedSize === 3,
          defective: !!data.defective,
          detectedSize: detectedSize
        };
        
        // Log received data for debugging
        if (data.defective) console.log('📡 Received from backend - DEFECTIVE:', data);

        if (mounted) handleSensorData(normalized);
      } catch (err) {
        console.error('Error polling sensor data:', err);
      }
    };

    // poll every 300ms
    const intervalId = setInterval(pollSensorData, 300);
    // initial immediate poll
    pollSensorData();

    return () => {
      mounted = false;
      clearInterval(intervalId);
      setSensorStates(prev => ({
        small: { ...prev.small, status: 'Inactive' },
        medium: { ...prev.medium, status: 'Inactive' },
        large: { ...prev.large, status: 'Inactive' }
      }));
    };
  }, []);

  // Start and manage webcam stream for live preview
  // Attempts getUserMedia and assigns stream to <video> ref
  useEffect(() => {
    let localStream = null;
    const startCamera = async () => {
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          setCameraError('Camera not supported (HTTP doesn\'t allow camera access)');
          return;
        }
        localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        if (videoRef.current) videoRef.current.srcObject = localStream;
      } catch (err) {
        if (err.name === 'NotAllowedError') {
          setCameraError('Camera access denied (switch to HTTPS for live feed)');
        } else if (err.name === 'NotFoundError') {
          setCameraError('Camera not found on this device');
        } else {
          setCameraError(err.message || 'Could not access camera');
        }
      }
    };

    startCamera();

    return () => {
      if (localStream) {
        localStream.getTracks().forEach(t => t.stop());
      }
    };
  }, []);

  // Fetch session list from backend (used for Batch History)
  const fetchSessions = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sessions`;
      const res = await fetch(apiUrl);
      if (res.ok) {
        const data = await res.json();        console.log('📋 Fetched sessions:', data);        setSessions(data);
      } else {
        console.error('Failed to fetch sessions');
      }
    } catch (err) {
      console.error('Error fetching sessions', err);
    }
  };

  useEffect(() => {
    fetchSessions();
  }, []);

  // show tutorial once when user logs in (persisted in localStorage)
  useEffect(() => {
    if (user) {
      const seen = localStorage.getItem('tutorialSeen');
      if (!seen) {
        setShowTutorial(true);
      }
    }
  }, [user]);

  // Delete all sessions on backend and clear local state
  const clearSessions = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sessions`;
      const res = await fetch(apiUrl, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' }
      });
      if (res.ok) {
        setSessions([]);
      } else {
        console.error('Failed to clear sessions');
      }
    } catch (err) {
      console.error('Error clearing sessions', err);
    }
  };

  // Inline rename handlers for session batch
  // startEditing: enable edit mode for a session
  const startEditing = (id, currentName) => {
    setEditingSessionId(id);
    setEditingName(currentName || '');
  };

  // cancelEditing: exit edit mode without saving
  const cancelEditing = () => {
    setEditingSessionId(null);
    setEditingName('');
  };

  // saveEditing: persist edited session name to backend
  const saveEditing = async (id) => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sessions/${id}`;
      const res = await fetch(apiUrl, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_name: editingName })
      });
      if (res.ok) {
        const updated = await res.json();
        setSessions(prev => prev.map(s => (s._id === id ? updated : s)));
        cancelEditing();
      } else {
        console.error('Failed to update session name');
      }
    } catch (err) {
      console.error('Error updating session name', err);
    }
  };

  // handleToggleSession: start or stop a sorting session (POST / PUT)
  // handleToggleSession: start or stop a sorting session (POST / PUT)
  // Also saves final counts to batch history when stopping
  const handleToggleSession = async () => {
    // Toggle local active flag immediately for responsive UI
    const turningOn = !sessionActive;
    setSessionActive(turningOn);

    if (turningOn) {
      // Start new session: clear old data FIRST, then POST to backend and reset counters
      try {
        // Clear any residual sensor data from previous batch
        const clearUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/clear-sensor-data`;
        await fetch(clearUrl, { method: 'POST' }).catch(err => console.error('Failed to clear sensor data:', err));
        
        const payload = {
          session_name: `Batch ${sessions.length + 1}`,
          counts: { small: 0, medium: 0, large: 0, defective: 0 },
          timestamps: { start_time: new Date() }
        };
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sessions`;
        const res = await fetch(apiUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          const created = await res.json();
          setCurrentSessionId(created._id);
          // Reset local counters on new batch start
          const initialCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
          setSortingStats(initialCounts);
          countsRef.current = initialCounts;
          console.log('✅ New batch started. Data cleared.');
          fetchSessions();
        } else {
          console.error('Failed to start session');
        }
      } catch (err) {
        console.error('Error starting session', err);
      }
    } else {
      // Stop current session: save final counts and end_time
      try {
        if (!currentSessionId) {
          setSortingStats({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
          return fetchSessions();
        }
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/sessions/${currentSessionId}`;
        const res = await fetch(apiUrl, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            counts: {
              small: countsRef.current.small,
              medium: countsRef.current.medium,
              large: countsRef.current.large,
              defective: countsRef.current.defective
            },
            quality_stats: {
              non_defective: countsRef.current.small + countsRef.current.medium + countsRef.current.large,
              defective: countsRef.current.defective,
              total: countsRef.current.total
            },
            'timestamps.end_time': new Date()
          })
        });
        const bodyToSend = {
          counts: {
            small: countsRef.current.small,
            medium: countsRef.current.medium,
            large: countsRef.current.large,
            defective: countsRef.current.defective
          },
          quality_stats: {
            non_defective: countsRef.current.small + countsRef.current.medium + countsRef.current.large,
            defective: countsRef.current.defective,
            total: countsRef.current.total
          }
        };
        console.log('📤 Sending PUT request with quality_stats:', bodyToSend);
        if (res.ok) {
          setCurrentSessionId(null);
          // Log BEFORE resetting
          console.log('✅ Batch stopped. Final counts saved:', countsRef.current);
          // Reset counters when batch stops
          setSortingStats({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
          countsRef.current = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
          // Clear sensor data on backend to prevent duplicate counting
          const clearUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/clear-sensor-data`;
          fetch(clearUrl, { method: 'POST' }).catch(err => console.error('Failed to clear sensor data:', err));
          // Small delay to ensure DB write completes before fetching
          setTimeout(() => {
            console.log('🔄 Re-fetching sessions...');
            fetchSessions();
          }, 500);
        } else {
          console.error('Failed to stop session');
        }
      } catch (err) {
        console.error('Error stopping session', err);
      }
    }
  };

  // Close sidebar overlay when clicking outside of it
  // Keeps menu state consistent with user interactions
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (!menuOpen) return;
      if (overlayRef.current && overlayRef.current.contains(e.target)) return;
      if (menuToggleRef.current && menuToggleRef.current.contains(e.target)) return;
      setMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  // Render dashboard with three views controlled by `currentView`
  return (
    <div className={`dashboard ${menuOpen ? 'menu-open' : ''}`}>
      {showTutorial && (
        <div className="tutorial-overlay" onClick={(e)=>e.stopPropagation()}>
          <div className="tutorial-box">
            <div className="tutorial-close" onClick={() => { setShowTutorial(false); localStorage.setItem('tutorialSeen','true'); }}>✕</div>
            {tutorialSteps.map((step, idx) => (
              <div key={idx} className="tutorial-step">
                <div className="tutorial-step-title">{step.title}</div>
                <div className="tutorial-step-body">{step.body}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      
      <div ref={overlayRef} className={`menu-overlay ${menuOpen ? 'open' : ''}`}>
          <div className="menu-inner">
            <div className="user-avatar">
              {user && user.username ? user.username.substring(0, 2).toUpperCase() : 'U'}
            </div>
            <div className="user-name-sidebar">
              {user && user.username ? user.username : 'User'}
            </div>
            <nav className="menu-items" aria-label="Main navigation">
              <button type="button" onClick={() => { setCurrentView('dashboard'); setMenuOpen(false); }} className={`menu-item ${currentView === 'dashboard' ? 'active' : ''}`}>Dashboard</button>
              <button type="button" onClick={() => { setCurrentView('batch-history'); setMenuOpen(false); }} className={`menu-item ${currentView === 'batch-history' ? 'active' : ''}`}>Batch History</button>
              <button type="button" onClick={() => { setCurrentView('sensor-status'); setMenuOpen(false); }} className={`menu-item ${currentView === 'sensor-status' ? 'active' : ''}`}>Sensor Status</button>
              <button type="button" onClick={() => { openTutorial(); setMenuOpen(false); }} className="menu-item">Tutorial</button>
            </nav>
            <button className="logout-button overlay-logout" onClick={onLogout}>Sign Out</button>
          </div>
        </div>

      <header className="dashboard-header di">
        <div className="header-content">
          <div ref={menuToggleRef} className="menu-toggle" onClick={() => setMenuOpen(v => !v)} aria-label="Toggle menu">
            <span />
            <span />
            <span />
          </div>
          <h1>Mango Sorting System</h1>
        </div>
      </header>

      <div className="parent">
        {currentView === 'dashboard' ? (
          <>
        <div className="dashboard-container">
          {/* LEFT: Camera + Controls */}
          <div className="dashboard-left">
            <div className="welcome-card camera-feed-section">
              <div className="camera-controls">
                <h2>Live Camera Feed</h2>
              </div>
              <div className="camera-container">
                {cameraError ? (
                  <div className="camera-placeholder">
                    <p><strong>Camera Unavailable</strong></p>
                    <p style={{fontSize: '12px', marginTop: '8px'}}>{cameraError}</p>
                    <p style={{fontSize: '12px', marginTop: '12px', color: '#666'}}>System will still track mango counts via sensors</p>
                  </div>
                ) : (
                  <video ref={videoRef} className="camera-video" autoPlay playsInline muted />
                )}
              </div>
            </div>

            <div className="system-controls">
              <h3>System Controls</h3>
              <button 
                className="control-button start-button" 
                onClick={handleToggleSession}
                disabled={sessionActive}
              >
                Start New Batch
              </button>
              <button 
                className="control-button stop-button" 
                onClick={handleToggleSession}
                disabled={!sessionActive}
              >
                Stop
              </button>
            </div>
          </div>

          {/* RIGHT: Statistics Cards */}
          <div className="dashboard-right">
            <h2 className="stats-title">Current Batch Statistics</h2>
            
            <div className="stats-grid">
              <div className="stats-card">
                <h3>SMALL SIZE</h3>
                <p className="stats-count">{sortingStats.small}</p>
              </div>

              <div className="stats-card">
                <h3>MEDIUM SIZE</h3>
                <p className="stats-count">{sortingStats.medium}</p>
              </div>

              <div className="stats-card">
                <h3>LARGE SIZE</h3>
                <p className="stats-count">{sortingStats.large}</p>
              </div>

              <div className="stats-card defective-card">
                <h3>DEFECTIVE</h3>
                <p className="stats-count">{sortingStats.defective}</p>
              </div>

              <div className="stats-card total-card">
                <h3>TOTAL PROCESSED</h3>
                <p className="stats-count">{sortingStats.total}</p>
              </div>
            </div>
          </div>
        </div>
          </>
        ) : currentView === 'sensor-status' ? (
          <>
        {/* Sensor Status View */}
        <div className="sensor-status-container">
          <h2 style={{ margin: '0 0 24px 0', fontSize: '20px', fontWeight: '600', color: '#333' }}>Sensor Status</h2>
          
          <div className="sensor-grid">
            <div className="welcome-card sensor-card small">
              <h3>Small Mango Sensor</h3>
              <p>
                <span className={`serial-monitor ${sensorStates.small.detecting ? 'detecting' : 'not-detecting'}`}>
                  {sensorStates.small.detecting ? 'Detecting' : 'Not Detecting'}
                </span>
              </p>
            </div>

            <div className="welcome-card sensor-card medium">
              <h3>Medium Mango Sensor</h3>
              <p>
                <span className={`serial-monitor ${sensorStates.medium.detecting ? 'detecting' : 'not-detecting'}`}>
                  {sensorStates.medium.detecting ? 'Detecting' : 'Not Detecting'}
                </span>
              </p>
            </div>

            <div className="welcome-card sensor-card large">
              <h3>Large Mango Sensor</h3>
              <p>
                <span className={`serial-monitor ${sensorStates.large.detecting ? 'detecting' : 'not-detecting'}`}>
                  {sensorStates.large.detecting ? 'Detecting' : 'Not Detecting'}
                </span>
              </p>
            </div>
          </div>
        </div>
          </>
        ) : (
          <>
        {/* Session history table */}
        <div className="batch-history-container">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
            <h2 style={{ margin: 0 }}>Sorting History (Sessions)</h2>
            <button onClick={clearSessions} style={{ padding: '10px 20px', backgroundColor: '#f44336', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600' }}>Clear</button>
          </div>
          <div className="history-table-container">
            <table className="history-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Small</th>
                <th>Medium</th>
                <th>Large</th>
                <th>Defective</th>
                <th>Total</th>
                <th>Start Time</th>
                <th>End Time</th>
              </tr>
            </thead>
            <tbody>
              {sessions && sessions.map((s, idx) => (
                <tr key={s._id || idx}>
                  {editingSessionId === s._id ? (
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <input value={editingName} onChange={e => setEditingName(e.target.value)} style={{ width: 200, padding: '6px 8px' }} />
                        <button onClick={() => saveEditing(s._id)} style={{ padding: '6px 10px' }}>Save</button>
                        <button onClick={cancelEditing} style={{ padding: '6px 10px' }}>Cancel</button>
                      </div>
                    </td>
                  ) : (
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span>{s.session_name || `Batch ${idx + 1}`}</span>
                        <button onClick={() => startEditing(s._id, s.session_name)} style={{ fontSize: 12, padding: '4px 8px' }}>Rename</button>
                      </div>
                    </td>
                  )}
                  <td>{s.counts?.small ?? 0}</td>
                  <td>{s.counts?.medium ?? 0}</td>
                  <td>{s.counts?.large ?? 0}</td>
                  <td>{s.counts?.defective ?? 0}</td>
                  <td>{(s.counts?.small||0) + (s.counts?.medium||0) + (s.counts?.large||0) + (s.counts?.defective||0)}</td>
                  <td>{s.timestamps?.start_time ? new Date(s.timestamps.start_time).toLocaleString() : '-'}</td>
                  <td>{s.timestamps?.end_time ? new Date(s.timestamps.end_time).toLocaleString() : '-'}</td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        </div>
          </>
        )}
      </div>
    </div>
  );
}

export default Dashboard;