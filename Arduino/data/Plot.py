import pandas as pd
import matplotlib.pyplot as plt

# =========================
# USER INPUT: CSV FILE PATH
# =========================
csv_file_path = r"V:\New folder\Multi_Channel_Potentiostat\Arduino\data\CV_29_05_2026__17_37_15\CV_29_05_2026__17_37_15_data.csv"     # <-- CHANGE THIS

# =========================
# READ CSV FILE
# =========================
data = pd.read_csv(csv_file_path)

time = data["Time (s)"]
voltage = data["Voltage (V)"]
current = data["Current (mA)"] 

# =========================
# 1. Voltage vs Current (CV)
# =========================
plt.figure(figsize=(7,7))
plt.plot(voltage, current, marker='.')
plt.xlabel("Voltage (V)")
plt.ylabel("Current (mA)")
plt.title("Cyclic Voltammetry (With Ferro-Ferri Solution)")
plt.grid(True)
plt.show()

# =========================
# 2. Voltage vs Time
# =========================
plt.figure(figsize=(7,7))
plt.plot(time, voltage, marker='.')
plt.xlabel("Time (s)")
plt.ylabel("Voltage (V)")
plt.title("Voltage vs Time (With Ferro-Ferri Solution)")
plt.grid(True)
plt.show()

# =========================
# 3. Current vs Time
# =========================
plt.figure(figsize=(7,7))
plt.plot(time, current, marker='.')
plt.xlabel("Time (s)")
plt.ylabel("Current (mA)")
plt.title("Current vs Time (With Ferro-Ferri Solution)")
plt.grid(True)
plt.show()


