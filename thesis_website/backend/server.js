 // backend/server.js
const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const https = require('https');
const fs = require('fs');
require('dotenv').config();

const app = express();

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

    // Generate token
    const token = jwt.sign({ userId: user._id }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' });

    res.status(201).json({ 
      message: 'User created successfully', 
      token,
      user: { id: user._id, username: user.username, role: user.role }
    });
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const { username, password } = req.body;

    console.log('Login attempt with:', { username, password: password ? '***' : 'empty' });

    // Check if username and password are provided
    if (!username || !password) {
      console.log('Missing credentials');
      return res.status(400).json({ message: 'Username and password are required' });
    }

    // Find user
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

    // Generate token
    const token = jwt.sign({ userId: user._id }, process.env.JWT_SECRET || 'your-secret-key', { expiresIn: '24h' });

    console.log('Login successful for user:', username);
    res.json({ 
      message: 'Login successful',
      token,
      user: { id: user._id, username: user.username, role: user.role }
    });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ message: err.message });
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
    const user = await User.findById(req.userId).select('-password');
    res.json({ id: user._id, username: user.username, role: user.role });
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

// ===== SESSION/SORTING ENDPOINTS =====

// GET: Fetch all sessions
app.get('/api/sessions', async (req, res) => {
  try {
    const sessions = await Session.find().sort({ 'timestamps.start_time': -1 });
    res.json(sessions);
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

// POST: Create a new session
app.post('/api/sessions', async (req, res) => {
  const session = new Session(req.body);
  try {
    const newSession = await session.save();
    res.status(201).json(newSession);
  } catch (err) {
    res.status(400).json({ message: err.message });
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
  try {
    // Debug: log FULL body to see everything received
    console.log('📥 PUT request received for ID:', req.params.id);
    console.log('📦 Full Body:', JSON.stringify(req.body, null, 2));
    
    // Build update object with proper nested field handling
    const updateData = {};
    
    // Handle counts object
    if (req.body.counts) {
      updateData.counts = req.body.counts;
      console.log('📊 Saving counts:', updateData.counts);
    } else {
      console.log('⚠️  No counts received in req.body');
    }
    
    // Handle quality_stats object
    if (req.body.quality_stats) {
      updateData.quality_stats = req.body.quality_stats;
      console.log('📈 Saving quality_stats:', updateData.quality_stats);
    }
    
    // Handle timestamps.end_time
    if (req.body['timestamps.end_time']) {
      updateData['timestamps.end_time'] = req.body['timestamps.end_time'];
      console.log('⏱️  Saving end_time:', updateData['timestamps.end_time']);
    }
    
    // Handle session_name for rename
    if (req.body.session_name) {
      updateData.session_name = req.body.session_name;
    }
    
    const updatedSession = await Session.findByIdAndUpdate(
      req.params.id,
      updateData,
      { new: true }
    );
    console.log('✅ Session updated:', updatedSession);
    res.json(updatedSession);
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

// DELETE: Clear all sessions
app.delete('/api/sessions', async (req, res) => {
  try {
    await Session.deleteMany({});
    res.json({ message: 'All sessions deleted' });
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
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

// GET: Return latest sensor data for frontend polling and clear it
app.get('/api/sensor-data', (req, res) => {
  const data = lastSensorData;
  lastSensorData = null;  // Clear after reading to prevent duplicates
  res.json(data || {});
});

// POST: Clear sensor data (called when batch stops)
app.post('/api/clear-sensor-data', (req, res) => {
  lastSensorData = null;
  res.json({ success: true, message: 'Sensor data cleared' });
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