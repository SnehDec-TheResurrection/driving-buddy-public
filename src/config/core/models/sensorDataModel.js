import mongoose from 'mongoose';

const sensorDataSchema = new mongoose.Schema({
  speed: { type: Number, required: true },
  acceleration: { type: Number, required: true },
  rpm: { type: Number, required: true },
  engine_load: { type: Number, required: true },
  // steering_angle_deg: { type: Number },
  sharp_turn: { type: Boolean },
  // lane_deviation: { type: Boolean }, // Uncomment when ready to use
  inconsistent_speed: { type: Boolean },
  hard_braking: { type: Boolean }
});

const SensorData = mongoose.model('SensorData', sensorDataSchema);
export default SensorData;
