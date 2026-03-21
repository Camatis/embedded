const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const axios = require('axios');
require('dotenv').config();

const app = express();

// Middleware
app.use(cors());
app.use(express.json());

// Offline-first constants
const LOCAL_DATA_DIR = process.env.LOCAL_DATA_DIR || path.join(__dirname, '..', 'local_data');
const USER_SHADOW_FILE = path.join(LOCAL_DATA_DIR, 'users_shadow.json');
const UNSYNCED_BATCH_DIR = path.join(LOCAL_DATA_DIR, 'unsynced_batches');
const MASTER_ADMIN_USERNAME = process.env.MASTER_ADMIN_USERNAME || 'admin';
const MASTER_ADMIN_PASSWORD_HASH = process.env.MASTER_ADMIN_PASSWORD_HASH || '$2b$10$BEKdhn6qQVuheUG6l2fR6.H.1Xgi7evgKiaTgB8Mzu8vFe9fl73Hq'; 

function ensureLocalDirs() {
  if (!fs.existsSync(LOCAL_DATA_DIR)) fs.mkdirSync(LOCAL_DATA_DIR, { recursive: true });
  if (!fs.existsSync(UNSYNCED_BATCH_DIR)) fs.mkdirSync(UNSYNCED_BATCH_DIR, { recursive: true });
}
ensureLocalDirs();

// --- HARDWARE CONTROL BRIDGE ---
const CONTROL_FILE = '/tmp/mangosort_control.json';

app.post('/api/control', (req, res) => {
  try {
    const { action } = req.body; 
    let state = "STOPPED";

    if (action === 'start') state = "RUNNING";
    else if (action === 'pause') state = "PAUSED";
    else if (action === 'stop') state = "STOPPED";

    // Write the state to the file for Python to read
    fs.writeFileSync(CONTROL_FILE, JSON.stringify({ state: state }));
    
    console.log(`[Hardware] System state changed to: ${state}`);
    res.json({ success: true, state: state });
  } catch (error) {
    console.error('Error writing control file:', error);
    res.status(500).json({ success: false, message: 'Failed to control hardware' });
  }
});

// Basic route
app.get('/', (req, res) => {
  res.json({ message: 'Welcome to the API' });
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
      console.log(`Secure Server running on port ${PORT}`);
    });
  } else {
    app.listen(PORT, () => {
      console.log(`Server running on port ${PORT} (HTTP)`);
    });
  }
}

startServer();