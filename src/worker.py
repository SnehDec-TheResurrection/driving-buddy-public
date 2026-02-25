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

window_size = 30 #editable parameter based on hardware sampling constraints. x Hz * 10 = window_size
stride = 5
feature_columns = ["speed", "acceleration_x", "acceleration_y", "accel_pedal", "yaw_rate"]
trip_ended = False
last_timestamp = 0
current_trip_id=0

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

    # Divide the summed values by window_size after completion of for loop
    speed = speed/window_size
    average_acceleration = average_acceleration/window_size
    acceleration_frequency = acceleration_frequency/10 #10 seconds
    yaw_rate = yaw_rate/window_size
    acceleration_y = acceleration_y/window_size

    return speed, average_acceleration, acceleration_frequency, yaw_rate, acceleration_y, jerk


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
data = np.array([
    [doc[col] for col in feature_columns]
    for doc in queue_of_events
])
# Normalize data before putting into the model. Define the file path where the scaler is saved
scaler_filename = 'artifacts\\scalerX.pkl'

# Load the scaler from the file
loaded_scaler = joblib.load(scaler_filename)

data_scaled = loaded_scaler.transform(data)


X_test = np.expand_dims(data_scaled, axis=0)
#Load the AI model from artifacts
loaded_model = keras.saving.load_model("artifacts/trained_lstm_model.keras")
# Put X_test tensor into the AI model and receive the predicted_events queue. Add batch_size as a dimension to make it 3D, matches the X_train and y_train.
# Batch size is 1 because we only have 1 window.
predictions_queue = loaded_model.predict(X_test, batch_size=32)
# Convert the predictions queue into usable values for vel and yaw
# Scale using the sklearn scaler
# classify real data
# classify AI predicted data
# Then, dequeue and then enqueue fetch_item(collection)
while trip_ended == False:
    next_packets = fetch_items(sensorData, stride)
    dequeue(queue_of_events)
    enqueue(queue_of_events, next_packets)
    if queue_of_events[-1]["trip_ended"]==True:
        trip_ended=True
        break
    else:
        pass #add all the code
# rinse and repeat the above two steps throughout the drive. 
# while loop for polling, can check mongoDB document ID of all items to verify if updates are ready to be propagated.



