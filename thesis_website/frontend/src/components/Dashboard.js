import React, { useState, useEffect, useRef } from 'react';
import { jsPDF } from 'jspdf';
import './Dashboard.css';

function Dashboard({ user, token, onLogout }) {
  // Main view state
  const [currentView, setCurrentView] = useState('dashboard');
  
  // Camera string, connection, and error states to prevent compiler crashes
  const [cameraStatus, setCameraStatus] = useState('Disconnected');
  const [cameraError, setCameraError] = useState('');
  const [webrtcReady, setWebrtcReady] = useState(false);

  // Sensor detection states
  const [sensorStates, setSensorStates] = useState({
    small: { status: 'Inactive', detecting: false },
    medium: { status: 'Inactive', detecting: false },
    large: { status: 'Inactive', detecting: false }
  });

  // Last detected mango size
  const [detectedSize, setDetectedSize] = useState('NONE');
  const [sortingStats, setSortingStats] = useState({
    small: 0,
    medium: 0,
    large: 0,
    total: 0,
    defective: 0
  });

  // Defective flag
  const [isDefective, setIsDefective] = useState(false);
  const [sortingHistory, setSortingHistory] = useState([]);

  // Session and batch management
  const [sessionActive, setSessionActive] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingName, setEditingName] = useState('');

  // Admin: view any user's batches via a user-picker dropdown
  const [adminUsers, setAdminUsers] = useState([]);
  const [selectedAdminUserId, setSelectedAdminUserId] = useState('');
  const [adminUserSessions, setAdminUserSessions] = useState([]);

  const videoRef = useRef(null);
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
  const [showTwoMangoesPopup, setShowTwoMangoesPopup] = useState(false);
  const [showRejectionPopup, setShowRejectionPopup] = useState(false);
  const [rejectionMessage, setRejectionMessage] = useState({ title: '', body: '' });
  
  // Gate state tracking
  const [gateStates, setGateStates] = useState({
    small: 'closed',
    medium: 'closed',
    large: 'closed'
  });

  // Fixed targeting port allocation mapping (targeting backend-express proxy route)
  const BACKEND_URL = `${window.location.protocol}//${window.location.hostname}:5001`;

  const tutorialSteps = [
    { title: 'Start a New Batch', body: 'Ready to begin? Click the Start New Batch button located under the System Controls panel to kick off the sorting process.' },
    { title: 'Stop the Batch', body: 'Whenever you need to finish or halt the current run, simply click the Stop button, also found in the System Controls.' },
    { title: 'Current Batch Statistics', body: 'Keep an eye on your numbers here! This section shows your live data, including total mangoes processed, size breakdowns, and the count of defective mangoes.' },
    { title: 'Open the Menu Bar', body: 'To access more system options, click the three horizontal lines (the hamburger icon) in the upper left corner.' },
    { title: 'Batch History & Renaming', body: 'Inside the menu bar, open the Batch History tab to review past runs and rename them for better organization.' },
    { title: 'Hardware Status', body: 'Ensure your hardware is running smoothly. Click the Hardware Status tab in the menu bar to verify that every single sensor is online.' }
  ];

  const openTutorial = () => setShowTutorial(true);
  const overlayRef = useRef(null);
  const menuToggleRef = useRef(null);
  const [pwCurrent, setPwCurrent] = useState('');
  const [pwNew, setPwNew] = useState('');
  const [pwConfirm, setPwConfirm] = useState('');
  const [passwordMessage, setPasswordMessage] = useState('');

  // Track counts with ref
  const countsRef = useRef({ small: 0, medium: 0, large: 0, defective: 0, total: 0 });
  const autoSaveIntervalRef = useRef(null);

  // Auto-save counts to database every 30 seconds during active batch
  const startAutoSave = (sessionId) => {
    if (autoSaveIntervalRef.current) clearInterval(autoSaveIntervalRef.current);
    
    autoSaveIntervalRef.current = setInterval(async () => {
      if (!sessionId || !sessionActive) return;
      
      try {
        const currentCounts = countsRef.current;
        console.log('💾 Auto-saving batch counts:', currentCounts);
        
        const res = await fetch(`${BACKEND_URL}/api/sessions/${sessionId}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({
            counts: {
              small: currentCounts.small,
              medium: currentCounts.medium,
              large: currentCounts.large,
              defective: currentCounts.defective
            },
            quality_stats: {
              non_defective: currentCounts.small + currentCounts.medium + currentCounts.large,
              defective: currentCounts.defective,
              total: currentCounts.total
            }
          })
        });
        
        if (!res.ok) console.warn('Auto-save failed with status:', res.status);
      } catch (err) {
        console.error('Auto-save error:', err);
      }
    }, 30000);
  };

  const stopAutoSave = () => {
    if (autoSaveIntervalRef.current) {
      clearInterval(autoSaveIntervalRef.current);
      autoSaveIntervalRef.current = null;
    }
  };

  const controlConveyor = async (action) => {
    try {
      let endpoint = '/api/hardware/conveyor';
      if (action === 'pause') endpoint = '/api/hardware/pause';
      else if (action === 'continue') endpoint = '/api/hardware/continue';

      const apiUrl = `${BACKEND_URL}${endpoint}`;
      const payload = action === 'pause' || action === 'continue' ? {} : { action };
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      return await res.json();
    } catch (err) {
      console.error('Conveyor control error:', err);
      return { success: false, message: err.message };
    }
  };

  const startHardware = async () => {
    try {
      const apiUrl = `${BACKEND_URL}/api/hardware/start`;
      const res = await fetch(apiUrl, { method: 'POST' });
      return await res.json();
    } catch (err) {
      console.error('Hardware start error:', err);
      return { success: false, message: err.message };
    }
  };

  const stopHardware = async () => {
    try {
      const apiUrl = `${BACKEND_URL}/api/hardware/stop`;
      const res = await fetch(apiUrl, { method: 'POST' });
      return await res.json();
    } catch (err) {
      console.error('Hardware stop error:', err);
      return { success: false, message: err.message };
    }
  };

  // OPTIMIZED: Implemented frontend ICE state auto-reconnect fallback loop to prevent feed freezes
  const initWebRTCStream = async () => {
    if (!videoRef.current) return;

    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }

    try {
      setCameraStatus('Initializing WebRTC connection...');
      setCameraError('');
      
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });
      pcRef.current = pc;

      pc.ontrack = (event) => {
        if (videoRef.current) {
          videoRef.current.srcObject = event.streams[0];
        }
      };

      pc.oniceconnectionstatechange = () => {
        const state = pc.iceConnectionState;
        console.log("WebRTC Connection State Change:", state);
        
        if (state === 'connected' || state === 'completed') {
          setCameraStatus('WebRTC stream connected');
          setWebrtcReady(true);
        } else if (state === 'failed' || state === 'disconnected') {
          setCameraStatus(`Connection ${state}. Reconnecting...`);
          setWebrtcReady(false);
          
          // Trigger hot reload loop recovery
          setTimeout(() => {
            setCameraReloadKey(prev => prev + 1);
          }, 2000);
        }
      };

      // Handle stream track negotiations
      pc.addTransceiver('video', { direction: 'recvonly' });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const response = await fetch(`${BACKEND_URL}/api/webrtc-offer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`WebRTC offer failed (${response.status}): ${text}`);
      }

      const answer = await response.json();
      await pc.setRemoteDescription(new RTCSessionDescription(answer));
      setCameraStatus('WebRTC stream is live');
      setWebrtcReady(true);
    } catch (err) {
      console.error('WebRTC init error:', err);
      setCameraError(err.message || 'Unable to start WebRTC stream');
      setCameraStatus('WebRTC stream failed');
      setWebrtcReady(false);
    }
  };

  useEffect(() => {
    if (currentView === 'dashboard') {
      initWebRTCStream();
    }
    return () => {
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
      }
    };
  }, [currentView, cameraReloadKey]);

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

  const handleSensorData = (data) => {
    try {
      setSensorStates(prev => ({
        small: { ...prev.small, detecting: data.small },
        medium: { ...prev.medium, detecting: data.medium },
        large: { ...prev.large, detecting: data.large }
      }));

      // Display-only indicators (defective banner / detected-size label). These
      // reflect the live camera decision and may stay on for several polls —
      // they do NOT drive the counts.
      if (data.defective) {
        setIsDefective(true);
        setIsDefectiveFlag(true);
        setDetectedSize('DEFECTIVE');
      } else {
        setIsDefective(false);
        setIsDefectiveFlag(false);
        if (typeof data.detectedSize === 'string') {
          const mapped = data.detectedSize.trim().toUpperCase();
          setDetectedSize(['SMALL', 'MEDIUM', 'LARGE'].includes(mapped) ? mapped : 'NONE');
        } else {
          setDetectedSize('NONE');
        }
      }

      if (!sessionActive || sessionPaused) return;

      // Counts: mirror servotest's authoritative counters. servotest increments
      // each count exactly once per mango (gated by the IR trigger breakbeam),
      // so the UI just displays its numbers. Previously the UI added +1 on every
      // 500 ms poll while a flag stayed true, which made counts climb continuously.
      const c = data.counts;
      if (c && typeof c === 'object' && Object.keys(c).length > 0) {
        const prev = countsRef.current;
        const updated = {
          small: c.small ?? prev.small,
          medium: c.medium ?? prev.medium,
          large: c.large ?? prev.large,
          defective: c.defective ?? prev.defective,
          total: c.total ?? prev.total
        };
        const changed =
          updated.small !== prev.small ||
          updated.medium !== prev.medium ||
          updated.large !== prev.large ||
          updated.defective !== prev.defective ||
          updated.total !== prev.total;
        if (changed) {
          // Append history once per newly sorted size mango.
          let addedSize = null;
          if (updated.small > prev.small) addedSize = 'SMALL';
          else if (updated.medium > prev.medium) addedSize = 'MEDIUM';
          else if (updated.large > prev.large) addedSize = 'LARGE';
          if (addedSize) {
            const timestamp = new Date().toLocaleTimeString();
            setSortingHistory(h => [...h, { timestamp, size: addedSize }].slice(-100));
          }
          countsRef.current = updated;
          setSortingStats(updated);
          checkLimits(updated);
        }
      }
    } catch (error) {
      console.error('Error processing sensor data:', error);
    }
  };

  // Poll sensor telemetry entries from hardware express routing layer
  useEffect(() => {
    setSensorStates(prev => ({
      small: { ...prev.small, status: 'Active' },
      medium: { ...prev.medium, status: 'Active' },
      large: { ...prev.large, status: 'Active' }
    }));

    let mounted = true;
    const pollSensorData = async () => {
      try {
        const apiUrl = `${BACKEND_URL}/api/hardware/sensors`;
        const res = await fetch(apiUrl);
        if (!res.ok) return;
        const data = await res.json();
        
        if (!data || Object.keys(data).length === 0) return;

        const normalized = {
          small: !!data.trigger,
          medium: !!data.medium,
          large: !!data.large,
          defective: !!data.defective,
          detectedSize: data.detectedSize,
          counts: data.counts || {}
        };

        if (mounted) {
          handleSensorData(normalized);

          // Task 2: two mangoes — auto show/hide as hardware resolves it
          setShowTwoMangoesPopup(!!data.twoMangoes);

          // Tasks 3, 7, 8: rejection pop-ups driven by hardware alert state
          const REJECTION_MESSAGES = {
            TWO_MANGOES: {
              title: '⚠️ Multiple Mangoes Detected',
              body: 'More than one mango detected on the conveyor. Reversing belt to entrance. Please remove the extra mango.'
            },
            NO_DETECTION: {
              title: '⚠️ No Mango Detected',
              body: 'No mango was found in the scanning chamber. Reversing belt to entrance. Please check the scanner area.'
            },
            NOT_CARABAO: {
              title: '⚠️ Not a Carabao Mango',
              body: 'A non-Carabao Mango was detected. Reversing belt to entrance. Please remove the rejected mango from the tray.'
            },
            ENTRANCE_DURING_SCAN: {
              title: '⚠️ Object Detected at Entry',
              body: 'An object was detected at the entrance while a scan was ongoing. Conveyor stopped. Please remove the object from the entry. The current mango will be re-scanned.'
            }
          };

          const alert = data.alertMessage || '';
          if (alert && REJECTION_MESSAGES[alert]) {
            setRejectionMessage(REJECTION_MESSAGES[alert]);
            setShowRejectionPopup(true);
          } else {
            setShowRejectionPopup(false);
          }
        }
      } catch (err) {
        console.error('Error polling sensor data:', err);
      }
    };

    const intervalId = setInterval(pollSensorData, 500);
    pollSensorData();

    return () => {
      mounted = false;
      clearInterval(intervalId);
    };
  }, [sessionActive, sessionPaused]);

  // CPU temperature monitor (RPi thermal zone pooling)
  useEffect(() => {
    const fetchTemp = async () => {
      try {
        const apiUrl = `${BACKEND_URL}/api/cpu-temp`;
        const res = await fetch(apiUrl);
        const data = await res.json();
        if (res.ok && data && typeof data.cpuTemp === 'number') {
          setCpuTemp(data.cpuTemp);
          if (data.cpuTemp >= 85) {
            setHardwareStatus('Over Limit (Shutdown)');
            setHardwareAlert('Temperature exceeded limits. Machine turning off. Saving current batch to history.');
            setShowTempPopup(true);
            stopSessionImmediately();
          } else if (data.cpuTemp >= 80) {
            setHardwareStatus('Throttling');
            setHardwareAlert('Temperature is nearly exceeding limits. Stop operations immediately to prevent damage.');
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
        }
      } catch (err) {
        console.error('Could not read CPU temperature', err);
      }
    };

    fetchTemp();
    const intervalId = setInterval(fetchTemp, 3000);
    return () => clearInterval(intervalId);
  }, [sessionActive, sessionPaused]);

  const fetchSessions = async () => {
    try {
      const apiUrl = `${BACKEND_URL}/api/sessions`;
      const res = await fetch(apiUrl, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setSessions(data);
      }
    } catch (err) {
      console.error('Error fetching sessions', err);
    }
  };

  useEffect(() => {
    fetchSessions();
    if (user) {
      const seen = localStorage.getItem('tutorialSeen');
      if (!seen) setShowTutorial(true);
    }
  }, [user]);

  // Admin: load the list of users for the batch-viewer dropdown
  const fetchAdminUsers = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/admin/users`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setAdminUsers(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error('Error fetching admin users', err);
    }
  };

  // Admin: fetch the batches belonging to the selected user
  const fetchAdminUserSessions = async (targetUserId) => {
    if (!targetUserId) {
      setAdminUserSessions([]);
      return;
    }
    try {
      const res = await fetch(`${BACKEND_URL}/api/sessions?userId=${encodeURIComponent(targetUserId)}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setAdminUserSessions(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error('Error fetching user sessions', err);
    }
  };

  useEffect(() => {
    if (user?.role === 'admin') {
      fetchAdminUsers();
    }
  }, [user]);

  // Poll physical gate servo feedbacks
  useEffect(() => {
    let mounted = true;
    const pollGateStatus = async () => {
      try {
        const apiUrl = `${BACKEND_URL}/api/hardware/gate`;
        const res = await fetch(apiUrl);
        if (res.ok) {
          const data = await res.json();
          if (mounted && data.gate_states) setGateStates(data.gate_states);
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

  // Detection status is now handled inside the main sensor poll above.

  const clearSessions = async () => {
    try {
      const apiUrl = `${BACKEND_URL}/api/sessions`;
      const res = await fetch(apiUrl, {
        method: 'DELETE',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        }
      });
      if (res.ok) {
        setSessions([]);
        const zeroCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
        setSortingStats(zeroCounts);
        countsRef.current = zeroCounts;
        setCurrentSessionId(null);
        await fetchSessions();
      }
    } catch (err) {
      console.error('Error clearing sessions', err);
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

      doc.setFillColor(253, 184, 19);
      doc.rect(0, 0, 297, 10, 'F');
      doc.setFillColor(253, 141, 19);
      doc.rect(0, 10, 297, 8, 'F');
      doc.setFillColor(107, 168, 47);
      doc.rect(0, 18, 297, 8, 'F');

      try {
        const logoDataUrl = await loadImageDataUrl('/login.png');
        doc.addImage(logoDataUrl, 'PNG', 250, 8, 34, 34);
      } catch (imgErr) {
        doc.setFillColor(255, 215, 0);
        doc.circle(268, 19, 8, 'F');
      }

      doc.setTextColor('#011627');
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(16);
      doc.text('AUTOMATED MANGO SORTING SYSTEM (MANGOPAIN)', margin, 15);
      doc.setFontSize(11);
      doc.setFont('helvetica', 'normal');
      doc.text('OFFICIAL QUALITY CONTROL & YIELD REPORT', margin, 22);

      y = 44;
      const username = user?.username || 'vince@email.com';
      const today = new Date();
      doc.text(`Operator: ${username}`, margin, y); y += 6;
      doc.text(`Date of Export: ${today.toLocaleDateString('en-US', { month:'long', day:'numeric', year:'numeric' })}`, margin, y); y += 6;
      doc.text('System Version: AI Vision Model v1.0 (YOLO)', margin, y);

      y += 12;
      doc.setFont('helvetica', 'bold'); doc.setFontSize(11);
      doc.text('1. EXECUTIVE SUMMARY', margin, y); y += 6;
      doc.setFont('helvetica', 'normal'); doc.setFontSize(10);

      const totalBatches = sessions.length;
      const totalSmall = sessions.reduce((acc, s) => acc + (s.counts?.small || 0), 0);
      const totalMedium = sessions.reduce((acc, s) => acc + (s.counts?.medium || 0), 0);
      const totalLarge = sessions.reduce((acc, s) => acc + (s.counts?.large || 0), 0);
      const totalDefective = sessions.reduce((acc, s) => acc + (s.counts?.defective || 0), 0);
      const totalProcessed = sessions.reduce((acc, s) => acc + ((s.counts?.small||0) + (s.counts?.medium||0) + (s.counts?.large||0) + (s.counts?.defective||0)), 0);
      const passRate = totalProcessed ? (((totalProcessed - totalDefective) / totalProcessed) * 100).toFixed(1) : '0.0';
      const rejectRate = totalProcessed ? ((totalDefective / totalProcessed) * 100).toFixed(1) : '0.0';

      doc.text(`Total Batches Analyzed: ${totalBatches}`, margin, y); y += 6;
      doc.text(`Total Mangoes Processed: ${totalProcessed}`, margin, y); y += 6;
      doc.text(`Overall System Yield: ${passRate}% Pass / ${rejectRate}% Reject`, margin, y);

      y += 12;
      doc.setFont('helvetica', 'bold'); doc.setFontSize(11);
      doc.text('2. BATCH BREAKDOWN', margin, y); y += 8;

      const headers = ['Batch Name', 'Start Time', 'End Time', 'Small', 'Medium', 'Large', 'Defective', 'Total', 'Pass Rate'];
      const colW = [38, 28, 28, 18, 18, 18, 20, 18, 24];
      const tableWidth = colW.reduce((a, b) => a + b, 0);
      let x = margin;

      doc.setFillColor(230, 230, 230);
      doc.rect(margin - 2, y - 5, tableWidth + 4, 8, 'F');
      doc.setFontSize(9); doc.setFont('helvetica', 'bold'); doc.setTextColor('#1a1a1a');
      
      headers.forEach((heading, i) => {
        doc.text(heading, x, y);
        x += colW[i];
      });

      y += 7;
      doc.setFont('helvetica', 'normal'); doc.setFontSize(9);

      sessions.forEach((s, idx) => {
        if (y > 270) {
          doc.addPage(); y = 18; x = margin;
          doc.setFillColor(230, 230, 230);
          doc.rect(margin - 2, y - 5, tableWidth + 4, 8, 'F');
          doc.setFont('helvetica', 'bold');
          headers.forEach((heading, i) => {
            doc.text(heading, x, y);
            x += colW[i];
          });
          y += 7; doc.setFont('helvetica', 'normal');
        }

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

      doc.save(`batch_history_export_${new Date().toISOString().replace(/[:.]/g, '-')}.pdf`);
    } catch (err) {
      console.error('PDF export failed:', err);
    }
  };

  const startEditing = (id, currentName) => {
    setEditingSessionId(id);
    setEditingName(currentName || '');
  };

  const cancelEditing = () => {
    setEditingSessionId(null);
    setEditingName('');
  };

  const saveEditing = async (id) => {
    try {
      const apiUrl = `${BACKEND_URL}/api/sessions/${id}`;
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
      }
    } catch (err) {
      console.error('Error updating session name', err);
    }
  };

  const startNewSession = async () => {
    try {
      setHardwareAlert('Starting new batch...');
      const initialCounts = { small: 0, medium: 0, large: 0, total: 0, defective: 0 };
      setSortingStats(initialCounts);
      countsRef.current = initialCounts;

      await fetch(`${BACKEND_URL}/api/clear-sensor-data`, { method: 'POST' });

      const res = await fetch(`${BACKEND_URL}/api/sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          session_name: `Batch ${sessions.length + 1}`,
          counts: initialCounts,
          timestamps: { start_time: new Date() }
        })
      });

      if (res.ok) {
        const created = await res.json();
        const newId = created._id || created.data?._id || null;
        setCurrentSessionId(newId);
        startAutoSave(newId);
      }

      await startHardware();
      await controlConveyor('start');

      setSessionActive(true);
      setSessionPaused(false);
      setHardwareAlert('New batch started');
      fetchSessions();
    } catch (err) {
      console.error('Error starting session', err);
      stopAutoSave();
    }
  };

  const continueBatch = async () => {
    if (!currentSessionId) return;
    setSessionActive(true);
    setSessionPaused(false);
    setHardwareAlert('Continuing batch');

    await fetch(`${BACKEND_URL}/api/hardware/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'continue' })
    });
    await controlConveyor('continue');
  };

  const endBatch = async () => {
    const saveBatchWithRetry = async (sessionId, data, maxRetries = 3) => {
      for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
          const res = await fetch(`${BACKEND_URL}/api/sessions/${sessionId}`, {
            method: 'PUT',
            headers: { 
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(data)
          });
          if (res.ok) return { success: true };
          if (attempt < maxRetries) await new Promise(r => setTimeout(r, 1000));
        } catch (err) {
          if (attempt === maxRetries) return { success: false, message: err.message };
        }
      }
      return { success: false, message: 'Max retries exceeded' };
    };

    try {
      if (!currentSessionId) return;
      setHardwareAlert('Stopping batch and saving data...');

      await fetch(`${BACKEND_URL}/api/hardware/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' })
      });
      await controlConveyor('stop');

      const batchData = {
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
      };

      const saveResult = await saveBatchWithRetry(currentSessionId, batchData);
      if (saveResult.success) {
        await stopHardware();
        setHardwareAlert('✅ Batch stopped and saved');
      }

      stopAutoSave();
      setSessionActive(false);
      setSessionPaused(false);
      setCurrentSessionId(null);
      setTimeout(() => { fetchSessions(); }, 500);
    } catch (err) {
      console.error('Error ending batch', err);
    }
  };

  const pauseBatch = async () => {
    try {
      if (!currentSessionId) return;
      await fetch(`${BACKEND_URL}/api/hardware/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'pause' })
      });
      await controlConveyor('pause');

      await fetch(`${BACKEND_URL}/api/sessions/${currentSessionId}`, {
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
          }
        })
      });

      setSessionActive(false);
      setSessionPaused(true);
      setHardwareAlert('Batch paused');
      setTimeout(() => { fetchSessions(); }, 500);
    } catch (err) {
      console.error('Error pausing session', err);
    }
  };

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
      {showTwoMangoesPopup && (
        <div className="tutorial-overlay">
          <div className="tutorial-box">
            <h3 style={{ color: 'orange' }}>⚠️ More than one mango detected</h3>
            <p>Multiple mangoes were detected on the conveyor. The belt is reversing to the entrance. Please remove the extra mango.</p>
            <p style={{ fontSize: '0.82em', color: '#888', marginTop: 8 }}>This window will close automatically once the belt is clear.</p>
          </div>
        </div>
      )}
      {showRejectionPopup && (
        <div className="tutorial-overlay">
          <div className="tutorial-box">
            <div className="tutorial-close" onClick={() => setShowRejectionPopup(false)}>✕</div>
            <h3 style={{ color: 'orange' }}>{rejectionMessage.title}</h3>
            <p>{rejectionMessage.body}</p>
            <p style={{ fontSize: '0.82em', color: '#888', marginTop: 8 }}>This window will close automatically once the belt is clear.</p>
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
            <button type="button" onClick={() => { setCurrentView('dashboard'); setCameraReloadKey(prev => prev + 1); setMenuOpen(false); }} className={`menu-item ${currentView === 'dashboard' ? 'active' : ''}`}>Dashboard</button>
            <button type="button" onClick={() => { setCurrentView('batch-history'); setMenuOpen(false); }} className={`menu-item ${currentView === 'batch-history' ? 'active' : ''}`}>Batch History</button>
            {user?.role === 'admin' && (
              <button type="button" onClick={() => { setCurrentView('admin-user-batches'); fetchAdminUsers(); setMenuOpen(false); }} className={`menu-item ${currentView === 'admin-user-batches' ? 'active' : ''}`}>User Batches</button>
            )}
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
          <h1>MangoPain</h1>
        </div>
      </header>

      <div className="parent">
        {currentView === 'dashboard' ? (
          <div className="dashboard-container">
            <div className="dashboard-left">
              <div className="welcome-card camera-feed-section">
                <div className="camera-controls">
                  <h2>Live Camera Feed</h2>
                </div>
                <div className="camera-container">
                  <video
                    ref={videoRef}
                    autoPlay
                    playsInline
                    muted
                    style={{ 
                      width: '100%', 
                      maxWidth: '480px', 
                      aspectRatio: '4 / 3', 
                      objectFit: 'cover', 
                      borderRadius: '12px', 
                      backgroundColor: '#000' 
                    }}
                  />
                </div>
                <div style={{ marginTop: '6px', color: '#444', fontSize: '12px' }}>
                  <div>Mode: WebRTC (480x360 @ 15fps)</div>
                  <div>Status: {cameraStatus}</div>
                  {cameraError && <div style={{ color: 'red' }}>{cameraError}</div>}
                </div>
              </div>

              <div className="system-controls">
                <h3>System Controls</h3>
                {isDefectiveFlag && <div style={{ color: 'red', fontWeight: 700, marginBottom: '8px' }}>DEFECTIVE</div>}
                <div className="system-control-actions">
                  <button className="control-button start-button" onClick={startNewSession} disabled={sessionActive || sessionPaused} style={{ fontSize: '15px', padding: '14px 16px' }}>Start New Batch</button>
                  {sessionActive && <button className="control-button stop-button" onClick={pauseBatch} style={{ fontSize: '15px', padding: '14px 16px' }}>Pause Batch</button>}
                  {sessionPaused && <button className="control-button start-button" onClick={continueBatch} style={{ fontSize: '15px', padding: '14px 16px' }}>Continue Batch</button>}
                  {(sessionActive || sessionPaused) && <button className="control-button stop-button" onClick={endBatch} style={{ fontSize: '15px', padding: '14px 16px' }}>Stop Batch</button>}
                </div>
              </div>
            </div>

            <div className="dashboard-right">
              <h2 className="stats-title">Current Batch Statistics</h2>
              <div className="stats-grid">
                <div className="stats-card"><h3>SMALL SIZE</h3><p className="stats-count">{sortingStats.small}</p></div>
                <div className="stats-card"><h3>MEDIUM SIZE</h3><p className="stats-count">{sortingStats.medium}</p></div>
                <div className="stats-card"><h3>LARGE SIZE</h3><p className="stats-count">{sortingStats.large}</p></div>
                <div className="stats-card defective-card"><h3>DEFECTIVE</h3><p className="stats-count">{sortingStats.defective}</p></div>
                <div className="stats-card total-card"><h3>TOTAL PROCESSED</h3><p className="stats-count">{sortingStats.total}</p></div>
              </div>
            </div>
          </div>
        ) : currentView === 'hardware-status' ? (
          <div className="sensor-status-container">
            <h2 style={{ margin: '0 0 24px 0', fontSize: '20px', fontWeight: '600', color: '#333' }}>Hardware Status</h2>
            <div className="sensor-grid">
              <div className="welcome-card sensor-card small">
                <h3>Small Mango Sensor</h3>
                <p><span className={`serial-monitor ${sensorStates.small.detecting ? 'detecting' : 'not-detecting'}`}>{sensorStates.small.detecting ? 'Detecting' : 'Not Detecting'}</span></p>
              </div>
              <div className="welcome-card sensor-card medium">
                <h3>Medium Mango Sensor</h3>
                <p><span className={`serial-monitor ${sensorStates.medium.detecting ? 'detecting' : 'not-detecting'}`}>{sensorStates.medium.detecting ? 'Detecting' : 'Not Detecting'}</span></p>
              </div>
              <div className="welcome-card sensor-card large">
                <h3>Large Mango Sensor</h3>
                <p><span className={`serial-monitor ${sensorStates.large.detecting ? 'detecting' : 'not-detecting'}`}>{sensorStates.large.detecting ? 'Detecting' : 'Not Detecting'}</span></p>
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
        ) : currentView === 'hardware-controls' ? (
          <div className="hardware-controls-container">
            <h2>Hardware Controls</h2>
            <p style={{ color: '#666', marginBottom: '20px' }}>Manually operate sorting gates for testing and calibration.</p>
            <div className="control-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              {['SMALL', 'MEDIUM', 'LARGE'].map((size) => (
                <div key={size} className="welcome-card" style={{ padding: '16px', borderLeft: `4px solid ${gateStates[size.toLowerCase()] === 'open' ? '#4caf50' : '#f44336'}` }}>
                  <h3 style={{ marginTop: 0 }}>{size} Mango Gate</h3>
                  <p>Status: <span style={{ fontWeight: 'bold', color: gateStates[size.toLowerCase()] === 'open' ? '#4caf50' : '#f44336' }}>{gateStates[size.toLowerCase()] === 'open' ? '🔓 OPEN' : '🔒 CLOSED'}</span></p>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="control-button start-button" disabled={gateStates[size.toLowerCase()] === 'open'} onClick={() => fetch(`${BACKEND_URL}/api/hardware/gate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ gate: size, action: 'open' }) })} style={{ flex: 1, padding: '10px' }}>Open</button>
                    <button className="control-button stop-button" disabled={gateStates[size.toLowerCase()] === 'closed'} onClick={() => fetch(`${BACKEND_URL}/api/hardware/gate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ gate: size, action: 'close' }) })} style={{ flex: 1, padding: '10px' }}>Close</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : currentView === 'change-password' ? (
          <div className="change-password-panel">
            <h2>Change Password</h2>
            <form onSubmit={handleChangePassword} style={{ display: 'grid', gap: '14px' }}>
              <label>Current Password<input type="password" value={pwCurrent} onChange={e => setPwCurrent(e.target.value)} required /></label>
              <label>New Password<input type="password" value={pwNew} onChange={e => setPwNew(e.target.value)} required /></label>
              <label>Confirm New Password<input type="password" value={pwConfirm} onChange={e => setPwConfirm(e.target.value)} required /></label>
              <button type="submit" className="control-button start-button" style={{ width: '220px' }}>Save Password</button>
              {passwordMessage && <p style={{ color: passwordMessage.includes('successfully') ? '#2e7d32' : '#d32f2f', fontWeight: 600 }}>{passwordMessage}</p>}
            </form>
          </div>
        ) : currentView === 'settings' ? (
          <div className="settings-panel">
            <h2>Settings</h2>
            <div className="settings-section">
              <h3>Mango Detection</h3>
              <div className="settings-grid">
                {['Small', 'Medium', 'Large', 'Defective'].map((L) => (
                  <label key={L}>{L} mango limit:
                    <input type="number" min="0" value={settings[`limit${L}`]} onChange={e => setSettings(s => ({ ...s, [`limit${L}`]: Number(e.target.value) }))} />
                  </label>
                ))}
              </div>
            </div>
            <div className="settings-actions">
              <button type="button" className="control-button start-button" onClick={() => setPasswordMessage('Settings saved')}>Save Settings</button>
              <button type="button" className="control-button stop-button" onClick={() => { setSettings(defaultSettings); setPasswordMessage('Settings reset'); }}>Reset to Defaults</button>
            </div>
          </div>
        ) : currentView === 'admin-user-batches' ? (
          <div className="batch-history-container">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h2>User Batches (Admin)</h2>
              <select
                value={selectedAdminUserId}
                onChange={e => { const id = e.target.value; setSelectedAdminUserId(id); fetchAdminUserSessions(id); }}
                style={{ padding: '10px 12px', borderRadius: '6px', minWidth: '220px', fontWeight: 600 }}
              >
                <option value="">Select a user…</option>
                {adminUsers.map(u => (
                  <option key={u.userId} value={u.userId}>{u.username}</option>
                ))}
              </select>
            </div>
            {!selectedAdminUserId ? (
              <p style={{ color: '#666' }}>Select a user from the dropdown to view their batches.</p>
            ) : (
              <div className="history-table-container">
                <table className="history-table">
                  <thead>
                    <tr>
                      <th>Batch</th><th>Small</th><th>Medium</th><th>Large</th><th>Defective</th><th>Total</th><th>Start Time</th><th>End Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {adminUserSessions && adminUserSessions.length > 0 ? adminUserSessions.map((s, idx) => (
                      <tr key={s._id || idx}>
                        <td>{s.session_name || `Batch ${idx + 1}`}</td>
                        <td>{s.counts?.small ?? 0}</td>
                        <td>{s.counts?.medium ?? 0}</td>
                        <td>{s.counts?.large ?? 0}</td>
                        <td>{s.counts?.defective ?? 0}</td>
                        <td>{(s.counts?.small||0) + (s.counts?.medium||0) + (s.counts?.large||0) + (s.counts?.defective||0)}</td>
                        <td>{s.timestamps?.start_time ? new Date(s.timestamps.start_time).toLocaleString() : '-'}</td>
                        <td>{s.timestamps?.end_time ? new Date(s.timestamps.end_time).toLocaleString() : '-'}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan={8} style={{ textAlign: 'center', color: '#666' }}>No batches for this user.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="batch-history-container">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h2>Sorting History (Sessions)</h2>
              <div>
                <button onClick={exportSortingHistory} style={{ padding: '10px 20px', backgroundColor: '#1976d2', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600', marginRight: '8px' }}>Export</button>
                {user?.role === 'admin' && (
                  <button onClick={clearSessions} style={{ padding: '10px 20px', backgroundColor: '#f44336', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: '600' }}>Clear</button>
                )}
              </div>
            </div>
            <div className="history-table-container">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Batch</th><th>Small</th><th>Medium</th><th>Large</th><th>Defective</th><th>Total</th><th>Start Time</th><th>End Time</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions && sessions.map((s, idx) => (
                    <tr key={s._id || idx}>
                      {editingSessionId === s._id ? (
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <input value={editingName} onChange={e => setEditingName(e.target.value)} style={{ width: 200, padding: '6px 8px' }} />
                            <button onClick={() => saveEditing(s._id)}>Save</button>
                            <button onClick={cancelEditing}>Cancel</button>
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
        )}
      </div>
    </div>
  );
}

export default Dashboard;