// frontend/src/components/Dashboard.js
import React, { useState, useEffect, useRef } from 'react';
import { jsPDF } from 'jspdf';
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
  const [showNoMangoPopup, setShowNoMangoPopup] = useState(false);
  // Session and history management
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingName, setEditingName] = useState('');
  const videoRef = useRef(null);
  const [cameraError, setCameraError] = useState(null);
  const [cameraDevices, setCameraDevices] = useState([]);
  const [selectedCameraId, setSelectedCameraId] = useState('');
  const [webrtcStatus, setWebrtcStatus] = useState('idle');
  const pcRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showTutorial, setShowTutorial] = useState(false);
  const defaultSettings = {
    limitSmall: 100,
    limitMedium: 100,
    limitLarge: 100,
    limitDefective: 20,
    showNoMangoPopup: true,
    enablePiStream: false,
    piStreamUrl: ''
  };
  const [settings, setSettings] = useState(defaultSettings);
  const [sessionPaused, setSessionPaused] = useState(false);
  const [hardwareStatus, setHardwareStatus] = useState('Normal');
  const [cpuTemp, setCpuTemp] = useState(45);
  const [hardwareAlert, setHardwareAlert] = useState('');
  const [showTempPopup, setShowTempPopup] = useState(false);
  const [isDefectiveFlag, setIsDefectiveFlag] = useState(false);
  const [limitAlert, setLimitAlert] = useState('');
  const [showLimitAlert, setShowLimitAlert] = useState(false);
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
      title: 'Hardware Status',
      body: 'Ensure your hardware is running smoothly. Click the Hardware Status tab in the menu bar to verify that every single sensor is online and working correctly.'
    }
  ];
  const openTutorial = () => setShowTutorial(true);
  const overlayRef = useRef(null);
  const menuToggleRef = useRef(null);
  const [pwCurrent, setPwCurrent] = useState('');
  const [pwNew, setPwNew] = useState('');
  const [pwConfirm, setPwConfirm] = useState('');
  const [passwordMessage, setPasswordMessage] = useState('');
  // Track counts with ref to ensure we always save current values (not stale state)
  const countsRef = useRef({ small: 0, medium: 0, large: 0, defective: 0, total: 0 });

  const controlConveyor = async (action) => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/conveyor`;
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      });
      const result = await res.json();
      if (!res.ok) {
        console.error('Conveyor control failed', result);
      }
      return result;
    } catch (err) {
      console.error('Conveyor control error', err);
      return { success: false, message: err.message };
    }
  };

  const stopSessionImmediately = async () => {
    if (!sessionActive && !sessionPaused) return;
    setHardwareAlert('Stopped due to limit or temperature condition.');
    await endBatch();
  };
  const checkLimits = (stats) => {
    if (settings.limitSmall && stats.small >= settings.limitSmall) {
      setLimitAlert('Amount limit reached: small mangoes');
      setShowLimitAlert(true);
      stopSessionImmediately();
      return true;
    }
    if (settings.limitMedium && stats.medium >= settings.limitMedium) {
      setLimitAlert('Amount limit reached: medium mangoes');
      setShowLimitAlert(true);
      stopSessionImmediately();
      return true;
    }
    if (settings.limitLarge && stats.large >= settings.limitLarge) {
      setLimitAlert('Amount limit reached: large mangoes');
      setShowLimitAlert(true);
      stopSessionImmediately();
      return true;
    }
    if (settings.limitDefective && stats.defective >= settings.limitDefective) {
      setLimitAlert('Amount limit reached: defective mangoes');
      setShowLimitAlert(true);
      stopSessionImmediately();
      return true;
    }
    return false;
  };

  const handleChangePassword = (e) => {
    e.preventDefault();
    setPasswordMessage('');

    if (!pwCurrent || !pwNew || !pwConfirm) {
      setPasswordMessage('Please complete all password fields.');
      return;
    }

    if (pwNew.length < 6) {
      setPasswordMessage('New password must be at least 6 characters.');
      return;
    }

    if (pwNew !== pwConfirm) {
      setPasswordMessage('New Password and Confirm Password do not match.');
      return;
    }

    const persisted = JSON.parse(localStorage.getItem('userPasswords') || '{}');
    const username = user?.username || user?.name || '';

    if (!username) {
      setPasswordMessage('User not recognized.');
      return;
    }

    const existingPassword = persisted[username];
    if (!existingPassword) {
      setPasswordMessage('No local password record found for this user.');
      return;
    }

    if (existingPassword !== pwCurrent) {
      setPasswordMessage('Current password is incorrect.');
      return;
    }

    persisted[username] = pwNew;
    localStorage.setItem('userPasswords', JSON.stringify(persisted));

    setPasswordMessage('Password changed successfully.');
    setPwCurrent('');
    setPwNew('');
    setPwConfirm('');
  };

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

      // Show no mango popup when all sensors report none for 1 update (if enabled)
      const anyDetected = data.small || data.medium || data.large || data.defective || data.detectedSize;
      if (!anyDetected && settings.showNoMangoPopup) {
        setShowNoMangoPopup(true);
        setTimeout(() => setShowNoMangoPopup(false), 1600);
      } else {
        setShowNoMangoPopup(false);
      }

      // only update counts when a session is active and not paused
      if (!sessionActive || sessionPaused) {
        if (data.defective) {
          setDetectedSize('DEFECTIVE');
          setIsDefective(true);
          setIsDefectiveFlag(true);
        } else {
          setIsDefective(false);
          setIsDefectiveFlag(false);
        }
        return;
      }

      // Check if defective
      if (data.defective) {
        setIsDefective(true);
        setIsDefectiveFlag(true);
        setDetectedSize('DEFECTIVE');
        console.log('🚨 Defective detected! Adding to count.');
        setSortingStats(prev => {
          const updated = { ...prev, defective: prev.defective + 1, total: prev.total + 1 };
          countsRef.current = updated;  // Keep ref in sync
          checkLimits(updated);
          return updated;
        });
      } else {
        setIsDefective(false);
        setIsDefectiveFlag(false);
        // Update detected size based on hardware data
        switch(data.detectedSize) {
          case 1:
            setDetectedSize('SMALL');
            setSortingStats(prev => {
              const updated = { ...prev, small: prev.small + 1, total: prev.total + 1 };
              countsRef.current = updated;
              checkLimits(updated);
              return updated;
            });
            break;
          case 2:
            setDetectedSize('MEDIUM');
            setSortingStats(prev => {
              const updated = { ...prev, medium: prev.medium + 1, total: prev.total + 1 };
              countsRef.current = updated;
              checkLimits(updated);
              return updated;
            });
            break;
          case 3:
            setDetectedSize('LARGE');
            setSortingStats(prev => {
              const updated = { ...prev, large: prev.large + 1, total: prev.total + 1 };
              countsRef.current = updated;
              checkLimits(updated);
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

    // poll every 500ms to reduce CPU load and improve responsiveness on low-end devices
    const intervalId = setInterval(pollSensorData, 500);
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

  // CPU temperature monitor (RPi read from /sys/class/thermal/thermal_zone0/temp)
  useEffect(() => {
    const fetchTemp = async () => {
      try {
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5000/api/cpu-temp`;
        const res = await fetch(apiUrl);
        const data = await res.json();
        if (res.ok && data && typeof data.cpuTemp === 'number') {
          setCpuTemp(data.cpuTemp);
          if (data.cpuTemp >= 85) {
            setHardwareStatus('Over Limit (Shutdown)');
            setHardwareAlert('Temperature has exceeded operational limits. Machine turning off. Saving the current batch to history.');
            setShowTempPopup(true);
            stopSessionImmediately();
          } else if (data.cpuTemp >= 80) {
            setHardwareStatus('Throttling');
            setHardwareAlert('Temperature is nearly exceeding limits. Stop operations immediately to prevent machine damage.');
            setShowTempPopup(true);
          } else if (data.cpuTemp >= 70) {
            setHardwareStatus('High Load');
            setHardwareAlert('High load. Consider cooling.');
            setShowTempPopup(true);
          } else if (data.cpuTemp >= 50) {
            setHardwareStatus('Normal');
            setHardwareAlert('Normal operating temperatures.');
            setShowTempPopup(false);
          } else {
            setHardwareStatus('Ambient');
            setHardwareAlert('Ambient temperature.');
            setShowTempPopup(false);
          }
        } else {
          // fallback to simulated value when no real sensor available
          setCpuTemp(prevTemp => {
            let nextTemp = prevTemp + (Math.random() * 4 - 1.5);
            nextTemp = Math.max(35, Math.min(92, nextTemp));
            return nextTemp;
          });
        }
      } catch (err) {
        console.error('Could not read CPU temperature', err);
      }
    };

    fetchTemp();
    const intervalId = setInterval(fetchTemp, 3000);
    return () => clearInterval(intervalId);
  }, [stopSessionImmediately]);

  // Start and manage webcam stream for live preview via WebRTC
  useEffect(() => {
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
    });
    pcRef.current = pc;

    setWebrtcStatus('connecting');

    pc.ontrack = (event) => {
      if (videoRef.current) {
        videoRef.current.srcObject = event.streams[0];
      }
    };

    pc.oniceconnectionstatechange = () => {
      setWebrtcStatus(pc.iceConnectionState);
      if (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected') {
        setCameraError('WebRTC connection failed. Please make sure cam_stream.py is running on Pi.');
      }
    };

    const startWebrtc = async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const response = await fetch('/api/webrtc-offer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
        });

        if (!response.ok) {
          const text = await response.text();
          throw new Error(`Offer failed: ${response.status} ${text}`);
        }

        const answer = await response.json();
        await pc.setRemoteDescription(new RTCSessionDescription(answer));

        setCameraError(null);
        setWebrtcStatus('connected');
      } catch (err) {
        console.error('WebRTC setup failed', err);
        setCameraError(err.message || 'WebRTC setup failed');
        setWebrtcStatus('error');
      }
    };

    startWebrtc();

    return () => {
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
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

  const exportSortingHistory = () => {
    if (!sessions || sessions.length === 0) {
      alert('No sessions to export');
      return;
    }

    try {
      const doc = new jsPDF('landscape');
      const margin = 18;
      let y = 18;

      doc.setTextColor('#2b2b2b');
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(18);
      doc.text('Mango Sorter Batch History', margin, y);

      y += 10;
      doc.setFontSize(11);
      doc.setFont('helvetica', 'normal');
      doc.text(`Generated: ${new Date().toLocaleString()}`, margin, y);
      y += 8;
      doc.text(`Total batches: ${sessions.length}`, margin, y);

      y += 12;
      const headers = ['Batch', 'Small', 'Medium', 'Large', 'Defective', 'Total', 'Start Time', 'End Time'];
      const colW = [30, 20, 20, 20, 25, 20, 40, 40];
      let x = margin;

      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      headers.forEach((heading, i) => {
        doc.text(heading, x, y);
        x += colW[i];
      });

      y += 8;
      doc.setFont('helvetica', 'normal');

      let totals = { small: 0, medium: 0, large: 0, defective: 0, total: 0 };

      sessions.forEach((s, idx) => {
        if (y > 265) {
          doc.addPage();
          y = 20;
        }

        x = margin;
        const start = s.timestamps?.start_time ? new Date(s.timestamps.start_time).toLocaleString() : '-';
        const end = s.timestamps?.end_time ? new Date(s.timestamps.end_time).toLocaleString() : '-';
        const row = [
          s.session_name || `Batch ${idx + 1}`,
          s.counts?.small ?? 0,
          s.counts?.medium ?? 0,
          s.counts?.large ?? 0,
          s.counts?.defective ?? 0,
          s.counts?.total ?? 0,
          start,
          end
        ];

        totals.small += row[1];
        totals.medium += row[2];
        totals.large += row[3];
        totals.defective += row[4];
        totals.total += row[5];

        row.forEach((cell, i) => {
          const value = String(cell);
          doc.text(value, x, y);
          x += colW[i];
        });
        y += 7;
      });

      if (y + 18 < 285) {
        y += 10;
      } else {
        doc.addPage();
        y = 20;
      }

      doc.setFont('helvetica', 'bold');
      doc.text('Totals', margin, y);
      doc.setFont('helvetica', 'normal');
      doc.text(String(totals.small), margin + colW[0], y);
      doc.text(String(totals.medium), margin + colW[0] + colW[1], y);
      doc.text(String(totals.large), margin + colW[0] + colW[1] + colW[2], y);
      doc.text(String(totals.defective), margin + colW[0] + colW[1] + colW[2] + colW[3], y);
      doc.text(String(totals.total), margin + colW[0] + colW[1] + colW[2] + colW[3] + colW[4], y);

      const fileName = `batch_history_export_${new Date().toISOString().replace(/[:.]/g, '-')}.pdf`;
      doc.save(fileName);

      return;
    } catch (err) {
      console.error('PDF export failed, check jsPDF installation:', err);
      alert('PDF export failed. Please install jsPDF and reload (npm install jspdf).');
      return;
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
  const startNewSession = async () => {
    try {
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
        const newId = created._id || created.data?._id || (created.offline && created.data?._id) || null;

        setCurrentSessionId(newId);
        const initialCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
        setSortingStats(initialCounts);
        countsRef.current = initialCounts;
        setSessionActive(true);
        setSessionPaused(false);
        setHardwareAlert('New batch started');
        await controlConveyor('start');
        fetchSessions();
      } else {
        console.error('Failed to start session', await res.text());
      }
    } catch (err) {
      console.error('Error starting session', err);
    }
  };

  const continueBatch = async () => {
    if (!currentSessionId) {
      setHardwareAlert('No stopped batch exists. Start new batch.');
      return;
    }
    setSessionActive(true);
    setSessionPaused(false);
    setHardwareAlert('Continuing existing batch');
    await controlConveyor('start');
  };

  const endBatch = async () => {
    try {
      if (!currentSessionId) {
        setHardwareAlert('No batch to stop');
        return;
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
          timestamps: { end_time: new Date() }
        })
      });
      if (res.ok) {
        await controlConveyor('stop');
        setSessionActive(false);
        setSessionPaused(false);
        setCurrentSessionId(null);
        setHardwareAlert('Batch stopped and finalized');
        setTimeout(() => { fetchSessions(); }, 500);
      } else {
        console.error('Failed to end batch');
      }
    } catch (err) {
      console.error('Error ending batch', err);
    }
  };

  const pauseBatch = async () => {
    try {
      if (!currentSessionId) {
        setHardwareAlert('No active batch to pause');
        setSessionActive(false);
        setSessionPaused(false);
        return;
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
          }
        })
      });
      if (res.ok) {
        await controlConveyor('stop');
        setSessionActive(false);
        setSessionPaused(true);
        setHardwareAlert('Batch paused - you may continue or stop batch');
        setTimeout(() => { fetchSessions(); }, 500);
      } else {
        console.error('Failed to pause session');
      }
    } catch (err) {
      console.error('Error pausing session', err);
    }
  };

  const handleToggleSession = async () => {
    if (sessionActive) {
      await pauseBatch();
    } else if (sessionPaused && currentSessionId) {
      continueBatch();
    } else {
      await startNewSession();
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
            <div className="tutorial-content">
              {tutorialSteps.map((step, idx) => (
                <div key={idx} className="tutorial-step">
                  <div className="tutorial-step-title">{step.title}</div>
                  <div className="tutorial-step-body">{step.body}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {showLimitAlert && (
        <div className="tutorial-overlay" onClick={() => setShowLimitAlert(false)}>
          <div className="tutorial-box">
            <div className="tutorial-close" onClick={() => setShowLimitAlert(false)}>✕</div>
            <h3 style={{ color: 'red' }}>Amount limit reached</h3>
            <p>{limitAlert}</p>
          </div>
        </div>
      )}
      {showTempPopup && (
        <div className="tutorial-overlay" onClick={() => setShowTempPopup(false)}>
          <div className="tutorial-box">
            <div className="tutorial-close" onClick={() => setShowTempPopup(false)}>✕</div>
            <h3 style={{ color: '#b71c1c' }}>CPU Temperature Alert</h3>
            <p>{hardwareAlert}</p>
          </div>
        </div>
      )}

      {showNoMangoPopup && (
        <div className="tutorial-overlay" onClick={() => setShowNoMangoPopup(false)}>
          <div className="tutorial-box">
            <div className="tutorial-close" onClick={() => setShowNoMangoPopup(false)}>✕</div>
            <h3 style={{ color: '#1565c0' }}>No Mangoes Detected</h3>
            <p>No mangoes are in view of the camera currently. Please check the conveyor and camera alignment.</p>
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
              <button type="button" onClick={() => { setCurrentView('hardware-status'); setMenuOpen(false); }} className={`menu-item ${currentView === 'hardware-status' ? 'active' : ''}`}>Hardware Status</button>
              <button type="button" onClick={() => { setCurrentView('settings'); setMenuOpen(false); }} className={`menu-item ${currentView === 'settings' ? 'active' : ''}`}>Settings</button>
              <button type="button" onClick={() => { setCurrentView('change-password'); setMenuOpen(false); }} className={`menu-item ${currentView === 'change-password' ? 'active' : ''}`}>Change Password</button>
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
                    <p style={{fontSize: '12px', marginTop: '12px', color: '#666'}}>
                      Make sure `cam_stream.py` is running on Raspberry Pi and that backend route `/api/webrtc-offer` is available.
                    </p>
                  </div>
                ) : (
                  <video ref={videoRef} className="camera-video" autoPlay playsInline muted />
                )}
              </div>
              <p style={{ marginTop: '6px', color: '#444', fontSize: '12px' }}>WebRTC status: {webrtcStatus}</p>
            </div>

            <div className="system-controls">
              <h3>System Controls</h3>
              {isDefectiveFlag && <div style={{ color: 'red', fontWeight: 700, marginBottom: '8px' }}>DEFECTIVE</div>}
              <div className="system-control-actions">
                <button
                  className="control-button start-button"
                  onClick={startNewSession}
                  disabled={sessionActive || sessionPaused}
                  style={{ fontSize: '15px', padding: '14px 16px' }}
                >
                  Start New Batch
                </button>

                {sessionActive && (
                  <button
                    className="control-button stop-button"
                    onClick={pauseBatch}
                    style={{ fontSize: '15px', padding: '14px 16px' }}
                  >
                    Pause Batch
                  </button>
                )}

                {sessionPaused && (
                  <button
                    className="control-button start-button"
                    onClick={continueBatch}
                    style={{ fontSize: '15px', padding: '14px 16px' }}
                  >
                    Continue Batch
                  </button>
                )}

                {(sessionActive || sessionPaused) && (
                  <button
                    className="control-button stop-button"
                    onClick={endBatch}
                    style={{ fontSize: '15px', padding: '14px 16px' }}
                  >
                    Stop Batch
                  </button>
                )}
              </div>
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
        ) : currentView === 'hardware-status' ? (
          <>
        {/* Hardware Status View */}
        <div className="sensor-status-container">
          <h2 style={{ margin: '0 0 24px 0', fontSize: '20px', fontWeight: '600', color: '#333' }}>Hardware Status</h2>
          
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
          <div className="hardware-status-panel" style={{ marginTop: '20px' }}>
            <h3>CPU Temperature Monitor</h3>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{ width: '70%', background: '#eee', borderRadius: '8px', height: '22px', overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, Math.round((cpuTemp / 90) * 100))}%`, height: '100%', background: cpuTemp >= 85 ? '#ff5252' : cpuTemp >= 80 ? '#ffeb3b' : cpuTemp >= 70 ? '#ff9800' : cpuTemp >= 50 ? '#4caf50' : '#2196f3', transition: 'width 0.3s ease' }} />
              </div>
              <strong>{cpuTemp.toFixed(1)}°C</strong>
            </div>
            <p style={{ marginTop: '8px', color: cpuTemp >= 85 ? '#b71c1c' : cpuTemp >= 80 ? '#ff6f00' : cpuTemp >= 70 ? '#f57c00' : '#333' }}>{hardwareStatus}: {hardwareAlert}</p>
          </div>
        </div>
          </>
        ) : currentView === 'change-password' ? (
          <>
            {/* Change Password View */}
            <div className="change-password-panel">
              <h2>Change Password</h2>
              <form onSubmit={handleChangePassword} style={{ display: 'grid', gap: '14px' }}>
                <label>
                  Current Password
                  <input type="password" value={pwCurrent} onChange={e => setPwCurrent(e.target.value)} required />
                </label>
                <label>
                  New Password
                  <input type="password" value={pwNew} onChange={e => setPwNew(e.target.value)} required />
                </label>
                <label>
                  Confirm New Password
                  <input type="password" value={pwConfirm} onChange={e => setPwConfirm(e.target.value)} required />
                </label>
                <button type="submit" className="control-button start-button" style={{ width: '220px' }}>Save Password</button>
                {passwordMessage && <p style={{ color: passwordMessage.includes('successfully') ? '#2e7d32' : '#d32f2f', fontWeight: 600 }}>{passwordMessage}</p>}
                <p className="hint">New password must be at least 6 characters.</p>
              </form>
            </div>
          </>
        ) : currentView === 'settings' ? (
          <>
            {/* Settings View */}
            <div className="settings-panel">
              <h2>Settings</h2>

              <div className="settings-section">
                <h3>Mango Detection</h3>
                <div className="settings-grid">
                  <label>
                    Small mango limit:
                    <input type="number" min="0" value={settings.limitSmall} onChange={e => setSettings(s => ({ ...s, limitSmall: Number(e.target.value) }))} />
                  </label>
                  <label>
                    Medium mango limit:
                    <input type="number" min="0" value={settings.limitMedium} onChange={e => setSettings(s => ({ ...s, limitMedium: Number(e.target.value) }))} />
                  </label>
                  <label>
                    Large mango limit:
                    <input type="number" min="0" value={settings.limitLarge} onChange={e => setSettings(s => ({ ...s, limitLarge: Number(e.target.value) }))} />
                  </label>
                  <label>
                    Defective mango limit:
                    <input type="number" min="0" value={settings.limitDefective} onChange={e => setSettings(s => ({ ...s, limitDefective: Number(e.target.value) }))} />
                  </label>
                </div>
                <div className="settings-popup-toggle">
                  <input
                    type="checkbox"
                    checked={settings.showNoMangoPopup}
                    onChange={e => setSettings(s => ({ ...s, showNoMangoPopup: e.target.checked }))}
                  />
                  <span>Show "No Mangoes Detected" popup</span>
                </div>
              </div>

              <div className="settings-actions">
                <button type="button" className="control-button start-button" onClick={() => setPasswordMessage('Settings saved')}>
                  Save Settings
                </button>
                <button type="button" className="control-button stop-button" onClick={() => {
                  setSettings(defaultSettings);
                  setPasswordMessage('Settings reset to defaults');
                }}>
                  Reset to Defaults
                </button>
              </div>
              <p className="hint">These limits automatically stop the batch when reached.</p>
            </div>
          </>
        ) : (
          <>
        {/* Session history table */}
        <div className="batch-history-container">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
            <h2 style={{ margin: 0 }}>Sorting History (Sessions)</h2>
            <div>
              <button onClick={exportSortingHistory} style={{ padding: '10px 20px', backgroundColor: '#1976d2', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600', marginRight: '8px' }}>Export</button>
              <button onClick={clearSessions} style={{ padding: '10px 20px', backgroundColor: '#f44336', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600' }}>Clear</button>
            </div>
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