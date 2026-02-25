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
const EventEmitter = require("events");

await connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.json());
app.use(express.text());
let user_id = ""
let counter = 0 // counts whether a websocket message is being sent for the first time or not.

// using events to get websockets to send data

const trip_status = new EventEmitter();
const dashboard_recommender = new EventEmitter();

// Whenever your start/end flag changes, emit an event used by the websocket:
function onTripStatusChange(newFlagValue) {
    trip_status.emit("trip_status", newFlagValue);
}

const wss = new WebSocketServer({ noServer:true, path:'/websocky' });
wss.on('connection', function connection(ws) {
  ws.on('error', console.error);

  ws.on('message', function message(data) {
    if(counter===0){
     user_id = data;
     console.log(user_id);
     ws.send(user_id);
     counter +=1;
    }
  });

    // Send flag updates to ESP32 when dashboard recommendations come in
  dashboard_recommender.on("flagChanged", (newFlag) => {
      ws.send(JSON.stringify({ type: newFlag }));
  });

  ws.send('recommendation and love letter from Mr. Heroku to Ms. ESP32');
});

const wssPython = new WebSocketServer({ noServer: true, path: '/python' });
let pythonClient = null;

wssPython.on("connection", (ws) => {
  console.log("Python connected");
  pythonClient = ws;

  //Receive dashboard messages
  pythonClient.on('message', function message(data) {
   // Emit event for WebSocket
      dashboard_recommender.emit("flagChanged", data);
  });

  // Send flag updates to Python when trip_status changes
  trip_status.on("flagChanged", (newFlag) => {
    if (pythonClient && pythonClient.readyState === ws.OPEN) {
      pythonClient.send(JSON.stringify({ type: newFlag }));
    }
  });

  ws.on("close", () => {
    pythonClient = null;
  });
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
let dashboard_recommendations = ""

//app.post('/from-python', function(req,res){
  //  dashboard_recommendations += req.body;
  //});
//const send_string = req.body;
//spawn python
//const py = spawn('python3', ['src/python_backend.py']);

//  py.stdin.write(send_string);
 // py.stdin.end();

//  py.stdout.on('data', data => {
//    console.log(`Python says: ${data}`);
 //   output += data;
  

  //py.stderr.on('data', data => {
  //  console.error(`Python error: ${data}`);
  //});
  //py.on('close', code => {
  //    res.send(`Python finished with code ${code}. Here is your row: ${output}`);
   // });
  //});


app.post("/esp32", async (req, res) => {
  try {
    let csv_data = req.body;
    if (csv_data === "start_of_trip") {
      trip_status_value="trip_started";
      end_trip = "";
      trip_ended = false;
      const now = new Date();
      current_tripID = formatTimestamp(now);
      // Emit event for WebSocket
      trip_status.emit("flagChanged", trip_status_value);
      return res.sendStatus(200);
    }

    else if (csv_data === "end_of_trip"){
      trip_status_value= "trip_ended";
      end_trip ="Thank you for driving!";
      trip_ended = true;
      // Emit event for WebSocket
      trip_status.emit("flagChanged", trip_status_value);
      csv_data = "00:12:00,1,2,3,4,5,right";
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
  return '${dd}${mm}${yy}${hh}${mi}${ss}';
}
