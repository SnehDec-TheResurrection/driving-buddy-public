import sys
import os
import numpy as np
import tensorflow as tf
import keras as keras
from pymongo import MongoClient
import sklearn as sk
import joblib
import requests
import time 
import pika
from datetime import datetime, date, timedelta

window_size = 30 #editable parameter based on hardware sampling constraints. x Hz * 10 = window_size
stride = 5
feature_columns = ["speed", "acceleration_x", "acceleration_y", "accel_pedal", "yaw_rate"]
trip_ended = False
last_timestamp = 0
current_trip_id=0
inconsistent_speed_instances = 0 
sudden_braking_instances = 0 
sharp_turning_instances = 0
lane_deviation_instances = 0 

last_recommendation_time = 0
dashboard_recommendation_value = ""
COOLDOWN_SECONDS = 5  # Adjusted for seconds (e.g., 5000ms = 5s)

IDX_YAW = 0
IDX_VEL = 1

def unscale_joint_preds(y_pred_s, scaler_dyaw, scaler_dv):
    # inverse-transform scaled predictions back to raw target units (still deltas).
    N, H, _ = y_pred_s.shape
    y_pred_raw = np.zeros_like(y_pred_s, dtype=np.float32)
    y_pred_raw[:, :, IDX_YAW]   = scaler_dyaw.inverse_transform(y_pred_s[:, :, IDX_YAW].reshape(-1, 1)).reshape(N, H)
    y_pred_raw[:, :, IDX_VEL]   = scaler_dv.inverse_transform(  y_pred_s[:, :, IDX_VEL].reshape(-1, 1)).reshape(N, H)
    return y_pred_raw

def reconstruct(y_pred_joint_raw, queue_of_events):
    # reconstruct ABS yaw/vel/accel predictions in original units
    yaw_pred_abs = queue_of_events[-1]["yaw_rate"] + y_pred_joint_raw[:, :, IDX_YAW]
    # vel_abs = vel_last + dv_to_last
    vel_pred_abs = queue_of_events[-1]["speed"] + y_pred_joint_raw[:, :, IDX_VEL]
    return yaw_pred_abs, vel_pred_abs

def cooldown(prediction_text):
    global last_recommendation_time, dashboard_recommendation_value
    current_time = time.time()
    time_difference = current_time - last_recommendation_time

    # Check if it's the same message AND within the cooldown period
    if prediction_text == dashboard_recommendation_value and time_difference < COOLDOWN_SECONDS:
        dashboard_recommendation_value = "Duplicate"
    else:
        # Update state and trigger recommendation
        dashboard_recommendation_value = prediction_text
        last_recommendation_time = current_time
        
def increment_persistent_data(prediction_text):
    if prediction_text == "Start slowing down early." :
         sudden_braking_instances += 1
    elif prediction_text == "Be careful before turning.":
        sharp_turning_instances += 1
    elif prediction_text == "Gradually speed up or slow down early.":
        inconsistent_speed_instances +=1
    elif prediction_text == "Adjust to the left to stay centred in the lane." or prediction_text=="Adjust to the right to stay centred in the lane.":
        lane_deviation_instances +=1
    else: #if duplicate
        pass

def connect_to_DB():
    mongo_url = os.getenv("MONGO_URL")
    client = MongoClient(mongo_url)
    db = client["DrivingBuddy"]
    collection = db["sensordatas"]
    return collection

