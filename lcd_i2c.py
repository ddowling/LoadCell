from utime import sleep_ms, sleep_us

# PCF8574 backpack bit positions (standard wiring)
_RS = 0x01   # Register Select
_EN = 0x04   # Enable
_BL = 0x08   # Backlight

# DDRAM row start addresses for up to 4 rows
_ROW_OFFSET = (0x00, 0x40, 0x14, 0x54)


class LCD:
    def __init__(self, i2c, address=0x27, cols=16, rows=2):
        self._i2c  = i2c
        self._addr = address
        self._cols = cols
        self._rows = rows
        self._bl   = _BL
        self._init()

    def _write(self, b):
        self._i2c.writeto(self._addr, bytes([b | self._bl]))

    def _pulse(self, b):
        self._write(b | _EN)
        sleep_us(1)
        self._write(b & ~_EN)
        sleep_us(50)

    def _nibble(self, n, rs=0):
        self._pulse((n << 4) | (rs & _RS))

    def _send(self, b, rs=0):
        self._nibble(b >> 4, rs)
        self._nibble(b & 0x0F, rs)

    def _cmd(self, b):
        self._send(b, 0)
        if b < 0x04:      # CLEAR (0x01) and HOME (0x02) need extra time
            sleep_ms(2)

    def _init(self):
        sleep_ms(50)
        # HD44780 4-bit init sequence
        for _ in range(3):
            self._nibble(0x03)
            sleep_ms(5)
        self._nibble(0x02)    # switch to 4-bit mode
        self._cmd(0x28)       # 4-bit, 2-line, 5x8 font
        self._cmd(0x0C)       # display on, cursor off, blink off
        self._cmd(0x01)       # clear display
        self._cmd(0x06)       # entry: left-to-right, no display shift

    def clear(self):
        self._cmd(0x01)

    def home(self):
        self._cmd(0x02)

    def move_to(self, col, row):
        self._cmd(0x80 | (_ROW_OFFSET[row % self._rows] + col))

    def putstr(self, s):
        for c in s:
            self._send(ord(c), _RS)

    def backlight(self, on):
        self._bl = _BL if on else 0
        self._write(0)
