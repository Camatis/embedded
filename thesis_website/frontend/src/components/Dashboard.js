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
    total: 0,
    defective: 0
  });
  const [isDefective, setIsDefective] = useState(false);
  const [sortingHistory, setSortingHistory] = useState([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);

  // Handle sensor data from hardware (abstracted for easy Raspberry Pi integration)
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
        setSortingStats(prev => ({
          ...prev,
          defective: prev.defective + 1
        }));
      } else {
        setIsDefective(false);
        // Update detected size based on hardware data
        switch(data.detectedSize) {
          case 1:
            setDetectedSize('SMALL');
            setSortingStats(prev => ({
              ...prev,
              small: prev.small + 1,
              total: prev.total + 1
            }));
            break;
          case 2:
            setDetectedSize('MEDIUM');
            setSortingStats(prev => ({
              ...prev,
              medium: prev.medium + 1,
              total: prev.total + 1
            }));
            break;
          case 3:
            setDetectedSize('LARGE');
            setSortingStats(prev => ({
              ...prev,
              large: prev.large + 1,
              total: prev.total + 1
            }));
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

  // TODO: Replace this with Raspberry Pi connection logic
  useEffect(() => {
    // Placeholder for hardware connection
    const connectToHardware = async () => {
      // Example: Set sensors to Active when connection is ready
      setSensorStates(prev => ({
        small: { ...prev.small, status: 'Active' },
        medium: { ...prev.medium, status: 'Active' },
        large: { ...prev.large, status: 'Active' }
      }));
      
      // TODO: Add your Raspberry Pi connection here
      // Once connected, call handleSensorData(data) when data arrives
    };

    connectToHardware();

    // Cleanup function
    return () => {
      // TODO: Add cleanup for Raspberry Pi connection here
      setSensorStates(prev => ({
        small: { ...prev.small, status: 'Inactive' },
        medium: { ...prev.medium, status: 'Inactive' },
        large: { ...prev.large, status: 'Inactive' }
      }));
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

  const clearSessions = async () => {
    try {
      const res = await fetch('http://localhost:5000/api/sessions', {
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
              <label className="toggle-label" style={{ color: sessionActive ? 'green' : 'red' }}>
                {sessionActive ? 'Session On' : 'Session Off'}
              </label>
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

          <div className="size-card" style={{ marginTop: 10, backgroundColor: isDefective ? '#ffebee' : '#fff', borderColor: isDefective ? '#d32f2f' : '#ddd' }}>
            <h3>Detected Mango Size</h3>
            <div className="size-display" style={{ color: isDefective ? '#d32f2f' : '#FDB813' }}>{detectedSize}</div>
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
                  <td>{idx + 1}</td>
                  <td>{s.counts?.small ?? 0}</td>
                  <td>{s.counts?.medium ?? 0}</td>
                  <td>{s.counts?.large ?? 0}</td>
                  <td>{s.counts?.defective ?? 0}</td>
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
    </div>
  );
}

export default Dashboard;