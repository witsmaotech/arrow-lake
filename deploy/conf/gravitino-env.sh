#!/bin/bash
# Override lance-rest config via sed on the config file
sed -i \
  -e 's|gravitino.lance-rest.gravitino-uri = .*|gravitino.lance-rest.gravitino-uri = http://arrow-lake-gravitino:8090|' \
  -e 's|gravitino.lance-rest.gravitino-metalake = .*|gravitino.lance-rest.gravitino-metalake = arrow_lake|' \
  "${GRAVITINO_CONF_DIR}/gravitino-lance-rest-server.conf"
