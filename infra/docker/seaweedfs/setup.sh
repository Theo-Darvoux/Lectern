#!/bin/sh
set -e

# Bootstrap the SeaweedFS S3 store for WikINT dev (analogue of the old MinIO
# setup.sh). Creates the `wikint` bucket via the S3 API. Anonymous read for
# branding/ assets is granted by the `anonymous` identity in s3.json, so no
# bucket policy step is needed here.

ENDPOINT="http://seaweedfs:8333"
BUCKET="${S3_BUCKET:-wikint}"
export AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-minioadmin}"
export AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-minioadmin}"
export AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}"

# Wait for the S3 gateway to accept requests.
echo "Waiting for SeaweedFS S3 gateway at ${ENDPOINT} ..."
i=0
until aws --endpoint-url "$ENDPOINT" s3 ls >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "SeaweedFS S3 gateway did not become ready in time." >&2
    exit 1
  fi
  sleep 2
done

# Create the bucket if it does not already exist.
if aws --endpoint-url "$ENDPOINT" s3 ls "s3://${BUCKET}" >/dev/null 2>&1; then
  echo "Bucket ${BUCKET} already exists."
else
  aws --endpoint-url "$ENDPOINT" s3 mb "s3://${BUCKET}"
  echo "Created bucket ${BUCKET}."
fi

echo "SeaweedFS setup successful."
