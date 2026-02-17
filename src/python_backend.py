import sys
import os
import numpy as np
import tensorflow as tf
from pymongo import MongoClient

window_size = 30 #editable parameter based on hardware sampling constraints. x Hz * 10 = window_size

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

#Connect to DB and fetch last window_size JSON docs. 
sensorData = connect_to_DB()
queue_of_events = fetch_items(sensorData)
# Convert this into a tensor, X_test, to feed into the AI model. 
feature_columns = ["timestamp", "speed", "acceleration"]
data = np.array([
    [doc[col] for col in feature_columns]
    for doc in queue_of_events
])
# Normalize data before putting into the model
data_scaled = scaler.transform(data)
#Add batch_size as a dimension to make it 3D, matches the X_train and y_train. Batch size is 1 because we only have 1 window.
X_test = np.expand_dims(data_scaled, axis=0)
# Put X_test tensor into the AI model.
# Then, dequeue and then enqueue fetch_item(collection). 
# rinse and repeat the above two steps throughout the drive. 
# while loop for polling, can check mongoDB document ID of all items to verify if updates are ready to be propagated.

# read CSV row from stdin
csv_row = sys.stdin.read().strip()
print(csv_row)
print("\n")
print(f"And the queue contains: ${queue_of_events[0]}")

