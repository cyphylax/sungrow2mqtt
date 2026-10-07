# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

# Copy root filesystem
COPY rootfs /

# Python itself comes from the base-python image (see build.yaml), installed
# under /usr/local - not Alpine's system Python, so pip needs no
# --break-system-packages and the interpreter version is pinned by the tag.

# Install application requirements
WORKDIR /app
RUN pip3 install --no-cache-dir -r requirements.txt


RUN chmod +x /etc/services.d/sungrow2mqtt/run
