FROM nginx:1.27-alpine

# Drop in our own config: directory listings on, range requests on, no caching.
COPY nginx.conf /etc/nginx/nginx.conf

# A 100 MB probe file used by ts-bw-probe to measure inter-node bandwidth.
# We pre-generate it at build time so probing has no producer dependency.
RUN mkdir -p /srv/data \
    && dd if=/dev/zero of=/srv/data/_probe.bin bs=1M count=100 \
    && chmod -R a+rX /srv/data

EXPOSE 8080
