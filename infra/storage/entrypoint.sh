#!/bin/sh
# Starts SeaweedFS (master + volume + filer + S3 gateway) with a single private S3 identity.
# There is no anonymous access: requests without valid credentials are rejected (403).
set -eu
: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
case "$S3_ACCESS_KEY$S3_SECRET_KEY" in
  *[!A-Za-z0-9_-]*) echo "S3 credentials may only contain letters, digits, '_' and '-'" >&2; exit 1 ;;
esac
umask 077
printf '{"identities":[{"name":"app","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
  "$S3_ACCESS_KEY" "$S3_SECRET_KEY" > /tmp/s3.json
exec /usr/bin/weed server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/tmp/s3.json \
  -master.volumeSizeLimitMB=512 -volume.max=40
