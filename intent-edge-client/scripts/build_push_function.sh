#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(
  cd "$(dirname "${BASH_SOURCE[0]}")"
  pwd
)"

CLIENT_ROOT="$(
  cd "$SCRIPT_DIRECTORY/.."
  pwd
)"

REPOSITORY_ROOT="$(
  cd "$CLIENT_ROOT/.."
  pwd
)"

CONFIG_FILE="$CLIENT_ROOT/config/client.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Missing client configuration: $CONFIG_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

SOURCE_DIRECTORY="$REPOSITORY_ROOT/hello"
FUNCTION_NAME="hello"
IMAGE_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
PLATFORM="linux/amd64"
BUILDER="s2i"

usage() {
  cat <<USAGE
Usage:
  $0 [options]

Options:
  --source DIRECTORY   Knative func project directory
                       Default: $SOURCE_DIRECTORY

  --name NAME          Function/image name
                       Default: $FUNCTION_NAME

  --tag TAG            Immutable image tag
                       Default: UTC timestamp

  --platform PLATFORM  Container platform
                       Default: $PLATFORM

  --builder BUILDER    Knative func builder
                       Default: $BUILDER

  --help               Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_DIRECTORY="$2"
      shift 2
      ;;
    --name)
      FUNCTION_NAME="$2"
      shift 2
      ;;
    --tag)
      IMAGE_TAG="$2"
      shift 2
      ;;
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --builder)
      BUILDER="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: Required command is not installed: $command_name" >&2
    exit 1
  fi
}

require_command func
require_command docker
require_command curl
require_command python3
require_command rsync

if [[ ! -d "$SOURCE_DIRECTORY" ]]; then
  echo "ERROR: Function source directory does not exist:" >&2
  echo "  $SOURCE_DIRECTORY" >&2
  exit 1
fi

if [[ ! -f "$SOURCE_DIRECTORY/func.yaml" ]]; then
  echo "ERROR: The source is not a Knative func project." >&2
  echo "Missing file: $SOURCE_DIRECTORY/func.yaml" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker Desktop is not running." >&2
  exit 1
fi

