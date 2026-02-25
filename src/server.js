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
import { EventEmitter } from 'events';

const app = express();
const server = createServer(app);

const dashboard_recommendation = new EventEmitter();

function on_rec(newRecValue) {
    dashboard_recommendation.emit("new_recommendation", newRecValue);
}

await connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.json());
app.use(express.text());
let user_id = ""
let last_recommendation_time = 0 //helps set a cooldown period for python dashboard recommendations
const COOLDOWN_MS = 60000;       // 1 minute in milliseconds
let client = null;

const wss = new WebSocketServer({ server, path:'/websocky' });
wss.on('connection', function connection(ws) {
  let counter = 0 // counts whether a websocket message is being sent for the first time or not.
  ws.on('error', console.error);
  client = ws;
  ws.on('message', function message(data) {
    if(counter===0){
     user_id = data;
     console.log(user_id);
     ws.send(user_id);
     counter +=1;
    }
  });

  ws.send('recommendation and love letter from Mr. Heroku to Ms. ESP32');
});

  dashboard_recommendation.on("new_recommendation", (newRecValue) => {
        client.send(newRecValue);
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
  //res.json(doc); // <- use res.json for pretty printing on browser
  //create a plain text string for sending to esp32
  const responseString = Object.values(doc).join(",");
  res.set("Content-Type", "text/plain");
  res.send(responseString);
})

let current_tripID = null; // Global variable to keep track of active tripID
let doc = null;
let end_trip = "";
let trip_ended = false;
let dashboard_recommendation_value = ""

app.post('/python', function(req,res){
    const current = Date.now();
    const time_difference = current-last_recommendation_time;
    if (req.body == dashboard_recommendation_value && time_difference < COOLDOWN_MS){
      on_rec("Duplicate");
    }
  else{
    dashboard_recommendation_value = req.body;
    on_rec(dashboard_recommendation_value);
    last_recommendation_time = Date.now()
  }
  res.sendStatus(200)
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
      csv_data = "trip_ended_babe,1,2,3,4,5,right";
    }
    
    const fields = csv_data.split(",");
    //if (fields.length < 7) throw new Error("Invalid CSV");
    
    let lane_offset = parseFloat(fields[10]);
    let lane_offset_direction = fields[11];
    
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
      acc_pedal: parseFloat(fields[2]), 
      acceleration_x: parseFloat(fields[3]), 
      acceleration_y: parseFloat(fields[4]), 
      acceleration_z: parseFloat(fields[5]), 
      angular_acceleration: parseFloat(fields[6]),
      yaw: parseFloat(fields[7]), 
      gps_latitude: parseFloat(fields[8]),
      gps_longitude: parseFloat(fields[9]),
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
