// frontend/src/components/Dashboard.js
import React, { useState, useEffect } from 'react';
import './Dashboard.css';

function Dashboard({ user, onLogout }) {
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });
  const [detectedSize, setDetectedSize] = useState('N/A');
  const [sortingStats, setSortingStats] = useState({
    small: 0,
    medium: 0,
    large: 0,
    total: 0
  });
  const [sortingHistory, setSortingHistory] = useState([]);
  const [sessionActive, setSessionActive] = useState(false);

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
            setDetectedSize('N/A');
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

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div className="header-content">
          <h1>Automated Carabao Mango Sorting System</h1>
            <div className="user-section">
              <button className="logout-button" onClick={onLogout}>
                Logout
              </button>
            </div>
        </div>
      </header>

      <main className="dashboard-main">
        <div className="welcome-card camera-feed-section">
          <h2>Live Camera Feed</h2>
          <div className="camera-container">
            <div className="camera-placeholder">
              <p>Camera stream will appear here</p>
            </div>
          </div>
        </div>
      </main>
      <section className="sensor-status welcome-card">
        <h2>IR Sensor Status</h2>
        <div className="sensor-cards">
          {Object.entries(sensorStates).map(([size, state]) => (
            <div className={`sensor-card ${size}`} key={size}>
              <h3>{size.charAt(0).toUpperCase() + size.slice(1)} Mango Sensor</h3>
              <p>Status: <span className={state.status.toLowerCase()}>{state.status}</span></p>
              <p>
                Detection Status: 
                <span className={`serial-monitor ${state.detecting ? 'detecting' : 'not-detecting'}`}>
                  {state.detecting ? 'Detecting' : 'Not Detecting'}
                </span>
              </p>
            </div>
          ))}
        </div>
        <div className="size-card">
          <h3>Detected Mango Size</h3>
          <div className="size-display">{detectedSize}</div>
        </div>
      </section>

      <section className="statistics-section welcome-card">
        <h2>Sorting Statistics</h2>
        <div className="stats-cards">
          <div className="stats-card">
            <h3>Small Mangoes</h3>
            <p className="stats-count">{sortingStats.small}</p>
          </div>
          <div className="stats-card">
            <h3>Medium Mangoes</h3>
            <p className="stats-count">{sortingStats.medium}</p>
          </div>
          <div className="stats-card">
            <h3>Large Mangoes</h3>
            <p className="stats-count">{sortingStats.large}</p>
          </div>
          <div className="stats-card total">
            <h3>Total Sorted</h3>
            <p className="stats-count">{sortingStats.total}</p>
          </div>
        </div>

        <div className="sorting-history">
          <h3>Recent Sorting History</h3>
          <div className="history-list">
            {sortingHistory.slice(-10).reverse().map((entry, index) => (
              <div key={index} className="history-item">
                <span className="history-time">{entry.timestamp}</span>
                <span className={`history-size ${entry.size.toLowerCase()}`}>
                  {entry.size}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="session-control">
          <h3>Sorting Session</h3>
          <button className={`session-button ${sessionActive ? 'stop-session' : ''}`} onClick={() => setSessionActive(!sessionActive)}>
            {sessionActive ? 'Stop Session' : 'Start Session'}
          </button>
        </div>
      </section>
    </div>
  );
}

export default Dashboard;