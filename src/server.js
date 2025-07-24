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

app.use(express.json());
await connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.text());

server.listen((config.port || 3000), () => {
  console.log(`Server running on port ${config.port}`);
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${config.port} is already in use. Terminate its running process or use a different port.`);
  } else {
    console.error('Server startup error:', err);
  }
});

app.get("/", function(req, res) {
  res.send("You made it!");
  });

app.get("/esp32", async (req, res) => {
  try {
    const latestEntry = await SensorData.findOne().sort({ timestamp: -1 }); // sort by most recent timestamp
    if (!latestEntry) return res.status(404).send("No data in database yet.");
    res.json(latestEntry);
  } catch (err) {
    console.error("Error fetching latest sensor data:", err);
    res.sendStatus(500);
  }
  if(end_trip){
    res.send(end_trip);
  }
});


let current_tripID = null; // Global variable to keep track of active tripID
let doc = null;
let end_trip = "";

app.post("/esp32", async (req, res) => {
  try {
    const csv_data = req.body;
    if (csv_data === "start_of_trip") {
      const now = new Date();
      current_tripID = formatTimestamp(now);
      return res.sendStatus(200);
    }

    else if (csv_data === "end_of_trip"){
      end_trip ="Thank you for driving!";
      res.send("Thank you for driving!");
    }
    

    const fields = csv_data.split(",");
    //if (fields.length < 7) throw new Error("Invalid CSV");

    const acceleration = parseFloat(fields[2]);
    const rpm = parseFloat(fields[3]);
    const [hours, minutes, seconds] = fields[0].split(":").map(Number);
    const now = new Date();
    const time_with_date = new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate(),
        hours,
        minutes,
        seconds);


    doc = {
      tripID: current_tripID,
      timestamp: time_with_date,
      speed: parseFloat(fields[1]),
      acceleration,
      rpm,
      engine_load: parseFloat(fields[4]),
      hard_braking: acceleration <= -3.0,
      inconsistent_speed: acceleration >= 3.0 && rpm >= 3500,
      lane_offset: fields[5],
      lane_offset_direction: fields[6]
    };

    await SensorData.create(doc);
    res.send("Received row!");
    //console.log(doc);
  } catch (err) {
    console.error("ESP32 route error:", err);
    res.sendStatus(500);
  }
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
