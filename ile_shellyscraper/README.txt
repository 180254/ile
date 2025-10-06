"ile" project - ile-tcp-cache - Scrape the data from Shelly Plug and Shelly H&T devices,
                                insert them into QuestDB, and visualize them in Grafana.

List of supported devices:
Device name                   | Device model | Tables in the QuestDB db                      | Scrape strategy
Shelly Plug    (Gen1)         | SHPLG-1      | shelly_plugs_meter1                           | API polling
Shelly Plug S  (Gen1)         | SHPLG-S      | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plug US (Gen1)         | SHPLG-U1     | shelly_plugs_meter1                           | API polling
Shelly Plug E  (Gen1)         | SHPLG2-1     | shelly_plugs_meter1                           | API polling
Shelly Plus Plug IT (Gen2)    | SNPL-00110IT | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug S  (Gen2 V1) | SNPL-00112EU | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug S  (Gen2 V2) | SNPL-0112EU  | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug UK (Gen2)    | SNPL-00112UK | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plus Plug US (Gen2)    | SNPL-00116US | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Plug S MTR   (Gen3)    | S3PL-00112EU | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly Outdoor Plug (Gen3)    | S3PL-20112EU | shelly_plugs_meter1,shelly_plugs_temperature1 | API polling
Shelly H&T      (Gen1)        | SHHT-1       | shelly_ht_meter1,shelly_ht_meter2             | webhook (HTTP server)
Shelly Plus H&T (Gen2)        | SNSN-0013A   | shelly_ht_meter1                              | receiving notifications (WebSocket)
Shelly H&T      (Gen3)        | S3SN-0U12AU  | shelly_ht_meter1                              | receiving notifications (WebSocket)

How to configure Shelly devices:
- Scape strategy: API polling
    When you run the script, provide the IP address of the device using the ILE_ISS_SHELLY_IPS environment variable.
- Scrape strategy: Webhook (HTTP server)
    Configure your devices so that the "report sensor values" URL is "http://{machine_ip}:9080/{ILE_ISS_AUTH_TOKEN}".
- Scape strategy: receiving notification (WebSocket)
    Configure your devices so that the outgoing WebSocket server is "ws://{machine_ip}:9081/{ILE_ISS_AUTH_TOKEN}".

You can configure the script using environment variables.
Refer to the Env class in the script, to see variables you can set.
