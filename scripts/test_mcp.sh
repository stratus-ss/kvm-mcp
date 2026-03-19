#!/bin/bash
# End-to-end test for the MCP server — validates every tool against host state.
# Mirrors the structure of test_quickstart.sh but exercises the MCP interface.
#
# Usage: bash scripts/test_mcp.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PASS=0
FAIL=0
SKIP=0
TEST_VM="mcp-test-$(date +%s)"
TEST_SNAPSHOT="mcp-snap-$(date +%s)"
DISK_PATH="/var/lib/libvirt/images"
DISK_FILE="$DISK_PATH/${TEST_VM}.qcow2"
AUDIT_DIR=""
BRIDGE_PID=""

# --- colours & logging -----------------------------------------------------

red()    { printf '\033[1;31m%s\033[0m' "$*"; }
green()  { printf '\033[1;32m%s\033[0m' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m' "$*"; }
bold()   { printf '\033[1m%s\033[0m' "$*"; }

log_pass() { PASS=$((PASS + 1)); echo "  $(green PASS) $1"; }
log_fail() { FAIL=$((FAIL + 1)); echo "  $(red FAIL) $1${2:+ — $2}"; }
log_skip() { SKIP=$((SKIP + 1)); echo "  $(yellow SKIP) $1${2:+ — $2}"; }

# --- virsh helpers (same as test_quickstart.sh) -----------------------------

virsh_state() { sudo virsh domstate "$1" 2>/dev/null | head -1; }

virsh_vm_exists() { sudo virsh dominfo "$1" &>/dev/null; }

virsh_snapshot_exists() {
    sudo virsh snapshot-list "$1" 2>/dev/null | awk 'NR>2{print $1}' | grep -qx "$2"
}

virsh_boot_order() {
    sudo virsh dumpxml "$1" --inactive 2>/dev/null \
        | grep -oP "boot dev=['\"]\\K[^'\"]+" | paste -sd,
}

wait_for_state() {
    local vm="$1" target="$2" max="${3:-30}"
    for _ in $(seq 1 "$max"); do
        [[ "$(virsh_state "$vm")" == "$target" ]] && return 0
        sleep 1
    done
    return 1
}

# --- MCP bridge helpers -----------------------------------------------------

start_bridge() {
    local venv_python="$PROJECT_DIR/kvm-venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        venv_python="$PROJECT_DIR/.venv/bin/python"
    fi
    if [[ ! -x "$venv_python" ]]; then
        venv_python="python3"
    fi

    AUDIT_DIR=$(mktemp -d /tmp/mcp-audit-XXXXXX)
    export MCP_AUDIT_LOG_DIR="$AUDIT_DIR"
    # Force local connection regardless of .env KVM_HOST setting
    export KVM_HOST=""
    export KVM_HOSTS_FILE=""

    coproc MCP_BRIDGE { "$venv_python" "$SCRIPT_DIR/_mcp_bridge.py"; }
    BRIDGE_PID=$MCP_BRIDGE_PID

    local ready
    read -r -t 15 ready <&${MCP_BRIDGE[0]}
    if [[ "$ready" != "READY" ]]; then
        echo "  $(red 'ERROR'): MCP bridge did not start (got: $ready)"
        return 1
    fi
    return 0
}

mcpcall() {
    echo "$*" >&${MCP_BRIDGE[1]}
    local resp
    read -r -t 120 resp <&${MCP_BRIDGE[0]}
    echo "$resp"
}

mcp_text() {
    local resp="$1"
    python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('text',''))" "$resp" 2>/dev/null
}

mcp_error() {
    local resp="$1"
    python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('error',''))" "$resp" 2>/dev/null
}

mcp_json_field() {
    local resp="$1" field="$2"
    python3 -c "
import sys,json
r=json.loads(sys.argv[1])
v=r.get(sys.argv[2], r.get('text',''))
if isinstance(v,str):
    try: v=json.loads(v)
    except: pass
print(json.dumps(v))
" "$resp" "$field" 2>/dev/null
}

mcp_json_len() {
    local json_str="$1"
    python3 -c "import sys,json; print(len(json.loads(sys.argv[1])))" "$json_str" 2>/dev/null
}

# --- cleanup ----------------------------------------------------------------

