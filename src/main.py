import time
import math
import machine
import M5
from M5 import *

MORSE_MAP = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E',
    '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I', '.---': 'J',
    '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O',
    '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T',
    '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y',
    '--..': 'Z', '-----': '0', '.----': '1', '..---': '2', '...--': '3',
    '....-': '4', '.....': '5', '-....': '6', '--...': '7', '---..': '8',
    '----.': '9', '.-.-.-': '.', '--..--': ',', '..--..': '?', '.----.': "'",
    '-.-.--': '!', '-....-': '-', '.-...': '&', '---...': ':', '-.-.-.': ';',
    '-...-': '=', '.-.-.': '+', '-.--.': '(', '-.--.-': ')', '. . .': 'SOS'
}

STATE_IDLE = 0
STATE_SOUND = 1
STATE_GAP = 2

# Adaptive Morse decoder
CAP_N = 128                 # 16 ms/frame @ 8 kHz

NOISE_ALPHA = 0.05
SIGNAL_ON_MARGIN = 180
SIGNAL_OFF_MARGIN = 70

MIN_PULSE_MS = 25
MIN_GAP_MS = 25

# Initial guess only. It will adapt.
UNIT_MS = 100.0

# Timing adaptation
UNIT_ALPHA = 0.20

# Morse timing ratios
DOT_MAX_RATIO = 2.0
LETTER_GAP_RATIO = 1.0
WORD_GAP_RATIO = 1.5

noise_floor = 100.0
signal_level = 0.0
unit_ms = UNIT_MS

RATE = 8000
BUF = bytearray(CAP_N * 2)
RAW = [0] * CAP_N

state = STATE_IDLE
sound_start = 0
symbol_start = 0
current_symbol = ''
last_action_time = 0
locked_variant = -1
DEAD = [0]
DEAD_FRAMES = 8


def put(s, x, y, fg, bg=0x14304A, px=12):
    f = getattr(M5.Lcd, "FONTS", None)
    if f is not None:
        for name in ("Montserrat12", "Montserrat14", "DejaVu12"):
            h = getattr(f, name, None)
            if h is not None:
                M5.Lcd.setFont(h)
                break
    M5.Lcd.setTextColor(fg, bg)
    M5.Lcd.drawString(s, int(x), int(y))


def draw_static():
    M5.Lcd.fillScreen(0x0A1018)
    M5.Lcd.fillRect(0, 0, 240, 16, 0x14304A)
    put("MORSE CODE", 78, 1, 0xE8F4FF, px=12)
    M5.Lcd.drawLine(0, 22, 239, 22, 0x24485F)
    M5.Lcd.drawLine(0, 56, 239, 56, 0x24485F)
    M5.Lcd.drawLine(0, 78, 239, 78, 0x24485F)
    M5.Lcd.drawLine(0, 100, 239, 100, 0x24485F)


def draw_morse(sym):
    M5.Lcd.fillRect(0, 23, 240, 33, 0x0A1018)
    if sym:
        display = sym.replace('|', '/')
        put(display, 4, 26, 0xFFD700, bg=0x0A1018, px=12)
    else:
        put("( listen... )", 90, 32, 0x4E7C93, bg=0x0A1018, px=12)


def draw_result(text):
    M5.Lcd.fillRect(0, 57, 240, 21, 0x0A1018)
    if text:
        put(text[:36], 4, 60, 0x3ADC8C, bg=0x0A1018, px=12)


def draw_volume(rms):
    M5.Lcd.fillRect(0, 84, 240, 12, 0x0A1018)
    bar_w = min(int(220 * rms / 4000), 220)
    if bar_w > 0:
        color = 0x18C26A if rms > THRESHOLD else 0x4E7C93
        M5.Lcd.fillRect(4, 84, bar_w, 12, color)
    thresh_x = 4 + int(220 * THRESHOLD / 4000)
    if 4 <= thresh_x <= 224:
        M5.Lcd.fillRect(thresh_x, 84, 1, 12, 0xE0503A)
    put(str(int(rms)), 224, 84, 0x7F9DB0, bg=0x0A1018, px=12)


def draw_status(txt, fg):
    # Jika teks kurang dari 25 karakter, tambahkan sisa spasi di kanannya
    pad_length = 25 - len(txt)
    if pad_length > 0:
        txt += " " * pad_length

    put(txt, 4, 104, fg, bg=0x0A1018, px=12)


def decode_morse(symbol):
    return MORSE_MAP.get(symbol, '?')


def redraw_all():
    draw_morse(current_symbol)
    parts = []
    acc = ''
    for ch in current_symbol:
        
        if ch in ('.', '-'):
            acc += ch
            continue
        elif ch == '|':
            letter = decode_morse(acc)
            parts.append(letter)
            acc = ''
    if acc:
        parts.append(decode_morse(acc))
    full = ''.join(parts)
    draw_result(full)


def clear_all():
    global current_symbol, state, last_action_time
    current_symbol = ''
    state = STATE_IDLE
    last_action_time = 0
    redraw_all()
    draw_status("Listening...", 0x7F9DB0)


