import express from 'express';
import { createServer } from 'http';
import fs from 'fs';
import path from 'path';
//import connectDB from './config/db.js';
//import config from './config/index.js';
//import authRoutes from './core/routes/authRoutes.js';
//import sensorDataRoutes from './core/routes/sensorDataRoutes.js';

const app = express();
const server = createServer(app);

//app.use(express.json());
//connectDB();

//app.use('/api/auth', authRoutes);
//app.use('/api/sensor-data', sensorDataRoutes);
app.use(express.text());

server.listen((process.env.PORT || 3000), () => {
  console.log(`Server running on port ${process.env.PORT}`);
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${process.env.PORT} is already in use. Terminate its running process or use a different port.`);
  } else {
    console.error('Server startup error:', err);
  }
});

let currentFilePath = null; 
let send_string = "" 

app.get("/", function(req, res) {
  res.send("You made it!");
  });

app.get("/esp32", function(req, res) {
  res.send(send_string);
  });

app.post("/esp32", function(req, res) {
  const csv_data = req.body;
  
  if(csv_data == "start_of_trip"){
      const now = new Date();
      // Get current timestamp
      currentFilePath = formatTimestamp(now) + '.csv';
      //currentFilePath = path.join(__dirname, filename);
      send_string = currentFilePath + "<br>"; 
  }
  
  else{
    send_string = currentFilePath + csv_data + "<br>";
  }

  res.send(send_string);
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
