import React, { useState, useEffect, useRef } from 'react';
import { jsPDF } from 'jspdf';
import './Dashboard.css';

function Dashboard({ user, token, onLogout }) {
  const [currentView, setCurrentView] = useState('dashboard');
  const [cameraStatus, setCameraStatus] = useState('Disconnected');
  const [cameraError, setCameraError] = useState('');
  const [webrtcReady, setWebrtcReady] = useState(false);
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });
  const [detectedSize, setDetectedSize] = useState('NONE');
  const [sortingStats, setSortingStats] = useState({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
  const [isDefective, setIsDefective] = useState(false);
  const [sortingHistory, setSortingHistory] = useState([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingName, setEditingName] = useState('');
  
  const videoRef = useRef(null);
  const [cameraReloadKey, setCameraReloadKey] = useState(0);
  const pcRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showTutorial, setShowTutorial] = useState(false);

  const [settings, setSettings] = useState({ limitSmall: 100, limitMedium: 100, limitLarge: 100, limitDefective: 20 });
  const [sessionPaused, setSessionPaused] = useState(false);
  const [hardwareStatus, setHardwareStatus] = useState('Normal');
  const [cpuTemp, setCpuTemp] = useState(45);
  const [hardwareAlert, setHardwareAlert] = useState('');
  const [showTempPopup, setShowTempPopup] = useState(false);
  const [isDefectiveFlag, setIsDefectiveFlag] = useState(false);
  const [showLimitAlert, setShowLimitAlert] = useState(false);
  const [showTwoMangoesPopup, setShowTwoMangoesPopup] = useState(false);
  const [showBuzzerPopup, setShowBuzzerPopup] = useState(false);
  
  const [gateStates, setGateStates] = useState({ small: 'closed', medium: 'closed', large: 'closed' });
  const BACKEND_URL = `${window.location.protocol}//${window.location.hostname}:5001`;

  const countsRef = useRef({ small: 0, medium: 0, large: 0, defective: 0, total: 0 });
  const autoSaveIntervalRef = useRef(null);
  const previousDefectiveStateRef = useRef(false);
  const previousTwoMangoesStateRef = useRef(false);

  // Persistence: Restore session on load
  useEffect(() => {
    const fetchActiveSession = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/sessions/active`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const active = await res.json();
          if (active) {
            const counts = active.counts || { small: 0, medium: 0, large: 0, defective: 0, total: 0 };
            setCurrentSessionId(active._id || active.id || null);
            setSessionActive(true);
            setSortingStats(counts);
            countsRef.current = counts;
            startAutoSave(active._id || active.id);
          }
        }
      } catch (err) { console.error('Failed to restore active session'); }
    };
    fetchActiveSession();
  }, []);

  const startAutoSave = (sessionId) => {
    if (autoSaveIntervalRef.current) clearInterval(autoSaveIntervalRef.current);
    autoSaveIntervalRef.current = setInterval(async () => {
      if (!sessionId || !sessionActive) return;
      try {
        const currentCounts = countsRef.current;
        await fetch(`${BACKEND_URL}/api/sessions/${sessionId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ counts: currentCounts })
        });
      } catch (err) { console.error('Auto-save error:', err); }
    }, 30000);
  };

  const stopAutoSave = () => {
    if (autoSaveIntervalRef.current) {
      clearInterval(autoSaveIntervalRef.current);
      autoSaveIntervalRef.current = null;
    }
  };

  const startHardware = async () => {
    await fetch(`${BACKEND_URL}/api/hardware/start`, { method: 'POST' });
  };

  const controlConveyor = async (action) => {
    await fetch(`${BACKEND_URL}/api/hardware/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    });
  };

  const initWebRTCStream = async () => {
    if (!videoRef.current) return;
    if (pcRef.current) { pcRef.current.close(); pcRef.current = null; }

    try {
      const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
      pcRef.current = pc;
      pc.ontrack = (event) => { if (videoRef.current) videoRef.current.srcObject = event.streams[0]; };
      
      pc.addTransceiver('video', { direction: 'recvonly' });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const response = await fetch(`${BACKEND_URL}/api/webrtc-offer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
      });
      const answer = await response.json();
      await pc.setRemoteDescription(new RTCSessionDescription(answer));
      setCameraStatus('WebRTC stream is live');
      setWebrtcReady(true);
    } catch (err) {
      setCameraError('Unable to start stream');
    }
  };

  useEffect(() => {
    if (currentView === 'dashboard') initWebRTCStream();
  }, [currentView, cameraReloadKey]);

  const handleSensorData = (data) => {
    setSensorStates(prev => ({
        small: { ...prev.small, detecting: data.small },
        medium: { ...prev.medium, detecting: data.medium },
        large: { ...prev.large, detecting: data.large }
    }));

    // Trigger alerts for hardware status codes
    if (data.buzzerTriggered) {
        setShowBuzzerPopup(true);
        setHardwareAlert(data.alertMessage || "System Alert: Check Conveyor");
    }

    // Handle two or more mangoes detected
    if (data.twoMangoes && !previousTwoMangoesStateRef.current) {
        setShowTwoMangoesPopup(true);
        if (sessionActive) {
            pauseBatch();
            setHardwareAlert('Multiple mangoes detected - Batch paused');
        }
    }
    previousTwoMangoesStateRef.current = data.twoMangoes || false;

    if (!sessionActive || sessionPaused) return;

    // Update Counts (Simplified for brevity)
    // Only increment on state transition from false to true (new defective detection)
    if (data.defective && !previousDefectiveStateRef.current) {
        setSortingStats(prev => {
            const updated = { ...prev, defective: prev.defective + 1, total: prev.total + 1 };
            countsRef.current = updated;
            return updated;
        });
    }
    previousDefectiveStateRef.current = data.defective;
    // ... logic for small/medium/large ...
  };

  // Poll sensor telemetry
  useEffect(() => {
    const pollSensorData = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/hardware/sensors`);
        if (!res.ok) return;
        const data = await res.json();
        handleSensorData(data);
      } catch (err) { console.error(err); }
    };
    const intervalId = setInterval(pollSensorData, 500);
    return () => clearInterval(intervalId);
  }, [sessionActive, sessionPaused]);

  // Pause Batch (Removed Database Save)
  const pauseBatch = async () => {
    if (!currentSessionId) return;
    await controlConveyor('pause');
    setSessionActive(false);
    setSessionPaused(true);
    setHardwareAlert('Batch paused - Waiting for input');
  };

  const endBatch = async () => {
    if (!currentSessionId) return;
    await controlConveyor('stop');
    // Save final state
    await fetch(`${BACKEND_URL}/api/sessions/${currentSessionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ counts: countsRef.current, timestamps: { end_time: new Date() } })
    });
    stopAutoSave();
    setSessionActive(false);
    setSessionPaused(false);
    setCurrentSessionId(null);
  };

  const startNewSession = async () => {
    try {
      const initialCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
      setSortingStats(initialCounts);
      countsRef.current = initialCounts;

      const res = await fetch(`${BACKEND_URL}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ session_name: `Batch ${sessions.length + 1}`, counts: initialCounts, timestamps: { start_time: new Date() } })
      });
      if (res.ok) {
        const created = await res.json();
        const sessionId = created._id || created.id || (created.data && created.data._id) || null;
        if (sessionId) {
          setCurrentSessionId(sessionId);
          startAutoSave(sessionId);
        }
      }
      await startHardware();
      await controlConveyor('start');
      setSessionActive(true);
    } catch (err) { console.error(err); }
  };

  return (
    <div className={`dashboard ${menuOpen ? 'menu-open' : ''}`}>
      {/* Popups... */}
      {showBuzzerPopup && (
        <div className="tutorial-overlay" onClick={() => setShowBuzzerPopup(false)}>
            <div className="tutorial-box">
                <h3 style={{ color: 'red' }}>⚠️ System Alert</h3>
                <p>{hardwareAlert}</p>
            </div>
        </div>
      )}

      {showTwoMangoesPopup && (
        <div className="tutorial-overlay" onClick={() => setShowTwoMangoesPopup(false)}>
            <div className="tutorial-box">
                <h3 style={{ color: 'orange' }}>⚠️ Multiple Mangoes Detected</h3>
                <p>Two or more mangoes detected in the chamber.{sessionActive ? ' Batch has been paused.' : ''}</p>
                <button onClick={() => setShowTwoMangoesPopup(false)} style={{ marginTop: '10px', padding: '8px 16px' }}>OK</button>
            </div>
        </div>
      )}

      {/* Header, Menu, Dashboard content... */}
      {/* NOTE: Remove the <button onClick={clearSessions}>Clear</button> element from Batch History view */}
      
      {currentView === 'batch-history' && (
        <div className="batch-history-container">
            <h2>Sorting History</h2>
            {/* Clear button removed by user requirement */}
            <table className="history-table">
               {/* ... Table content ... */}
            </table>
        </div>
      )}
      
      {/* ... rest of the component ... */}
    </div>
  );
}

export default Dashboard;