cleanup() {
    echo ""
    echo "$(bold 'Cleanup...')"

    if virsh_vm_exists "$TEST_VM"; then
        if [[ "$(virsh_state "$TEST_VM")" == "running" ]]; then
            sudo virsh destroy "$TEST_VM" &>/dev/null
        fi
        sudo virsh undefine "$TEST_VM" --snapshots-metadata --remove-all-storage &>/dev/null
        echo "  Removed test VM $TEST_VM"
    fi

    if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
        echo "QUIT" >&${MCP_BRIDGE[1]} 2>/dev/null || true
        wait "$BRIDGE_PID" 2>/dev/null || true
        echo "  Stopped MCP bridge"
    fi

    if [[ -n "$AUDIT_DIR" && -d "$AUDIT_DIR" ]]; then
        rm -rf "$AUDIT_DIR"
        echo "  Cleaned up audit dir"
    fi
}
trap cleanup EXIT

# ============================================================================
# TESTS
# ============================================================================

echo "$(bold '=== MCP Server End-to-End Test ===')"
echo "  Test VM  : $TEST_VM"
echo "  Disk     : $DISK_FILE"
echo ""

# === [1/10] Prerequisites ===================================================

echo "$(bold '[1/10] Prerequisites')"

for cmd in python3 virsh virt-install qemu-img; do
    if command -v "$cmd" &>/dev/null; then
        log_pass "$cmd found"
    else
        log_fail "$cmd not found"
    fi
done

# Check for venv with mcp package
VENV_PYTHON="$PROJECT_DIR/kvm-venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
fi
if [[ -x "$VENV_PYTHON" ]]; then
    log_pass "Virtual environment found"
    if "$VENV_PYTHON" -c "import mcp" &>/dev/null; then
        log_pass "mcp package installed"
    else
        log_fail "mcp package not installed in venv"; exit 1
    fi
    if "$VENV_PYTHON" -c "import libvirt" &>/dev/null; then
        log_pass "libvirt-python package installed"
    else
        log_fail "libvirt-python package not installed in venv"; exit 1
    fi
else
    log_fail "No virtual environment found (kvm-venv or .venv)"; exit 1
fi

if sudo -n true &>/dev/null; then
    log_pass "sudo configured"
else
    log_skip "sudo passwordless not configured" "some virsh checks may fail"
fi

# === [2/10] Start MCP bridge ================================================

echo ""
echo "$(bold '[2/10] Start MCP bridge')"

if start_bridge; then
    log_pass "MCP bridge started (PID $BRIDGE_PID)"
else
    log_fail "MCP bridge failed to start"; exit 1
fi

# === [3/10] Discovery ========================================================

echo ""
echo "$(bold '[3/10] Discovery (tools, resources, prompts)')"

# Tools
resp=$(mcpcall "TOOLS")
tools=$(mcp_json_field "$resp" "tools")
tool_count=$(mcp_json_len "$tools")
if [[ "$tool_count" == "28" ]]; then
    log_pass "28 tools registered"
else
    log_fail "Tool count" "expected 28, got $tool_count"
fi

for tool_name in kvm_list_vms kvm_create_vm kvm_delete_vm guest_exec guest_inject_ssh_key kvm_list_hosts kvm_fleet_status; do
    if echo "$tools" | grep -q "\"$tool_name\""; then
        log_pass "Tool $tool_name present"
    else
        log_fail "Tool $tool_name missing"
    fi
done

# Static resources
resp=$(mcpcall "RESOURCES")
resources=$(mcp_json_field "$resp" "resources")
for uri in "kvm://vms" "kvm://networks" "kvm://hosts" "kvm://storage-pools"; do
    if echo "$resources" | grep -q "\"$uri\""; then
        log_pass "Resource $uri present"
    else
        log_fail "Resource $uri missing"
    fi
done

# Resource templates
resp=$(mcpcall "TEMPLATES")
templates=$(mcp_json_field "$resp" "templates")
for tpl in "kvm://vms/{vm_name}" "kvm://vms/{vm_name}/snapshots" "kvm://vms/{vm_name}/disks" "kvm://hosts/{host_name}/vms"; do
    if echo "$templates" | grep -q "$tpl"; then
        log_pass "Template $tpl present"
    else
        log_fail "Template $tpl missing"
    fi
done

# Prompts
resp=$(mcpcall "PROMPTS")
prompts=$(mcp_json_field "$resp" "prompts")
prompt_count=$(mcp_json_len "$prompts")
if [[ "$prompt_count" == "8" ]]; then
    log_pass "8 prompts registered"
else
    log_fail "Prompt count" "expected 8, got $prompt_count"
fi

