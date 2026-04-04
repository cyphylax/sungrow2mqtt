# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

# Copy root filesystem
COPY rootfs /

# Install packages
RUN apk add --no-cache python3 py3-pip
RUN python3 -m pip install --upgrade pip

# Install application requirements
WORKDIR /app
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt


RUN chmod +x /etc/services.d/sungrow2mqtt/run
# Set the default command to run when starting the container
CMD ["/etc/services.d/sungrow2mqtt/run"]