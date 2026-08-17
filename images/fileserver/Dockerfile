FROM nginx:1.27-alpine

# Drop in our own config: directory listings on, range requests on, no caching.
COPY nginx.conf /etc/nginx/nginx.conf

# Keep the seed outside /srv/data because that path is replaced by a hostPath
# volume at runtime. The DaemonSet init container copies it onto each node.
RUN dd if=/dev/zero of=/opt/ts-probe.bin bs=1M count=100 \
    && chmod a+r /opt/ts-probe.bin

EXPOSE 8080
