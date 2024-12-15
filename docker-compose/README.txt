This startup script.
Consolidates the ile_* files in the Docker Compose configuration.

The system comprises the following types of machines:
- cloudserver
    This server is accessible from the Internet and is responsible for collecting measurement data.
    It scrapes data from some sensors - devices located outside the home connect to this machine.
- homeserver
    This server is not accessible from the Internet and also collects measurement data.
    It scrapes data from some sensors - devices located within the home connect to this machine.
    It fetches data from the cloudserver and stores it locally.
    This server hosts persistent data, including databases and Grafana.
- laptop:
    This is a home computer that runs the 'telegraf' sensor.

Components of the setup:
- deps/
    Contains Dockerfiles for external dependencies.
- envs/
    Contains environment variables for Docker Compose YAML files.
- envs/local/
    Includes sample configurations for running everything locally.
- envs/prod/
    Space allocated for production configuration.
- tls/
    Contains SSL certificates.
- tls/local/
    Includes self-signed certificates for local development.
- tls/prod/
    Space allocated for production certificates.
- *.yml
    Docker Compose configuration files, one for each type of machine.
- docker-compose.yml
    The main Docker Compose startup script.
