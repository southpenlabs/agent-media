# Things that place themselves (22 Nov 2026)

Maps step 4 places David's devices, rooms and speakers by hand. Most of them
have no GPS. This proposes letting each one be placed by whichever way of
finding it suits at the time, with placing by hand still the final say.
(David, 22 Nov 2026: "Can we use a little bit of all of them? whichever one
suits at the time?")

## The ways, best first

1. **By hand.** As built (`POST /map/things`). Always wins, and is the only
   way to place a room. A thing placed by hand is never moved by the ones
   below; its card can say "seen elsewhere" if they disagree by more than
   their accuracy.
2. **Beside the phone.** When the phone has a GPS fix and sees the thing
   close by, the thing takes the phone's point. "Close by" means:
   - on the same Wi-Fi network as the phone, which the server can tell from
     the two devices' reports (gateway MAC / SSID hash, never the SSID text
     sent anywhere outside), giving a point good to a house or ~50 m; or
   - a Bluetooth LE signal from the thing at strong RSSI (a device running
     Sasonica advertises a beacon), giving a few metres.
   Suits speakers, TVs, Pis and anything that cannot scan Wi-Fi itself.
3. **Wi-Fi lookup.** A device that can scan (a laptop, a Pi with Wi-Fi, an
   Android device without GPS) sends the access points it hears; the
   server asks BeaconDB (open, a successor to Mozilla Location Service)
   for a point, and Google's Geolocation API only if a key is set and
   inside a monthly ceiling, as place hours do. 10–50 m in town.
   Lookups are cached by access-point set; nothing is sent while the
   device is beside a placed thing that already answers.
4. **IP address.** Last resort: suburb or city, from a local GeoLite2-style
   file (no outside call). Shown as a wide circle, never as a pin.

## How the server picks

Each answer carries `source` (`hand`, `phone`, `wifi`, `ip`) and an
accuracy in metres. `GET /map/things` gives each thing its best current
answer: by hand if placed, else the most accurate answer younger than its
kind's age limit (a fixed speaker's last good point holds for good; a
laptop's goes stale after an hour and falls back a step). The app draws the
accuracy as a circle and the card says how it was found ("beside your phone,
10 min ago").

## Privacy

Points found this way are the thing's, not David's movements, and follow the
location history keep setting (30/90/365/forever); forget all clears them.
Access-point lists go to BeaconDB only, Google only if he adds a key; an
outside-services row for each.

## Order

1. Beside the phone over shared Wi-Fi (no new outside service; the phone
   already sends fixes).
2. Wi-Fi lookup via BeaconDB, for devices that run Sasonica's agent.
3. IP fallback.
4. Bluetooth beacons, with the floor plan, for which room.

Open source first: BeaconDB (server AGPL, used as a service only, nothing
copied in); for the IP file, DB-IP Lite (CC BY 4.0) over GeoLite2 (its
licence needs an account and forbids some uses).
