import numpy as np
import pandas as pd
import glob

import matplotlib.pyplot as plt

from tensorflow.keras import Input
from tensorflow import constant, square, reduce_mean, float32, reshape 
from tensorflow.keras.models import Sequential
from tensorflow.keras.losses import MeanSquaredError, Huber
from tensorflow.keras.metrics import RootMeanSquaredError, MeanAbsoluteError
from tensorflow.keras.layers import Dense, Input, LSTM
from tensorflow.keras.callbacks import ModelCheckpoint, EarlyStopping
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.models import load_model

from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

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
    # set timestamp as an index
    df['iso_timestamp'] = pd.to_datetime(df['iso_timestamp'], format='%Y-%m-%dT%H:%M:%S.%fZ', utc=True)
    df = df.set_index('iso_timestamp')
    # print(df.index.is_monotonic_increasing) # == True; we do not need to sort by index

    # segment by big timestamp gaps to avoid resampling across long missing spaces
    gaps = df.index.to_series().diff().dt.total_seconds()
    seg_id = (gaps > GAP_SEC).cumsum()

    for _, seg in df.groupby(seg_id):
        # aggregate to fit a ~5Hz rate (200ms)
        df_5hz = seg.resample('200ms').mean().ffill().bfill()
        
        drives.append(df_5hz)

# sanity check
print(drives[0])  # [15391 rows x 3 columns]

# =============================================================================
# 2) windowing (input_len=30 → predict next output_len=30)
# =============================================================================

# creating arrays of each set of input and output windows per driving session
# stride=5 controls overlap to reduce near-duplicate training samples
def create_windows(df, input_len=30, output_len=30, stride=5):
    X = []
    y = []

    data = df.values
    max_i = len(data) - input_len - output_len + 1

    for i in range(0, max_i, stride):
        X.append(data[i:i + input_len])
        y.append(data[i + input_len:i + input_len + output_len])

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

print("XALL", X_all, X_all.shape)  # (382680, 30, 3)
print("YALL", y_all, y_all.shape)  # (382680, 30, 3)

# =============================================================================
# 3) train/test split
# =============================================================================

# split train and test data (using classic 80/20 split)
X_train, X_test, y_train, y_test = train_test_split(
    X_all, y_all, test_size=0.2, shuffle=False  # shuffle=False is important for time series
)

# print(np.isnan(X_train).any())  # False!
# print(np.isnan(y_train).any())  # False!

# =============================================================================
# 4) scaling + targets
# =============================================================================

# column indices in the 3-signal dataframe (yaw, vel, accel_pedal)
IDX_YAW = 0
IDX_VEL = 1
IDX_ACCEL = 2

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
    # y_joint_raw: (N, H, 3) where channels are:
    # 0: dyaw_to_last (yaw[t+k] - yaw_last_input)
    # 1: dv_to_last   (vel[t+k] - vel_last_input) for each horizon step k
    # 2: dacc_step    (per-step accel delta)
    # plus last inputs (yaw_last, vel_last, accel_last) in raw units to reconstruct ABS later.

    N, H, F = y_raw.shape
    assert F == 3

    # last values in the input window (used for reconstruction)
    yaw_last   = X_raw[:, -1, IDX_YAW].reshape(-1, 1)
    vel_last   = X_raw[:, -1, IDX_VEL].reshape(-1, 1)
    accel_last = X_raw[:, -1, IDX_ACCEL].reshape(-1, 1)

    y_joint = np.zeros_like(y_raw)

    # (1) velocity target: dv_to_last (future vel relative to the last input vel)
    y_joint[:, :, IDX_VEL] = y_raw[:, :, IDX_VEL] - vel_last

    # (2) yaw target: dyaw_to_last
    y_joint[:, :, IDX_YAW] = y_raw[:, :, IDX_YAW] - yaw_last

    # (3) accel target: per-step dacc
    y_joint[:, 0, IDX_ACCEL]  = y_raw[:, 0, IDX_ACCEL] - accel_last[:, 0]
    y_joint[:, 1:, IDX_ACCEL] = y_raw[:, 1:, IDX_ACCEL] - y_raw[:, :-1, IDX_ACCEL]

    return y_joint, yaw_last, vel_last, accel_last

# build RAW joint targets (still in original units, but in delta space)
y_train_joint_raw, yaw_last_train_raw, vel_last_train_raw, accel_last_train_raw = make_joint_targets_raw(X_train, y_train)
y_test_joint_raw,  yaw_last_test_raw,  vel_last_test_raw,  accel_last_test_raw  = make_joint_targets_raw(X_test,  y_test)

# scale inputs X (single scaler across the 3 input channels)
scalerX = StandardScaler()
scalerX.fit(X_train.reshape(-1, 3))
X_train_s = scale_3d(X_train, scalerX)
X_test_s  = scale_3d(X_test,  scalerX)

# scale targets per-channel (avoids mixing dyaw/dv/dacc statistics)
scaler_dyaw = StandardScaler()
scaler_dv   = StandardScaler()
scaler_acc  = StandardScaler()

