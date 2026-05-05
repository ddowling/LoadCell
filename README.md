# LoadCell Scale

A standalone digital scale built on a Raspberry Pi Pico running MicroPython. Reads a load cell via an HX711 amplifier, displays weight on a 2×16 I2C LCD, and supports multiple units. Calibration is handled via the REPL when a PC is connected.

## Features

- Live weight display in **g, kg, oz, or lbs**
- Adaptive lowpass filter — snappy response to load changes, stable at rest
- **Short press** tare button: tare and zero the scale
- **Long press** tare button (≥1s): cycle through units
- **Peak hold mode** — captures the stable peak weight, ignoring loading impulses
- **Item count mode** — for coin and pill counting; shows count on line 1, weight on line 2
- Calibration and tare saved to flash (`cal.json`) — survives power cycles
- Timer-driven measurement loop — REPL remains available while the scale runs

## Hardware

| Component | Details |
|---|---|
| Microcontroller | Raspberry Pi Pico (RP2040) |
| Load cell amplifier | HX711 |
| Display | HD44780 2×16 LCD via PCF8574 I2C backpack |
| Power | LiPo via VSYS |

### Pin Assignments

| Function | GPIO |
|---|---|
| HX711 SCK | GP6 |
| HX711 DATA | GP7 |
| LCD SDA (I2C0) | GP4 |
| LCD SCL (I2C0) | GP5 |
| Tare button | GP2 (active-low) |

## Files

| File | Description |
|---|---|
| `scale.py` | Main application — copy to `main.py` on the Pico for auto-run |
| `lcd_i2c.py` | Minimal HD44780 driver over PCF8574 I2C backpack |
| `hx711/hx711_gpio.py` | HX711 driver with adaptive lowpass filter |

## Getting Started

1. Copy `hx711/hx711_gpio.py`, `lcd_i2c.py`, and `scale.py` to the Pico's filesystem
2. Update the pin assignments and LCD I2C address (`0x27` or `0x3F`) in `scale.py` if needed
3. Rename `scale.py` to `main.py` on the Pico to auto-run on boot
4. Connect via REPL and run the calibration workflow below

## Calibration

Calibration is done once via the REPL (e.g. with `mpremote` or Thonny). The values are saved to `cal.json` and loaded automatically on boot.

```python
# 1. Empty platform — zero the scale
tare()

# 2. Place a known weight on the platform, then:
calibrate(500, "2025-05-05")   # known weight in grams, date as string
```

To reload calibration from flash without rebooting:

```python
load_cal()
```

## Unit Switching

From the REPL:

```python
set_unit('oz')   # g, kg, oz, lbs
```

Or hold the tare button for ≥1 second to cycle through units on the device.

## Peak Hold

Captures the stable peak weight — loading impulses are ignored. The peak updates only once the reading has been steady for ~500ms.

```python
peak_hold()       # enable
peak_hold(False)  # disable
peak_reset()      # clear stored peak without disabling
```

Line 1 shows live weight; line 2 shows `PK: 1234.5 g` when active.

## Item Count Mode

Useful for counting coins, pills, or other uniform items.

```python
tare()        # empty platform
peak_hold()   # enable peak hold
# place one item on the platform and wait ~1s for a stable reading
count_mode()  # switch to count mode using the captured unit weight
```

Or with a known unit weight:

```python
count_mode(5.23)   # 5.23g per item — skips the peak hold step
count_mode(False)  # return to normal mode
```

Line 1 shows the item count; line 2 shows the total weight.

## LCD I2C Address

Most PCF8574 backpacks use `0x27`. If the display is blank, try `0x3F`. You can scan for the address from the REPL:

```python
from machine import I2C, Pin
I2C(0, sda=Pin(4), scl=Pin(5)).scan()
```
