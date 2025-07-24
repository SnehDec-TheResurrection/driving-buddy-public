import express from 'express';
import { createServer } from 'http';
import fs from 'fs';
import path from 'path';
import mongoose from 'mongoose';
import connectDB from './config/db.js';
import config from './config/index.js';
//import authRoutes from './core/routes/authRoutes.js';
//import sensorDataRoutes from './core/routes/sensorDataRoutes.js';
import SensorData from './core/models/sensorDataModel.js';

const app = express();
const server = createServer(app);

//app.use(express.json());
await connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.text());

server.listen((config.PORT || 3000), () => {
  console.log(`Server running on port ${config.PORT}`);
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${config.PORT} is already in use. Terminate its running process or use a different port.`);
  } else {
    console.error('Server startup error:', err);
  }
});

app.get("/", function(req, res) {
  res.send("You made it!");
  });


let current_tripID = null; // Global variable to keep track of active tripID

app.post("/esp32", async function(req, res) {
  const csv_data = req.body;
  const fields = csv_data.split(",");
  
  if(csv_data == "start_of_trip"){
      const now = new Date();
      // Get current timestamp
      current_tripID = formatTimestamp(now);
  }
  
  else{
     const acceleration = parseFloat(fields[2]);
     const rpm = parseFloat(fields[3]);
     const hard_braking = acceleration <=-3.0;
     const inconsistent_speed = acceleration >= 3.0 && rpm >= 3500;
    
   const doc = {
          tripID: current_tripID,
          timestamp: fields[0],
          speed:  parseFloat(fields[1]),
          acceleration,
          rpm,
          engine_load: parseFloat(fields[4]),
          // flags (independent)
          hard_braking,
          inconsistent_speed
                                       };
          await SensorData.create(doc);
                  
  }
  res.sendStatus(200);           // Sends HTTP 200
});

// Helper function to format date as DDMMYY[Hour][Min][Sec]
function formatTimestamp(date) {
  const pad = (n) => n.toString().padStart(2, '0');
  const dd = pad(date.getDate());
  const mm = pad(date.getMonth() + 1); // Months are 0-indexed
  const yy = date.getFullYear().toString().slice(-2);
  const hh = pad(date.getHours());
  const mi = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  return `${dd}${mm}${yy}${hh}${mi}${ss}`;
}
