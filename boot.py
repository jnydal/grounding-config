# boot.py (robust for ESP32-C3)
import time
import network
import ntptime

SSID = "<SSID>"
PASS = "<PASSWORD>"

def connect_wifi(ssid, password, timeout_s=30):
    # Fully reset WLAN
    try:
        network.WLAN(network.STA_IF).active(False)
        time.sleep(0.2)
    except:
        pass

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep(0.3)

    # Optional: improve stability on some boards
    try:
        wlan.config(pm=0xA11140)   # modem-sleep power mgmt
        # wlan.config(country='NO') # uncomment if your build supports it
    except:
        pass

    wlan.connect(ssid, password)

    t0 = time.ticks_ms()
    last_status = None
    while (time.ticks_diff(time.ticks_ms(), t0) < timeout_s*1000):
        s = wlan.status()
        if s != last_status:
            # Common statuses: 0=IDLE, 1=CONNECTING, 2=WRONG_PWD, 3=NO_AP_FOUND, 4=FAIL, 5=GOT_IP
            print("WLAN status:", s)
            last_status = s
        if s == network.STAT_GOT_IP or s == 5:
            print("Connected, ifconfig:", wlan.ifconfig())
            return wlan
        if s in (network.STAT_WRONG_PASSWORD, 2, network.STAT_NO_AP_FOUND, 3, network.STAT_CONNECT_FAIL, 4):
            raise OSError("WiFi connect failed, status=%s" % s)
        time.sleep(0.2)

    raise OSError("WiFi timeout")

# --- Run ---
try:
    wlan = connect_wifi(SSID, PASS, timeout_s=30)
except Exception as e:
    # If it glitched, try a clean re-init once
    print("WiFi error:", e)
    try:
        network.WLAN(network.STA_IF).active(False)
        time.sleep(0.5)
        wlan = connect_wifi(SSID, PASS, timeout_s=30)
    except Exception as e2:
        print("WiFi second attempt failed:", e2)

# Sync RTC to UTC (retry a few times)
for _ in range(5):
    try:
        ntptime.settime()
        print("NTP sync OK")
        break
    except Exception as e:
        print("NTP error:", e)
        time.sleep(1)

