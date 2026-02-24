import numpy as np
import pandas as pd
import glob

import os
import joblib

from tensorflow.keras import Input
from tensorflow.keras.models import Sequential
from tensorflow.keras.losses import Huber
from tensorflow.keras.metrics import RootMeanSquaredError, MeanAbsoluteError
from tensorflow.keras.layers import Dense, Input, LSTM
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.optimizers import Adam

from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

# joint model! 

# =============================================================================
# 1) load & preprocess drive sessions
# =============================================================================

# load each driving session as a dataframe in a list of df's

files = glob.glob("drivedata/*.csv")

drives = []

GAP_SEC = 2.0          # break a segment if raw gap > 2s

for file in files:
    df = pd.read_csv(file)
    df.drop(columns=['unix_ts'], inplace=True) # take out unix column
    df.drop(columns=['steer'], inplace=True) # take out steer column
    # df.drop(columns=['accel_pedal'], inplace=True) # take out accel_pedal column
    # set timestamp as an index
    df['iso_timestamp'] = pd.to_datetime(df['iso_timestamp'], format='%Y-%m-%dT%H:%M:%S.%fZ', utc=True)
    df = df.set_index('iso_timestamp')
    # print(df.index.is_monotonic_increasing) # == True; we do not need to sort by index

    # segment by big timestamp gaps to avoid resampling across long missing spaces
    gaps = df.index.to_series().diff().dt.total_seconds()
    seg_id = (gaps > GAP_SEC).cumsum()

    # enforce order to be safe
    df = df[['yaw', 'vel', 'accel_pedal']]

    for _, seg in df.groupby(seg_id):
        # aggregate to fit a ~5Hz rate (200ms)
        df_5hz = seg.resample('200ms').mean().ffill().bfill()
        
        drives.append(df_5hz)

# =============================================================================
# 2) windowing (input_len=30 → predict next output_len=30)
# =============================================================================

# creating arrays of each set of input and output windows per driving session
# stride=5 controls overlap to reduce near-duplicate training samples
def create_windows(df, input_len=30, output_len=30, stride=5):
    X = []
    y = []

    data = df.values
    data_y = df[['yaw', 'vel']].values  # only predict yaw and vel, not accel_pedal
    max_i = len(data) - input_len - output_len + 1

    for i in range(0, max_i, stride):
        X.append(data[i:i + input_len])
        y.append(data_y[i + input_len:i + input_len + output_len])

    return np.array(X), np.array(y)

# with data length of 90:
# x: 1-30 y: 30-60
# x: 6-35 y: 35-65    (stride=5)
# ...

X_all = []
y_all = []

for drive in drives:
    X, y = create_windows(drive)
    X_all.append(X)
    y_all.append(y)

X_all = np.concatenate(X_all, axis=0)
y_all = np.concatenate(y_all, axis=0)

# =============================================================================
# 3) train/test split
# =============================================================================

# split train and test data (using classic 80/20 split)
X_train, X_test, y_train, y_test = train_test_split(
    X_all, y_all, test_size=0.2, shuffle=False  # shuffle=False is important for time series
)

# =============================================================================
# 4) scaling + targets
# =============================================================================

# column indices in the 2-signal dataframe (yaw, vel)
IDX_YAW = 0
IDX_VEL = 1

def scale_3d(arr_3d, scaler):
    # applies a scikit-learn scaler (fit on 2D) to a (N, T, F) time-series tensor.
    N, T, F = arr_3d.shape
    arr_2d = arr_3d.reshape(-1, F)
    arr_2d = scaler.transform(arr_2d)
    return arr_2d.reshape(N, T, F)

def make_joint_targets_raw(X_raw, y_raw):
    # build joint targets in RAW/original units.
    #
    # inputs:
    # X_raw, y_raw: (N, H, 3) where H=30 and features are [yaw, vel, accel_pedal]
    # these are absolute values in original units.
    #
    # outputs:
    # y_joint_raw: (N, H, 2) where channels are:
    # 0: dyaw_to_last  (yaw[t+k] - yaw_last_input) for each horizon step k
    # 1: dv_to_last   (vel[t+k] - vel_last_input) for each horizon step k
    # plus last inputs (yaw_last, vel_last) in raw units to reconstruct ABS later.

    N, H, F = y_raw.shape
    assert F == 2

    # last values in the input window (used for reconstruction)
    yaw_last   = X_raw[:, -1, IDX_YAW].reshape(-1, 1)
    vel_last   = X_raw[:, -1, IDX_VEL].reshape(-1, 1)

    y_joint = np.zeros_like(y_raw)

    # (1) velocity target: dv_to_last (future vel relative to the last input vel)
    y_joint[:, :, IDX_VEL] = y_raw[:, :, IDX_VEL] - vel_last

    # (2) yaw target: dv_to_last (future yaw relative to the last input yaw)
    y_joint[:, :, IDX_YAW] = y_raw[:, :, IDX_YAW] - yaw_last

    return y_joint, yaw_last, vel_last

