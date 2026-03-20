const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
require('dotenv').config();

const app = express();

// Offline-first constants
const LOCAL_DATA_DIR = process.env.LOCAL_DATA_DIR || path.join(__dirname, '..', 'local_data');
const USER_SHADOW_FILE = path.join(LOCAL_DATA_DIR, 'users_shadow.json');
const UNSYNCED_BATCH_DIR = path.join(LOCAL_DATA_DIR, 'unsynced_batches');
const MASTER_ADMIN_USERNAME = process.env.MASTER_ADMIN_USERNAME || 'admin';
const MASTER_ADMIN_PASSWORD_HASH = process.env.MASTER_ADMIN_PASSWORD_HASH || '$2b$10$BEKdhn6qQVuheUG6l2fR6.H.1Xgi7evgKiaTgB8Mzu8vFe9fl73Hq'; // strongpassword

function ensureLocalDirs() {
  if (!fs.existsSync(LOCAL_DATA_DIR)) fs.mkdirSync(LOCAL_DATA_DIR, { recursive: true });
  if (!fs.existsSync(UNSYNCED_BATCH_DIR)) fs.mkdirSync(UNSYNCED_BATCH_DIR, { recursive: true });
  if (!fs.existsSync(USER_SHADOW_FILE)) fs.writeFileSync(USER_SHADOW_FILE, JSON.stringify({}), 'utf8');
}

function loadShadowUsers() {
  try {
    const text = fs.readFileSync(USER_SHADOW_FILE, 'utf8');
    return JSON.parse(text || '{}');
  } catch (e) {
    console.error('Failed to load shadow users', e);
    return {};
  }
}

function saveShadowUsers(users) {
  try {
    fs.writeFileSync(USER_SHADOW_FILE, JSON.stringify(users, null, 2), 'utf8');
  } catch (e) {
    console.error('Failed to save shadow users', e);
  }
}

function upsertShadowUser(user) {
  const users = loadShadowUsers();
  users[user.username] = { password: user.password, role: user.role || 'user', updatedAt: new Date().toISOString() };
  saveShadowUsers(users);
}

function ensureMasterAdminUser() {
  const users = loadShadowUsers();
  if (!users[MASTER_ADMIN_USERNAME]) {
    users[MASTER_ADMIN_USERNAME] = {
      password: MASTER_ADMIN_PASSWORD_HASH,
      role: 'admin',
      updatedAt: new Date().toISOString()
    };
    saveShadowUsers(users);
    console.log('Master admin user ensured in offline shadow store');
  }
}

async function checkInternet() {
  try {
    const response = await fetch('https://www.google.com/generate_204', { timeout: 3000 });
    return response.status === 204;
  } catch (err) {
    return false;
  }
}

const offlineQueue = [];
let enqueueFlushHandle = null;

// ===== HARDWARE CONTROL =====
const HARDWARE_CONTROL_FILE = '/tmp/mangosort_control.json';
let hardwareProcess = null;
let hardwareRunning = false;

function writeHardwareControlFile(running) {
  try {
    fs.writeFileSync(HARDWARE_CONTROL_FILE, JSON.stringify({ running }), 'utf8');
    hardwareRunning = running;
  } catch (err) {
    console.error('Failed to write hardware control file:', err);
  }
}

