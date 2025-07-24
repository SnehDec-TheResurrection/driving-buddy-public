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

app.get("/", function(req, res) {
  res.send("You made it!");
  });

app.get("/:filename", (req, res) => {
  const filePath = __dirname + req.params.filename + ".txt";
  res.sendFile(filePath, err => {
    if (err) {
      console.error(err);
      res.status(404).send("File not found.");
    }
  });
});


let currentFilePath = null; // Global variable to keep track of active trip file
let counter = 0;

app.post("/esp32", function(req, res) {
  const csv_data = req.body;
  // Get current timestamp

  if(csv_data == "start_of_trip"){
      counter += 1;
      const now = new Date();
     // const filename = formatTimestamp(now) + '.csv';
      const filename = counter + ".txt"
      currentFilePath = path.join(__dirname, filename);
  }
  
  else{
  fs.appendFile(currentFilePath, csv_data + '\n', err => {
    if (err) {
      console.log(err);
      res.send("Could not write to file");
    } else {
      res.send("Successfully wrote to file");
    }
  });
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
