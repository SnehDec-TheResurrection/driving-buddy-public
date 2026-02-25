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
import { spawn } from 'child_process';

const app = express();
const server = createServer(app);

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
  if (!doc) {
    return res.status(404).send("No data received yet.");
  }
  res.json(doc); // <- use res.json instead of json.stringify


   // if(trip_ended){
    //return res.send(end_trip);
  //}
 // try {
 //   const latestEntry = await SensorData.findOne().sort({ timestamp: -1 }); // sort by most recent timestamp
 //   if (!latestEntry) return res.status(404).send("No data in database yet.");
 //   res.json(latestEntry);
 // } catch (err) {
 //   console.error("Error fetching latest sensor data:", err);
  //  res.sendStatus(500);
 // }

});


let current_tripID = null; // Global variable to keep track of active tripID
let doc = null;
let end_trip = "";
let trip_ended = false;

app.post('/python', function(req,res){
let output = ""
const send_string = req.body;
//spawn python
const py = spawn('python3', ['src/python_backend.py']);

  py.stdin.write(send_string);
  py.stdin.end();

  py.stdout.on('data', data => {
    console.log(`Python says: ${data}`);
    output += data;
  });

  py.stderr.on('data', data => {
    console.error(`Python error: ${data}`);
  });
  py.on('close', code => {
      res.send(`Python finished with code ${code}. Here is your row: ${output}`);
    });
  });

app.post("/esp32", async (req, res) => {
  try {
    let csv_data = req.body;
    console.log(`Received: [${csv_data}]`);
    if (csv_data === "start_of_trip") {
      end_trip = "";
      trip_ended = false;
      const now = new Date();
      current_tripID = formatTimestamp(now);
      return res.sendStatus(200);
    }

    else if (csv_data === "end_of_trip"){
      end_trip ="Thank you for driving!";
      trip_ended = true;
      csv_data = "user, 00:12:00,1,2,3,4,5,6,latu, longu, right";
    }
    

    const fields = csv_data.split(",");
    //if (fields.length < 7) throw new Error("Invalid CSV");

    let lane_offset = 0;
   let lane_offset_direction = "centre";
    
    //if(lane_offset <-1 || lane_offset >1 || lane_offset_direction ==="0"){
      //lane_offset = 0; 
      //lane_offset_direction = "centre";
    //}
    
    const [hours, minutes, seconds] = fields[1].split(":").map(Number);
    const now = new Date();
    const time_with_date = new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate(),
        hours,
        minutes,
        seconds);


    doc = {
      userID: fields[0],
      tripID: current_tripID,
      timestamp: time_with_date,
      speed: parseFloat(fields[2]),
      acc_pedal: parseFloat(fields[3]), 
      acceleration_x: parseFloat(fields[4]), 
      acceleration_y: parseFloat(fields[5]), 
      acceleration_z: parseFloat(fields[6]), 
      yaw_rate: parseFloat(fields[7]),
      gps_latitude: fields[8],
      gps_longitude: fields[9],
      lane_offset,
      lane_offset_direction,
      trip_ended
    };

    await SensorData.create(doc);
    res.send("Received row!");
    //console.log(doc);
  } catch (err) {
    console.error("DETAILED ERROR:", err.message); // This tells you the EXACT line that failed
    res.status(500).send("Crash reason: " + err.message);
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
