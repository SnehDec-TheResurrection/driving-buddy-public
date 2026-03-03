import sys
import os
import numpy as np
from pymongo import MongoClient
import sklearn as sk
import joblib
import requests
import time 
import pika
from datetime import datetime, date, timedelta
import onnxruntime as ort

#Load the AI model from artifacts using ort
#loaded_model = keras.saving.load_model(os.path.join("artifacts", "trained_lstm_model.keras")
onnx_model_path = os.path.join("src", "artifacts", "trained_lstm_model.onnx")
ort_session = ort.InferenceSession(onnx_model_path)
input_name = ort_session.get_inputs()[0].name


# Normalize data before putting into the model. Define the file path where the scaler is saved
input_scaler_filename = os.path.join("src", "artifacts","scalerX.pkl")
output_vel_scaler_filename = os.path.join("src", "artifacts", "scaler_dv.pkl")
output_yaw_scaler_filename = os.path.join("src", "artifacts", "scaler_dyaw.pkl")

# Load the scaler from the file
loaded_input_scaler = joblib.load(input_scaler_filename)
loaded_output_vel_scaler = joblib.load(output_vel_scaler_filename)
loaded_output_yaw_scaler = joblib.load(output_yaw_scaler_filename)

window_size = 30 #editable parameter based on hardware sampling constraints. x Hz * 10 = window_size
stride = 5
feature_columns = ["speed", "acceleration_x", "acceleration_y", "accel_pedal", "yaw_rate"]

COOLDOWN = 5 # Adjusted for seconds (e.g., 5000ms = 5s)
FETCH_COOLDOWN = 45
FETCH_SECONDS = timedelta(seconds=FETCH_COOLDOWN)  
COOLDOWN_SECONDS = timedelta(seconds=COOLDOWN)  

IDX_YAW = 0
IDX_VEL = 1

def predict_with_onnx(X_test_scaled):
    """Replaces loaded_model.predict()"""
    # Ensure data is float32 for ONNX
    onnx_inputs = {input_name: X_test_scaled.astype(np.float32)}
    onnx_output = ort_session.run(None, onnx_inputs)
    return onnx_output[0]

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
    vel_pred_abs = queue_of_events[-1]["speed"] + y_pred_joint_raw[:, :, IDX_VEL]
    return yaw_pred_abs, vel_pred_abs

def cooldown(prediction_text, rec_string, send_out_duplicate):
    global last_recommendation_time
    current_time = datetime.now()
    time_difference = current_time - last_recommendation_time
    verdict_true = 0
    # Check if it's the same message AND within the cooldown period
    if prediction_text and prediction_text.split(",")[0] == rec_string and time_difference < COOLDOWN_SECONDS:
        send_out_duplicate = "Duplicate"
        verdict_true = 0
    elif prediction_text:
        # Update state and trigger recommendation
        rec_string = prediction_text.split(",")[0]
        send_out_duplicate = prediction_text.split(",")[0]
        last_recommendation_time = current_time
        verdict_true = 1
    return rec_string, verdict_true, send_out_duplicate
        
def increment_persistent_data(prediction_text, verdict_true):
    global sudden_braking_instances, sharp_turning_instances, inconsistent_speed_instances, lane_deviation_instances
    prediction_packet = prediction_text.split(",")
    if verdict_true:
        if prediction_packet[0] == "Start slowing down early." :
             sudden_braking_instances.append(prediction_packet[1:])
        elif prediction_packet[0] == "Be careful before turning.":
            sharp_turning_instances.append(prediction_packet[1:])
        elif prediction_packet[0] == "Gradually speed up or slow down early.":
            inconsistent_speed_instances.append(prediction_packet[1:])
        elif prediction_packet[0] == "Adjust to the left to stay centred in the lane." or prediction_packet[0]=="Adjust to the right to stay centred in the lane.":
            lane_deviation_instances.append(prediction_packet[1:])

