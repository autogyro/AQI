#!/usr/bin/env python3
"""
PTQS1005 live dashboard with AQI — logs to CSV and auto-updates plot.

Usage:
  python3 ptqs1005_live.py [interval] [csvfile]

  interval : seconds between readings (default 5)
  csvfile  : CSV log file (default ptqs1005_log.csv)
"""

import csv
import os
import sys
import threading
import time
from datetime import datetime

import matplotlib.animation as animation
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
from matplotlib.widgets import Button
import pandas as pd
import serial

PORT   = '/dev/ttyUSB0'
BAUD   = 9600
CMD    = bytes([0x42, 0x4D, 0xAB, 0x00, 0x00, 0x01, 0x3A])
FIELDS = ['timestamp', 'pm25_cf1_ugm3', 'tvoc_ppm', 'hcho_mgm3',
          'co2_ppm', 'temperature_c', 'humidity_pct']

# ── AQI tables ────────────────────────────────────────────────────────────────

# EPA 2024 PM2.5 24-hour breakpoints (µg/m³)
# (C_lo, C_hi, AQI_lo, AQI_hi, category, bg_color, text_color)
PM25_BP = [
    (  0.0,   9.0,   0,  50, 'Good',                    '#00e400', '#222222'),
    (  9.1,  35.4,  51, 100, 'Moderate',                '#ffff00', '#222222'),
    ( 35.5,  55.4, 101, 150, 'Unhealthy for Sensitive', '#ff7e00', '#ffffff'),
    ( 55.5, 150.4, 151, 200, 'Unhealthy',               '#ff0000', '#ffffff'),
    (150.5, 250.4, 201, 300, 'Very Unhealthy',          '#8f3f97', '#ffffff'),
    (250.5, 500.4, 301, 500, 'Hazardous',               '#7e0023', '#ffffff'),
]

# Indoor CO2 quality thresholds (PPM)
CO2_BANDS = [
    (   0,  600, '#d4edda', 'Good'),
    ( 600, 1000, '#fff3cd', 'Moderate'),
    (1000, 2000, '#ffe0b2', 'Poor'),
    (2000, 5000, '#ffcccc', 'Unhealthy'),
]

# TVOC quality thresholds (PPM)
TVOC_BANDS = [
    (0.000, 0.065, '#d4edda', 'Excellent'),
    (0.065, 0.220, '#fff3cd', 'Good'),
    (0.220, 0.660, '#ffe0b2', 'Moderate'),
    (0.660, 2.200, '#ffcccc', 'Poor'),
    (2.200, 10.00, '#e8c8f0', 'Very Poor'),
]


def pm25_aqi(concentration: float) -> tuple[int, str, str, str]:
    """Return (aqi, category, bg_color, text_color) for a PM2.5 value."""
    c = int(concentration * 10) / 10  # truncate to 1 decimal per EPA spec
    for c_lo, c_hi, i_lo, i_hi, label, bg, fg in PM25_BP:
        if c_lo <= c <= c_hi:
            aqi = round((i_hi - i_lo) / (c_hi - c_lo) * (c - c_lo) + i_lo)
            return aqi, label, bg, fg
    if c < 0:
        return 0, 'Good', '#00e400', '#222222'
    return 500, 'Hazardous', '#7e0023', '#ffffff'


# ── sensor helpers ────────────────────────────────────────────────────────────

def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == ((data[-2] << 8) | data[-1])

def u16(data: bytes, i: int) -> int:
    return (data[i] << 8) | data[i + 1]

def parse(data: bytes) -> dict | None:
    if len(data) != 24 or data[0] != 0x42 or data[1] != 0x4D:
        return None
    if not checksum_ok(data):
        return None
    return {
        'pm25_cf1_ugm3': u16(data, 4),
        'tvoc_ppm':       round(u16(data, 6)  / 100, 2),
        'hcho_mgm3':      round(u16(data, 9)  / 100, 2),
        'co2_ppm':        u16(data, 12),
        'temperature_c':  round(u16(data, 14) / 10, 1),
        'humidity_pct':   round(u16(data, 16) / 10, 1),
    }

def read_sensor(ser: serial.Serial, timeout: float = 2.0) -> bytes | None:
    ser.reset_input_buffer()
    ser.write(CMD)
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(24 - len(buf))
        if chunk:
            buf.extend(chunk)
        if len(buf) >= 24:
            return bytes(buf[:24])
    return None

