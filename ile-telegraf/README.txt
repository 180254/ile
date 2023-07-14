"ile" project - my telegraf configuration.
https://github.com/search?q=user%3A180254+ile

# basic config
# https://hub.docker.com/_/telegraf
# https://www.influxdata.com/blog/docker-run-telegraf-as-non-root/
docker run -d --restart=unless-stopped \
    --net host \
    --name=ile-telegraf \
    --user telegraf:$(stat -c '%g' /var/run/docker.sock) \
    -v "$PWD/telegraf.conf":/etc/telegraf/telegraf.conf:ro \
    -e SOCKET_WRITER_ADDRESS=tcp://192.168.130.10:9009 \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v /:/hostfs:ro \
    -e HOST_ETC=/hostfs/etc \
    -e HOST_PROC=/hostfs/proc \
    -e HOST_SYS=/hostfs/sys \
    -e HOST_VAR=/hostfs/var \
    -e HOST_RUN=/hostfs/run \
    -e HOST_MOUNT_PREFIX=/hostfs \
    telegraf:1.26.2

# basic + nvidia config
# https://github.com/influxdata/telegraf/blob/release-1.26/plugins/inputs/nvidia_smi/
docker run -d --restart=unless-stopped \
    --net host \
    --name=ile-telegraf \
    --user telegraf:$(stat -c '%g' /var/run/docker.sock) \
    -v "$PWD/telegraf.conf":/etc/telegraf/telegraf.conf:ro \
    -v "$PWD/telegraf-nvidia.conf":/etc/telegraf/telegraf.d/telegraf-nvidia.conf:ro \
    -e SOCKET_WRITER_ADDRESS=tcp://192.168.50.21:9009 \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v /:/hostfs:ro \
    -e HOST_ETC=/hostfs/etc \
    -e HOST_PROC=/hostfs/proc \
    -e HOST_SYS=/hostfs/sys \
    -e HOST_VAR=/hostfs/var \
    -e HOST_RUN=/hostfs/run \
    -e HOST_MOUNT_PREFIX=/hostfs \
    --device /dev/nvidiactl:/dev/nvidiactl \
    --device /dev/nvidia0:/dev/nvidia0 \
    -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
    -v "$(find /usr/lib/x86_64-linux-gnu -name 'libnvidia-ml.so')":/usr/lib/x86_64-linux-gnu/libnvidia-ml.so:ro \
    -e LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libnvidia-ml.so \
    telegraf:1.26.2 \
    telegraf --config /etc/telegraf/telegraf.conf --config-directory /etc/telegraf/telegraf.d