def speaker_release():
    try:
        Speaker.stop()
    except Exception:
        pass
    try:
        Speaker.end()
    except Exception:
        pass
    try:
        Speaker.begin()
        Speaker.setVolumePercentage(1)
        Speaker.end()
    except Exception:
        pass


def park_mic_pin():
    try:
        p = machine.Pin(46, machine.Pin.OUT)
        p.value(0)
        time.sleep_ms(20)
        p.value(1)
        time.sleep_ms(20)
        p.init(machine.Pin.IN)
        time.sleep_ms(20)
        return
    except Exception:
        pass
    try:
        p = machine.Pin(46, machine.Pin.IN, machine.Pin.PULL_DOWN)
        time.sleep_ms(20)
        p.init(machine.Pin.IN)
        time.sleep_ms(20)
    except Exception:
        pass


def mic_start(park, over_sampling):
    try:
        Mic.end()
    except Exception:
        pass
    speaker_release()
    if park:
        park_mic_pin()
    try:
        Mic.config(pin_data_in=46, sample_rate=RATE, stereo=False,
                   over_sampling=over_sampling, magnification=1,
                   noise_filter_level=0, use_adc=False)
    except Exception:
        try:
            Mic.config(sample_rate=RATE, over_sampling=over_sampling)
        except Exception:
            pass
    try:
        return Mic.begin()
    except Exception:
        return False


def capture():
    BUF[:] = bytes([0x55]) * (CAP_N * 2)
    t0 = time.ticks_ms()
    try:
        Mic.record(BUF, RATE, False)
    except Exception:
        return -1
    nominal = CAP_N * 1000 // RATE
    limit = nominal + 2000
    while True:
        el = time.ticks_diff(time.ticks_ms(), t0)
        if Mic.isRecording() == 0 and el >= nominal:
            return el
        if el > limit:
            return el
        time.sleep_ms(2)


def decode():
    mn = 32767
    mx = -32768
    total = 0
    seen = set()

    for i in range(CAP_N):
        o = i * 2
        v = BUF[o] | (BUF[o + 1] << 8)

        if v >= 32768:
            v -= 65536

        RAW[i] = v

        if v < mn:
            mn = v
        if v > mx:
            mx = v

        total += v
        seen.add(v)

    # Remove DC offset
    mean = total / CAP_N

    total_sq = 0
    for i in range(CAP_N):
        x = RAW[i] - mean
        total_sq += x * x

    rms = (total_sq / CAP_N) ** 0.5

    distinct = len(seen)

    debris = False
    f0 = RAW[0]

    if -30000 < f0 < 30000:
        for i in range(1, min(8, CAP_N)):
            if RAW[i] != f0:
                debris = False
                break
        else:
            debris = True

    return mn, mx, distinct, debris, rms


def is_live():
    mn, mx, distinct, debris, rms = decode()
    if debris:
        return False, "DEBRIS"
    if distinct < 16:
        return False, "CONST"
    return True, "LIVE"


def search_mic():
    variants = ((1, False), (4, False), (1, True), (4, True))
    for idx in range(len(variants)):
        os_, park = variants[idx]
        ok = mic_start(park, os_) is True
        note = "NO DATA"
        if ok:
            capture()
            good = 0
            for _ in range(2):
                if capture() < 0:
                    note = "REC FAIL"
                    good = 0
                    break
                live, why = is_live()
                if live:
                    good += 1
                    note = "OK"
                else:
                    note = why
                    good = 0
                    break
            ok = good >= 2
        else:
            note = "BEGIN FAIL"
        if not ok:
            try:
                Mic.end()
            except Exception:
                pass
        if ok:
            return idx
    return -1


def btnA_event(val):
    global state
    if state == STATE_IDLE:
        clear_all()
    else:
        try:
            Mic.end()
        except Exception:
            pass
        time.sleep_ms(50)
        mic_start(True, 1)
        clear_all()


def setup():
    global locked_variant

    M5.begin()
    try:
        Widgets.setRotation(1)
    except Exception:
        try:
            M5.Lcd.setRotation(1)
        except Exception:
            pass

    draw_static()
    draw_status("Searching mic...", 0xE0A03A)

    locked_variant = search_mic()

    if locked_variant < 0:
        draw_status("Mic fail!", 0xE0503A)
        M5.Lcd.fillRect(0, 23, 240, 50, 0x0A1018)
        put("No mic found", 70, 35, 0xE0503A, px=12)
        put("BtnA=retry", 75, 50, 0x7F9DB0, px=12)
        BtnA.setCallback(type=BtnA.CB_TYPE.WAS_CLICKED, cb=btnA_event)
        return

    draw_status("Listening...", 0x7F9DB0)
    BtnA.setCallback(type=BtnA.CB_TYPE.WAS_CLICKED, cb=btnA_event)

def update_level(rms, active):
    global noise_floor, signal_level

    if not active:
        # Slowly learn background noise
        noise_floor += (rms - noise_floor) * NOISE_ALPHA

    signal_level = rms


