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
import { WebSocketServer } from 'ws';

const app = express();
const server = createServer(app);

await connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.json());
app.use(express.text());
let user_id = ""

const wss = new WebSocketServer({ server, path:'/websocky' });
wss.on('connection', function connection(ws) {
  ws.on('error', console.error);

  ws.on('message', function message(data) {
     user_id = data;
     console.log(user_id);
  });

  ws.send('recommendation and love letter from Mr. Heroku to Ms. ESP32');
});

  
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
      csv_data = "00:12:00,1,2,3,4,5,right";
    }
    
    const fields = csv_data.split(",");
    //if (fields.length < 7) throw new Error("Invalid CSV");
    
    let lane_offset = parseFloat(fields[5]);
    let lane_offset_direction = fields[6];
    
    if(lane_offset <-1 || lane_offset >1 || lane_offset_direction ==="0"){
      lane_offset = 0; 
      lane_offset_direction = "centre";
    }
    
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
      userID: user_id,
      tripID: current_tripID,
      timestamp: time_with_date,
      speed: parseFloat(fields[1]),
      throttle1: parseFloat(fields[2]),
      throttle2: parseFloat(fields[3]), 
      acc_pedald: parseFloat(fields[4]), 
      acc_pedale: parseFloat(fields[5]), 
      acc_pedalf: parseFloat(fields[6]),
      throttle3: parseFloat(fields[7]),
      acceleration_x: parseFloat(fields[8]), 
      acceleration_y: parseFloat(fields[9]), 
      acceleration_z: parseFloat(fields[10]), 
      angular_acceleration: parseFloat(fields[11]), 
      gps_latitude: parseFloat(fields[12]),
      gps_longitude: parseFloat(fields[13]),
      lane_offset,
      lane_offset_direction,
      trip_ended
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
