"ile" project - ile-tcp-cache - Temporary data store.
                                Don't lose data when the persistent data store is not always reachable.

ile-tcp-cache is useful when the target TCP server is unreachable from the data-producing device.

Use case example: telegraf on a laptop with access to the database (target TCP server) once a day.
                  Valkey, collector, and delivery man components run on the laptop.

Use case example: smart-home device is on a different network than a database (target TCP server).
                  The smart home device writes the measurements to a cloud virtual machine.
                  Valkey and the collector components run in the cloud.
                  The delivery man component runs on the database host computer.

     [Pickup location: my TCP] -> [Parcel collector] -> [Warehouse: Valkey stream]
             -> [Delivery man] -> [Destination location: target TCP]
