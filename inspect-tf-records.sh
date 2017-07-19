#!/bin/bash

source prepare-shell.sh

python -m sciencebeam_gym.tools.inspect_tfrecords \
  --records_paths "${PREPROC_PATH}/train/*tfrecord*" \
  --inspect_key "input_uri" \
  --extract_dir ".temp/train" \
  --extract_image "input_image" \
  --extract_image "annotation_image"

python -m sciencebeam_gym.tools.inspect_tfrecords \
  --records_paths "${PREPROC_PATH}/test/*tfrecord*" \
  --inspect_key "input_uri" \
  --extract_dir ".temp/test" \
  --extract_image "input_image" \
  --extract_image "annotation_image"