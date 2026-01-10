The ultimate startup script.

The system comprises the following types of machines:
- cloudserver
    This server is accessible from the Internet and collects some measurement data.
    It collects data from some sensors - devices located outside the home connect to this machine.
- homeserver
    This server is not accessible from the Internet and collects some measurement data.
    It collects data from some sensors - devices located within the home connect to this machine.
    It fetches data from the cloudserver,laptop and stores it permanently.
    This server hosts persistent data, including databases and Grafana.
- laptop:
    This is a home computer that runs the 'telegraf' sensor.

Components of the setup:
- envs/
    Variables for Docker Compose YAML files.
- tls/
    SSL certificates.
- yamls/
    Docker Compose configuration files.
- docker-compose.yml
    The main Docker Compose startup script.

QuestDB capacity planning:
  - https://questdb.io/docs/deployment/capacity-planning/
    $ cat /etc/security/limits.conf
      ...
      * soft nofile 1048576
      * hard nofile 1048576
    $ cat /etc/sysctl.conf
      ...
      fs.file-max=1048576
      vm.max_map_count=1048576