scaler_dyaw.fit(y_train_joint_raw[:, :, IDX_YAW].reshape(-1, 1))
scaler_dv.fit(  y_train_joint_raw[:, :, IDX_VEL].reshape(-1, 1))
scaler_acc.fit( y_train_joint_raw[:, :, IDX_ACCEL].reshape(-1, 1))

def scale_joint_targets(y_joint_raw):
    # applies the per-target scalers and returns (N,H,3) float32

    y_s = np.zeros_like(y_joint_raw, dtype=np.float32)
    y_s[:, :, IDX_YAW]   = scaler_dyaw.transform(y_joint_raw[:, :, IDX_YAW].reshape(-1, 1)).reshape(y_joint_raw.shape[0], y_joint_raw.shape[1])
    y_s[:, :, IDX_VEL]   = scaler_dv.transform(  y_joint_raw[:, :, IDX_VEL].reshape(-1, 1)).reshape(y_joint_raw.shape[0], y_joint_raw.shape[1])
    y_s[:, :, IDX_ACCEL] = scaler_acc.transform(y_joint_raw[:, :, IDX_ACCEL].reshape(-1, 1)).reshape(y_joint_raw.shape[0], y_joint_raw.shape[1])
    return y_s

y_train_joint_s = scale_joint_targets(y_train_joint_raw)
y_test_joint_s  = scale_joint_targets(y_test_joint_raw)

print("X_train_s", X_train_s.shape, "y_train_joint_s", y_train_joint_s.shape)
print("X_test_s ", X_test_s.shape,  "y_test_joint_s ", y_test_joint_s.shape)

# X_train_s (306144, 30, 3) y_train_joint_s (306144, 30, 3)
# X_test_s  (76536, 30, 3) y_test_joint_s  (76536, 30, 3)

# =============================================================================
# 5) model definition + training
# =============================================================================

num_features = 3