def on_threshold():
    return max(
        noise_floor + SIGNAL_ON_MARGIN,
        noise_floor * 1.8
    )


def off_threshold():
    return max(
        noise_floor + SIGNAL_OFF_MARGIN,
        noise_floor * 1.25
    )

def update_unit(duration):
    global unit_ms

    if duration < MIN_PULSE_MS:
        return

    # Current estimate determines whether this was
    # probably a dot or dash.
    if duration >= unit_ms * 2.0:
        candidate = duration / 3.0
    else:
        candidate = duration

    # Reject absurd estimates
    if candidate < 30:
        return

    if candidate > 1000:
        return

    unit_ms += (candidate - unit_ms) * UNIT_ALPHA

def loop():
    global state
    global sound_start
    global symbol_start
    global current_symbol
    global last_action_time
    global locked_variant
    global DEAD
    global unit_ms

    if locked_variant < 0:
        M5.update()
        time.sleep_ms(100)
        return

    M5.update()

    if capture() < 0:
        draw_status("ERR", 0xE0503A)
        time.sleep_ms(100)
        return

    live, why = is_live()

    if not live:
        DEAD[0] += 1

        if DEAD[0] >= DEAD_FRAMES:
            DEAD[0] = 0
            draw_status("Rescan...", 0xE0A03A)
            locked_variant = search_mic()

            if locked_variant < 0:
                draw_status("Mic fail!", 0xE0503A)
        else:
            draw_status("No data", 0x7F9DB0)

        return

    DEAD[0] = 0

    _, _, _, _, rms = decode()

    draw_volume(rms)

    # -----------------------------------------
    # Adaptive thresholds
    # -----------------------------------------

    on_th = on_threshold()
    off_th = off_threshold()

    # -----------------------------------------
    # IDLE
    # -----------------------------------------

    if state == STATE_IDLE:

        update_level(rms, False)

        if rms > on_th:

            now = time.ticks_ms()

            state = STATE_SOUND
            sound_start = now
            symbol_start = now
            last_action_time = now

            draw_status("SOUND", 0x18C26A)

            print(
                "START rms:",
                int(rms),
                "noise:",
                int(noise_floor),
                "ON:",
                int(on_th),
                "unit:",
                int(unit_ms)
            )

        return

    # -----------------------------------------
    # SOUND
    # -----------------------------------------

    if state == STATE_SOUND:

        update_level(rms, True)

        # Hysteresis:
        # once ON, stay ON until we fall below OFF threshold.
        if rms > off_th:
            duration = time.ticks_diff(
                time.ticks_ms(),
                symbol_start
            )

            if duration >= unit_ms * 2:
                draw_status("DASH", 0xFF8800)
            else:
                draw_status("DOT", 0x88FF88)

            return

        # Signal ended
        now = time.ticks_ms()

        duration = time.ticks_diff(
            now,
            symbol_start
        )

        if duration >= MIN_PULSE_MS:

            # Learn Morse timing
            update_unit(duration)

            # Classify AFTER learning
            if duration >= unit_ms * 2.0:

                current_symbol += '-'

                print(
                    "DASH:",
                    duration,
                    "unit:",
                    int(unit_ms)
                )

            else:

                current_symbol += '.'

                print(
                    "DOT:",
                    duration,
                    "unit:",
                    int(unit_ms)
                )

            redraw_all()

        state = STATE_GAP
        last_action_time = now

        draw_status(
            "Gap " + str(int(unit_ms)),
            0x8888FF
        )

        return

    # -----------------------------------------
    # GAP
    # -----------------------------------------

    if state == STATE_GAP:

        update_level(rms, False)

        now = time.ticks_ms()

        elapsed = time.ticks_diff(
            now,
            last_action_time
        )

        # New Morse pulse
        if rms > on_th:

            state = STATE_SOUND
            symbol_start = now

            draw_status("SOUND", 0x18C26A)

            return

        # -------------------------------------
        # WORD GAP
        # -------------------------------------

        if elapsed >= unit_ms * WORD_GAP_RATIO:

            current_symbol += ' '

            redraw_all()

            print(
                "WORD GAP:",
                elapsed,
                "unit:",
                int(unit_ms)
            )

            state = STATE_IDLE
            last_action_time = 0

            draw_status(
                "Listening...",
                0x7F9DB0
            )

            return

        # -------------------------------------
        # LETTER GAP
        # -------------------------------------

        if elapsed >= unit_ms * LETTER_GAP_RATIO:

            current_symbol += '|'

            redraw_all()

            print(
                "LETTER GAP:",
                elapsed,
                "unit:",
                int(unit_ms)
            )

            state = STATE_IDLE
            last_action_time = 0

            draw_status(
                "Listening...",
                0x7F9DB0
            )

            return

        draw_status(
            "Gap:" + str(int(elapsed)),
            0x4E7C93
        )

        return


if __name__ == "__main__":
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg
            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")
