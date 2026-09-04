#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/honeybuy-tg
ENV_DIR=/etc/honeybuy-tg
DATA_DIR=/var/lib/honeybuy-tg
CACHE_DIR=/var/cache/honeybuy-tg
STATE_DIR=/var/lib/honeybuy-release-controller
BACKUP_DIR=/var/backups/honeybuy-tg
LOCK_DIR=/run/honeybuy-release-controller
LOCK_PATH=/run/honeybuy-release-controller/controller.lock
RELEASE_CONTROLLER_BIN=/usr/local/lib/honeybuy/release_controller.py
ALLOWED_SIGNERS_FILE=/etc/honeybuy-tg/allowed_signers
UV_BIN=/usr/local/bin/uv
UV_VERSION=0.11.6
UV_SHA256=0c6bab77a67a445dc849ed5e8ee8d3cb333b6e2eba863643ce1e228075f27943
SERVICE_NAME=honeybuy-tg
CONTROLLER_SERVICE_NAME=honeybuy-release-controller.service
TIMER_NAME=honeybuy-release-controller.timer

ENV_FILE=/etc/honeybuy-tg/env
REPOSITORY_DIR=/var/lib/honeybuy-release-controller/repository
DEPLOYED_STATE_FILE=/var/lib/honeybuy-release-controller/deployed-sha
JOURNAL_FILE=/var/lib/honeybuy-release-controller/deployment-journal.json
BOOTSTRAP_JOURNAL_FILE=/var/lib/honeybuy-release-controller/bootstrap-journal.json
CONTROL_PLANE_MANIFEST_FILE=/var/lib/honeybuy-release-controller/control-plane-manifest.json
CURRENT_LINK=/opt/honeybuy-tg/current
EXPECTED_SIGNER_FINGERPRINT=SHA256:mktJ6te9V48RCf8Aw5+ihHZ6wdUTvjQX++rWA6jxxbY
UV_ARTIFACT=uv-x86_64-unknown-linux-gnu.tar.gz
UV_URL=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ARTIFACT

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 0027
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)

fail() {
  printf 'Installation rejected: %s\n' "$1" >&2
  exit 1
}

assert_not_symlink() {
  local target=$1
  if [[ -L "$target" || -h "$target" ]]; then
    fail "unsafe symbolic link at $target"
  fi
}

