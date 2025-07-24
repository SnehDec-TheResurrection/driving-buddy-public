import mongoose from 'mongoose';
import config from './index.js';

const connectDB = async () => {
  try {
    await mongoose.connect(config.mongoUrl, {
      useNewUrlParser: true,
      useUnifiedTopology: true
    });
    console.log('MongoDB connected');
  } catch (err) {
    console.error('error:', err.message);
    process.exit(1);
  }
};

export default connectDB;