model_joint = Sequential([
    Input(shape=(30, num_features)),
    LSTM(64, return_sequences=True),
    LSTM(64, return_sequences=True),
    Dense(32, activation="relu"),
    Dense(3)  # outputs: [dyaw_step, dv_to_last, dacc_step] (scaled)
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

# =============================================================================
# 6) evaluation: unscale → reconstruct ABS predictions → compare vs persistence
# =============================================================================

def unscale_joint_preds(y_pred_s):
    # inverse-transform scaled predictions back to raw target units (still deltas).

    N, H, _ = y_pred_s.shape
    y_pred_raw = np.zeros_like(y_pred_s, dtype=np.float32)
    y_pred_raw[:, :, IDX_YAW]   = scaler_dyaw.inverse_transform(y_pred_s[:, :, IDX_YAW].reshape(-1, 1)).reshape(N, H)
    y_pred_raw[:, :, IDX_VEL]   = scaler_dv.inverse_transform(  y_pred_s[:, :, IDX_VEL].reshape(-1, 1)).reshape(N, H)
    y_pred_raw[:, :, IDX_ACCEL] = scaler_acc.inverse_transform(y_pred_s[:, :, IDX_ACCEL].reshape(-1, 1)).reshape(N, H)
    return y_pred_raw

def rmse(a, b):
    return float(np.sqrt(np.mean((a - b) ** 2)))

# predict scaled joint targets (deltas)
y_pred_joint_s = model_joint.predict(X_test_s, batch_size=256, verbose=0)
y_pred_joint_raw = unscale_joint_preds(y_pred_joint_s)   # (N,30,3) in raw delta units
y_true_joint_raw = y_test_joint_raw                      # (N,30,3) in raw delta units

# reconstruct ABS yaw/vel/accel predictions in original units
# yaw_abs = yaw_last + dyaw_to_last
yaw_pred_abs = yaw_last_test_raw + y_pred_joint_raw[:, :, IDX_YAW]
yaw_true_abs = y_test[:, :, IDX_YAW]

# vel_abs = vel_last + dv_to_last
vel_pred_abs = vel_last_test_raw + y_pred_joint_raw[:, :, IDX_VEL]
vel_true_abs = y_test[:, :, IDX_VEL]

# accel_abs = accel_last + cumsum(dacc_step)
acc_pred_abs = accel_last_test_raw + np.cumsum(y_pred_joint_raw[:, :, IDX_ACCEL], axis=1)
acc_true_abs = y_test[:, :, IDX_ACCEL]

# persistence baseline (repeat last input)
yaw_base = np.repeat(yaw_last_test_raw[:, None, :], repeats=yaw_true_abs.shape[1], axis=1)[:, :, 0]
vel_base = np.repeat(vel_last_test_raw[:, None, :], repeats=vel_true_abs.shape[1], axis=1)[:, :, 0]
acc_base = np.repeat(accel_last_test_raw[:, None, :], repeats=acc_true_abs.shape[1], axis=1)[:, :, 0]

print("\n=== Joint model ABS (original units) RMSE vs persistence ===")
print("Yaw RMSE     | Model:", rmse(yaw_pred_abs, yaw_true_abs), "| Baseline:", rmse(yaw_base, yaw_true_abs))
print("Vel RMSE     | Model:", rmse(vel_pred_abs, vel_true_abs), "| Baseline:", rmse(vel_base, vel_true_abs))
print("Accel RMSE   | Model:", rmse(acc_pred_abs, acc_true_abs), "| Baseline:", rmse(acc_base, acc_true_abs))

# overall RMSE across the 3 features
y_pred_stack = np.stack([yaw_pred_abs, vel_pred_abs, acc_pred_abs], axis=2)
y_true_stack = np.stack([yaw_true_abs, vel_true_abs, acc_true_abs], axis=2)
y_base_stack = np.stack([yaw_base, vel_base, acc_base], axis=2)

print("Overall RMSE | Model:", rmse(y_pred_stack, y_true_stack), "| Baseline:", rmse(y_base_stack, y_true_stack))

# =============================================================================
# 7) plotting (save to disk)
# =============================================================================

import os
import matplotlib.pyplot as plt
import numpy as np

def _downsample_xy(t, p, max_points, seed=0):
    n = t.shape[0]
    if n > max_points:
        rng = np.random.default_rng(seed)
        idx = rng.choice(n, size=max_points, replace=False)
        t = t[idx]
        p = p[idx]
    return t, p

def _scatter_plot(t, p, title, xlabel, ylabel, out_path=None, show=False):
    plt.figure()
    plt.scatter(t, p, s=1, alpha=0.3)

    lo = float(min(t.min(), p.min()))
    hi = float(max(t.max(), p.max()))
    plt.plot([lo, hi], [lo, hi])  # y=x reference

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True)

    if out_path is not None:
        plt.tight_layout()
        plt.savefig(out_path, dpi=200)
    if show:
        plt.show()
    plt.close()

def save_scatter_all_horizons(y_true_abs, y_pred_abs, name, save_dir="plots", max_points=200_000, show=False):
    """
    Saves a single 'all horizons' scatter for a signal.
    y_true_abs, y_pred_abs: (N, H)
    """
    os.makedirs(save_dir, exist_ok=True)

    t = y_true_abs.reshape(-1)
    p = y_pred_abs.reshape(-1)
    t, p = _downsample_xy(t, p, max_points=max_points, seed=0)

    out_path = os.path.join(save_dir, f"{name}_all_horizons.png")
    _scatter_plot(
        t, p,
        title=f"{name} - True vs Predicted (all horizons)",
        xlabel=f"True {name}",
        ylabel=f"Predicted {name}",
        out_path=out_path,
        show=show
    )
    return out_path

def save_scatter_by_step(y_true_abs, y_pred_abs, name, steps=(1, 10, 30), save_dir="plots", max_points=200_000, show=False):
    """
    Saves per-step scatters for a signal.
    steps are 1-indexed horizon steps.
    """
    os.makedirs(save_dir, exist_ok=True)

    H = y_true_abs.shape[1]
    saved = []

    for s in steps:
        k = s - 1
        if k < 0 or k >= H:
            continue

        t = y_true_abs[:, k]
        p = y_pred_abs[:, k]
        t, p = _downsample_xy(t, p, max_points=max_points, seed=s)

        out_path = os.path.join(save_dir, f"{name}_step{s:02d}.png")
        _scatter_plot(
            t, p,
            title=f"{name} - True vs Predicted (step {s})",
            xlabel=f"True {name}",
            ylabel=f"Predicted {name}",
            out_path=out_path,
            show=show
        )
        saved.append(out_path)

    return saved

def save_all_signal_plots(acc_true_abs, acc_pred_abs,
                          yaw_true_abs, yaw_pred_abs,
                          vel_true_abs, vel_pred_abs,
                          save_dir="plots", steps=(1, 10, 30),
                          max_points=200_000, show=False):
    outputs = []

    # accel
    outputs.append(save_scatter_all_horizons(acc_true_abs, acc_pred_abs, "accel_pedal", save_dir, max_points, show))
    outputs += save_scatter_by_step(acc_true_abs, acc_pred_abs, "accel_pedal", steps, save_dir, max_points, show)

    # yaw
    outputs.append(save_scatter_all_horizons(yaw_true_abs, yaw_pred_abs, "yaw", save_dir, max_points, show))
    outputs += save_scatter_by_step(yaw_true_abs, yaw_pred_abs, "yaw", steps, save_dir, max_points, show)

    # vel
    outputs.append(save_scatter_all_horizons(vel_true_abs, vel_pred_abs, "vel", save_dir, max_points, show))
    outputs += save_scatter_by_step(vel_true_abs, vel_pred_abs, "vel", steps, save_dir, max_points, show)

    return outputs

# --- run: saves into ./plots/ ---
saved_files = save_all_signal_plots(
    acc_true_abs, acc_pred_abs,
    yaw_true_abs, yaw_pred_abs,
    vel_true_abs, vel_pred_abs,
    save_dir="plots",
    steps=(1, 10, 30),
    max_points=200_000,
    show=False  # set True if you also want pop-up windows
)

print("\nSaved plots:")
for f in saved_files:
    print(" -", f)