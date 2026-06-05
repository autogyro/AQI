#!/usr/bin/env python3
"""Plot PTQS1005 CSV log data."""

import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

csvfile = sys.argv[1] if len(sys.argv) > 1 else 'ptqs1005_log.csv'

df = pd.read_csv(csvfile, parse_dates=['timestamp'])

if df.empty:
    print('No data in CSV.')
    sys.exit(1)

fig, axes = plt.subplots(3, 2, figsize=(14, 10))
fig.suptitle('PTQS1005 Air Quality Monitor', fontsize=14, fontweight='bold')

plots = [
    (axes[0, 0], 'pm25_cf1_ugm3',  'PM2.5 (CF=1)',    'µg/m³',  'steelblue'),
    (axes[0, 1], 'co2_ppm',        'CO2',              'PPM',     'tomato'),
    (axes[1, 0], 'temperature_c',  'Temperature',      '°C',      'darkorange'),
    (axes[1, 1], 'humidity_pct',   'Humidity',         '%',       'mediumseagreen'),
    (axes[2, 0], 'tvoc_ppm',       'TVOC',             'PPM',     'mediumpurple'),
    (axes[2, 1], 'hcho_mgm3',      'HCHO (Formaldehyde)', 'mg/m³','indianred'),
]

fmt = mdates.DateFormatter('%H:%M:%S')

for ax, col, title, unit, color in plots:
    ax.plot(df['timestamp'], df[col], color=color, linewidth=1.5, marker='o', markersize=3)
    ax.set_title(title)
    ax.set_ylabel(unit)
    ax.xaxis.set_major_formatter(fmt)
    ax.tick_params(axis='x', rotation=30, labelsize=8)
    ax.grid(True, linestyle='--', alpha=0.4)

fig.tight_layout()
outfile = csvfile.replace('.csv', '.png')
fig.savefig(outfile, dpi=150)
print(f'Saved: {outfile}')
plt.show()