# Render a prompt
resp=$(mcpcall "PROMPT investigate_vm {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "guest_exec"; then
    log_pass "Prompt rendering includes expected content"
else
    log_fail "Prompt rendering" "missing guest_exec in output"
fi

# === [4/10] Create VM =======================================================

echo ""
echo "$(bold '[4/10] Create VM')"

if virsh_vm_exists "$TEST_VM"; then
    log_fail "Test VM '$TEST_VM' already exists"; exit 1
fi

resp=$(mcpcall "CALL kvm_create_vm {\"name\":\"$TEST_VM\",\"memory_mb\":512,\"vcpus\":1,\"disk_size_gb\":10}")
text=$(mcp_text "$resp")
err=$(mcp_error "$resp")

if echo "$text" | grep -q "created successfully"; then
    log_pass "kvm_create_vm returned success"
elif [[ -n "$err" ]]; then
    log_fail "kvm_create_vm" "$err"
else
    log_fail "kvm_create_vm" "$text"
fi

# Verify on host
if virsh_vm_exists "$TEST_VM"; then
    log_pass "virsh confirms VM '$TEST_VM' exists"
else
    log_fail "VM not found in virsh after create"; exit 1
fi

if [[ -f "$DISK_FILE" ]]; then
    log_pass "Disk file $DISK_FILE created"
else
    log_fail "Disk file not found at $DISK_FILE"
fi

# Verify via MCP list
resp=$(mcpcall "CALL kvm_list_vms")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "\"$TEST_VM\""; then
    log_pass "VM appears in kvm_list_vms"
else
    log_fail "VM missing from kvm_list_vms"
fi

# Verify via resource
resp=$(mcpcall "READ kvm://vms")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "\"$TEST_VM\""; then
    log_pass "VM appears in kvm://vms resource"
else
    log_fail "VM missing from kvm://vms resource"
fi

# virt-install --import auto-starts
if wait_for_state "$TEST_VM" "running" 15; then
    log_pass "VM is running after create"
else
    log_skip "VM not running after create"
fi

# === [5/10] VM details & boot order =========================================

echo ""
echo "$(bold '[5/10] VM details, boot order')"

# Status via tool
resp=$(mcpcall "CALL kvm_get_vm_status {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
api_status=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('status',''))" "$text" 2>/dev/null)
host_status=$(virsh_state "$TEST_VM")
if [[ "$api_status" == "$host_status" ]]; then
    log_pass "Status '$api_status' matches virsh"
else
    log_fail "Status mismatch" "MCP='$api_status' virsh='$host_status'"
fi

# Status via resource template
resp=$(mcpcall "READ kvm://vms/$TEST_VM")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "\"$TEST_VM\""; then
    log_pass "kvm://vms/$TEST_VM resource returned detail"
else
    log_fail "kvm://vms/$TEST_VM resource" "missing VM name in output"
fi

# Boot order: set to cdrom,hd
resp=$(mcpcall "CALL kvm_set_boot_order {\"vm_name\":\"$TEST_VM\",\"boot_order\":\"cdrom,hd\"}")
text=$(mcp_text "$resp")
host_boot=$(virsh_boot_order "$TEST_VM")
if [[ "$host_boot" == "cdrom,hd" ]]; then
    log_pass "Boot order cdrom,hd verified in XML"
else
    log_fail "Boot order set" "expected 'cdrom,hd', got '$host_boot'"
fi

# Restore to hd,cdrom
resp=$(mcpcall "CALL kvm_set_boot_order {\"vm_name\":\"$TEST_VM\",\"boot_order\":\"hd,cdrom\"}")
host_boot=$(virsh_boot_order "$TEST_VM")
if [[ "$host_boot" == "hd,cdrom" ]]; then
    log_pass "Boot order hd,cdrom restored"
else
    log_fail "Boot order restore" "expected 'hd,cdrom', got '$host_boot'"
fi

# Invalid boot order rejected
resp=$(mcpcall "CALL kvm_set_boot_order {\"vm_name\":\"$TEST_VM\",\"boot_order\":\"usb\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "Invalid boot order"; then
    log_pass "Invalid boot order rejected"
else
    log_fail "Invalid boot order not rejected" "$text"
fi

# === [6/10] Snapshots ========================================================

echo ""
echo "$(bold '[6/10] Snapshots')"

resp=$(mcpcall "CALL kvm_create_snapshot {\"vm_name\":\"$TEST_VM\",\"snapshot_name\":\"$TEST_SNAPSHOT\",\"description\":\"mcp e2e test\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "created"; then
    log_pass "kvm_create_snapshot returned success"
else
    log_fail "kvm_create_snapshot" "$text"
fi

if virsh_snapshot_exists "$TEST_VM" "$TEST_SNAPSHOT"; then
    log_pass "virsh confirms snapshot '$TEST_SNAPSHOT' exists"
else
    log_fail "Snapshot not found in virsh"
fi

# List snapshots — compare count
resp=$(mcpcall "CALL kvm_list_snapshots {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
mcp_count=$(python3 -c "import sys,json; print(len(json.loads(sys.argv[1])['snapshots']))" "$text" 2>/dev/null)
host_count=$(sudo virsh snapshot-list "$TEST_VM" 2>/dev/null | awk 'NR>2 && NF{n++} END{print n+0}')
if [[ "$mcp_count" == "$host_count" ]]; then
    log_pass "Snapshot count matches: MCP=$mcp_count virsh=$host_count"
else
    log_fail "Snapshot count mismatch" "MCP=$mcp_count virsh=$host_count"
fi

# Resource template for snapshots
resp=$(mcpcall "READ kvm://vms/$TEST_VM/snapshots")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "$TEST_SNAPSHOT"; then
    log_pass "kvm://vms/$TEST_VM/snapshots includes test snapshot"
else
    log_fail "Snapshot resource" "missing $TEST_SNAPSHOT"
fi

# Delete snapshot
resp=$(mcpcall "CALL kvm_delete_snapshot {\"vm_name\":\"$TEST_VM\",\"snapshot_name\":\"$TEST_SNAPSHOT\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "deleted"; then
    log_pass "kvm_delete_snapshot returned success"
else
    log_fail "kvm_delete_snapshot" "$text"
fi

if ! virsh_snapshot_exists "$TEST_VM" "$TEST_SNAPSHOT"; then
    log_pass "virsh confirms snapshot deleted"
else
    log_fail "Snapshot still present in virsh"
fi

# === [7/10] Stop / start / restart ==========================================

echo ""
echo "$(bold '[7/10] Stop, start, restart')"

# Ensure running
if [[ "$(virsh_state "$TEST_VM")" != "running" ]]; then
    mcpcall "CALL kvm_start_vm {\"vm_name\":\"$TEST_VM\"}" &>/dev/null
    wait_for_state "$TEST_VM" "running" 15
fi

# Force stop
resp=$(mcpcall "CALL kvm_stop_vm {\"vm_name\":\"$TEST_VM\",\"force\":true}")
if wait_for_state "$TEST_VM" "shut off" 15; then
    log_pass "Force stop → virsh confirms 'shut off'"
else
    log_fail "Force stop" "virsh state: $(virsh_state "$TEST_VM")"
fi

# Stop when already stopped
resp=$(mcpcall "CALL kvm_stop_vm {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "not running"; then
    log_pass "Stop idempotent: 'not running' returned"
else
    log_fail "Stop idempotent" "$text"
fi

# Start
resp=$(mcpcall "CALL kvm_start_vm {\"vm_name\":\"$TEST_VM\"}")
if wait_for_state "$TEST_VM" "running" 15; then
    log_pass "Start → virsh confirms 'running'"
else
    log_fail "Start" "virsh state: $(virsh_state "$TEST_VM")"
fi

# Start when already running
resp=$(mcpcall "CALL kvm_start_vm {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "already running"; then
    log_pass "Start idempotent: 'already running' returned"
else
    log_fail "Start idempotent" "$text"
fi

# Force restart
resp=$(mcpcall "CALL kvm_restart_vm {\"vm_name\":\"$TEST_VM\",\"force\":true}")
sleep 3
if [[ "$(virsh_state "$TEST_VM")" == "running" ]]; then
    log_pass "Force restart → virsh confirms 'running'"
else
    log_fail "Force restart" "virsh state: $(virsh_state "$TEST_VM")"
fi

# === [8/10] Guest agent =====================================================

echo ""
echo "$(bold '[8/10] Guest agent (no agent — error responses expected)')"

# Ensure running
if [[ "$(virsh_state "$TEST_VM")" != "running" ]]; then
    mcpcall "CALL kvm_start_vm {\"vm_name\":\"$TEST_VM\"}" &>/dev/null
    wait_for_state "$TEST_VM" "running" 15
fi

resp=$(mcpcall "CALL guest_ping {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if [[ -n "$text" ]]; then
    log_pass "guest_ping returned a response (agent unavailable is expected)"
else
    log_fail "guest_ping" "empty response"
fi

resp=$(mcpcall "CALL guest_get_network {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if [[ -n "$text" ]]; then
    log_pass "guest_get_network returned a response"
else
    log_fail "guest_get_network" "empty response"
fi

resp=$(mcpcall "CALL guest_get_ip {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if [[ -n "$text" ]]; then
    log_pass "guest_get_ip returned a response"
else
    log_fail "guest_get_ip" "empty response"
fi

resp=$(mcpcall "CALL guest_exec {\"vm_name\":\"$TEST_VM\",\"command\":\"hostname\"}")
text=$(mcp_text "$resp")
if [[ -n "$text" ]]; then
    log_pass "guest_exec returned a response"
else
    log_fail "guest_exec" "empty response"
fi

# === [9/10] Delete VM ========================================================

echo ""
echo "$(bold '[9/10] Delete VM')"

# Must stop first — MCP tool rejects delete on running VM
resp=$(mcpcall "CALL kvm_delete_vm {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "stop it first"; then
    log_pass "Delete rejected while running"
else
    log_fail "Delete should reject running VM" "$text"
fi

sudo virsh destroy "$TEST_VM" &>/dev/null
wait_for_state "$TEST_VM" "shut off" 10

resp=$(mcpcall "CALL kvm_delete_vm {\"vm_name\":\"$TEST_VM\",\"remove_storage\":true}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "deleted successfully"; then
    log_pass "kvm_delete_vm returned success"
else
    log_fail "kvm_delete_vm" "$text"
fi

if ! virsh_vm_exists "$TEST_VM"; then
    log_pass "virsh confirms VM no longer exists"
else
    log_fail "VM still present in virsh after delete"
fi

if [[ ! -f "$DISK_FILE" ]]; then
    log_pass "Disk file removed (remove_storage=true)"
else
    log_fail "Disk file still exists"
fi

# Non-existent VM returns sensible message
resp=$(mcpcall "CALL kvm_get_vm_status {\"vm_name\":\"$TEST_VM\"}")
text=$(mcp_text "$resp")
if echo "$text" | grep -q "not found"; then
    log_pass "Deleted VM → 'not found'"
else
    log_fail "Deleted VM status" "$text"
fi

# Validation: bad VM name triggers ToolError
resp=$(mcpcall "CALL kvm_start_vm {\"vm_name\":\"bad name!\"}")
err=$(mcp_error "$resp")
if echo "$err" | grep -qi "invalid\|ToolError"; then
    log_pass "Invalid VM name rejected with ToolError"
else
    log_fail "Invalid VM name not rejected" "$err"
fi

# === [10/10] Audit log =======================================================

echo ""
echo "$(bold '[10/10] Audit log')"

AUDIT_FILE="$AUDIT_DIR/mcp-audit.jsonl"
if [[ -f "$AUDIT_FILE" ]]; then
    log_pass "Audit log file created"

    line_count=$(wc -l < "$AUDIT_FILE")
    if [[ "$line_count" -gt 0 ]]; then
        log_pass "Audit log has $line_count entries"
    else
        log_fail "Audit log is empty"
    fi

    # Check for tool_call events
    if grep -q '"event": "tool_call"' "$AUDIT_FILE" 2>/dev/null || \
       grep -q '"event":"tool_call"' "$AUDIT_FILE" 2>/dev/null; then
        log_pass "Audit log contains tool_call events"
    else
        log_fail "Audit log missing tool_call events"
    fi

    # Check for resource_read events
    if grep -q '"event": "resource_read"' "$AUDIT_FILE" 2>/dev/null || \
       grep -q '"event":"resource_read"' "$AUDIT_FILE" 2>/dev/null; then
        log_pass "Audit log contains resource_read events"
    else
        log_fail "Audit log missing resource_read events"
    fi

    # Verify sensitive args are redacted
    if grep -q "REDACTED" "$AUDIT_FILE" 2>/dev/null || \
       ! grep -q "public_key" "$AUDIT_FILE" 2>/dev/null; then
        log_pass "Sensitive arguments redacted or absent"
    else
        log_fail "Sensitive arguments may be exposed in audit log"
    fi
else
    log_fail "Audit log file not found at $AUDIT_FILE"
fi

# === SUMMARY =================================================================

echo ""
echo "$(bold '=== Results ===')"
TOTAL=$((PASS + FAIL + SKIP))
echo "  $(green "$PASS passed")  $(red "$FAIL failed")  $(yellow "$SKIP skipped")  / $TOTAL total"

[[ "$FAIL" -gt 0 ]] && exit 1 || exit 0