# ── logger thread ─────────────────────────────────────────────────────────────

def logger(csvfile: str, interval: float, stop: threading.Event) -> None:
    write_header = not os.path.exists(csvfile) or os.path.getsize(csvfile) == 0
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f'[logger] Serial error: {e}')
        stop.set()
        return

    with ser, open(csvfile, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        while not stop.is_set():
            ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            raw = read_sensor(ser)
            if raw is None:
                print(f'[{ts}] WARN: no response')
            else:
                reading = parse(raw)
                if reading is None:
                    print(f'[{ts}] WARN: bad frame: {raw.hex()}')
                else:
                    aqi, cat, _, _ = pm25_aqi(reading['pm25_cf1_ugm3'])
                    writer.writerow({'timestamp': ts, **reading})
                    f.flush()
                    print(f'[{ts}]  AQI={aqi} ({cat})  '
                          f'PM2.5={reading["pm25_cf1_ugm3"]} µg/m³  '
                          f'CO2={reading["co2_ppm"]} PPM  '
                          f'T={reading["temperature_c"]}°C  '
                          f'RH={reading["humidity_pct"]}%')
            stop.wait(interval)

# ── plot helpers ──────────────────────────────────────────────────────────────

def add_hbands(ax, bands: list, ymax: float = None) -> None:
    """Draw static horizontal color bands on an axes."""
    for lo, hi, color, _ in bands:
        top = hi if ymax is None else min(hi, ymax)
        ax.axhspan(lo, top, facecolor=color, alpha=0.35, zorder=0)

def draw_banner(ax, aqi: int, category: str, ts: str, pm25: float,
                bg: str, fg: str) -> None:
    ax.cla()
    ax.set_facecolor(bg)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    # Large AQI number
    ax.text(0.08, 0.5, str(aqi),
            transform=ax.transAxes, color=fg,
            fontsize=52, fontweight='bold', va='center', ha='center')
    # Category + sub-label
    ax.text(0.22, 0.65, category,
            transform=ax.transAxes, color=fg,
            fontsize=16, fontweight='bold', va='center', ha='left')
    ax.text(0.22, 0.32, f'PM2.5 = {pm25} µg/m³  |  EPA AQI (2024)',
            transform=ax.transAxes, color=fg,
            fontsize=10, va='center', ha='left', alpha=0.85)
    # Legend chips
    x = 0.55
    for _, _, chip_bg, chip_label in [
        ('#00e400', '#222', '#00e400', 'Good'),
        ('#ffff00', '#222', '#ffff00', 'Moderate'),
        ('#ff7e00', '#fff', '#ff7e00', 'Unhealthy*'),
        ('#ff0000', '#fff', '#ff0000', 'Unhealthy'),
        ('#8f3f97', '#fff', '#8f3f97', 'Very Unhealthy'),
        ('#7e0023', '#fff', '#7e0023', 'Hazardous'),
    ]:
        ax.text(x, 0.5, f'  {chip_label}  ',
                transform=ax.transAxes, va='center', ha='left',
                fontsize=7.5,
                bbox=dict(boxstyle='round,pad=0.3', facecolor=chip_bg,
                          edgecolor='none', alpha=0.9),
                color='#222' if chip_bg in ('#00e400', '#ffff00') else '#fff')
        x += 0.09
    # Timestamp
    ax.text(0.99, 0.08, f'Updated {ts}',
            transform=ax.transAxes, color=fg,
            fontsize=8, va='bottom', ha='right', alpha=0.7)

# ── live plot ─────────────────────────────────────────────────────────────────

SUBPLOTS = [
    ('pm25_cf1_ugm3', 'PM2.5 (CF=1)', 'µg/m³', 'steelblue',      PM25_BP),
    ('co2_ppm',       'CO2',           'PPM',    'tomato',          CO2_BANDS),
    ('temperature_c', 'Temperature',   '°C',     'darkorange',      None),
    ('humidity_pct',  'Humidity',      '%',      'mediumseagreen',  None),
    ('tvoc_ppm',      'TVOC',          'PPM',    'mediumpurple',    TVOC_BANDS),
    ('hcho_mgm3',     'HCHO',          'mg/m³',  'indianred',       None),
]


def build_live_plot(csvfile: str, interval_ms: int):
    fig = plt.figure(figsize=(15, 12))
    fig.patch.set_facecolor('#f5f5f5')

    gs = gridspec.GridSpec(4, 2, figure=fig,
                           height_ratios=[1.1, 2, 2, 2],
                           hspace=0.55, wspace=0.32,
                           bottom=0.07)

    ax_banner = fig.add_subplot(gs[0, :])
    ax_banner.set_facecolor('#00e400')
    ax_banner.axis('off')

    meas_axes = []
    for idx, (col, title, unit, color, bands) in enumerate(SUBPLOTS):
        row, c = divmod(idx, 2)
        ax = fig.add_subplot(gs[row + 1, c])
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(unit, fontsize=9)
        ax.tick_params(axis='x', rotation=30, labelsize=8)
        ax.grid(True, linestyle='--', alpha=0.4, zorder=1)
        ax.set_facecolor('#fafafa')

        # Static threshold bands (for PM2.5 use concentration values not AQI)
        if bands is not None:
            if col == 'pm25_cf1_ugm3':
                for c_lo, c_hi, i_lo, i_hi, _, bg, _ in bands:
                    ax.axhspan(c_lo, c_hi, facecolor=bg, alpha=0.25, zorder=0)
            else:
                add_hbands(ax, bands)

        line, = ax.plot([], [], color=color, linewidth=1.5,
                        marker='o', markersize=3, zorder=2)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        meas_axes.append((ax, col, line, color, bands))

    def update(_frame):
        try:
            df = pd.read_csv(csvfile, parse_dates=['timestamp'])
        except Exception:
            return
        if df.empty:
            return

        latest = df.iloc[-1]
        aqi, cat, bg, fg = pm25_aqi(latest['pm25_cf1_ugm3'])
        draw_banner(ax_banner, aqi, cat,
                    str(latest['timestamp'])[:19],
                    latest['pm25_cf1_ugm3'], bg, fg)

        for ax, col, line, color, bands in meas_axes:
            line.set_data(df['timestamp'], df[col])

            # Color the line by current AQI level on the PM2.5 plot
            if col == 'pm25_cf1_ugm3':
                line.set_color(bg)
                line.set_markerfacecolor(bg)
                line.set_markeredgecolor('white')

            # Autoscale x from data; set y manually so axhspan bands don't stretch the axis
            ax.relim()
            ax.autoscale_view(scaley=False)
            ydata = df[col].dropna().tolist()
            if ydata:
                lo, hi = min(ydata), max(ydata)
                pad = max((hi - lo) * 0.20, hi * 0.05, 0.1)
                ax.set_ylim(max(0, lo - pad), hi + pad)

        fig.canvas.draw_idle()

    ani = animation.FuncAnimation(fig, update, interval=interval_ms,
                                  cache_frame_data=False)

    # ── Save button ───────────────────────────────────────────────────────────
    ax_btn = fig.add_axes([0.42, 0.01, 0.16, 0.04])
    btn = Button(ax_btn, 'Save PNG',
                 color='#e8e8e8', hovercolor='#d0e8ff')
    btn.label.set_fontsize(10)

    _reset_timer = [None]

    def on_save(_event):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        stem = os.path.splitext(os.path.basename(csvfile))[0]
        outfile = os.path.join(os.path.dirname(csvfile) or '.', f'{stem}_{ts}.png')
        fig.savefig(outfile, dpi=150, bbox_inches='tight')
        print(f'Saved: {outfile}')
        btn.label.set_text(f'Saved  {os.path.basename(outfile)}')
        fig.canvas.draw_idle()
        if _reset_timer[0] is not None:
            _reset_timer[0].cancel()
        import threading as _th
        t = _th.Timer(3.0, lambda: (btn.label.set_text('Save PNG'),
                                    fig.canvas.draw_idle()))
        _reset_timer[0] = t
        t.start()

    btn.on_clicked(on_save)
    # keep a reference so the button isn't garbage-collected
    fig._save_btn = btn

    return fig, ani


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    csvfile  = sys.argv[2] if len(sys.argv) > 2 else 'ptqs1005_log.csv'

    print(f'Logging to {csvfile} every {interval}s — close the plot window to stop.\n')

    stop = threading.Event()
    t = threading.Thread(target=logger, args=(csvfile, interval, stop), daemon=True)
    t.start()

    fig, ani = build_live_plot(csvfile, int(interval * 1000))
    plt.show()
    stop.set()
    t.join(timeout=3)
    print('Stopped.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except serial.SerialException as e:
        print(f'Serial error: {e}')
        sys.exit(1)
