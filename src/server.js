import express from 'express';
import { createServer } from 'http';
#import connectDB from './config/db.js';
#import config from './config/index.js';
#import authRoutes from './core/routes/authRoutes.js';
#import sensorDataRoutes from './core/routes/sensorDataRoutes.js';

const app = express();
const server = createServer(app);

app.use(express.json());
#connectDB();

#app.use('/api/auth', authRoutes);
#app.use('/api/sensor-data', sensorDataRoutes);
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

app.post("/esp32", function(req, res) {
  const csv_data = req.body;

  fs.appendFile(__dirname + '/esp32_comms.txt', csv_data + '\n', err => {
    if (err) {
      console.log(err);
      res.send("Could not write to file");
    } else {
      res.send("Successfully wrote to file");
    }
  });
});
