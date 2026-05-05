import json
from machine import Pin, I2C, Timer
from utime import ticks_ms, ticks_diff, sleep_ms
from hx711_gpio import HX711
from lcd_i2c import LCD

# --- Pin assignments ---
_SCK      = Pin(6, Pin.OUT)
_DATA     = Pin(7, Pin.IN, Pin.PULL_DOWN)
_TARE_BTN = Pin(2, Pin.IN, Pin.PULL_UP)   # active-low, internal pull-up
_I2C_SDA  = Pin(4)
_I2C_SCL  = Pin(5)

# --- Hardware init ---
_i2c = I2C(0, sda=_I2C_SDA, scl=_I2C_SCL, freq=400_000)
lcd  = LCD(_i2c, address=0x27)             # change to 0x3F if needed
hx   = HX711(_SCK, _DATA)

# --- Calibration ---
_CAL_FILE    = "cal.json"
_DEFAULT_CAL = {"scale": 1069400 / 2500, "offset": -408421.8}
_cal_date    = "Unknown"


def load_cal():
    """Load scale, offset, and calibration date from cal.json; falls back to hardcoded defaults."""
    global _cal_date
    try:
        with open(_CAL_FILE) as f:
            cal = json.load(f)
        hx.set_scale(cal["scale"])
        hx.set_offset(cal["offset"])
        _cal_date = cal.get("cal_date", "Unknown")
        print(f"Cal loaded  date={_cal_date}  offset={hx.OFFSET:.1f}")
    except OSError:
        hx.set_scale(_DEFAULT_CAL["scale"])
        hx.set_offset(_DEFAULT_CAL["offset"])
        print("cal.json not found — using hardcoded defaults")


def save_cal():
    """Save current scale, offset, and calibration date to cal.json."""
    with open(_CAL_FILE, "w") as f:
        json.dump({"scale": hx.SCALE, "offset": hx.OFFSET, "cal_date": _cal_date}, f)
    print(f"Cal saved   date={_cal_date}  offset={hx.OFFSET:.1f}")


def tare():
    """Tare with empty platform and save offset to cal.json.
    Can be called from REPL or triggered by the tare button."""
    _status("Taring...")
    hx.tare(15)
    save_cal()
    _status("Tared OK", 2000)
    print(f"Tared. Offset={hx.OFFSET:.1f}")


def calibrate(known_g, date):
    """Compute scale factor with known_g on the platform, then save.

    Workflow:
        1. Empty platform  →  tare()
        2. Place known weight  →  calibrate(500, "2025-05-05")
    """
    global _cal_date
    raw = hx.read_average(15)
    hx.set_scale((raw - hx.OFFSET) / known_g)
    _cal_date = date
    save_cal()
    _status("Cal saved", 2000)
    print(f"Calibrated  date={_cal_date}  for {known_g}g  raw={raw:.0f}")


# --- Units ---

_UNITS    = ('g',   'kg',    'oz',      'lbs')
_SCALES   = (1.0,   1/1000,  1/28.3495, 1/453.592)
_DECIMALS = (1,     3,       2,         3)
_unit_idx = 0


def set_unit(unit):
    """Switch display unit: set_unit('oz')  — g, kg, oz, lbs"""
    global _unit_idx
    unit = unit.lower()
    if unit not in _UNITS:
        print(f"Unknown unit. Choose from: {_UNITS}")
        return
    _unit_idx = _UNITS.index(unit)
    _status(_UNITS[_unit_idx], 1000)


def _cycle_unit():
    global _unit_idx
    _unit_idx = (_unit_idx + 1) % len(_UNITS)
    _status(_UNITS[_unit_idx], 1000)


# --- Display helpers ---

_status_clear_at = 0


def _status(msg, hold_ms=0):
    global _status_clear_at
    lcd.move_to(0, 1)
    lcd.putstr(msg.ljust(16)[:16])
    _status_clear_at = (ticks_ms() + hold_ms) if hold_ms else 0


def _fmt_weight(grams):
    val = grams * _SCALES[_unit_idx]
    dec = _DECIMALS[_unit_idx]
    s = f"{val:.{dec}f} {_UNITS[_unit_idx]}"
    return s.rjust(16)


# --- Poll timer ---

poll_timer = Timer()
_btn_last       = 1
_btn_pressed_at = 0


def _poll(t):
    global _btn_last, _btn_pressed_at
    now = ticks_ms()

    w = hx.get_units()
    lcd.move_to(0, 0)
    lcd.putstr(_fmt_weight(w))

    if _status_clear_at and ticks_diff(now, _status_clear_at) >= 0:
        _status("")

    # Tare button: active-low, short press (>=50ms) = tare, long press (>=1s) = cycle unit
    btn = _TARE_BTN.value()
    if btn == 0 and _btn_last == 1:
        _btn_pressed_at = now
    elif btn == 1 and _btn_last == 0:
        held = ticks_diff(now, _btn_pressed_at)
        if held >= 1000:
            _cycle_unit()
        elif held >= 50:
            tare()
    _btn_last = btn


def monitor(active=True):
    if active:
        poll_timer.init(mode=Timer.PERIODIC, period=100, callback=_poll)
    else:
        poll_timer.deinit()


def run():
    load_cal()
    hx.set_adaptive_threshold(int(50 * hx.SCALE))
    hx.set_time_constant(0.1)

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("  Load  Cell    ")
    _status(f"Cal:{_cal_date}", 3000)
    sleep_ms(800)

    monitor()


run()