def message_dyno():
    global current_trip_id
      # 1. Get the shared connection URL
    url = os.environ.get('CLOUDAMQP_URL')
    params = pika.URLParameters(url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
        
        # 2. "Join" the queue
        # Note: We 'declare' it again just to be safe. 
        # If it already exists (from Node), RabbitMQ just says "Yup, I know that one."
    channel.queue_declare(queue='trip_signals', durable=True)
        
        # 3. Blocking wait for the Node.js message
    print("Waiting for Node.js to send 'start_of_trip'...")
        
        # This is the "Sentry" loop we discussed
    for method_frame, properties, body in channel.consume('trip_signals', auto_ack=True):
        if "start_of_trip" in body.decode():
            print("Signal received! Starting MongoDB fetch...")
            current_trip_id = body.decode().split(',')[1]
            break # Exit this loop to start your LSTM logic
  

def enqueue(queue, items):
    for item in items:
        queue.append(item)


def dequeue(queue):
    for i in range(stride):
        queue.pop(0)


def fetch_items(collection, number_of_items):
   global last_timestamp
   while True:
    query = {"tripID": current_trip_id, "timestamp": {"$gt": last_timestamp}}
    # Efficiently check the count without pulling the actual data
    if collection.count_documents(query) >= number_of_items:
        # Now that we know 30+ or 5+ exist, fetch them
        last_group_of_items = list(collection.find(query).sort("timestamp", 1).limit(number_of_items))
        last_timestamp = last_group_of_items[-1]['timestamp']
        return last_group_of_items
    else:
        time.sleep(0.5)
        
def convert_into_tensor(queue_of_events):
    data = np.array([
    [doc[col] for col in ["yaw" ,"speed", "accel_pedal"]]
    for doc in queue_of_events
        ])
    return data
    
def squish_into_average(queue_of_events):
    # The parameters that we care about for classification
    speed = 0
    average_acceleration = 0 # from accelerometer, simple average. Find the orientation and direction in which the car is moving and use the acceleration
    yaw_rate = 0 # from gyroscope readings, in the longitudinal direction. Refers to yaw rate.
    acceleration_y = 0 #from gyroscope readings, in the lateral direction
    acceleration_array = []
    acceleration_frequency = 0 # use the queue of events to calculate the number of 0-crossings, use that to find the Hz value.
    jerk = 0 # peak derivative of acceleration between two readings
    # take the average of each attribute from feature_columns. By definition, we have a moving average, by using sliding windows.
    #First, sum them up:
    for doc in queue_of_events:
      speed+=doc["speed"]
      momentary_acceleration = doc["acceleration_x"]
      average_acceleration += momentary_acceleration
      # code for frequency tracking; check for change in sign
      acceleration_array.append(momentary_acceleration)
      if len(acceleration_array) > 1 and acceleration_array[-1] * acceleration_array[-2] < 0:
        acceleration_frequency +=1
      yaw_rate += doc ["yaw_rate"]
      acceleration_y += doc["acceleration_y"]
      if len(acceleration_array) >1:
        potential_peak_jerk = (acceleration_array[-1] - acceleration_array[-2])/0.5 # 0.5 seconds approximately between each reading.
        if potential_peak_jerk > jerk:
          jerk = potential_peak_jerk
      lane_deviation_direction = doc["lane_offset_direction"]

    # Divide the summed values by window_size after completion of for loop
    speed = speed/window_size
    average_acceleration = average_acceleration/window_size
    acceleration_frequency = acceleration_frequency/10 #10 seconds
    yaw_rate = yaw_rate/window_size
    acceleration_y = acceleration_y/window_size

    return speed, average_acceleration, acceleration_frequency, yaw_rate, acceleration_y, jerk, lane_deviation_direction


def classifier(speed, average_acceleration, acceleration_frequency, yaw_rate, acceleration_y, jerk, lane_deviation_direction):
    #Sudden Braking
    if abs(average_acceleration) > 3.0:
        return "Start slowing down early."
    #Sharp Turning
    if abs(acceleration_y) > 3.7 or abs(yaw_rate*speed) > 3.7:
        return "Be careful before turning."
    #Inconsistent Acceleration
    if (jerk > 4 and acceleration_frequency < 0.3) or jerk > 9:
        return "Gradually speed up or slow down early."
    # Lane deviation
    if(lane_deviation_direction == "left"):
        return "Adjust to the right to stay centred in the lane."
    elif(lane_deviation_direction == "right"):
        return "Adjust to the left to stay centred in the lane."

#Wait for start of trip and get Trip ID
message_dyno()
#Connect to DB and fetch last window_size JSON docs. 
sensorData = connect_to_DB()
queue_of_events = fetch_items(sensorData, window_size)
# Convert this into a tensor, X_test, to feed into the AI model.
data = convert_into_tensor(queue_of_events)
# Normalize data before putting into the model. Define the file path where the scaler is saved
input_scaler_filename = 'artifacts\\scalerX.pkl'
output_vel_scaler_filename = 'artifacts\\scaler_dv.pkl'
output_yaw_scaler_filename = 'artifacts\\scaler_dyaw.pkl'

# Load the scaler from the file
loaded_input_scaler = joblib.load(input_scaler_filename)
loaded_output_vel_scaler = joblib.load(output_vel_scaler_filename)
loaded_output_yaw_scaler = joblib.load(output_yaw_scaler_filename)

data_scaled = loaded_input_scaler.transform(data)

X_test = np.expand_dims(data_scaled, axis=0)
#Load the AI model from artifacts
loaded_model = keras.saving.load_model("artifacts/trained_lstm_model.keras")
# Put X_test tensor into the AI model and receive the predicted_events queue. Add batch_size as a dimension to make it 3D, matches the X_train and y_train.
# Batch size is 1 because we only have 1 window.
predictions_queue = loaded_model.predict(X_test, batch_size=1)
#Scale the predictions_queue
scaled_predictions_queue = unscale_joint_preds(predictions_queue, loaded_output_yaw_scaler, loaded_output_vel_scaler)
# Convert the predictions queue into usable values for vel and yaw
yaw_predicted, vel_predicted = reconstruct(scaled_predictions_queue, queue_of_events)
# classify real data
squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk, 
squished_lane_deviation_direction=squish_into_average(queue_of_events)
verdict = classifier(squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk, 
                     squished_lane_deviation_direction)
cooldown(verdict)
increment_persistent_data(dashboard_recommendation_value)
# classify AI predicted data and send to the dashboard display 

# Then, dequeue and then enqueue fetch_item(collection)
while trip_ended == False:
    next_packets = fetch_items(sensorData, stride)
    dequeue(queue_of_events)
    enqueue(queue_of_events, next_packets)
    data = convert_into_tensor(queue_of_events)
    data_scaled = loaded_input_scaler.transform(data)
    X_test = np.expand_dims(data_scaled, axis=0)
    
    if queue_of_events[-1]["trip_ended"]==True:
        trip_ended=True
        break
    else:
        pass #add all the code
# Now create the persistent data object




