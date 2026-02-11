// frontend/src/components/Dashboard.js
import React, { useState, useEffect } from 'react';
import './Dashboard.css';

function Dashboard({ user, onLogout }) {
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });
  const [detectedSize, setDetectedSize] = useState('NONE');
  const [sortingStats, setSortingStats] = useState({
    small: 0,
    medium: 0,
    large: 0,
    total: 0
  });
  const [sortingHistory, setSortingHistory] = useState([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);

  useEffect(() => {
    // Create WebSocket connection
    const ws = new WebSocket('ws://192.168.1.100:81'); // Replace with your ESP32 IP
    
    ws.onopen = () => {
      console.log('Connected to ESP32');
      setSensorStates(prev => ({
        small: { ...prev.small, status: 'Active' },
        medium: { ...prev.medium, status: 'Active' },
        large: { ...prev.large, status: 'Active' }
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        // Update sensor states
        setSensorStates(prev => ({
          small: { ...prev.small, detecting: data.small },
          medium: { ...prev.medium, detecting: data.medium },
          large: { ...prev.large, detecting: data.large }
        }));

        // Update detected size based on ESP32 data
        switch(data.detectedSize) {
          case 1:
            setDetectedSize('SMALL');
            // Update sorting stats for small mangoes
            setSortingStats(prev => ({
              ...prev,
              small: prev.small + 1,
              total: prev.total + 1
            }));
            break;
          case 2:
            setDetectedSize('MEDIUM');
            // Update sorting stats for medium mangoes
            setSortingStats(prev => ({
              ...prev,
              medium: prev.medium + 1,
              total: prev.total + 1
            }));
            break;
          case 3:
            setDetectedSize('LARGE');
            // Update sorting stats for large mangoes
            setSortingStats(prev => ({
              ...prev,
              large: prev.large + 1,
              total: prev.total + 1
            }));
            break;
          default:
            setDetectedSize('NONE');
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
        console.error('Error parsing WebSocket message:', error);
      }
    };

    ws.onclose = () => {
      console.log('Disconnected from ESP32');
      setSensorStates(prev => ({
        small: { ...prev.small, status: 'Inactive' },
        medium: { ...prev.medium, status: 'Inactive' },
        large: { ...prev.large, status: 'Inactive' }
      }));
    };

    // Cleanup on unmount
    return () => {
      ws.close();
    };
  }, []);

  // Fetch past sessions from backend
  const fetchSessions = async () => {
    try {
      const res = await fetch('http://localhost:5000/api/sessions');
      if (res.ok) {
        const data = await res.json();
        setSessions(data);
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

  const handleToggleSession = async () => {
    // Toggle locally first
    const turningOn = !sessionActive;
    setSessionActive(turningOn);

    if (turningOn) {
      // Start new session: POST
      try {
        const payload = {
          session_name: `Session ${new Date().toLocaleString()}`,
          counts: { small: 0, medium: 0, large: 0, extra_large: 0 },
          timestamps: { start_time: new Date() }
        };
        const res = await fetch('http://localhost:5000/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          const created = await res.json();
          setCurrentSessionId(created._id);
          fetchSessions();
        } else {
          console.error('Failed to start session');
        }
      } catch (err) {
        console.error('Error starting session', err);
      }
    } else {
      // Stop current session: PUT end_time
      try {
        if (!currentSessionId) return fetchSessions();
        const res = await fetch(`http://localhost:5000/api/sessions/${currentSessionId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 'timestamps.end_time': new Date() })
        });
        if (res.ok) {
          setCurrentSessionId(null);
          fetchSessions();
        } else {
          console.error('Failed to stop session');
        }
      } catch (err) {
        console.error('Error stopping session', err);
      }
    }
  };

  return (
    <div className="dashboard">
      
      <header className="dashboard-header di">
        <div className="header-content">
          <h1>Automated Carabao Mango Sorting System</h1>
          <div className="user-section">
            <button className="logout-button" onClick={onLogout}>Logout</button>
          </div>
        </div>
      </header>

      <div className="parent">
        {/* Camera + detected size + toggle */}
        <div className="welcome-card camera-feed-section div2">
          <div className="camera-controls">
            <h2>Live Camera Feed</h2>
            <div className="camera-toggle">
              <label className="toggle-label">Session</label>
              <label className="switch">
                <input type="checkbox" checked={sessionActive} onChange={handleToggleSession} />
                <span className="slider" />
              </label>
            </div>
          </div>
          <div className="camera-container">
            <div className="camera-placeholder">
              <p>Camera stream will appear here</p>
            </div>
          </div>

          <div className="size-card" style={{ marginTop: 12 }}>
            <h3>Detected Mango Size</h3>
            <div className="size-display">{detectedSize}</div>
          </div>
        </div>

        {/* Sensors: fixed order small / medium / large */}
        <div className="welcome-card div3 sensor-card small">
          <h3>Small Mango Sensor</h3>
          <p>
            <span className={`serial-monitor ${sensorStates.small.detecting ? 'detecting' : 'not-detecting'}`}>
              {sensorStates.small.detecting ? 'Detecting' : 'Not Detecting'}
            </span>
          </p>
        </div>

        <div className="welcome-card div4 sensor-card medium">
          <h3>Medium Mango Sensor</h3>
          <p>
            <span className={`serial-monitor ${sensorStates.medium.detecting ? 'detecting' : 'not-detecting'}`}>
              {sensorStates.medium.detecting ? 'Detecting' : 'Not Detecting'}
            </span>
          </p>
        </div>

        <div className="welcome-card div5 sensor-card large">
          <h3>Large Mango Sensor</h3>
          <p>
            <span className={`serial-monitor ${sensorStates.large.detecting ? 'detecting' : 'not-detecting'}`}>
              {sensorStates.large.detecting ? 'Detecting' : 'Not Detecting'}
            </span>
          </p>
        </div>

        {/* Stats cards */}
        <div className="stats-card welcome-card div6">
          <h3>Small Mangoes</h3>
          <p className="stats-count">{sortingStats.small}</p>
        </div>

        <div className="stats-card welcome-card div7">
          <h3>Medium Mangoes</h3>
          <p className="stats-count">{sortingStats.medium}</p>
        </div>

        <div className="stats-card welcome-card div8">
          <h3>Large Mangoes</h3>
          <p className="stats-count">{sortingStats.large}</p>
        </div>

        <div className="stats-card welcome-card div9">
          <h3>Total Sorted</h3>
          <p className="stats-count">{sortingStats.total}</p>
        </div>

        {/* Session history table */}
        <div className="welcome-card div10">
          <h2>Sorting History (Sessions)</h2>
          <table className="history-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Small</th>
                <th>Medium</th>
                <th>Large</th>
                <th>Total</th>
                <th>Start Time</th>
                <th>End Time</th>
              </tr>
            </thead>
            <tbody>
              {sessions && sessions.map((s, idx) => (
                <tr key={s._id || idx}>
                  <td>{idx + 1}</td>
                  <td>{s.counts?.small ?? 0}</td>
                  <td>{s.counts?.medium ?? 0}</td>
                  <td>{s.counts?.large ?? 0}</td>
                  <td>{(s.counts?.small||0) + (s.counts?.medium||0) + (s.counts?.large||0)}</td>
                  <td>{s.timestamps?.start_time ? new Date(s.timestamps.start_time).toLocaleString() : '-'}</td>
                  <td>{s.timestamps?.end_time ? new Date(s.timestamps.end_time).toLocaleString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default Dashboard;