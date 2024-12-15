"ile" project - ile-tcp-cache - Scrape the data from Shelly Plug and Shelly H&T devices,
                                insert them into QuestDB, and visualize them in Grafana.

List of supported devices:
Name                | Model        | Tables in the QuestDB db                      | Scrape strategy
Shelly Plug         | SHPLG-1      | shelly_plugs_meter1                           | API polling
Shelly Plug S       | SHPLG-S      | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plug US      | SHPLG-U1     | shelly_plugs_meter1                           | API polling
Shelly Plug E       | SHPLG2-1     | shelly_plugs_meter1                           | API polling
Shelly Plus Plug IT | SNPL-00110IT | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug S  | SNPL-00112EU | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug UK | SNPL-00112UK | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug US | SNPL-00116US | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly H&T          | SHHT-1       | shelly_ht_meter1,shelly_ht_meter2             | webhook (HTTP server)
Shelly Plus H&T     | SNSN-0013A   | shelly_ht_meter1                              | receiving notifications (WebSocket)

How to configure Shelly devices:
- Scape strategy: API polling
    When you run the script, provide the IP address of the device using the ILE_ISS_SHELLY_IPS environment variable.
- Scrape strategy: Webhook (HTTP server)
    Configure your devices so that the "report sensor values" URL is "http://{machine_ip}:9080/{ILE_ISS_AUTH_TOKEN}".
- Scape strategy: receiving notification (WebSocket)
    Configure your devices so that the outgoing WebSocket server is "ws://{machine_ip}:9081/{ILE_ISS_AUTH_TOKEN}".

You can configure the script using environment variables.
Refer to the Env class in the script, to see variables you can set.
