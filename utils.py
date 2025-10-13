# utils.py
import time
import ujson as json
from machine import Pin

# ---------- HTTP ----------
def http_get_json(url, timeout=10):
    """
    Prøver urequests først (om installert), ellers enkel socket+SSL.
    Returnerer dict fra JSON-respons, eller kaster Exception ved feil.
    """
    try:
        import urequests as rq
        r = rq.get(url, timeout=timeout)
        try:
            return r.json()
        finally:
            r.close()
    except Exception:
        import usocket as socket, ussl as ssl
        proto, _, host_path = url.partition("://")
        if proto == "":
            raise OSError("Bad URL")
        port = 443 if proto == "https" else 80
        host, _, path = host_path.partition("/")
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.connect(addr)
        if proto == "https":
            s = ssl.wrap_socket(s, server_hostname=host)
        req = "GET /{} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n".format(path, host)
        s.write(req.encode())
        data = b""
        s.settimeout(timeout)
        try:
            while True:
                chunk = s.read(1024)
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
        head, _, body = data.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0]
        if b"200" not in status_line:
            raise OSError("HTTP not 200: " + status_line.decode())
        return json.loads(body)

def load_config(url, fallback_path="/config.json", timeout=10):
    """
    Hent JSON-konfig fra URL, lagre kopi til fallback.
    Hvis nett feiler, les fra fallback_path.
    """
    cfg = None
    try:
        cfg = http_get_json(url, timeout=timeout)
        with open(fallback_path, "w") as f:
            f.write(json.dumps(cfg))
    except Exception as e:
        try:
            with open(fallback_path) as f:
                cfg = json.loads(f.read())
        except:
            raise e
    return cfg

# ---------- TID / OSLO ----------
def _last_sunday(year, month):
    # Finn siste søndag i måned (for DST-beregning)
    for day in range(31, 27, -1):
        try:
            wk = time.gmtime(time.mktime((year, month, day, 12, 0, 0, 0, 0)))[6]  # 0=Mon..6=Sun
            if wk == 6:
                return (year, month, day)
        except:
            pass
    return (year, month, 31)

def is_dst_oslo_now_utc():
    """
    DST i Europe/Oslo:
      Starter: siste søndag i mars kl 01:00 UTC
      Slutter: siste søndag i okt  kl 01:00 UTC
    """
    y, mo, d, hh, mi, ss, _, _ = time.gmtime()
    start_y, _, start_d = _last_sunday(y, 3)
    end_y, _, end_d = _last_sunday(y, 10)
    dst_start = time.mktime((start_y, 3,  start_d, 1, 0, 0, 0, 0))
    dst_end   = time.mktime((end_y,   10, end_d, 1, 0, 0, 0, 0))
    now_sec   = time.mktime((y, mo, d, hh, mi, ss, 0, 0))
    return dst_start <= now_sec < dst_end

def local_minutes_since_midnight():
    dst = is_dst_oslo_now_utc()
    tz = 2 if dst else 1  # CEST=UTC+2, CET=UTC+1
    y, mo, d, hh, mi, ss, _, _ = time.gmtime()
    hh = (hh + tz) % 24
    return hh * 60 + mi, (y, mo, d)

def mins(h, m):
    return h * 60 + m

# ---------- PARSING ----------
def parse_events(cfg):
    """
    Forventer JSON som:
    {
      "relay_pin": 5,
      "active_high": true,
      "click_ms": 0,
      "events": [ {"t":"HH:MM","state":"on|off"}, ... ]
    }
    Returnerer (pin, active_high, click_ms, schedule_mins_sorted)
    der schedule-elementer er (minute_of_day, disconnect_bool)
      - disconnect=True  => åpne (frakoble laken)
      - disconnect=False => lukke (tilkoble laken)
    """
    pin = int(cfg.get("relay_pin", 5))
    active_high = bool(cfg.get("active_high", True))
    click_ms = int(cfg.get("click_ms", 0))
    events = cfg.get("events", [])
    parsed = []
    for e in events:
        t = e.get("t", "00:00")
        st = e.get("state", "off").lower()
        hh, mm = t.split(":")
        minute = mins(int(hh), int(mm))
        disconnect = (st == "off")  # "off" = frakoble
        parsed.append((minute, disconnect))
    parsed.sort(key=lambda x: x[0])
    # komprimer duplikater samme minutt (siste vinner)
    compact = []
    last_m = None
    for m, s in parsed:
        if m == last_m:
            compact[-1] = (m, s)
        else:
            compact.append((m, s))
        last_m = m
    return pin, active_high, click_ms, compact

# ---------- RELÉ ----------
class Relay:
    """
    active_high=True  => IN=HIGH -> energize
    Bruk COM-NO hvis du vil at "energize" skal koble til (lukket).
    set_disconnect(True)  => energize (som standard), åpne/lukke avhenger av din kontaktkobling.
    """
    def __init__(self, pin_no, active_high=True, click_ms=0):
        self.pin = Pin(pin_no, Pin.OUT)
        self.active_high = active_high
        self.click_ms = click_ms

    def _apply(self, energize):
        if self.active_high:
            self.pin.value(1 if energize else 0)
        else:
            self.pin.value(0 if energize else 1)

    def set_disconnect(self, disconnect):
        # Standard mapping: energize når vi skal frakoble (passer når COM-NO = lukket ved energize)
        energize = disconnect
        if self.click_ms <= 0:
            self._apply(energize)
        else:
            self._apply(energize)
            time.sleep_ms(self.click_ms)
            self._apply(False)

# ---------- SCHEDULER ----------
class Scheduler:
    """
    Enkelt minuttbasert scheduler med dagsrullering og "trigger once per minute".
    """
    def __init__(self, schedule_minutes):
        self.schedule = schedule_minutes  # liste av (minute_of_day, disconnect_bool)
        self._triggered = [False] * len(self.schedule)
        self._last_date = None
        self._last_min = -1

    def reset_day(self):
        self._triggered = [False] * len(self.schedule)

    def initial_state(self):
        now_m, _ = local_minutes_since_midnight()
        last = None
        for m, st in self.schedule:
            if m <= now_m:
                last = st
            else:
                break
        return last

    def tick(self, relay):
        mins_now, date_now = local_minutes_since_midnight()

        # ny dag?
        if self._last_date != date_now:
            self._last_date = date_now
            self.reset_day()
            st = self.initial_state()
            relay.set_disconnect(st if st is not None else False)  # default: tilkoblet før første event

        # fyr kun én gang per minutt
        if mins_now != self._last_min:
            self._last_min = mins_now
            for i, (m, state) in enumerate(self.schedule):
                if m == mins_now and not self._triggered[i]:
                    relay.set_disconnect(state)
                    self._triggered[i] = True

        time.sleep(2)  # poll noen ganger per min

