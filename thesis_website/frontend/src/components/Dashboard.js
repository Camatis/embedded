import React, { useState, useEffect, useRef } from 'react';
import { jsPDF } from 'jspdf';
import './Dashboard.css';

function Dashboard({ user, token, onLogout }) {
  //main view state
  const [currentView, setCurrentView] = useState('dashboard');
  //sensor detection states
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });
  //last detected mango size
  const [detectedSize, setDetectedSize] = useState('NONE');
  const [sortingStats, setSortingStats] = useState({
    small: 0,
    medium: 0,
    large: 0,
    total: 0,
    defective: 0
  });
  //defective flag
  const [isDefective, setIsDefective] = useState(false);
  const [sortingHistory, setSortingHistory] = useState([]);
  //session and batch management
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingName, setEditingName] = useState('');
  const videoRef = useRef(null);
  const [cameraError, setCameraError] = useState(null);
  const [cameraReloadKey, setCameraReloadKey] = useState(0);
  const pcRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showTutorial, setShowTutorial] = useState(false);
  const defaultSettings = {
    limitSmall: 100,
    limitMedium: 100,
    limitLarge: 100,
    limitDefective: 20,
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
  
  // Gate state tracking
  const [gateStates, setGateStates] = useState({
    small: 'closed',
    medium: 'closed',
    large: 'closed'
  });
  const BACKEND_URL = `${window.location.protocol}//${window.location.hostname}:5001`;
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
  //track counts with ref
  const countsRef = useRef({ small: 0, medium: 0, large: 0, defective: 0, total: 0 });
  const lastDetectedRef = useRef({ size: null, timestamp: null });

  const controlGate = async (gate, action) => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`;
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gate, action })
      });
      const result = await res.json();
      if (!res.ok) {
        console.error('Gate control failed', result);
      }
      return result;
    } catch (err) {
      console.error('Gate control error', err);
      return { success: false, message: err.message };
    }
  };

  const controlConveyor = async (action) => {
    try {
      let endpoint = '/api/hardware/conveyor';
      if (action === 'pause') {
        endpoint = '/api/hardware/pause';
      } else if (action === 'continue') {
        endpoint = '/api/hardware/continue';
      }
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001${endpoint}`;
      const payload = action === 'pause' || action === 'continue' ? {} : { action };
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
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

  const startHardware = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/start`;
      const res = await fetch(apiUrl, { method: 'POST' });
      const result = await res.json();
      if (res.ok) {
        console.log('Hardware process started:', result);
        return result;
      } else {
        console.error('Failed to start hardware:', result);
        return result;
      }
    } catch (err) {
      console.error('Hardware start error:', err);
      return { success: false, message: err.message };
    }
  };

  const stopHardware = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/stop`;
      const res = await fetch(apiUrl, { method: 'POST' });
      const result = await res.json();
      if (res.ok) {
        console.log('Hardware process stopped:', result);
        return result;
      } else {
        console.error('Failed to stop hardware:', result);
        return result;
      }
    } catch (err) {
      console.error('Hardware stop error:', err);
      return { success: false, message: err.message };
    }
  };

  const getHardwareStatus = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/status`;
      const res = await fetch(apiUrl);
      const result = await res.json();
      return result;
    } catch (err) {
      console.error('Hardware status error:', err);
      return { running: false, process_active: false };
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

  //process sensor data and update stats
  const handleSensorData = (data) => {
    try {
      // Debug logging for received sensor data
      if (data.detectedSize || data.defective) {
        console.log('📡 [SENSOR DATA] Received:', { 
          sessionActive, 
          sessionPaused, 
          detectedSize: data.detectedSize, 
          defective: data.defective,
          timestamp: new Date().toISOString()
        });
      }

      //update sensor states
      setSensorStates(prev => ({
        small: { ...prev.small, detecting: data.small },
        medium: { ...prev.medium, detecting: data.medium },
        large: { ...prev.large, detecting: data.large }
      }));

      const parseCount = (value) => {
        if (typeof value === 'number') return value;
        if (typeof value === 'string' && value.trim() !== '') {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : 0;
        }
        return 0;
      };

      const countsSource = data.counts && (data.counts.small !== undefined || data.counts.medium !== undefined || data.counts.large !== undefined || data.counts.defective !== undefined)
        ? {
            small: parseCount(data.counts.small),
            medium: parseCount(data.counts.medium),
            large: parseCount(data.counts.large),
            defective: parseCount(data.counts.defective),
            total: typeof data.counts.total === 'number'
              ? data.counts.total
              : parseCount(data.counts.small) + parseCount(data.counts.medium) + parseCount(data.counts.large) + parseCount(data.counts.defective)
          }
        : null;

      if (countsSource) {
        const needsSync = countsSource.small !== countsRef.current.small
          || countsSource.medium !== countsRef.current.medium
          || countsSource.large !== countsRef.current.large
          || countsSource.defective !== countsRef.current.defective
          || countsSource.total !== countsRef.current.total;

        if (needsSync) {
          console.log('🔄 [SYNC] Hardware count snapshot received, syncing counts:', countsSource);
          countsRef.current = countsSource;
          setSortingStats(prev => ({ ...prev, ...countsSource }));
        }
      }

      if (!currentSessionId) {
        if (!countsSource) {
          if (data.defective || data.detectedSize) {
            console.log('⚠️  [SENSOR] Current session missing but sensor event received:', data.detectedSize || 'DEFECTIVE');
          }
        }

        if (data.defective) {
          setDetectedSize('DEFECTIVE');
          setIsDefective(true);
          setIsDefectiveFlag(true);
        } else if (data.detectedSize) {
          const sizeLabel = String(data.detectedSize).toUpperCase();
          setDetectedSize(sizeLabel);
          setIsDefective(false);
          setIsDefectiveFlag(false);
        }
        return;
      }

      const mangoKey = data.lastMango?.timestamp
        ? `${data.lastMango.timestamp}-${String(data.detectedSize || '').toUpperCase()}`
        : null;

      const updateDetectedSize = (newSize) => {
        setDetectedSize(newSize);
        setIsDefective(newSize === 'DEFECTIVE');
        setIsDefectiveFlag(newSize === 'DEFECTIVE');
      };

      if (data.defective) {
        if (mangoKey && mangoKey !== lastDetectedRef.current.key) {
          lastDetectedRef.current.key = mangoKey;
          updateDetectedSize('DEFECTIVE');
          console.log('🚨 [COUNT] Defective detected from hardware snapshot');
          setSortingHistory(prev => {
            const newHistory = [...prev, { timestamp: new Date().toLocaleTimeString(), size: 'DEFECTIVE' }];
            return newHistory.slice(-100);
          });
        }
        return;
      }

      let sizeIndex = null;
      if (typeof data.detectedSize === 'string') {
        const mapped = data.detectedSize.trim().toUpperCase();
        if (mapped === 'SMALL') sizeIndex = 1;
        else if (mapped === 'MEDIUM') sizeIndex = 2;
        else if (mapped === 'LARGE') sizeIndex = 3;
        console.log('📏 [SIZE PARSE] Received:', data.detectedSize, '-> sizeIndex:', sizeIndex);
      } else if (typeof data.detectedSize === 'number') {
        sizeIndex = data.detectedSize;
        console.log('📏 [SIZE PARSE] Received number:', data.detectedSize, '-> sizeIndex:', sizeIndex);
      }

      if (sizeIndex >= 1 && sizeIndex <= 3 && mangoKey && mangoKey !== lastDetectedRef.current.key) {
        lastDetectedRef.current.key = mangoKey;
        const sizeLabel = sizeIndex === 1 ? 'SMALL' : sizeIndex === 2 ? 'MEDIUM' : 'LARGE';
        updateDetectedSize(sizeLabel);
        setSortingHistory(prev => {
          const newHistory = [...prev, { timestamp: new Date().toLocaleTimeString(), size: sizeLabel }];
          return newHistory.slice(-100);
        });
      }
    } catch (error) {
      console.error('Error processing sensor data:', error);
    }
  };

  //poll sensor data
  useEffect(() => {
    //mark sensors active
    setSensorStates(prev => ({
      small: { ...prev.small, status: 'Active' },
      medium: { ...prev.medium, status: 'Active' },
      large: { ...prev.large, status: 'Active' }
    }));

    let mounted = true;
    const pollSensorData = async () => {
      try {
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/sensors`;
        const res = await fetch(apiUrl);
        if (!res.ok) return;
        const data = await res.json();
        
        // Check if we actually got data
        if (!data || Object.keys(data).length === 0) return;

        const normalized = {
          small: !!data.trigger,
          medium: !!data.medium,
          large: !!data.large,
          defective: !!data.defective,
          detectedSize: data.detectedSize,
          counts: data.counts || {},
          lastMango: data.lastMango || {}
        };
        
        // Log received data for debugging
        if (data.defective || data.detectedSize) {
          console.log('📡 Received from backend sensor endpoint:', {
            detectedSize: data.detectedSize,
            counts: normalized.counts,
            lastMango: normalized.lastMango,
            sessionActive,
            sessionPaused
          });
        }

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
  }, [sessionActive, sessionPaused]);

  // CPU temperature monitor (RPi read from /sys/class/thermal/thermal_zone0/temp)
  useEffect(() => {
    const fetchTemp = async () => {
      try {
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/cpu-temp`;
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

    // Fetch session list from backend (used for Batch History)
  const fetchSessions = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/sessions`;
      const res = await fetch(apiUrl, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
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

  // Poll gate status for real-time feedback
  useEffect(() => {
    let mounted = true;
    const pollGateStatus = async () => {
      try {
        const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`;
        const res = await fetch(apiUrl);
        if (res.ok) {
          const data = await res.json();
          if (mounted && data.gate_states) {
            setGateStates(data.gate_states);
          }
        }
      } catch (err) {
        console.error('Error fetching gate status', err);
      }
    };
    
    pollGateStatus();
    const intervalId = setInterval(pollGateStatus, 1000);
    return () => {
      mounted = false;
      clearInterval(intervalId);
    };
  }, []);

  // Autosave counts periodically while session is active
  useEffect(() => {
    if (!sessionActive || !currentSessionId) return;

    const autosaveInterval = setInterval(async () => {
      try {
        const countsToSave = {
          small: countsRef.current.small,
          medium: countsRef.current.medium,
          large: countsRef.current.large,
          defective: countsRef.current.defective
        };
        console.log('💾 [AUTOSAVE] Saving counts to DB:', countsToSave, 'for session:', currentSessionId);
        
        const response = await fetch(`${BACKEND_URL}/api/sessions/${currentSessionId}`, {
          method: 'PUT',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({ counts: countsToSave })
        });
        
        if (response.ok) {
          console.log('✅ [AUTOSAVE] Counts saved successfully');
        } else {
          console.warn('⚠️  [AUTOSAVE] Failed - status:', response.status);
        }
      } catch (err) {
        console.warn('⚠️  [AUTOSAVE] Error:', err.message);
      }
    }, 3000);

    return () => clearInterval(autosaveInterval);
  }, [sessionActive, currentSessionId, token]);

  // Keep the active session row in history updated while a batch is running
  useEffect(() => {
    if (!currentSessionId) return;
    setSessions(prev => prev.map(session => {
      if (session._id !== currentSessionId) return session;
      return {
        ...session,
        counts: {
          small: sortingStats.small,
          medium: sortingStats.medium,
          large: sortingStats.large,
          defective: sortingStats.defective
        }
      };
    }));
  }, [sortingStats, currentSessionId]);

  // Delete all sessions on backend and clear local state
  const clearSessions = async () => {
    try {
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/sessions`;
      const res = await fetch(apiUrl, {
        method: 'DELETE',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        }
      });
      if (res.ok) {
        setSessions([]);
        setSortingStats({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
        setCurrentSessionId(null);
        await fetchSessions();
      } else {
        console.error('Failed to clear sessions');
        // fallback: clear UI state to avoid stale views
        setSessions([]);
        setSortingStats({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
      }
    } catch (err) {
      console.error('Error clearing sessions', err);
      setSessions([]);
      setSortingStats({ small: 0, medium: 0, large: 0, total: 0, defective: 0 });
    }
  };

  const loadImageDataUrl = (src) =>
    new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        resolve(canvas.toDataURL('image/png'));
      };
      img.onerror = (err) => reject(err);
      img.src = src;
    });

  const exportSortingHistory = async () => {
    if (!sessions || sessions.length === 0) {
      alert('No sessions to export');
      return;
    }

    try {
      const doc = new jsPDF('landscape');
      const margin = 18;
      let y = 28;

      // App gradient style from AuthStyles (header and subheader gradient bands)
      doc.setFillColor(253, 184, 19);
      doc.rect(0, 0, 297, 10, 'F');
      doc.setFillColor(253, 141, 19);
      doc.rect(0, 10, 297, 8, 'F');
      doc.setFillColor(107, 168, 47);
      doc.rect(0, 18, 297, 8, 'F');

      // Add login logo from public asset
      try {
        const logoDataUrl = await loadImageDataUrl('/login.png');
        doc.addImage(logoDataUrl, 'PNG', 250, 8, 34, 34);
      } catch (imgErr) {
        console.warn('Logo image load failed; fallback to icon.', imgErr);
        doc.setFillColor(255, 215, 0);
        doc.circle(268, 19, 8, 'F');
      }

      doc.setTextColor('#011627');
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(16);
      doc.text('AUTOMATED MANGO SORTING SYSTEM', margin, 15);
      doc.setFontSize(11);
      doc.setFont('helvetica', 'normal');
      doc.text('OFFICIAL QUALITY CONTROL & YIELD REPORT', margin, 22);

      // Place metadata below header
      y = 44;
      const username = user?.username || 'vince@email.com';
      const today = new Date();
      doc.setTextColor('#011627');
      doc.setFontSize(10);
      doc.text(`Operator: ${username}`, margin, y);
      y += 6;
      doc.text(`Date of Export: ${today.toLocaleDateString('en-US', { month:'long', day:'numeric', year:'numeric' })}`, margin, y);
      y += 6;
      doc.text('System Version: AI Vision Model v1.0 (YOLO)', margin, y);

      y += 12;
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(11);
      doc.text('1. EXECUTIVE SUMMARY', margin, y);
      y += 6;
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);

      const totalBatches = sessions.length;
      const totalSmall = sessions.reduce((acc, s) => acc + (s.counts?.small || 0), 0);
      const totalMedium = sessions.reduce((acc, s) => acc + (s.counts?.medium || 0), 0);
      const totalLarge = sessions.reduce((acc, s) => acc + (s.counts?.large || 0), 0);
      const totalDefective = sessions.reduce((acc, s) => acc + (s.counts?.defective || 0), 0);
      const totalProcessed = sessions.reduce((acc, s) => acc + ((s.counts?.small||0) + (s.counts?.medium||0) + (s.counts?.large||0) + (s.counts?.defective||0)), 0);
      const totalPass = totalProcessed - totalDefective;
      const passRate = totalProcessed ? ((totalPass / totalProcessed) * 100).toFixed(1) : '0.0';
      const rejectRate = totalProcessed ? ((totalDefective / totalProcessed) * 100).toFixed(1) : '0.0';

      doc.text(`Total Batches Analyzed: ${totalBatches}`, margin, y); y += 6;
      doc.text(`Total Mangoes Processed: ${totalProcessed}`, margin, y); y += 6;
      doc.text(`Overall System Yield: ${passRate}% Pass / ${rejectRate}% Reject`, margin, y);

      y += 12;
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(11);
      doc.text('2. BATCH BREAKDOWN', margin, y);

      y += 8;
      const headers = ['Batch Name', 'Start Time', 'End Time', 'Small', 'Medium', 'Large', 'Defective', 'Total', 'Pass Rate'];
      const colW = [38, 28, 28, 18, 18, 18, 20, 18, 24];
      const tableWidth = colW.reduce((a, b) => a + b, 0);
      let x = margin;

      // Header background
      doc.setFillColor(230, 230, 230);
      doc.rect(margin - 2, y - 5, tableWidth + 4, 8, 'F');

      doc.setFontSize(9);
      doc.setFont('helvetica', 'bold');
      doc.setTextColor('#1a1a1a');
      headers.forEach((heading, i) => {
        doc.text(heading, x, y);
        x += colW[i];
      });

      y += 7;
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);

      sessions.forEach((s, idx) => {
        if (y > 270) {
          doc.addPage();
          y = 18;
          // repeat header on new page
          x = margin;
          doc.setFillColor(230, 230, 230);
          doc.rect(margin - 2, y - 5, tableWidth + 4, 8, 'F');
          doc.setFont('helvetica', 'bold');
          headers.forEach((heading, i) => {
            doc.text(heading, x, y);
            x += colW[i];
          });
          y += 7;
          doc.setFont('helvetica', 'normal');
          x = margin;
        }

        // alternating row stripes
        if (idx % 2 === 0) {
          doc.setFillColor(245, 245, 255);
          doc.rect(margin - 2, y - 4.5, tableWidth + 4, 7.5, 'F');
        }

        x = margin;
        const start = s.timestamps?.start_time ? new Date(s.timestamps.start_time).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit' }) : '-';
        const end = s.timestamps?.end_time ? new Date(s.timestamps.end_time).toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit' }) : '-';
        const small = s.counts?.small || 0;
        const medium = s.counts?.medium || 0;
        const large = s.counts?.large || 0;
        const defective = s.counts?.defective || 0;
        const total = small + medium + large + defective;
        const passRateLine = total ? `${(((total - defective) / total) * 100).toFixed(1)}%` : '0.0%';

        const row = [s.session_name || `Batch ${idx + 1}`, start, end, small, medium, large, defective, total, passRateLine];
        row.forEach((cell, i) => {
          doc.text(String(cell), x, y);
          x += colW[i];
        });

        y += 7;
      });

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
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/sessions/${id}`;
      const res = await fetch(apiUrl, {
        method: 'PUT',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
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
      // fast UI response
      const initialCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
      setSortingStats(initialCounts);
      countsRef.current = initialCounts;
      setSessionActive(true);
      setSessionPaused(false);
      setHardwareAlert('Starting new batch (offline-safe)...');

      const clearUrl = `${BACKEND_URL}/api/clear-sensor-data`;
      await fetch(clearUrl, { method: 'POST' }).catch(err => console.error('Failed to clear sensor data:', err));

      const payload = {
        session_name: `Batch ${sessions.length + 1}`,
        counts: initialCounts,
        timestamps: { start_time: new Date() }
      };
      const apiUrl = `${BACKEND_URL}/api/sessions`;

      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        const created = await res.json();
        const newId = created._id || created.data?._id || (created.offline && created.data?._id) || null;
        setCurrentSessionId(newId);
        setHardwareAlert('New batch started');
      } else {
        const errorText = await res.text();
        console.error('Failed to start session', errorText);
        setHardwareAlert(`Started offline, sync pending${errorText ? ': ' + errorText : ''}`);
      }

      // Start the sorting process by launching the hardware controller program
      const startResult = await startHardware();
      if (!startResult.success) {
        console.error('Hardware start failed:', startResult);
        setHardwareAlert(`Hardware start failed: ${startResult.message}`);
      }

      await controlConveyor('start');
      fetchSessions();
    } catch (err) {
      console.error('Error starting session', err);
      setHardwareAlert(`Error starting batch: ${err.message}`);
      setSessionActive(false);
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

    // Continue the sorting process
    try {
      await fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'continue' })
      });
    } catch (err) {
      console.error('Error continuing sorting process:', err);
    }

    await controlConveyor('continue');
  };

  const endBatch = async () => {
    try {
      if (!currentSessionId) {
        setHardwareAlert('No batch to stop');
        return;
      }
      const apiUrl = `${BACKEND_URL}/api/sessions/${currentSessionId}`;
      const res = await fetch(apiUrl, {
        method: 'PUT',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
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
        // Stop the hardware controller program
        try {
          const stopResult = await stopHardware();
          if (!stopResult.success) {
            console.error('Hardware stop failed:', stopResult);
            setHardwareAlert(`Hardware stop failed: ${stopResult.message}`);
          }
        } catch (err) {
          console.error('Error stopping sorting process:', err);
        }
        
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
      const apiUrl = `${window.location.protocol}//${window.location.hostname}:5001/api/sessions/${currentSessionId}`;
      const res = await fetch(apiUrl, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
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
        // Pause the sorting process
        try {
          await fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/control`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'pause' })
          });
        } catch (err) {
          console.error('Error pausing sorting process:', err);
        }

        await controlConveyor('pause');
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

      <div ref={overlayRef} className={`menu-overlay ${menuOpen ? 'open' : ''}`}>
          <div className="menu-inner">
            <div className="user-avatar">
              {user && user.username ? user.username.substring(0, 2).toUpperCase() : 'U'}
            </div>
            <div className="user-name-sidebar">
              {user && user.username ? user.username : 'User'}
            </div>
            <nav className="menu-items" aria-label="Main navigation">
              <button type="button" onClick={() => { setCurrentView('dashboard'); setCameraReloadKey(prev => prev + 1); setCameraError(null); setMenuOpen(false); }} className={`menu-item ${currentView === 'dashboard' ? 'active' : ''}`}>Dashboard</button>
              <button type="button" onClick={() => { setCurrentView('batch-history'); setMenuOpen(false); }} className={`menu-item ${currentView === 'batch-history' ? 'active' : ''}`}>Batch History</button>
              <button type="button" onClick={() => { setCurrentView('hardware-status'); setMenuOpen(false); }} className={`menu-item ${currentView === 'hardware-status' ? 'active' : ''}`}>Hardware Status</button>
              <button type="button" onClick={() => { setCurrentView('hardware-controls'); setMenuOpen(false); }} className={`menu-item ${currentView === 'hardware-controls' ? 'active' : ''}`}>Hardware Controls</button>
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
          <h1>MangoSort</h1>
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
                <img
                  key={cameraReloadKey}
                  src={`${window.location.protocol}//${window.location.hostname}:8081/mjpeg?t=${cameraReloadKey}`}
                  alt="MJPEG camera stream"
                  style={{ width: '100%', minHeight: '240px', objectFit: 'cover', borderRadius: '12px' }}
                  onError={() => setCameraError('MJPEG stream unavailable. Is cam_stream.py running?')}
                />
              </div>
              {cameraError && (
                <div style={{ marginTop: '8px', color: '#c62828', fontSize: '13px', fontWeight: 600 }}>
                  {cameraError}
                </div>
              )}
              <p style={{ marginTop: '6px', color: '#444', fontSize: '12px' }}>
                Mode: MJPEG
              </p>
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
        ) : currentView === 'hardware-controls' ? (
          <>
        {/* Hardware Controls View - Manual Gate Control */}
        <div className="hardware-controls-container">
          <h2 style={{ margin: '0 0 24px 0', fontSize: '20px', fontWeight: '600', color: '#333' }}>Hardware Controls</h2>
          <p style={{ color: '#666', marginBottom: '20px' }}>Manually operate sorting gates for testing and calibration.</p>
          
          <div className="control-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
            
            {/* Small Gate */}
            <div className="welcome-card" style={{ padding: '16px', borderLeft: `4px solid ${gateStates.small === 'open' ? '#4caf50' : '#f44336'}` }}>
              <h3 style={{ marginTop: 0 }}>Small Mango Gate</h3>
              <p style={{ margin: '8px 0', fontSize: '14px', color: '#666' }}>Status: <span style={{ fontWeight: 'bold', color: gateStates.small === 'open' ? '#4caf50' : '#f44336' }}>{gateStates.small === 'open' ? '🔓 OPEN' : '🔒 CLOSED'}</span></p>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  className="control-button start-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'SMALL', action: 'open' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.small === 'open'}
                >
                  Open
                </button>
                <button
                  className="control-button stop-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'SMALL', action: 'close' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.small === 'closed'}
                >
                  Close
                </button>
              </div>
            </div>

            {/* Medium Gate */}
            <div className="welcome-card" style={{ padding: '16px', borderLeft: `4px solid ${gateStates.medium === 'open' ? '#4caf50' : '#f44336'}` }}>
              <h3 style={{ marginTop: 0 }}>Medium Mango Gate</h3>
              <p style={{ margin: '8px 0', fontSize: '14px', color: '#666' }}>Status: <span style={{ fontWeight: 'bold', color: gateStates.medium === 'open' ? '#4caf50' : '#f44336' }}>{gateStates.medium === 'open' ? '🔓 OPEN' : '🔒 CLOSED'}</span></p>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  className="control-button start-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'MEDIUM', action: 'open' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.medium === 'open'}
                >
                  Open
                </button>
                <button
                  className="control-button stop-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'MEDIUM', action: 'close' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.medium === 'closed'}
                >
                  Close
                </button>
              </div>
            </div>

            {/* Large Gate */}
            <div className="welcome-card" style={{ padding: '16px', borderLeft: `4px solid ${gateStates.large === 'open' ? '#4caf50' : '#f44336'}` }}>
              <h3 style={{ marginTop: 0 }}>Large Mango Gate</h3>
              <p style={{ margin: '8px 0', fontSize: '14px', color: '#666' }}>Status: <span style={{ fontWeight: 'bold', color: gateStates.large === 'open' ? '#4caf50' : '#f44336' }}>{gateStates.large === 'open' ? '🔓 OPEN' : '🔒 CLOSED'}</span></p>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  className="control-button start-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'LARGE', action: 'open' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.large === 'open'}
                >
                  Open
                </button>
                <button
                  className="control-button stop-button"
                  onClick={() => {
                    fetch(`${window.location.protocol}//${window.location.hostname}:5001/api/hardware/gate`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ gate: 'LARGE', action: 'close' })
                    }).catch(err => console.error('Error:', err));
                  }}
                  style={{ flex: 1, padding: '10px' }}
                  disabled={gateStates.large === 'closed'}
                >
                  Close
                </button>
              </div>
            </div>
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