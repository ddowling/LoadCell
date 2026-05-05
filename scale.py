import json
import _thread
from machine import Pin, I2C, ADC
from utime import ticks_ms, ticks_diff, sleep_ms
from hx711_gpio import HX711
from lcd_i2c import LCD

# --- Pin assignments ---
_SCK      = Pin(6, Pin.OUT)
_DATA     = Pin(7, Pin.IN, Pin.PULL_DOWN)
_TARE_BTN = Pin(2, Pin.IN, Pin.PULL_UP)   # active-low, internal pull-up
_I2C_SDA  = Pin(4, pull=Pin.PULL_UP)
_I2C_SCL  = Pin(5, pull=Pin.PULL_UP)

# --- Hardware init ---
_i2c = I2C(0, sda=_I2C_SDA, scl=_I2C_SCL, freq=400_000)
lcd  = LCD(_i2c, address=0x3F)             # change to 0x27 if needed

hx   = HX711(_SCK, _DATA)
Pin(23, Pin.OUT).value(1)                  # SMPS power-save mode for better battery efficiency

# --- Battery monitor (GP26, 100k/100k divider) ---
_bat_adc        = ADC(Pin(26))
_BAT_SCALE      = 2 * 3.3 / 65535         # adjust numerator if using different divider ratio
_BAT_LOW_V      = 3.5                      # show warning below this voltage
_BAT_CRIT_V     = 3.2                      # always-on warning below this voltage
_bat_v          = 4.2                      # cached reading, updated every 5s
_bat_poll_count = 0
_BAT_POLL_EVERY = 50                       # 50 × 100ms = every 5s


def get_battery_v():
    """Return current battery voltage (GP26, 100k/100k divider)."""
    return _bat_adc.read_u16() * _BAT_SCALE


# 5×8 battery icons, fill rises from bottom. CGRAM slot 0.
# Each row is 5 bits: 0x1F=full, 0x11=side walls only, 0x0E=nub (centre 3).
_BAT_CHARS = [
    [0x0E, 0x1F, 0x11, 0x11, 0x11, 0x11, 0x1F, 0x00],  # empty
    [0x0E, 0x1F, 0x11, 0x11, 0x11, 0x1F, 0x1F, 0x00],  # 1/4
    [0x0E, 0x1F, 0x11, 0x11, 0x1F, 0x1F, 0x1F, 0x00],  # 1/2
    [0x0E, 0x1F, 0x11, 0x1F, 0x1F, 0x1F, 0x1F, 0x00],  # 3/4
    [0x0E, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x00],  # full
]


def _bat_level():
    if _bat_v >= 4.0: return 4
    if _bat_v >= 3.8: return 3
    if _bat_v >= 3.6: return 2
    if _bat_v >= 3.5: return 1
    return 0


def _update_bat_char():
    lcd.define_char(0, _BAT_CHARS[_bat_level()])


def _fmt_battery():
    s = f"\x00 {_bat_v:.2f}V"
    if _bat_v < _BAT_CRIT_V:
        s += "  CRIT!"
    elif _bat_v < _BAT_LOW_V:
        s += "  Low"
    return f"{s:<16}"[:16]

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
    global _paused
    _paused = True
    sleep_ms(200)    # wait for thread to finish current HX711 read
    _status("Taring...")
    hx.tare(15)
    save_cal()
    _status("Tared OK", 2000)
    _paused = False
    print(f"Tared. Offset={hx.OFFSET:.1f}")


def calibrate(known_g, date):
    """Compute scale factor with known_g on the platform, then save.

    Workflow:
        1. Empty platform  →  tare()
        2. Place known weight  →  calibrate(500, "2025-05-05")
    """
    global _cal_date, _paused
    _paused = True
    sleep_ms(200)
    raw = hx.read_average(15)
    hx.set_scale((raw - hx.OFFSET) / known_g)
    _cal_date = date
    save_cal()
    _status("Cal saved", 2000)
    _paused = False
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


# --- Peak hold ---

_peak_hold    = False
_peak_value   = 0.0
_prev_weight  = 0.0
_stable_count = 0

_STABLE_THRESHOLD = 2.0   # grams change per sample to be considered stable
_STABLE_NEEDED    = 5     # consecutive stable samples required before updating peak


def peak_hold(active=True):
    """Enable or disable peak hold mode. peak_hold(False) to turn off."""
    global _peak_hold, _peak_value, _stable_count
    _peak_hold    = active
    _peak_value   = 0.0
    _stable_count = 0
    if not active:
        _status("")
    print(f"Peak hold {'on' if active else 'off'}")


