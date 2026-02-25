import mongoose from 'mongoose';

const sensorDataSchema = new mongoose.Schema({
  tripID: {type: String, required: true},
  timestamp:{type:Date, required:true},
  speed: { type: Number, required: true },
  acc_pedal: { type: Number, required: true },
  
  acceleration_x:{type:Number, required:true},
  acceleration_y:{type:Number, required:true},
  acceleration_z:{type:Number, required:true},
  yaw_rate:{type:Number, required:true},
  gps_latitude:{type:String, required:true},
  gps_longitude:{type:String, required:true},
  lane_offset: {type:Number},
  lane_offset_direction: {type:String},
  trip_ended:{type:Boolean}
});

const SensorData = mongoose.model('SensorData', sensorDataSchema);
export default SensorData;
