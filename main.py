# main.py
from utils import load_config, parse_events, Relay, Scheduler, mins
import time, sys, select
print("Press any key in 3 s to skip main.py...")
start = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), start) < 3000:
    if select.select([sys.stdin], [], [], 0)[0]:
        print("Main aborted.")
        raise SystemExit
    time.sleep_ms(50)

# ---- KONFIG ----
CONFIG_URL    = "https://jnydal.github.io/grounding-config/grounding-config.json"  # <- din URL
FALLBACK_PATH = "/config.json"
REFETCH_MIN   = mins(23, 55)   # hent ny config hver kveld 23:55 lokal tid
# ----------------

# last config
cfg = load_config(CONFIG_URL, FALLBACK_PATH)
pin, active_high, click_ms, sched = parse_events(cfg)

# sett opp rele og scheduler
relay = Relay(pin, active_high=active_high, click_ms=click_ms)
sch   = Scheduler(sched)

# init til riktig tilstand
st = sch.initial_state()
relay.set_disconnect(st if st is not None else False)

last_min = -1
while True:
    # kjør scheduler
    sch.tick(relay)

    # refetch ved bestemt minutt
    from utils import local_minutes_since_midnight
    now_min, _ = local_minutes_since_midnight()
    if now_min == REFETCH_MIN and now_min != last_min:
        last_min = now_min
        try:
            cfg = load_config(CONFIG_URL, FALLBACK_PATH)
            pin, active_high, click_ms, sched = parse_events(cfg)
            relay = Relay(pin, active_high=active_high, click_ms=click_ms)
            sch   = Scheduler(sched)
            # reinit state ved ny plan
            st = sch.initial_state()
            relay.set_disconnect(st if st is not None else False)
        except Exception:
            # behold gammel plan ved feil
            pass

    time.sleep(0.2)

