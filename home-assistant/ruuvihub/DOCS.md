# Using RuuviHub

Start RuuviHub and select **Open Web UI**. The dashboard uses HTTPS with a
self-signed certificate by default. Accept the warning for your own Home
Assistant host. Sign in with the project's initial admin credentials and change
the password under **Settings → System settings**. The
[RuuviHub README](https://github.com/TeemuTikka/ruuvihub#first-login) lists the
initial credentials.

The app uses the Home Assistant host's Bluetooth adapter through BlueZ. Place
the host within range of the tags. To see the readings as Home Assistant
entities, use Home Assistant's built-in
[Ruuvi BLE integration](https://www.home-assistant.io/integrations/ruuvitag_ble/).

Settings, the SQLite database, logs, and certificates live in the app's
persistent data directory and are included when you back up the app.
Install updates through the Home Assistant app store.

The dashboard and sensor history are visible on the local network. Keep the
dashboard port on a trusted network.