assert_safe_existing() {
  local target=$1
  local expected_type=$2
  local expected_owner=$3
  local expected_group=$4
  local visibility=$5
  local exact_mode=${6:-}
  local actual_owner
  local actual_group
  local actual_mode
  local numeric_mode

  if [[ ! -e "$target" ]]; then
    return 0
  fi
  case "$expected_type" in
    directory)
      [[ -d "$target" ]] || fail "$target is not a safe directory"
      ;;
    file)
      [[ -f "$target" ]] || fail "$target is not a safe regular file"
      ;;
    *)
      fail "invalid metadata policy for $target"
      ;;
  esac

  actual_owner=$(stat -c '%U' -- "$target")
  actual_group=$(stat -c '%G' -- "$target")
  actual_mode=$(stat -c '%a' -- "$target")
  [[ "$actual_owner" == "$expected_owner" ]] || fail "$target has an unexpected owner"
  [[ "$actual_group" == "$expected_group" ]] || fail "$target has an unexpected group"
  numeric_mode=$((8#$actual_mode))
  (( (numeric_mode & 0022) == 0 )) || fail "$target is group- or world-writable"
  if [[ "$visibility" == private ]]; then
    (( (numeric_mode & 0077) == 0 )) || fail "$target exposes private state"
  fi
  if [[ -n "$exact_mode" ]]; then
    [[ "$actual_mode" == "${exact_mode#0}" || "$actual_mode" == "$exact_mode" ]] || fail "$target has an unexpected mode"
  fi
}

validate_signer_file() {
  local target=$1
  local signer_line
  local principal
  local options
  local key_type
  local key_data
  local trailing
  local fingerprint
  local line_count

  if [[ ! -e "$target" ]]; then
    return 0
  fi
  [[ -f "$target" && ! -L "$target" ]] || fail "$target is not a safe signer file"
  line_count=$(awk 'NF && $1 !~ /^#/ { count++ } END { print count + 0 }' "$target")
  [[ "$line_count" == 1 ]] || fail "$target must contain exactly one signer"
  signer_line=$(awk 'NF && $1 !~ /^#/ { print }' "$target")
  read -r principal options key_type key_data trailing <<<"$signer_line"
  [[ "$principal" == atiurin@proton.me ]] || fail "$target has an unexpected principal"
  [[ "$options" == 'namespaces="git"' ]] || fail "$target is not restricted to Git signatures"
  [[ "$key_type" == ssh-rsa && -n "$key_data" && -z "$trailing" ]] || fail "$target has malformed key data"
  fingerprint=$(printf '%s %s\n' "$key_type" "$key_data" | ssh-keygen -lf - -E sha256 | awk '{ print $2 }')
  [[ "$fingerprint" == "$EXPECTED_SIGNER_FINGERPRINT" ]] || fail "$target has the wrong signer fingerprint"
}

preflight_bootstrap_journal() {
  local deployed_sha
  local marker_status

  assert_not_symlink "$BOOTSTRAP_JOURNAL_FILE"
  assert_safe_existing "$BOOTSTRAP_JOURNAL_FILE" file root root private 0600
  if [[ ! -e "$BOOTSTRAP_JOURNAL_FILE" ]]; then
    return 0
  fi
  [[ "$(stat -c '%h' -- "$BOOTSTRAP_JOURNAL_FILE")" == 1 ]] || fail "$BOOTSTRAP_JOURNAL_FILE must not have hard links"
  [[ -f "$DEPLOYED_STATE_FILE" && ! -L "$DEPLOYED_STATE_FILE" ]] || fail "bootstrap marker requires canonical deployed state"
  [[ "$(wc -l < "$DEPLOYED_STATE_FILE")" -eq 1 ]] || fail "bootstrap marker requires one deployed SHA"
  IFS= read -r deployed_sha < "$DEPLOYED_STATE_FILE" || fail "bootstrap marker requires readable deployed SHA"
  [[ "$deployed_sha" =~ ^[0-9a-f]{40}$ ]] || fail "bootstrap marker references a non-canonical deployed SHA"

  set +e
  /usr/bin/python3 -c 'import json,re,sys; path,sha=sys.argv[1:3]; expected={"version":1,"candidate_sha":sha,"phase":"awaiting_service","phases":["intent","release_prepared","current_linked","state_written","awaiting_service"]}; data=json.load(open(path,encoding="utf-8")); ok=isinstance(data,dict) and data==expected and re.fullmatch(r"[0-9a-f]{40}",data.get("candidate_sha","")) is not None; raise SystemExit(0 if ok else 1)' "$BOOTSTRAP_JOURNAL_FILE" "$deployed_sha"
  marker_status=$?
  set -e
  [[ "$marker_status" -eq 0 ]] || fail "bootstrap marker is not the exact awaiting_service state"
}

require_awaiting_service_bot_inactive() {
  local service_status
  local systemctl_status
  local load_state
  local active_state
  local sub_state

  if [[ ! -e "$BOOTSTRAP_JOURNAL_FILE" ]]; then
    return 0
  fi

  set +e
  service_status=$(systemctl show "$SERVICE_NAME.service" --property=LoadState --property=ActiveState --property=SubState --value)
  systemctl_status=$?
  set -e
  [[ "$systemctl_status" -eq 0 ]] || fail "bootstrap requires a managed $SERVICE_NAME.service unit"
  load_state=$(sed -n '1p' <<<"$service_status")
  active_state=$(sed -n '2p' <<<"$service_status")
  sub_state=$(sed -n '3p' <<<"$service_status")
  [[ "$load_state" == loaded ]] || fail "bootstrap requires $SERVICE_NAME.service LoadState=loaded"
  [[ "$active_state" == inactive ]] || fail "bootstrap requires $SERVICE_NAME.service ActiveState=inactive"
  [[ "$sub_state" == dead ]] || fail "bootstrap requires $SERVICE_NAME.service SubState=dead"
}

deployment_state_is_coherent() {
  local deployed_sha
  local release
  local current_target
  local ready_manifest
  local uv_lock
  local manifest_values
  local manifest_sha
  local manifest_digest
  local extra
  local actual_digest
  local release_mode
  local link
  local link_target
  local link_target_name
  local link_target_parent
  local link_relative

  [[ -f "$DEPLOYED_STATE_FILE" && ! -L "$DEPLOYED_STATE_FILE" ]] || return 1
  [[ ! -e "$JOURNAL_FILE" && ! -L "$JOURNAL_FILE" ]] || return 1
  [[ -L "$CURRENT_LINK" ]] || return 1
  [[ "$(wc -l < "$DEPLOYED_STATE_FILE")" -eq 1 ]] || return 1
  IFS= read -r deployed_sha < "$DEPLOYED_STATE_FILE" || return 1
  [[ "$deployed_sha" =~ ^[0-9a-f]{40}$ ]] || return 1

  release="$APP_DIR/releases/$deployed_sha"
  current_target=$(readlink -f -- "$CURRENT_LINK") || return 1
  [[ "$current_target" == "$release" ]] || return 1
  [[ -d "$release" && ! -L "$release" ]] || return 1
  [[ "$(stat -c '%U:%G' -- "$release")" == root:root ]] || return 1
  release_mode=$(stat -c '%a' -- "$release") || return 1
  (( (8#$release_mode & 0022) == 0 )) || return 1
  if find "$release" -xdev ! -type d ! -type f ! -type l -print -quit | grep -q .; then
    return 1
  fi
  if find "$release" -xdev ! -type l \( ! -user root -o -perm /022 \) -print -quit | grep -q .; then
    return 1
  fi
  while IFS= read -r -d '' link; do
    [[ "$(stat -c '%U:%G' -- "$link")" == root:root ]] || return 1
    link_relative=${link#"$release"/}
    if [[ "$link_relative" == ".venv/lib64" ]]; then
      [[ "$(readlink -- "$link")" == "lib" ]] || return 1
      [[ -d "$release/.venv/lib" && ! -L "$release/.venv/lib" ]] || return 1
      link_target=$(readlink -f -- "$link") || return 1
      [[ "$link_target" == "$release/.venv/lib" ]] || return 1
      continue
    fi
    [[ "$link_relative" =~ ^\.venv/bin/python(3(\.[0-9]+)?)?$ ]] || return 1
    link_target=$(readlink -f -- "$link") || return 1
    link_target_name=${link_target##*/}
    link_target_parent=${link_target%/*}
    [[ "$link_target_name" =~ ^python(3(\.[0-9]+)?)?$ ]] || return 1
    [[ "$link_target_parent" == "$release/.venv/bin" || "$link_target_parent" == /usr/bin || "$link_target_parent" == /usr/local/bin ]] || return 1
  done < <(find "$release" -xdev -type l -print0)

  ready_manifest="$release/.ready.json"
  uv_lock="$release/uv.lock"
  [[ -f "$ready_manifest" && ! -L "$ready_manifest" ]] || return 1
  [[ -f "$uv_lock" && ! -L "$uv_lock" ]] || return 1
  [[ -x "$release/.venv/bin/python" ]] || return 1
  manifest_values=$(
    /usr/bin/python3 -c 'import hashlib,json,re,sys; mp,lp=sys.argv[1:3]; data=json.load(open(mp,encoding="utf-8")); ek={"version","sha","source_tree_sha256","archive_sha256","uv_lock_sha256","venv_sha256","artifact_sha256","provenance_sha256"}; pf=("sha","source_tree_sha256","archive_sha256","uv_lock_sha256","venv_sha256","artifact_sha256"); df=pf[1:]+("provenance_sha256",); ok=isinstance(data,dict) and set(data)==ek and data.get("version")==2 and isinstance(data.get("sha"),str) and re.fullmatch(r"[0-9a-f]{40}",data["sha"]) is not None and all(isinstance(data.get(f),str) and re.fullmatch(r"[0-9a-f]{64}",data[f]) is not None for f in df); actual=hashlib.sha256(open(lp,"rb").read()).hexdigest() if ok else ""; prov={f:data[f] for f in pf} if ok else {}; expected=hashlib.sha256(json.dumps(prov,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest() if ok else ""; ok=ok and data["uv_lock_sha256"]==actual and data["provenance_sha256"]==expected; print(data["sha"],data["uv_lock_sha256"]) if ok else sys.exit(1)' "$ready_manifest" "$uv_lock"
  ) || return 1
  read -r manifest_sha manifest_digest extra <<<"$manifest_values"
  [[ -z "$extra" && "$manifest_sha" == "$deployed_sha" ]] || return 1
  [[ "$manifest_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  actual_digest=$(sha256sum "$uv_lock" | awk '{ print $1 }') || return 1
  [[ "$actual_digest" == "$manifest_digest" ]] || return 1
}

cleanup_staging() {
  if [[ -n "${STAGING_DIR:-}" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
}

atomic_replace_file() {
  local source=$1
  local destination=$2
  local mode=$3
  local owner=$4
  local group=$5
  local destination_dir
  local temporary

  destination_dir=$(dirname -- "$destination")
  temporary=$(mktemp --tmpdir="$destination_dir" ".${destination##*/}.XXXXXXXX")
  install -m "$mode" -o "$owner" -g "$group" "$source" "$temporary"
  mv -T -- "$temporary" "$destination"
}

install_file_if_absent() {
  local source=$1
  local destination=$2
  local mode=$3
  local owner=$4
  local group=$5
  local destination_dir
  local temporary

  if [[ -e "$destination" || -L "$destination" ]]; then
    return 0
  fi

  destination_dir=$(dirname -- "$destination")
  temporary=$(mktemp "$destination_dir/.${destination##*/}.XXXXXXXX")
  if ! install -m "$mode" -o "$owner" -g "$group" "$source" "$temporary"; then
    rm -f -- "$temporary"
    fail "could not prepare $destination"
  fi
  if ln -T -- "$temporary" "$destination" 2>/dev/null; then
    rm -f -- "$temporary"
    return 0
  fi

  rm -f -- "$temporary"
  if [[ -e "$destination" || -L "$destination" ]]; then
    return 0
  fi
  fail "could not install $destination without replacing it"
}

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  fail "run this installer as root"
fi
if [[ "$(uname -m)" != x86_64 ]]; then
  fail "the pinned uv artifact supports x86_64 hosts only"
fi

for source_file in \
  "$REPO_ROOT/deploy/ubuntu/env.example" \
  "$REPO_ROOT/deploy/ubuntu/allowed_signers" \
  "$REPO_ROOT/deploy/ubuntu/release_controller.py" \
  "$REPO_ROOT/deploy/systemd/honeybuy-tg.service" \
  "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service" \
  "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer"
do
  [[ -f "$source_file" && ! -L "$source_file" ]] || fail "unsafe or missing installer source: $source_file"
done

if systemctl cat "$TIMER_NAME" >/dev/null 2>&1; then
  systemctl stop "$TIMER_NAME"
  systemctl disable "$TIMER_NAME"
fi

assert_not_symlink "$LOCK_DIR"
assert_safe_existing "$LOCK_DIR" directory root root private
install -d -m 0700 -o root -g root "$LOCK_DIR"
assert_not_symlink "$LOCK_PATH"
assert_safe_existing "$LOCK_PATH" file root root private
exec {controller_lock_fd}>"$LOCK_PATH"
flock -x -w 2100 "$controller_lock_fd" || fail "timed out waiting for the release controller"
chmod 0600 "$LOCK_PATH"
if [[ -e "$JOURNAL_FILE" || -L "$JOURNAL_FILE" ]]; then
  fail "unfinished deployment journal at $JOURNAL_FILE; recover with systemctl start honeybuy-release-controller.service before reinstalling"
fi
preflight_bootstrap_journal
require_awaiting_service_bot_inactive
assert_not_symlink "$CONTROL_PLANE_MANIFEST_FILE"
assert_safe_existing "$CONTROL_PLANE_MANIFEST_FILE" file root root private 0600

write_control_plane_manifest() {
  local state=$1
  local manifest_tmp
  local controller_source="$REPO_ROOT/deploy/ubuntu/release_controller.py"
  local controller_destination="$RELEASE_CONTROLLER_BIN"
  local signer_source="$REPO_ROOT/deploy/ubuntu/allowed_signers"
  local signer_destination="$ALLOWED_SIGNERS_FILE"
  local app_unit_source="$REPO_ROOT/deploy/systemd/honeybuy-tg.service"
  local app_unit_destination="/etc/systemd/system/$SERVICE_NAME.service"
  local controller_unit_source="$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service"
  local controller_unit_destination="/etc/systemd/system/honeybuy-release-controller.service"
  local timer_unit_source="$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer"
  local timer_unit_destination="/etc/systemd/system/honeybuy-release-controller.timer"
  local controller_source_digest
  local controller_destination_digest
  local signer_source_digest
  local signer_destination_digest
  local app_unit_source_digest
  local app_unit_destination_digest=
  local controller_unit_source_digest
  local controller_unit_destination_digest
  local timer_unit_source_digest
  local timer_unit_destination_digest

  [[ "$state" == bootstrap_pending || "$state" == installed ]] || fail "invalid control-plane manifest state"
  controller_source_digest=$(sha256sum "$controller_source" | awk '{ print $1 }')
  controller_destination_digest=$(sha256sum "$controller_destination" | awk '{ print $1 }')
  [[ "$controller_source_digest" == "$controller_destination_digest" ]] || fail "release controller digest mismatch"
  signer_source_digest=$(sha256sum "$signer_source" | awk '{ print $1 }')
  signer_destination_digest=$(sha256sum "$signer_destination" | awk '{ print $1 }')
  [[ "$signer_source_digest" == "$signer_destination_digest" ]] || fail "allowed signer digest mismatch"
  controller_unit_source_digest=$(sha256sum "$controller_unit_source" | awk '{ print $1 }')
  controller_unit_destination_digest=$(sha256sum "$controller_unit_destination" | awk '{ print $1 }')
  [[ "$controller_unit_source_digest" == "$controller_unit_destination_digest" ]] || fail "controller unit digest mismatch"
  timer_unit_source_digest=$(sha256sum "$timer_unit_source" | awk '{ print $1 }')
  timer_unit_destination_digest=$(sha256sum "$timer_unit_destination" | awk '{ print $1 }')
  [[ "$timer_unit_source_digest" == "$timer_unit_destination_digest" ]] || fail "controller timer digest mismatch"
  app_unit_source_digest=$(sha256sum "$app_unit_source" | awk '{ print $1 }')
  if [[ "$state" == installed ]]; then
    app_unit_destination_digest=$(sha256sum "$app_unit_destination" | awk '{ print $1 }')
    [[ "$app_unit_source_digest" == "$app_unit_destination_digest" ]] || fail "bot unit digest mismatch"
  fi

  manifest_tmp="$STAGING_DIR/control-plane-manifest.json"
  /usr/bin/python3 -c 'import json,sys; state,path,*values=sys.argv[1:]; it=iter(values); files=dict(zip(it,it)); data={"version":1,"phase":state,"files":files}; open(path,"w",encoding="utf-8").write(json.dumps(data,sort_keys=True,separators=(",",":"))+"\n")' "$state" "$manifest_tmp" \
    deploy/ubuntu/release_controller.py "$controller_source_digest" \
    deploy/ubuntu/allowed_signers "$signer_source_digest" \
    deploy/systemd/honeybuy-tg.service "$app_unit_source_digest" \
    deploy/systemd/honeybuy-release-controller.service "$controller_unit_source_digest" \
    deploy/systemd/honeybuy-release-controller.timer "$timer_unit_source_digest"
  atomic_replace_file "$manifest_tmp" "$CONTROL_PLANE_MANIFEST_FILE" 0600 root root
}

apt-get update
apt-get install -y ca-certificates curl ffmpeg git openssh-client python3 tar util-linux
/usr/bin/python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' || fail "Python 3.13 or newer is required"

if ! getent group honeybuy >/dev/null; then
  groupadd --system honeybuy
fi
if ! getent group honeybuy-build >/dev/null; then
  groupadd --system honeybuy-build
fi
if ! id -u honeybuy >/dev/null 2>&1; then
  useradd --system --gid honeybuy --home-dir /var/lib/honeybuy-tg --no-create-home --shell /usr/sbin/nologin honeybuy
fi
if ! id -u honeybuy-build >/dev/null 2>&1; then
  useradd --system --gid honeybuy-build --home-dir /var/lib/honeybuy-release-controller --no-create-home --shell /usr/sbin/nologin honeybuy-build
fi
[[ "$(id -gn honeybuy)" == honeybuy ]] || fail "honeybuy has the wrong primary group"
[[ "$(id -gn honeybuy-build)" == honeybuy-build ]] || fail "honeybuy-build has the wrong primary group"
[[ "$(getent passwd honeybuy | cut -d: -f7)" == /usr/sbin/nologin ]] || fail "honeybuy has an unsafe login shell"
[[ "$(getent passwd honeybuy-build | cut -d: -f7)" == /usr/sbin/nologin ]] || fail "honeybuy-build has an unsafe login shell"

assert_not_symlink "$APP_DIR"
assert_safe_existing "$APP_DIR" directory root root protected
assert_not_symlink "$APP_DIR/releases"
assert_safe_existing "$APP_DIR/releases" directory root root protected
assert_not_symlink "$ENV_DIR"
assert_safe_existing "$ENV_DIR" directory root root protected
assert_not_symlink "$ENV_FILE"
assert_safe_existing "$ENV_FILE" file root root private
assert_not_symlink "$DATA_DIR"
assert_safe_existing "$DATA_DIR" directory honeybuy honeybuy protected
assert_not_symlink "$CACHE_DIR"
assert_safe_existing "$CACHE_DIR" directory root root protected
assert_not_symlink "$CACHE_DIR/uv"
assert_safe_existing "$CACHE_DIR/uv" directory honeybuy-build honeybuy-build protected
assert_not_symlink "$STATE_DIR"
assert_safe_existing "$STATE_DIR" directory root root protected
assert_not_symlink "$REPOSITORY_DIR"
assert_safe_existing "$REPOSITORY_DIR" directory root root protected
assert_not_symlink "$STATE_DIR/scratch"
assert_safe_existing "$STATE_DIR/scratch" directory root root protected
assert_not_symlink "$DEPLOYED_STATE_FILE"
assert_safe_existing "$DEPLOYED_STATE_FILE" file root root protected
assert_not_symlink "$JOURNAL_FILE"
assert_safe_existing "$JOURNAL_FILE" file root root private
assert_not_symlink "$BACKUP_DIR"
assert_safe_existing "$BACKUP_DIR" directory root root private
assert_not_symlink "$ALLOWED_SIGNERS_FILE"
assert_safe_existing "$ALLOWED_SIGNERS_FILE" file root root protected
assert_not_symlink /usr/local/lib/honeybuy
assert_safe_existing /usr/local/lib/honeybuy directory root root protected
assert_not_symlink "$UV_BIN"
assert_safe_existing "$UV_BIN" file root root protected
assert_not_symlink /var/empty/honeybuy-healthcheck
assert_safe_existing /var/empty/honeybuy-healthcheck directory root root protected
assert_not_symlink "$RELEASE_CONTROLLER_BIN"
assert_safe_existing "$RELEASE_CONTROLLER_BIN" file root root protected
assert_not_symlink "/etc/systemd/system/$SERVICE_NAME.service"
assert_safe_existing "/etc/systemd/system/$SERVICE_NAME.service" file root root protected
assert_not_symlink /etc/systemd/system/honeybuy-release-controller.service
assert_safe_existing /etc/systemd/system/honeybuy-release-controller.service file root root protected
assert_not_symlink /etc/systemd/system/honeybuy-release-controller.timer
assert_safe_existing /etc/systemd/system/honeybuy-release-controller.timer file root root protected

install -d -m 0755 -o root -g root "$APP_DIR"
install -d -m 0755 -o root -g root "$APP_DIR/releases"
install -d -m 0750 -o root -g root "$ENV_DIR"
install -d -m 0750 -o honeybuy -g honeybuy "$DATA_DIR"
install -d -m 0755 -o root -g root "$CACHE_DIR"
install -d -m 0755 -o honeybuy-build -g honeybuy-build "$CACHE_DIR/uv"
install -d -m 0711 -o root -g root "$STATE_DIR"
install -d -m 0700 -o root -g root "$STATE_DIR/repository"
install -d -m 0711 -o root -g root "$STATE_DIR/scratch"
install -d -m 0700 -o root -g root "$BACKUP_DIR"
install -d -m 0755 -o root -g root /usr/local/lib/honeybuy
install -d -m 0755 -o root -g root /var/empty/honeybuy-healthcheck

if [[ ! -e "$ENV_FILE" ]]; then
  install -m 0600 -o root -g root "$REPO_ROOT/deploy/ubuntu/env.example" "$ENV_FILE"
  printf 'Created %s; configure its secrets before starting the bot.\n' "$ENV_FILE"
fi

validate_signer_file "$REPO_ROOT/deploy/ubuntu/allowed_signers"
validate_signer_file "$ALLOWED_SIGNERS_FILE"
install_file_if_absent \
  "$REPO_ROOT/deploy/ubuntu/allowed_signers" \
  "$ALLOWED_SIGNERS_FILE" \
  0644 root root
validate_signer_file "$ALLOWED_SIGNERS_FILE"

STAGING_DIR=$(mktemp -d /tmp/honeybuy-install.XXXXXXXX)
trap cleanup_staging EXIT
UV_ARCHIVE_PATH="$STAGING_DIR/$UV_ARTIFACT"
curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 300 --retry 3 --output "$UV_ARCHIVE_PATH" "$UV_URL"
printf '%s  %s\n' "$UV_SHA256" "$UV_ARCHIVE_PATH" | sha256sum --check --strict -
tar --extract --gzip --file "$UV_ARCHIVE_PATH" --directory "$STAGING_DIR"
UV_EXTRACTED_BIN="$STAGING_DIR/uv-x86_64-unknown-linux-gnu/uv"
[[ -f "$UV_EXTRACTED_BIN" && ! -L "$UV_EXTRACTED_BIN" && -x "$UV_EXTRACTED_BIN" ]] || fail "uv archive has an unexpected layout"
atomic_replace_file "$UV_EXTRACTED_BIN" "$UV_BIN" 0755 root root
case "$("$UV_BIN" --version)" in "uv $UV_VERSION" | "uv $UV_VERSION "*) ;; *) fail "installed uv version is not pinned" ;; esac

install -m 0755 -o root -g root "$REPO_ROOT/deploy/ubuntu/release_controller.py" "$STAGING_DIR/release_controller.py"
install -m 0644 -o root -g root "$REPO_ROOT/deploy/systemd/honeybuy-tg.service" "$STAGING_DIR/honeybuy-tg.service"
install -m 0644 -o root -g root "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service" "$STAGING_DIR/honeybuy-release-controller.service"
install -m 0644 -o root -g root "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer" "$STAGING_DIR/honeybuy-release-controller.timer"

atomic_replace_file "$STAGING_DIR/release_controller.py" "$RELEASE_CONTROLLER_BIN" 0755 root root
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$STAGING_DIR/honeybuy-release-controller.service" "$STAGING_DIR/honeybuy-release-controller.timer"
fi
atomic_replace_file "$STAGING_DIR/honeybuy-release-controller.service" /etc/systemd/system/honeybuy-release-controller.service 0644 root root
atomic_replace_file "$STAGING_DIR/honeybuy-release-controller.timer" /etc/systemd/system/honeybuy-release-controller.timer 0644 root root
systemctl daemon-reload

if deployment_state_is_coherent; then
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$STAGING_DIR/honeybuy-tg.service"
  fi
  atomic_replace_file "$STAGING_DIR/honeybuy-tg.service" "/etc/systemd/system/$SERVICE_NAME.service" 0644 root root
  systemctl daemon-reload
  write_control_plane_manifest installed
  systemctl enable "$SERVICE_NAME.service"
  systemctl enable --now "$TIMER_NAME"
else
  systemctl disable "$TIMER_NAME" 2>/dev/null || true
  write_control_plane_manifest bootstrap_pending
  printf 'Control plane installed, but automatic deployment remains disabled.\n' >&2
  printf 'Bootstrap the existing host in this order:\n' >&2
  printf '1. systemctl stop honeybuy-tg.service\n' >&2
  printf '2. systemctl start honeybuy-release-controller.service\n' >&2
  printf '3. rerun this installer\n' >&2
  printf '4. systemctl start honeybuy-tg.service\n' >&2
  printf '5. systemctl is-active --quiet honeybuy-tg.service and inspect journalctl -u honeybuy-tg.service\n' >&2
  printf 'See docs/operations-and-testing.md for the bootstrap procedure.\n' >&2
  exit 1
fi

printf 'Install complete. The release timer is active; the bot was not restarted.\n'
