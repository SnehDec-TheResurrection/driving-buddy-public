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

sensorData = connect_to_DB()

queue_of_events = [] # the size of this queue is currently 30 


# read CSV row from stdin
csv_row = sys.stdin.read().strip()
print(csv_row)