def peak_reset():
    """Clear the stored peak value."""
    global _peak_value, _stable_count
    _peak_value   = 0.0
    _stable_count = 0


# --- Count mode ---

_count_mode  = False
_unit_weight = 0.0


def count_mode(unit_g=None):
    """Enter item count mode for coin/pill counting.

    Workflow:
        1. tare()                  — empty platform
        2. Place one item, wait for peak hold to capture it
        3. count_mode()            — uses peak value as unit weight
           count_mode(False)       — exit count mode
           count_mode(5.23)        — explicit unit weight in grams

    Display: line 1 = count, line 2 = live weight.
    """
    global _count_mode, _unit_weight, _peak_hold
    if unit_g is False:
        _count_mode = False
        _status("")
        print("Count mode off")
        return
    if unit_g is None:
        if _peak_value <= 0:
            print("No peak value — enable peak_hold(), weigh one item, then call count_mode()")
            return
        unit_g = _peak_value
    _unit_weight = float(unit_g)
    _count_mode  = True
    _peak_hold   = False
    _status("")
    print(f"Count mode on  unit={_unit_weight:.3f}g")


# --- Display helpers ---

_status_clear_at = 0


def _status(msg, hold_ms=0):
    global _status_clear_at
    lcd.move_to(0, 1)
    lcd.putstr(f"{msg:<16}"[:16])
    _status_clear_at = (ticks_ms() + hold_ms) if hold_ms else 0


def _fmt_weight(grams):
    val = grams * _SCALES[_unit_idx]
    dec = _DECIMALS[_unit_idx]
    s = f"{val:.{dec}f} {_UNITS[_unit_idx]}"
    return f"{s:>16}"


def _fmt_peak(grams):
    val = grams * _SCALES[_unit_idx]
    dec = _DECIMALS[_unit_idx]
    s = f"PK:{val:.{dec}f} {_UNITS[_unit_idx]}"
    return f"{s:<16}"[:16]


def _fmt_count(grams):
    count = max(0, int(grams / _unit_weight + 0.5)) if _unit_weight > 0 else 0
    return f"Count:{count:>10}"


# --- Measure thread (core 1) ---

_thread_running = False
_paused         = False
_btn_last       = 1
_btn_pressed_at = 0


def _measure_loop():
    global _btn_last, _btn_pressed_at
    global _prev_weight, _stable_count, _peak_value
    global _bat_v, _bat_poll_count

    while _thread_running:
        if _paused:
            sleep_ms(10)
            continue

        now = ticks_ms()
        w = hx.get_units()          # blocks ~100ms for HX711 conversion

        if _paused:                  # re-check after the long read
            continue

        # Line 1: count (count mode) or live weight (all other modes)
        lcd.move_to(0, 0)
        lcd.putstr(_fmt_count(w) if _count_mode else _fmt_weight(w))

        # Peak hold: update peak only when stable for _STABLE_NEEDED samples
        if _peak_hold:
            if abs(w - _prev_weight) < _STABLE_THRESHOLD:
                _stable_count += 1
                if _stable_count >= _STABLE_NEEDED and w > _peak_value:
                    _peak_value = w
            else:
                _stable_count = 0
            _prev_weight = w

        # Battery check every 5s — update cached voltage and CGRAM icon
        _bat_poll_count += 1
        if _bat_poll_count >= _BAT_POLL_EVERY:
            _bat_poll_count = 0
            _bat_v = get_battery_v()
            _update_bat_char()

        # Line 2 priority: critical battery > status > mode content > battery indicator
        if _status_clear_at and ticks_diff(now, _status_clear_at) >= 0:
            _status("")
        if _bat_v < _BAT_CRIT_V:
            lcd.move_to(0, 1)
            lcd.putstr(_fmt_battery())
        elif not _status_clear_at:
            if _count_mode:
                lcd.move_to(0, 1)
                lcd.putstr(_fmt_weight(w))
            elif _peak_hold:
                lcd.move_to(0, 1)
                lcd.putstr(_fmt_peak(_peak_value))
            else:
                lcd.move_to(0, 1)
                lcd.putstr(_fmt_battery())

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
    global _thread_running
    if active:
        _thread_running = True
        _thread.start_new_thread(_measure_loop, ())
    else:
        _thread_running = False


def run():
    global _bat_v
    load_cal()
    hx.set_adaptive_threshold(int(50 * hx.SCALE))
    hx.set_time_constant(0.1)

    _bat_v = get_battery_v()
    _update_bat_char()

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("  Load  Cell    ")
    _status(f"Cal:{_cal_date}", 3000)
    sleep_ms(800)

    monitor()


run()
