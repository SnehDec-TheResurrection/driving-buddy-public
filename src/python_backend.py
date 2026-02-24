import sys
import os
import numpy as np
import tensorflow as tf
from pymongo import MongoClient

window_size = 30 #editable parameter based on hardware sampling constraints. x Hz * 10 = window_size
feature_columns = ["timestamp", "speed", "acceleration"] 

def connect_to_DB():
  mongo_url = os.getenv("MONGO_URL")
  client = MongoClient(mongo_url)
  db = client["DrivingBuddy"]
  collection = db["sensordatas"]
  return collection

def enqueue(queue, item):
  queue.append[item]

def dequeue(queue):
  queue.pop(0)

def fetch_items(collection):
  last_thirty_items = list(
    collection.find()
    .sort("timestamp", 1)  # oldest first so that LSTM gets events in proper order
    .limit(window_size))
  return last_thirty_items

def fetch_item(collection): 
  last_item_singular = collection.find_one(sort=[("timestamp", -1)])
  return last_item_singular

def calculate_yaw(queue_of_events): 
  # add code to calculate yaw from multiple angular acceleration readings. idk if hardware will be doing this calculation already.

def squish_into_average(queue_of_events):
  # The parameters that we care about for classification
  speed = 0
  average_acceleration = 0 # from accelerometer, simple average. Find the orientation and direction in which the car is moving and use the acceleration 
  angular_acceleration = 0 # from gyroscope readings, simple average
  acceleration_array = [] 
  acceleration_frequency = 0 # use the queue of events to calculate the number of 0-crossings, use that to find the Hz value.
  jerk = 0 # peak derivative of acceleration between two readings
  # take the average of each attribute from feature_columns. By definition, we have a moving average, by using sliding windows.
  #First, sum them up:
  for doc in queue_of_events: 
    speed+=doc[speed]
    momentary_acceleration = doc[acceleration]
    average_acceleration += momentary_acceleration
    # code for frequency tracking; check for change in sign
    acceleration_array.append(momentary_acceleration)
    if len(acceleration_array) > 1 and acceleration_array[-1] * acceleration_array[-2] < 0:
      acceleration_frequency +=1
    angular_acceleration += doc [angular_acceleration]
    if len(acceleration_array) >1: 
      potential_peak_jerk = (acceleration_array[-1] - acceleration_array[-2])/0.5 # 0.5 seconds approximately between each reading.
      if potential_peak_jerk > jerk: 
        jerk = potential_peak_jerk
  
  # Divide the summed values by window_size after completion of for loop
  speed = speed/window_size 
  average_acceleration = average_acceleration/window_size
  acceleration_frequency = acceleration_frequency/10 #10 seconds
  angular_acceleration = angular_acceleration/window_size

  return speed, average_acceleration, acceleration_frequency, angular_acceleration, jerk

def classifier(speed, average_acceleration, acceleration_frequency, angular_acceleration, jerk):
  #Sharp Turning
  if average_acceleration > 3.0 or average_acceleration < -3.0: 
    return "“Start slowing down early.”
  #Sharp Braking
  if angular_acceleration > 3.7 or angular_acceleration < 3.7:
    return “Be careful before turning.”
  #Inconsistent Acceleration
  if jerk > 5 and acceleration_frequency < 0.3: 
    return “Gradually speed up or slow down early.”
  
#Connect to DB and fetch last window_size JSON docs. 
sensorData = connect_to_DB()
queue_of_events = fetch_items(sensorData)
# Convert this into a tensor, X_test, to feed into the AI model. 
data = np.array([
    [doc[col] for col in feature_columns]
    for doc in queue_of_events
])
# Normalize data before putting into the model
data_scaled = scaler.transform(data)
#Add batch_size as a dimension to make it 3D, matches the X_train and y_train. Batch size is 1 because we only have 1 window.
X_test = np.expand_dims(data_scaled, axis=0)
# Put X_test tensor into the AI model and receive the predicted_events queue.
# Then, dequeue and then enqueue fetch_item(collection).
next_packet = fetch_item(sensorData)
dequeue(queue)
enqueue(queue, next_packet)
# rinse and repeat the above two steps throughout the drive. 
# while loop for polling, can check mongoDB document ID of all items to verify if updates are ready to be propagated.

# read CSV row from stdin
csv_row = sys.stdin.read().strip()
print(csv_row)
print("\n")
print(f"And the queue contains: ${queue_of_events[0]}")