# build RAW joint targets (still in original units, but in delta space)
y_train_joint_raw, yaw_last_train_raw, vel_last_train_raw = make_joint_targets_raw(X_train, y_train)
y_test_joint_raw,  yaw_last_test_raw,  vel_last_test_raw  = make_joint_targets_raw(X_test,  y_test)

# scale inputs X (single scaler across the 3 input channels)
scalerX = StandardScaler()
scalerX.fit(X_train.reshape(-1, 3))
X_train_s = scale_3d(X_train, scalerX)
X_test_s  = scale_3d(X_test,  scalerX)

# scale targets per-channel (avoids mixing dyaw/dv/dacc statistics)
scaler_dyaw = StandardScaler()
scaler_dv   = StandardScaler()

scaler_dyaw.fit(y_train_joint_raw[:, :, IDX_YAW].reshape(-1, 1))
scaler_dv.fit(  y_train_joint_raw[:, :, IDX_VEL].reshape(-1, 1))

def scale_joint_targets(y_joint_raw):
    # applies the per-target scalers and returns (N,H,2) float32
    y_s = np.zeros_like(y_joint_raw, dtype=np.float32)
    y_s[:, :, IDX_YAW]   = scaler_dyaw.transform(y_joint_raw[:, :, IDX_YAW].reshape(-1, 1)).reshape(y_joint_raw.shape[0], y_joint_raw.shape[1])
    y_s[:, :, IDX_VEL]   = scaler_dv.transform(  y_joint_raw[:, :, IDX_VEL].reshape(-1, 1)).reshape(y_joint_raw.shape[0], y_joint_raw.shape[1])
    return y_s

y_train_joint_s = scale_joint_targets(y_train_joint_raw)
y_test_joint_s  = scale_joint_targets(y_test_joint_raw)

# =============================================================================
# 5) model definition + training
# =============================================================================
num_features = 3

model_joint = Sequential([
    Input(shape=(30, num_features)),
    LSTM(64, return_sequences=True),
    LSTM(64, return_sequences=True),
    Dense(32, activation="relu"),
    Dense(2)  # outputs: [yaw_delta_to_last, vel_delta_to_last]
])

early_stopping = EarlyStopping(
    monitor="val_loss",
    patience=5,
    min_delta=1e-4,
    restore_best_weights=True
)

model_joint.compile(
    optimizer=Adam(learning_rate=1e-3, clipnorm=1.0),
    loss=Huber(delta=5.0),
    metrics=[RootMeanSquaredError(name="rmse"), MeanAbsoluteError(name="mae")]
)

history_joint = model_joint.fit(
    X_train_s, y_train_joint_s,
    epochs=20,
    batch_size=32,
    validation_data=(X_test_s, y_test_joint_s),
    callbacks=[early_stopping]
)

# ------ save model + scalers ------
ARTIFACT_DIR = "artifacts"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

# save keras model (architecture + weights)
MODEL_PATH = os.path.join(ARTIFACT_DIR, "trained_lstm_model.keras")
model_joint.save(MODEL_PATH)

# save scalers needed for inference
joblib.dump(scalerX, os.path.join(ARTIFACT_DIR, "scalerX.pkl"))
joblib.dump(scaler_dyaw, os.path.join(ARTIFACT_DIR, "scaler_dyaw.pkl"))
joblib.dump(scaler_dv, os.path.join(ARTIFACT_DIR, "scaler_dv.pkl"))

print(f"\nSaved model to: {MODEL_PATH}")
print(f"Saved scalers to: {ARTIFACT_DIR}/scalerX.pkl, scaler_dyaw.pkl, scaler_dv.pkl")