if [[ ! "$FUNCTION_NAME" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "ERROR: Invalid function name: $FUNCTION_NAME" >&2
  exit 1
fi

if [[ ! "$IMAGE_TAG" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "ERROR: Invalid image tag: $IMAGE_TAG" >&2
  exit 1
fi

WORK_DIRECTORY="$CLIENT_ROOT/work/${FUNCTION_NAME}-${IMAGE_TAG}"
RESPONSES_DIRECTORY="$CLIENT_ROOT/responses"

VM1_LATEST_IMAGE="${VM1_PUSH_REGISTRY}/${FUNCTION_NAME}:latest"
VM1_VERSIONED_IMAGE="${VM1_PUSH_REGISTRY}/${FUNCTION_NAME}:${IMAGE_TAG}"
VM2_VERSIONED_IMAGE="${VM2_PUSH_REGISTRY}/${FUNCTION_NAME}:${IMAGE_TAG}"
RUNTIME_IMAGE="${RUNTIME_REGISTRY}/${FUNCTION_NAME}:${IMAGE_TAG}"

BUILD_RESULT="$RESPONSES_DIRECTORY/build-${FUNCTION_NAME}-${IMAGE_TAG}.json"
LATEST_BUILD_RESULT="$RESPONSES_DIRECTORY/latest-build.json"

mkdir -p "$RESPONSES_DIRECTORY"

echo "===== Function image build ====="
echo "Source project:       $SOURCE_DIRECTORY"
echo "Temporary project:    $WORK_DIRECTORY"
echo "Function name:        $FUNCTION_NAME"
echo "Image tag:            $IMAGE_TAG"
echo "Platform:             $PLATFORM"
echo "Builder:              $BUILDER"
echo "VM1 push image:       $VM1_VERSIONED_IMAGE"
echo "VM2 push image:       $VM2_VERSIONED_IMAGE"
echo "Cluster runtime image: $RUNTIME_IMAGE"
echo

echo "===== Verifying registry tunnels ====="

"$SCRIPT_DIRECTORY/check_registries.sh"

echo
echo "===== Creating isolated function copy ====="

rm -rf "$WORK_DIRECTORY"
mkdir -p "$WORK_DIRECTORY"

rsync -a \
  --exclude '.func' \
  --exclude '.git' \
  --exclude '__pycache__' \
  "$SOURCE_DIRECTORY/" \
  "$WORK_DIRECTORY/"

echo "Copied function project without modifying the source."

echo
echo "===== Preparing copied func.yaml ====="

python3 - \
  "$WORK_DIRECTORY/func.yaml" \
  "$VM1_PUSH_REGISTRY" \
  "$FUNCTION_NAME" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
registry = sys.argv[2]
function_name = sys.argv[3]
latest_image = f"{registry}/{function_name}:latest"

lines = path.read_text(encoding="utf-8").splitlines()
updated = []

for line in lines:
    if re.match(r"^registry:\s*", line):
        updated.append(f"registry: {registry}")
    elif re.match(r"^image:\s*", line):
        updated.append(f"image: {latest_image}")
    elif re.match(r"^\s+image:\s*", line):
        indentation = line[: len(line) - len(line.lstrip())]
        updated.append(f"{indentation}image: {latest_image}")
    else:
        updated.append(line)

path.write_text(
    "\n".join(updated) + "\n",
    encoding="utf-8",
)
PY

grep -nE '^(registry|image):|^[[:space:]]+image:' \
  "$WORK_DIRECTORY/func.yaml" || true

echo
echo "===== Building function with Knative func CLI ====="

(
  cd "$WORK_DIRECTORY"

  rm -rf .func

  func build \
    --registry="$VM1_PUSH_REGISTRY" \
    --platform="$PLATFORM" \
    --builder="$BUILDER"
)

if ! docker image inspect "$VM1_LATEST_IMAGE" >/dev/null 2>&1; then
  echo "ERROR: Expected image was not created:" >&2
  echo "  $VM1_LATEST_IMAGE" >&2
  exit 1
fi

LOCAL_IMAGE_ID="$(
  docker image inspect \
    --format '{{.Id}}' \
    "$VM1_LATEST_IMAGE"
)"

echo
echo "Local image ID: $LOCAL_IMAGE_ID"

echo
echo "===== Tagging immutable images ====="

docker tag \
  "$VM1_LATEST_IMAGE" \
  "$VM1_VERSIONED_IMAGE"

docker tag \
  "$VM1_LATEST_IMAGE" \
  "$VM2_VERSIONED_IMAGE"

echo
echo "===== Pushing image to VM1 registry ====="

docker push "$VM1_VERSIONED_IMAGE"

echo
echo "===== Pushing identical image to VM2 registry ====="

docker push "$VM2_VERSIONED_IMAGE"

echo
echo "===== Verifying registry tags ====="

VM1_TAGS="$(
  curl -fsS \
    "http://127.0.0.1:5000/v2/elif/${FUNCTION_NAME}/tags/list"
)"

VM2_TAGS="$(
  curl -fsS \
    "http://127.0.0.1:5001/v2/elif/${FUNCTION_NAME}/tags/list"
)"

python3 - \
  "$FUNCTION_NAME" \
  "$IMAGE_TAG" \
  "$VM1_TAGS" \
  "$VM2_TAGS" <<'PY'
import json
import sys

function_name = sys.argv[1]
required_tag = sys.argv[2]
vm1 = json.loads(sys.argv[3])
vm2 = json.loads(sys.argv[4])

for registry_name, value in (
    ("VM1", vm1),
    ("VM2", vm2),
):
    tags = value.get("tags") or []

    if required_tag not in tags:
        raise SystemExit(
            f"{registry_name} registry is missing "
            f"{function_name}:{required_tag}"
        )

    print(
        f"{registry_name}: "
        f"{function_name}:{required_tag} present"
    )
PY

CREATED_AT="$(
  date -u +%Y-%m-%dT%H:%M:%SZ
)"

python3 - \
  "$BUILD_RESULT" \
  "$FUNCTION_NAME" \
  "$IMAGE_TAG" \
  "$CREATED_AT" \
  "$SOURCE_DIRECTORY" \
  "$WORK_DIRECTORY" \
  "$VM1_VERSIONED_IMAGE" \
  "$VM2_VERSIONED_IMAGE" \
  "$RUNTIME_IMAGE" \
  "$LOCAL_IMAGE_ID" \
  "$PLATFORM" \
  "$BUILDER" <<'PY'
import json
from pathlib import Path
import sys

(
    output_path,
    function_name,
    image_tag,
    created_at,
    source_directory,
    work_directory,
    vm1_push_image,
    vm2_push_image,
    runtime_image,
    local_image_id,
    platform,
    builder,
) = sys.argv[1:]

result = {
    "schema_version": 1,
    "function": function_name,
    "image_tag": image_tag,
    "created_at": created_at,
    "source_directory": source_directory,
    "temporary_work_directory": work_directory,
    "vm1_push_image": vm1_push_image,
    "vm2_push_image": vm2_push_image,
    "runtime_image": runtime_image,
    "local_image_id": local_image_id,
    "platform": platform,
    "builder": builder,
    "source_modified": False,
    "pushed_to_vm1": True,
    "pushed_to_vm2": True,
}

path = Path(output_path)
path.write_text(
    json.dumps(result, indent=2) + "\n",
    encoding="utf-8",
)
PY

cp "$BUILD_RESULT" "$LATEST_BUILD_RESULT"

echo
echo "===== Build completed ====="
echo "Function:      $FUNCTION_NAME"
echo "Tag:           $IMAGE_TAG"
echo "Runtime image: $RUNTIME_IMAGE"
echo "Build record:  $BUILD_RESULT"
echo
echo "The original function project was not modified."