function startHardwareProcess() {
  if (hardwareProcess) {
    console.log('Hardware process already running');
    return { success: true, message: 'Hardware already running' };
  }

  try {
    console.log('Starting hardware controller process...');
    hardwareProcess = spawn('python3', [path.join(__dirname, '..', 'hardware_controller.py')], {
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    hardwareProcess.stdout.on('data', (data) => {
      console.log(`[Hardware] ${data.toString().trim()}`);
    });

    hardwareProcess.stderr.on('data', (data) => {
      console.error(`[Hardware ERROR] ${data.toString().trim()}`);
    });

    hardwareProcess.on('close', (code) => {
      console.log(`Hardware process exited with code ${code}`);
      hardwareProcess = null;
      hardwareRunning = false;
    });

    writeHardwareControlFile(true);
    return { success: true, message: 'Hardware process started' };
  } catch (err) {
    console.error('Failed to start hardware process:', err);
    return { success: false, message: err.message };
  }
}

function stopHardwareProcess() {
  if (!hardwareProcess) {
    console.log('No hardware process running');
    return { success: false, message: 'No hardware process to stop' };
  }

  try {
    console.log('Stopping hardware controller...');
    writeHardwareControlFile(false);
    
    if (hardwareProcess) {
      hardwareProcess.kill('SIGTERM');
      hardwareProcess = null;
    }
    
    return { success: true, message: 'Hardware process stopped' };
  } catch (err) {
    console.error('Failed to stop hardware process:', err);
    hardwareProcess = null;
    return { success: false, message: err.message };
  }
}

function flushOfflineQueue() {
  if (offlineQueue.length === 0) return;
  const queued = offlineQueue.splice(0, offlineQueue.length);

  for (const batch of queued) {
    try {
      const name = `batch_${Date.now()}_${Math.random().toString(36).slice(2, 10)}.json`;
      const filePath = path.join(UNSYNCED_BATCH_DIR, name);
      fs.writeFileSync(filePath, JSON.stringify(batch, null, 2), 'utf8');
    } catch (err) {
      console.error('Error writing offline batch file', err);
      // if failure, push back for retry
      offlineQueue.unshift(batch);
    }
  }
}

function scheduleOfflineFlush() {
  if (enqueueFlushHandle) return;
  enqueueFlushHandle = setTimeout(() => {
    enqueueFlushHandle = null;
    flushOfflineQueue();
  }, 150);
}

async function enqueueBatch(batch) {
  const localId = batch._id || `offline-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const sessionToSave = { ...batch, _id: localId };
  offlineQueue.push(sessionToSave);
  scheduleOfflineFlush();
  return sessionToSave;
}

async function syncUnsyncedBatches() {
  if (!await checkInternet()) return;

  const files = fs.existsSync(UNSYNCED_BATCH_DIR)
    ? fs.readdirSync(UNSYNCED_BATCH_DIR).filter(f => f.endsWith('.json'))
    : [];

  if (!files.length) return;

  for (const file of files) {
    const filePath = path.join(UNSYNCED_BATCH_DIR, file);
    try {
      const raw = fs.readFileSync(filePath, 'utf8');
      const batch = JSON.parse(raw);
      const session = new Session(batch);
      await session.save();
      fs.unlinkSync(filePath);
      console.log(`Synced offline batch ${file}`);
    } catch (err) {
      console.error('Failed to sync batch', file, err);
    }
  }
}

ensureLocalDirs();
ensureMasterAdminUser();
setInterval(syncUnsyncedBatches, 5_000);

// Middleware
app.use(cors());
app.use(express.json());

// MongoDB Connection
const uri = "mongodb+srv://admin:123@emtech.tlubq5q.mongodb.net/?appName=EMTECH";
mongoose.connect(uri, {
  useNewUrlParser: true,
  useUnifiedTopology: true,
})
.then(() => console.log('✅ MongoDB connected successfully'))
.catch(err => console.error('❌ MongoDB connection error:', err));

// User Schema
const userSchema = new mongoose.Schema({
  username: {
    type: String,
    required: true,
    unique: true
  },
  password: {
    type: String,
    required: true
  },
  role: {
    type: String,
    default: 'user'
  },
  createdAt: {
    type: Date,
    default: Date.now
  }
});

const User = mongoose.model('User', userSchema);

// Session Schema for Sorting Data
const SessionSchema = new mongoose.Schema({
  session_name: String,
  counts: {
    small: { type: Number, default: 0 },
    medium: { type: Number, default: 0 },
    large: { type: Number, default: 0 },
    defective: { type: Number, default: 0 }
  },
  quality_stats: {
    defective: { type: Number, default: 0 },
    non_defective: { type: Number, default: 0 },
    total: { type: Number, default: 0 }
  },
  timestamps: {
    start_time: { type: Date, default: Date.now },
    end_time: Date
  }
});

const Session = mongoose.model('Session', SessionSchema);

// Auth Routes
app.post('/api/auth/signup', async (req, res) => {
  try {
    const { username, password, role } = req.body;

    // Check if user exists
    const existingUser = await User.findOne({ username });
    if (existingUser) {
      return res.status(400).json({ message: 'Username already exists' });
    }

    // Hash password
    const hashedPassword = await bcrypt.hash(password, 10);

    // Create new user
    const user = new User({
      username,
      password: hashedPassword,
      role: role || 'user'
    });

    await user.save();

    // Mirror user in offline shadow db
    upsertShadowUser({ username, password: hashedPassword, role: role || 'user' });

    // Generate token
    const token = jwt.sign({ userId: user._id }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' });

    res.status(201).json({ 
      message: 'User created successfully', 
      token,
      user: { id: user._id, username: user.username, role: user.role }
    });
  } catch (err) {
    // create local-only user in offline mode
    console.error('Signup cloud failed, saving locally:', err.message);
    const offlineUser = {
      username,
      password: await bcrypt.hash(password, 10),
      role: role || 'user'
    };
    upsertShadowUser(offlineUser);
    const token = jwt.sign({ userId: `offline-${username}` }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' });
    res.status(201).json({ 
      message: 'User created locally (offline mode)', 
      token,
      user: { id: null, username: offlineUser.username, role: offlineUser.role }
    });
  }
});

app.post('/api/auth/login', async (req, res) => {
  const { username, password } = req.body || {};

  console.log('Login attempt with:', { username, password: password ? '***' : 'empty' });

  // Check if username and password are provided
  if (!username || !password) {
    console.log('Missing credentials');
    return res.status(400).json({ message: 'Username and password are required' });
  }

  const checkShadowOrMaster = async () => {
    const shadowUsers = loadShadowUsers();
    const shadow = shadowUsers[username];
    if (shadow && await bcrypt.compare(password, shadow.password)) {
      return {
        message: 'Login successful (offline shadow user)',
        token: jwt.sign({ userId: `offline-${username}` }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' }),
        user: { id: null, username, role: shadow.role || 'user' }
      };
    }

    if (username === MASTER_ADMIN_USERNAME && await bcrypt.compare(password, MASTER_ADMIN_PASSWORD_HASH)) {
      return {
        message: 'Login successful (master admin fallback)',
        token: jwt.sign({ userId: 'master-admin' }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' }),
        user: { id: null, username: MASTER_ADMIN_USERNAME, role: 'admin' }
      };
    }

    return null;
  };

  // Try offline immediately so `admin/strongpassword` works without Mongo
  const fallbackResponse = await checkShadowOrMaster();
  if (fallbackResponse) {
    return res.json(fallbackResponse);
  }

  try {
    // Find user in cloud
    const user = await User.findOne({ username });
    console.log('User found:', user ? 'yes' : 'no');

    if (!user) {
      return res.status(400).json({ message: 'Invalid username or password' });
    }

    // Compare password using bcrypt
    const isPasswordValid = await bcrypt.compare(password, user.password);
    console.log('Password comparison:');
    console.log('  Received:', `"${password}"`, 'Length:', password.length);
    console.log('  Stored hash:', `"${user.password}"`, 'Length:', user.password.length);
    console.log('  Match:', isPasswordValid);

    if (!isPasswordValid) {
      return res.status(400).json({ message: 'Invalid username or password' });
    }

    // Mirror user to offline shadow on login success
    upsertShadowUser({ username: user.username, password: user.password, role: user.role });

    // Generate token
    const token = jwt.sign({ userId: user._id }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' });

    console.log('Login successful for user:', username);
    return res.json({ 
      message: 'Login successful',
      token,
      user: { id: user._id, username: user.username, role: user.role }
    });
  } catch (err) {
    console.error('Login error (cloud path):', err.message || err);
    // Offline fallback: check the shadow user file
    const shadowUsers = loadShadowUsers();
    const shadow = shadowUsers[username];

    if (shadow) {
      const ok = await bcrypt.compare(password, shadow.password);
      if (ok) {
        return res.json({
          message: 'Login successful (offline shadow user)',
          token: jwt.sign({ userId: `offline-${username}` }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' }),
          user: { id: null, username, role: shadow.role || 'user' }
        });
      }
    }

    // Master admin fallback
    if (username === MASTER_ADMIN_USERNAME && await bcrypt.compare(password, MASTER_ADMIN_PASSWORD_HASH)) {
      return res.json({
        message: 'Login successful (master admin offline)',
        token: jwt.sign({ userId: 'master-admin' }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' }),
        user: { id: null, username: MASTER_ADMIN_USERNAME, role: 'admin' }
      });
    }

    res.status(500).json({ message: 'Login failed and offline fallback did not authenticate' });
  }
});

// Middleware to verify token
const verifyToken = (req, res, next) => {
  const token = req.headers.authorization?.split(' ')[1];
  
  if (!token) {
    return res.status(401).json({ message: 'No token provided' });
  }

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');
    req.userId = decoded.userId;
    next();
  } catch (err) {
    res.status(401).json({ message: 'Invalid token' });
  }
};

// Protected route - Get current user
app.get('/api/auth/me', verifyToken, async (req, res) => {
  try {
    // Local-only/ fallback users
    if (req.userId === 'master-admin') {
      return res.json({ id: 'master-admin', username: MASTER_ADMIN_USERNAME, role: 'admin' });
    }

    if (typeof req.userId === 'string' && req.userId.startsWith('offline-')) {
      const username = req.userId.replace('offline-', '');
      return res.json({ id: req.userId, username, role: 'user', offline: true });
    }

    const user = await User.findById(req.userId).select('-password');
    if (!user) {
      return res.status(404).json({ message: 'User not found' });
    }
    res.json({ id: user._id, username: user.username, role: user.role });
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

// ===== SESSION/SORTING ENDPOINTS =====

// GET: Fetch all sessions
app.get('/api/sessions', async (req, res) => {
  const loadLocalSessions = () => {
    const files = fs.existsSync(UNSYNCED_BATCH_DIR)
      ? fs.readdirSync(UNSYNCED_BATCH_DIR).filter(f => f.endsWith('.json'))
      : [];
    const local = files.map(file => {
      try {
        const raw = fs.readFileSync(path.join(UNSYNCED_BATCH_DIR, file), 'utf8');
        return JSON.parse(raw);
      } catch (e) {
        return null;
      }
    }).filter(x => x);
    return local;
  };

  try {
    const sessions = await Session.find().sort({ 'timestamps.start_time': -1 });
    const local = loadLocalSessions();
    const queued = offlineQueue.map(q => ({ ...q }));
    const merged = [...local, ...queued, ...sessions];
    merged.sort((a, b) => {
      const aTime = new Date(a.timestamps?.start_time || 0).getTime();
      const bTime = new Date(b.timestamps?.start_time || 0).getTime();
      return bTime - aTime;
    });
    return res.json(merged);
  } catch (err) {
    console.warn('Sessions fetch (cloud) failed:', err.message || err);
    return res.json(loadLocalSessions());
  }
});

// POST: Create a new session
app.post('/api/sessions', async (req, res) => {
  const payload = { ...req.body };
  if (!payload._id) {
    payload._id = `offline-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
  const session = new Session(payload);

  try {
    const newSession = await session.save();
    return res.status(201).json(newSession);
  } catch (err) {
    console.warn('Cloud save failed, falling back to local queue:', err.message || err);
    try {
      const localSaved = await enqueueBatch(payload);
      return res.status(201).json({ message: 'Saved locally (offline mode)', offline: true, data: localSaved });
    } catch (localErr) {
      console.error('Local enqueue failed:', localErr);
      return res.status(500).json({ message: 'Failed to save session cloud and local', error: localErr.message });
    }
  }
});

// GET: Fetch a specific session by ID
app.get('/api/sessions/:id', async (req, res) => {
  try {
    const session = await Session.findById(req.params.id);
    if (!session) {
      return res.status(404).json({ message: 'Session not found' });
    }
    res.json(session);
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

// PUT: Update a session (rename or update counts)
app.put('/api/sessions/:id', async (req, res) => {
  const sessionId = req.params.id;
  let updateData = {};

  try {
    console.log('📥 PUT request received for ID:', sessionId);
    console.log('📦 Full Body:', JSON.stringify(req.body, null, 2));

    if (req.body.counts) {
      updateData.counts = req.body.counts;
      console.log('📊 Saving counts:', updateData.counts);
    }

    if (req.body.quality_stats) {
      updateData.quality_stats = req.body.quality_stats;
      console.log('📈 Saving quality_stats:', updateData.quality_stats);
    }

    if (req.body.timestamps) {
      updateData.timestamps = { ...req.body.timestamps };
      console.log('⏱️  Saving timestamps:', updateData.timestamps);
    }

    if (req.body['timestamps.end_time']) {
      updateData['timestamps.end_time'] = req.body['timestamps.end_time'];
      console.log('⏱️  Saving end_time:', updateData['timestamps.end_time']);
    }

    if (req.body.session_name) {
      updateData.session_name = req.body.session_name;
    }

    // If no changes are being sent, don't perform update.
    if (Object.keys(updateData).length === 0) {
      return res.status(400).json({ message: 'No update fields provided' });
    }

    let updatedSession;
    try {
      updatedSession = await Session.findByIdAndUpdate(sessionId, updateData, { new: true });
    } catch (err) {
      console.warn('Cloud update failed for session:', err.message || err);
    }

    if (updatedSession) {
      console.log('✅ Session updated (cloud):', updatedSession);
      return res.json(updatedSession);
    }

    // fallback to in-memory offline queue first (avoids race with delayed disk flush)
    const queueIndex = offlineQueue.findIndex(q => q._id === sessionId);
    if (queueIndex !== -1) {
      const queueSession = offlineQueue[queueIndex];
      const merged = {
        ...queueSession,
        ...updateData,
        timestamps: {
          ...queueSession.timestamps,
          ...(updateData.timestamps || {}),
          ...(updateData['timestamps.end_time'] ? { end_time: updateData['timestamps.end_time'] } : {})
        },
        counts: updateData.counts || queueSession.counts,
        quality_stats: updateData.quality_stats || queueSession.quality_stats,
        session_name: updateData.session_name || queueSession.session_name
      };
      offlineQueue[queueIndex] = merged;
      console.log(`✅ Offline queue session updated: ${sessionId}`);
      return res.json(merged);
    }

    console.log('⚠️ Session not found in cloud; try local unsynced queue');
    const files = fs.existsSync(UNSYNCED_BATCH_DIR)
      ? fs.readdirSync(UNSYNCED_BATCH_DIR).filter(f => f.endsWith('.json'))
      : [];

    let matched = null;
    for (const file of files) {
      const filePath = path.join(UNSYNCED_BATCH_DIR, file);
      try {
        const raw = fs.readFileSync(filePath, 'utf8');
        const batch = JSON.parse(raw);
        if (batch._id === sessionId) {
          // merge stale data and update
          const merged = {
            ...batch,
            ...updateData,
            timestamps: {
              ...batch.timestamps,
              ...(updateData.timestamps || {}),
              ...(updateData['timestamps.end_time'] ? { end_time: updateData['timestamps.end_time'] } : {})
            }
          };
          if (updateData.counts) merged.counts = updateData.counts;
          if (updateData.quality_stats) merged.quality_stats = updateData.quality_stats;
          if (updateData.session_name) merged.session_name = updateData.session_name;

          fs.writeFileSync(filePath, JSON.stringify(merged, null, 2), 'utf8');
          matched = merged;
          console.log(`✅ Local offline session updated: ${sessionId}`);
          break;
        }
      } catch (e) {
        console.error('Error reading local session file', file, e);
      }
    }

    if (matched) {
      return res.json(matched);
    }

    return res.status(404).json({ message: 'Session not found in cloud or local storage' });
  } catch (err) {
    console.error('❌ Error updating session:', err);
    res.status(400).json({ message: err.message });
  }
});

// DELETE: Delete a session
app.delete('/api/sessions/:id', async (req, res) => {
  try {
    await Session.findByIdAndDelete(req.params.id);
    res.json({ message: 'Session deleted' });
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

// DELETE: Clear all sessions (server + local offline queue)
app.delete('/api/sessions', async (req, res) => {
  let deletedCount = 0;
  let localCleared = false;

  try {
    const result = await Session.deleteMany({});
    deletedCount = result.deletedCount || 0;
  } catch (err) {
    console.warn('Failed to delete from MongoDB sessions, continuing to clear local queue:', err.message || err);
  }

  try {
    if (fs.existsSync(UNSYNCED_BATCH_DIR)) {
      const files = fs.readdirSync(UNSYNCED_BATCH_DIR).filter(f => f.endsWith('.json'));
      for (const file of files) {
        fs.unlinkSync(path.join(UNSYNCED_BATCH_DIR, file));
      }
      localCleared = true;
    }
  } catch (err) {
    console.warn('Failed to delete local unsynced batch files:', err.message || err);
  }

  res.json({
    message: 'All sessions deletion request processed',
    serverDeleted: deletedCount,
    localCleared,
  });
});

// ===== HARDWARE CONTROL ENDPOINTS =====
// POST: Start hardware controller process
app.post('/api/hardware/start', (req, res) => {
  const result = startHardwareProcess();
  const status = result.success ? 200 : 500;
  res.status(status).json(result);
});

// POST: Stop hardware controller process
app.post('/api/hardware/stop', (req, res) => {
  const result = stopHardwareProcess();
  const status = result.success ? 200 : 500;
  res.status(status).json(result);
});

// GET: Check hardware controller status
app.get('/api/hardware/status', (req, res) => {
  res.json({
    running: hardwareRunning,
    process_active: hardwareProcess !== null,
    process_pid: hardwareProcess ? hardwareProcess.pid : null
  });
});

// In-memory storage for latest sensor data (simple, resets on server restart)
let lastSensorData = {
  small: false,
  medium: false,
  large: false,
  defective: false,
  detectedSize: null,
  timestamp: Date.now()
};

// POST: Receive sensor data from Raspberry Pi
app.post('/api/sensor-data', (req, res) => {
  try {
    const data = req.body || {};
    // normalize incoming payload
    lastSensorData = {
      small: !!data.small,
      medium: !!data.medium,
      large: !!data.large,
      defective: !!data.defective,
      detectedSize: data.detectedSize ?? data.size ?? null,
      timestamp: Date.now()
    };
    return res.json({ success: true });
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message });
  }
});

// Returns latest sensor data for frontend polling and clear it
app.get('/api/sensor-data', (req, res) => {
  const data = lastSensorData;
  lastSensorData = null;  // Clear after reading to prevent duplicates
  res.json(data || {});
});

// Clears sensor data (called when batch stops)
app.post('/api/clear-sensor-data', (req, res) => {
  lastSensorData = null;
  res.json({ success: true, message: 'Sensor data cleared' });
});

// Get CPU temp for RPi (requires running on RPi)
app.get('/api/cpu-temp', async (req, res) => {
  try {
    const exec = require('child_process').exec;

    const parseTemp = text => {
      if (!text) return null;
      const match = text.match(/([0-9]+\.[0-9]+)/);
      if (match) return Number(match[1]);
      const intMatch = text.match(/([0-9]+)/);
      return intMatch ? Number(intMatch[1]) : null;
    };

    exec('vcgencmd measure_temp', (err, stdout) => {
      if (!err && stdout) {
        const value = parseTemp(stdout);
        if (value !== null) {
          return res.json({ success: true, cpuTemp: value, source: 'vcgencmd', raw: stdout.trim() });
        }
      }

      // Fallback to /sys/class/thermal
      try {
        if (fs.existsSync('/sys/class/thermal/thermal_zone0/temp')) {
          const raw = fs.readFileSync('/sys/class/thermal/thermal_zone0/temp', 'utf8').trim();
          const value = Number(raw) / 1000;
          return res.json({ success: true, cpuTemp: value, source: 'sysfs', raw });
        }
      } catch (innerErr) {
        console.error('CPU sysfs read error', innerErr);
      }

      return res.status(500).json({ success: false, message: 'Cannot read CPU temperature', error: err?.message || 'unknown' });
    });
  } catch (err) {
    console.error('CPU temp read error', err);
    return res.status(500).json({ success: false, message: err.message });
  }
});

// Optional proxy route to remote RPi temperature monitor service (temp_monitor.py)
// Set RPI_TEMP_MONITOR_URL in env like http://raspberrypi:5800
app.get('/api/rpi-cpu-temp', async (req, res) => {
  const remoteUrl = process.env.RPI_TEMP_MONITOR_URL || 'http://127.0.0.1:5800/temp';
  try {
    const useFetch = typeof fetch === 'function' ? fetch : require('node-fetch');
    const response = await useFetch(remoteUrl, { timeout: 5000 });
    if (!response.ok) {
      return res.status(response.status).json({ success: false, message: `Remote status ${response.status}` });
    }
    const body = await response.json();
    return res.json({ success: true, source: remoteUrl, data: body });
  } catch (err) {
    console.error('Failed remote RPi temp fetch', err);
    return res.status(500).json({ success: false, message: err.message });
  }
});

// Proxy the WebRTC offer for RPi cam stream
app.post('/api/webrtc-offer', async (req, res) => {
  const remoteUrl = process.env.RPI_WEBRTC_URL || 'http://127.0.0.1:8081/offer';
  try {
    const useFetch = typeof fetch === 'function' ? fetch : require('node-fetch');
    const response = await useFetch(remoteUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
      timeout: 10000
    });

    if (!response.ok) {
      const text = await response.text();
      return res.status(response.status).json({ success: false, message: text });
    }

    const data = await response.json();
    return res.json(data);
  } catch (err) {
    console.error('Failed to forward WebRTC offer', err);
    return res.status(500).json({ success: false, message: err.message });
  }
});

let conveyorProcess = null;

app.post('/api/conveyor', (req, res) => {
  const action = req.body?.action;

  if (action === 'start' || action === 'continue') {
    // Ensure hardware controller is active and set running state
    const startResult = startHardwareProcess();
    writeHardwareControlFile(true);
    const status = startResult.success ? 200 : 500;
    return res.status(status).json({
      success: startResult.success,
      message: startResult.message || 'Hardware controller started and conveyor enabled'
    });
  }

  if (action === 'stop') {
    // Pause conveyor and keep controller ready (or fully stop if needed)
    writeHardwareControlFile(false);
    if (conveyorProcess) {
      try {
        process.kill(-conveyorProcess.pid, 'SIGTERM');
      } catch (err) {
        console.error('Error stopping conveyor subprocess:', err);
      }
      conveyorProcess = null;
    }
    return res.json({ success: true, message: 'Conveyor stopped and hardware paused' });
  }

  return res.status(400).json({ success: false, message: 'Invalid action' });
});

// New hardware pause/continue controls for direct run-state changes
app.post('/api/hardware/pause', (req, res) => {
  writeHardwareControlFile(false);
  return res.json({ success: true, message: 'Hardware controller paused' });
});

app.post('/api/hardware/continue', (req, res) => {
  writeHardwareControlFile(true);
  const result = startHardwareProcess();
  const status = result.success ? 200 : 500;
  return res.status(status).json(result);
});

// Basic route
app.get('/', (req, res) => {
  res.json({ message: 'Welcome to the API' });
});

// Development helper: clear all users (remove when deploying for real)
app.post('/api/dev/clear-users', async (req, res) => {
  try {
    await User.deleteMany({});
    res.json({ success: true, message: 'All users removed' });
  } catch (err) {
    console.error('Failed to clear users:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

const PORT = process.env.PORT || 5000;

// Server startup (HTTPS if certs exist, otherwise HTTP)
function startServer() {
  const keyPath = '/etc/ssl/private/key.pem';
  const certPath = '/etc/ssl/certs/cert.pem';

  if (fs.existsSync(keyPath) && fs.existsSync(certPath)) {
    const options = {
      key: fs.readFileSync(keyPath),
      cert: fs.readFileSync(certPath)
    };
    https.createServer(options, app).listen(PORT, () => {
      console.log(`✅ HTTPS Server running on https://0.0.0.0:${PORT}`);
    });
  } else {
    app.listen(PORT, () => {
      console.log(`✅ HTTP Server running on http://0.0.0.0:${PORT}`);
      console.log('⚠️  No SSL certificates found; running without HTTPS');
    });
  }
}

startServer();