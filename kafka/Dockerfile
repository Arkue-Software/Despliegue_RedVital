# syntax=docker/dockerfile:1.7
# RedVital — imagen del broker y de su trabajo de inicialización.
# Apache Kafka 4.3.1 oficial, fijada por resumen (Herramientas V4.0, entrada 85).
FROM apache/kafka:4.3.1@sha256:77e3df9054047a88b520d0cc46e16696d3b22022e1d580aeccd2632df6532837

USER root
RUN apk add --no-cache gettext \
 && mkdir -p /var/lib/kafka/datos /opt/redvital \
 && chown -R appuser:appuser /var/lib/kafka
ADD --chmod=0644 https://repo.maven.apache.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/1.0.1/jmx_prometheus_javaagent-1.0.1.jar /opt/redvital/jmx_prometheus_javaagent.jar
COPY config/ /opt/redvital/config/
COPY catalogo-temas.conf /opt/redvital/catalogo-temas.conf
COPY scripts/ /opt/redvital/
RUN chmod -R a+rX,go-w /opt/redvital && chmod 0755 /opt/redvital/*.sh

USER appuser
VOLUME /var/lib/kafka
EXPOSE 9092 9094 9404
HEALTHCHECK --interval=15s --timeout=10s --start-period=40s --retries=5 \
  CMD bash -c '</dev/tcp/127.0.0.1/9092' || exit 1
ENTRYPOINT ["/opt/redvital/arrancar.sh"]
