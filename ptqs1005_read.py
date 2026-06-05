#!/usr/bin/env python3
"""
PTQS1005 air quality sensor reader via UART (CP210x on /dev/ttyUSB0).
Protocol: 9600 8N1, request/response over RS232-TTL.

Usage:
  python3 ptqs1005_read.py [mode] [interval] [csvfile]

  mode     : general (default) or full
  interval : seconds between readings (default 5)
  csvfile  : path to CSV log file (default ptqs1005_log.csv)
"""

import csv
import os
import serial
import sys
import time
from datetime import datetime

PORT = '/dev/ttyUSB0'
BAUD = 9600

CMD_GENERAL = bytes([0x42, 0x4D, 0xAB, 0x00, 0x00, 0x01, 0x3A])  # 24-byte reply
CMD_FULL    = bytes([0x42, 0x4D, 0xAC, 0x00, 0x00, 0x01, 0x3B])  # 42-byte reply

GENERAL_FIELDS = [
    'timestamp',
    'pm25_cf1_ugm3',
    'tvoc_ppm',
    'hcho_mgm3',
    'co2_ppm',
    'temperature_c',
    'humidity_pct',
]

FULL_FIELDS = [
    'timestamp',
    'pm10_cf1_ugm3', 'pm25_cf1_ugm3', 'pm100_cf1_ugm3',
    'pm10_atm_ugm3', 'pm25_atm_ugm3', 'pm100_atm_ugm3',
    'count_03um', 'count_05um', 'count_10um',
    'count_25um', 'count_50um', 'count_100um',
    'tvoc_ppm', 'hcho_mgm3', 'co2_ppm',
    'temperature_c', 'humidity_pct',
]


def checksum_ok(data: bytes) -> bool:
    expected = (data[-2] << 8) | data[-1]
    return (sum(data[:-2]) & 0xFFFF) == expected


def u16(data: bytes, i: int) -> int:
    return (data[i] << 8) | data[i + 1]


def parse_general(data: bytes) -> dict | None:
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


def parse_full(data: bytes) -> dict | None:
    if len(data) != 42 or data[0] != 0x42 or data[1] != 0x4D:
        return None
    if not checksum_ok(data):
        return None
    return {
        'pm10_cf1_ugm3':  u16(data,  4),
        'pm25_cf1_ugm3':  u16(data,  6),
        'pm100_cf1_ugm3': u16(data,  8),
        'pm10_atm_ugm3':  u16(data, 10),
        'pm25_atm_ugm3':  u16(data, 12),
        'pm100_atm_ugm3': u16(data, 14),
        'count_03um':     u16(data, 16),
        'count_05um':     u16(data, 18),
        'count_10um':     u16(data, 20),
        'count_25um':     u16(data, 22),
        'count_50um':     u16(data, 24),
        'count_100um':    u16(data, 26),
        'tvoc_ppm':       round(u16(data, 28) / 100, 2),
        'hcho_mgm3':      round(u16(data, 31) / 100, 2),
        'co2_ppm':        u16(data, 34),
        'temperature_c':  round(u16(data, 36) / 10, 1),
        'humidity_pct':   round(u16(data, 38) / 10, 1),
    }


DISPLAY_LABELS = {
    'pm10_cf1_ugm3':  ('PM1.0  (CF=1)',  'µg/m³'),
    'pm25_cf1_ugm3':  ('PM2.5  (CF=1)',  'µg/m³'),
    'pm100_cf1_ugm3': ('PM10   (CF=1)',  'µg/m³'),
    'pm10_atm_ugm3':  ('PM1.0  (atm)',   'µg/m³'),
    'pm25_atm_ugm3':  ('PM2.5  (atm)',   'µg/m³'),
    'pm100_atm_ugm3': ('PM10   (atm)',   'µg/m³'),
    'count_03um':     ('>0.3 µm/0.1L',  ''),
    'count_05um':     ('>0.5 µm/0.1L',  ''),
    'count_10um':     ('>1.0 µm/0.1L',  ''),
    'count_25um':     ('>2.5 µm/0.1L',  ''),
    'count_50um':     ('>5.0 µm/0.1L',  ''),
    'count_100um':    ('>10  µm/0.1L',  ''),
    'tvoc_ppm':       ('TVOC',           'PPM'),
    'hcho_mgm3':      ('HCHO',           'mg/m³'),
    'co2_ppm':        ('CO2',            'PPM'),
    'temperature_c':  ('Temperature',    '°C'),
    'humidity_pct':   ('Humidity',       '%'),
}


def print_reading(ts: str, reading: dict) -> None:
    print(f'\n[{ts}]')
    for key, value in reading.items():
        label, unit = DISPLAY_LABELS[key]
        print(f'  {label:<18} {value} {unit}'.rstrip())


def log_csv(path: str, fields: list, ts: str, reading: dict) -> None:
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow({'timestamp': ts, **reading})


def read_sensor(ser: serial.Serial, cmd: bytes, reply_len: int, timeout: float = 2.0) -> bytes | None:
    ser.reset_input_buffer()
    ser.write(cmd)
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(reply_len - len(buf))
        if chunk:
            buf.extend(chunk)
        if len(buf) >= reply_len:
            return bytes(buf[:reply_len])
    return None


def main():
    mode     = sys.argv[1] if len(sys.argv) > 1 else 'general'
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    csvfile  = sys.argv[3] if len(sys.argv) > 3 else 'ptqs1005_log.csv'

    if mode == 'full':
        cmd, reply_len, parser, fields = CMD_FULL, 42, parse_full, FULL_FIELDS
    else:
        cmd, reply_len, parser, fields = CMD_GENERAL, 24, parse_general, GENERAL_FIELDS

    print(f'Port: {PORT} @ {BAUD} baud  |  mode={mode}  |  interval={interval}s  |  log={csvfile}')
    print('Press Ctrl+C to stop.\n')

    with serial.Serial(PORT, BAUD, timeout=1) as ser:
        while True:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            raw = read_sensor(ser, cmd, reply_len)
            if raw is None:
                print(f'[{ts}] WARN: no response from sensor.')
            else:
                reading = parser(raw)
                if reading is None:
                    print(f'[{ts}] WARN: bad frame or checksum: {raw.hex()}')
                else:
                    print_reading(ts, reading)
                    log_csv(csvfile, fields, ts, reading)
            time.sleep(interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except serial.SerialException as e:
        print(f'Serial error: {e}')
        sys.exit(1)
