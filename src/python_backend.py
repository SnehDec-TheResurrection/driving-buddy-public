import sys
import os
from pymongo import MongoClient

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
    .sort("timestamp", -1)  # newest first
    .limit(30))
  return last_thirty_items

def fetch_item(collection): 
  last_item_singular = collection.find_one(sort=[("timestamp", -1)])
  return last_item_singular
  
sensorData = connect_to_DB()
queue_of_events = fetch_items(sensorData)
# pass queue_of_events into the AI model. 
# Then, dequeue and then enqueue fetch_item(collection). 
# rinse and repeat the above two steps throughout the drive. 
# while loop for polling, can sleep to ensure 3 Hz polling rate from the DB.

# read CSV row from stdin
csv_row = sys.stdin.read().strip()
print(csv_row)