def connect_to_DB():
    mongo_url = os.getenv("MONGO_URL")
    client = MongoClient(mongo_url)
    db = client["DrivingBuddy"]
    return db

def set_up_mq():
    url = os.environ.get('CLOUDAMQP_URL')
    params = pika.URLParameters(url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
        
        # 2. "Join" the queue
    channel.queue_declare(queue='trip_signals', durable=True)
    channel.queue_declare(queue='predictions', durable=True)    
    return connection, channel

def send_message_to_node(channel, message):
        # Send the message
        channel.basic_publish(
            exchange='',
            routing_key='predictions',
            body=str(message))
    
def message_dyno(channel):
    global current_trip_id
    while True:
         method_frame, header_frame, body = channel.basic_get(queue='trip_signals', auto_ack=False)
         if body and "start_of_trip" in body.decode():
            channel.basic_ack(delivery_tag=method_frame.delivery_tag)
            print(body.decode())
            print("Signal received! Starting MongoDB fetch...")
            current_trip_id = body.decode().split(',')[1]
            print(current_trip_id)
            break # Exit this loop tc begin ride processing
         else:
            time.sleep(0.5)
            
def enqueue(queue, items):
    for item in items:
        queue.append(item)


def dequeue(queue):
    for i in range(stride):
        queue.pop(0)


def fetch_items(collection, number_of_items):
   global last_timestamp, current_trip_id, channel
   current_timestamp = datetime.now() 
   while True:
    query = {"tripID": current_trip_id, "timestamp": {"$gt": last_timestamp}}
    print(last_timestamp)
    # Efficiently check the count without pulling the actual data
    if collection.count_documents(query) >= number_of_items:
        # Now that we know 30+ or 5+ exist, fetch them
        last_group_of_items = list(collection.find(query).sort("timestamp", 1).limit(number_of_items))
        last_timestamp = last_group_of_items[-1]['timestamp']
        return last_group_of_items
    else:
        if datetime.now() - current_timestamp > FETCH_SECONDS:
            print("No MongoDB data. Trip ended or disconnected.")
            return -1
        time.sleep(0.5)
        
def convert_into_tensor(queue_of_events):
    data = np.array([
    [doc[col] for col in ["yaw_rate" ,"speed", "accel_pedal"]]
    for doc in queue_of_events
        ])
    return data
    
def squish_into_average(series):
    global queue_of_events
    # The parameters that we care about for classification
    speed = 0
    average_acceleration = 0 # from accelerometer, simple average. Find the orientation and direction in which the car is moving and use the acceleration
    yaw_rate = 0 # from gyroscope readings, in the longitudinal direction. Refers to yaw rate.
    acceleration_y = 0 #from gyroscope readings, in the lateral direction
    acceleration_array = []
    acceleration_frequency = 0 # use the queue of events to calculate the number of 0-crossings, use that to find the Hz value.
    jerk = 0 # peak derivative of acceleration between two readings
    lane_deviation_direction = "centre"
    # take the average of each attribute from feature_columns. By definition, we have a moving average, by using sliding windows.
    #First, sum them up:
    for doc in series:
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
      if (doc["lane_offset"] != 0):
          lane_deviation_direction = doc["lane_offset_direction"]

    # Divide the summed values by window_size after completion of for loop
    speed = speed/window_size
    average_acceleration = average_acceleration/window_size
    acceleration_frequency = acceleration_frequency/15 #10 seconds
    yaw_rate = yaw_rate/window_size
    acceleration_y = acceleration_y/window_size
    current_average_timestamp = str(queue_of_events[window_size//2]["timestamp"]) # take from middle of queue
    current_average_gps_latitude = str(queue_of_events[window_size//2]["gps_latitude"]) # take from middle of queue
    current_average_gps_longitude = str(queue_of_events[window_size//2]["gps_longitude"]) # take from middle of queue 

    return speed, average_acceleration, acceleration_frequency, yaw_rate, acceleration_y, jerk, lane_deviation_direction, current_average_timestamp, current_average_gps_latitude, current_average_gps_longitude


def classifier(speed, average_acceleration, acceleration_frequency, yaw_rate, acceleration_y, jerk, lane_deviation_direction,current_average_timestamp, current_average_gps_latitude, current_average_gps_longitude):
    #Inconsistent Acceleration
    if (jerk > 2 and acceleration_frequency < 0.3) or jerk > 3:
        return f"Gradually speed up or slow down early.,{current_average_gps_latitude},{current_average_gps_longitude},{current_average_timestamp}" 
    # Lane deviation
    if(lane_deviation_direction == "left"):
        return f"Adjust to the right to stay centred in the lane.,{current_average_gps_latitude},{current_average_gps_longitude},{current_average_timestamp}" 
    elif(lane_deviation_direction == "right"):
        return f"Adjust to the left to stay centred in the lane.,{current_average_gps_latitude},{current_average_gps_longitude},{current_average_timestamp}" 
    #Sharp Turning
    if abs(acceleration_y) > 1 or abs((yaw_rate*3.14/180)*(speed/3.6)) > 1:
        return f"Be careful before turning.,{current_average_gps_latitude},{current_average_gps_longitude},{current_average_timestamp}"
     #Sudden Braking
    if abs(average_acceleration) > 1.0:
        return f"Start slowing down early.,{current_average_gps_latitude},{current_average_gps_longitude},{current_average_timestamp}" 

#Set up the message queue connection
connection, channel = set_up_mq()
#Connect to DB and fetch last window_size JSON docs.
db=connect_to_DB()

while True:

    trip_ended = False
    last_timestamp = datetime.now() - timedelta(hours=6)
    current_trip_id=0
    inconsistent_speed_instances = []
    sudden_braking_instances = []
    sharp_turning_instances = []
    lane_deviation_instances = [] 
    
    
    last_recommendation_time = datetime.now()
    dashboard_recommendation_value = ""
    dashboard_recommendation_value_AI = ""
    send_out_duplicate = ""
    send_out_duplicate_AI = ""
    #test_strings = ["Hello Fola", "Hello Maya", "Hello ESP32", "Hello Sneha", "Hello Vic", "Hello Keya", "Hello Avril", "Hello Lavigne", "Hello Heroku", "Hello Yash."]
    #for test_string in test_strings:
      #  send_message_to_node(channel, test_string)
       # time.sleep(30)
    #Wait for start of trip and get Trip ID
    message_dyno(channel)
    sensorData = db["sensordatas"]
    queue_of_events = fetch_items(sensorData, window_size)
    if queue_of_events == -1:
        time.sleep(10)
        queue_of_events = fetch_items(sensorData, window_size)
    if queue_of_events == -1:
        trip_ended = True
    recommendation_lag = datetime.now()
    if trip_ended == False:
        start_of_trip_timestamp = queue_of_events[0]["timestamp"]
        start_of_trip_location = [queue_of_events[0]["gps_latitude"], queue_of_events[0]["gps_longitude"]]
        print(start_of_trip_location)
        # Convert this into a tensor, X_test, to feed into the AI model.
        data = convert_into_tensor(queue_of_events)
        
        data_scaled = loaded_input_scaler.transform(data)
        
        X_test = np.expand_dims(data_scaled, axis=0)
        # Put X_test tensor into the AI model and receive the predicted_events queue. Add batch_size as a dimension to make it 3D, matches the X_train and y_train.
        # Batch size is 1 because we only have 1 window.
        predictions_queue = predict_with_onnx(X_test)
        #Scale the predictions_queue
        scaled_predictions_queue = unscale_joint_preds(predictions_queue, loaded_output_yaw_scaler, loaded_output_vel_scaler)
        # Convert the predictions queue into usable values for vel and yaw
        yaw_predicted, vel_predicted = reconstruct(scaled_predictions_queue, queue_of_events)
        # Create list of dictionaries for AI predictions
        AI_pred_list = []
        for i in range(window_size):
            # Get velocity of THIS step
            curr_v = float(vel_predicted[0][i])
            # Get velocity of PREVIOUS step (or the last real speed if i=0)
            prev_v = float(vel_predicted[0][i-1]) if i > 0 else queue_of_events[-1]["speed"]
            entry = {
                "speed": float(vel_predicted[0][i]),
                "acceleration_x": float((curr_v-prev_v)/0.5),
                "yaw_rate": float(yaw_predicted[0][i]), 
                "acceleration_y": 0.0,
                "lane_offset": 0,
                "lane_offset_direction": "centre",
                "jerk": 0.0
            }
            AI_pred_list.append(entry)
            AI_pred_list.append(entry)
            predictions_data = db["predictions"]
            predictions_data.insert_one(entry)
        # classify AI predicted data and send to the dashboard display 
        squished_AI_speed, squished_AI_average_acceleration, squished_AI_acceleration_frequency, squished_AI_yaw_rate, squished_AI_acceleration_y, squished_AI_jerk, squished_AI_lane_deviation_direction, squished_AI_timestamp, squished_AI_lat, squished_AI_long=squish_into_average(AI_pred_list)
        verdict_AI = classifier(squished_AI_speed, squished_AI_average_acceleration, squished_AI_acceleration_frequency, squished_AI_yaw_rate, squished_AI_acceleration_y, squished_AI_jerk, squished_AI_lane_deviation_direction, squished_AI_timestamp, squished_AI_lat, squished_AI_long)
        dashboard_recommendation_value_AI, verdict_true, send_out_duplicate_AI = cooldown(verdict_AI, dashboard_recommendation_value_AI, send_out_duplicate_AI)
        print(send_out_duplicate_AI)
        timedelta = datetime.now() - recommendation_lag
        print("TIme lag: {str(timedelta)}")
        if send_out_duplicate_AI == "Start slowing down early." or send_out_duplicate_AI == "Be careful before turning.":
            #send the recommendation via message queue
            send_message_to_node(channel, send_out_duplicate_AI)
          # classify real data
        squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk, squished_lane_deviation_direction, squished_timestamp, squished_gps_latitude, squished_gps_longitude=squish_into_average(queue_of_events)
        verdict = classifier(squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk, squished_lane_deviation_direction, squished_timestamp, squished_gps_latitude, squished_gps_longitude)
        dashboard_recommendation_value, verdict_true, send_out_duplicate = cooldown(verdict, dashboard_recommendation_value, send_out_duplicate)
        increment_persistent_data(verdict, verdict_true)
        if send_out_duplicate == "Gradually speed up or slow down early." or send_out_duplicate == "Adjust to the right to stay centred in the lane." or send_out_duplicate == "Adjust to the left to stay centred in the lane.":
            send_message_to_node(channel, send_out_duplicate)
    while trip_ended == False:
        next_packets = fetch_items(sensorData, stride)
        if next_packets == -1:
            time.sleep(10)
            next_packets = fetch_items(sensorData, window_size)
        if next_packets == -1:
            trip_ended = True
            break
        recommendation_lag = datetime.now()
        dequeue(queue_of_events)
        enqueue(queue_of_events, next_packets)
        # Convert this into a tensor, X_test, to feed into the AI model.
        data = convert_into_tensor(queue_of_events)
        data_scaled = loaded_input_scaler.transform(data)
        X_test = np.expand_dims(data_scaled, axis=0)
        predictions_queue = predict_with_onnx(X_test)
        #Scale the predictions_queue
        scaled_predictions_queue = unscale_joint_preds(predictions_queue, loaded_output_yaw_scaler, loaded_output_vel_scaler)
        # Convert the predictions queue into usable values for vel and yaw
        yaw_predicted, vel_predicted = reconstruct(scaled_predictions_queue, queue_of_events)
        # Create list of dictionaries for AI predictions
        AI_pred_list = []
        for i in range(window_size):
            # Get velocity of THIS step
            curr_v = float(vel_predicted[0][i])
            # Get velocity of PREVIOUS step (or the last real speed if i=0)
            prev_v = float(vel_predicted[0][i-1]) if i > 0 else queue_of_events[-1]["speed"]
            
            entry = {
                "speed": float(vel_predicted[0][i]),
                "acceleration_x": float((curr_v-prev_v)/0.5),
                "yaw_rate": float(yaw_predicted[0][i]), 
                "acceleration_y": 0.0,
                "lane_offset":0,
                "lane_offset_direction": "centre",
                "timestamp": queue_of_events[-1]["timestamp"], 
                "gps_latitude": queue_of_events[-1]["gps_latitude"],
                "gps_longitude": queue_of_events[-1]["gps_longitude"],
                "jerk": 0.0
            }
            AI_pred_list.append(entry)
            predictions_data = db["predictions"]
            predictions_data.insert_one(entry)
        # classify AI predicted data and send to the dashboard display 
        squished_AI_speed, squished_AI_average_acceleration, squished_AI_acceleration_frequency, squished_AI_yaw_rate, squished_AI_acceleration_y, squished_AI_jerk,squished_AI_lane_deviation_direction, squished_AI_time, squished_AI_lat, squished_AI_long=squish_into_average(AI_pred_list)
        verdict_AI = classifier(squished_AI_speed, squished_AI_average_acceleration, squished_AI_acceleration_frequency, squished_AI_yaw_rate, squished_AI_acceleration_y, squished_AI_jerk, squished_AI_lane_deviation_direction, squished_AI_time, squished_AI_lat, squished_AI_long)
        dashboard_recommendation_value_AI, verdict_true, send_out_duplicate_AI = cooldown(verdict_AI, dashboard_recommendation_value_AI, send_out_duplicate_AI)
        print(send_out_duplicate_AI)
        if send_out_duplicate_AI == "Start slowing down early." or send_out_duplicate_AI == "Be careful before turning.":
            #send the recommendation via message queue
            send_message_to_node(channel, send_out_duplicate_AI)
        timedelta = datetime.now() - recommendation_lag
        print("TIme lag: {str(timedelta)}")
        # classify real data
        squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk,squished_lane_deviation_direction, squished_time, squished_lat, squished_long=squish_into_average(queue_of_events)
        verdict = classifier(squished_speed, squished_average_acceleration, squished_acceleration_frequency, squished_yaw_rate, squished_acceleration_y, squished_jerk, squished_lane_deviation_direction, squished_time, squished_lat, squished_long)
        dashboard_recommendation_value, verdict_true, send_out_duplicate = cooldown(verdict, dashboard_recommendation_value, send_out_duplicate)
        increment_persistent_data(verdict, verdict_true)
        if send_out_duplicate == "Gradually speed up or slow down early." or send_out_duplicate == "Adjust to the right to stay centred in the lane." or send_out_duplicate == "Adjust to the left to stay centred in the lane.":
            send_message_to_node(channel, send_out_duplicate)
        if queue_of_events[-1]["trip_ended"]==True:
            trip_ended=True
            break
    if queue_of_events != -1:
        print(queue_of_events)
        # Now create the persistent data object
        persistent_data_doc = {
                "tripID":current_trip_id,
                "userID":queue_of_events[-1]["userID"],
                "start_of_trip_timestamp":start_of_trip_timestamp,
                "end_of_trip_timestamp":queue_of_events[-1]["timestamp"],
                "start_of_trip_location": start_of_trip_location,
                "end_of_trip_location":[queue_of_events[-1]["gps_latitude"], queue_of_events[-1]["gps_longitude"]],
                "inconsistent_speed": inconsistent_speed_instances,
                "hard_braking": sudden_braking_instances,
                "sharp_turning":  sharp_turning_instances,
                "lane_deviation": lane_deviation_instances
        }
        persistent_data = db["persistent_summary_data"]
        persistent_data.insert_one(persistent_data_doc)
        
