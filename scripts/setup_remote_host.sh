#!/bin/bash
#
# Setup a remote KVM host for secure MCP/API access.
# Run this ON THE REMOTE KVM HOST as root.
#
# Creates:
#   - Dedicated kvm-mcp user (no shell login)
#   - Scoped sudoers: only virsh, virt-install, qemu-img, virt-clone
#   - SSH directory with correct permissions
#   - Libvirt group membership for socket access
#
# Usage:
#   scp scripts/setup_remote_host.sh root@kvm-host:/tmp/
#   ssh root@kvm-host 'bash /tmp/setup_remote_host.sh'

set -euo pipefail

MCP_USER="kvm-mcp"
MCP_HOME="/var/lib/kvm-mcp"
SUDOERS_FILE="/etc/sudoers.d/kvm-mcp"

# ── Preflight checks ─────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must run as root" >&2
    exit 1
fi

for cmd in virsh virt-install qemu-img; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found -- install libvirt/qemu first" >&2
        exit 1
    fi
done

echo "=== KVM Remote Host Setup for MCP ==="
echo ""

# ── Create dedicated user ─────────────────────────────────────────────────

if id "$MCP_USER" &>/dev/null; then
    echo "[ok] User $MCP_USER already exists"
else
    useradd \
        --system \
        --home-dir "$MCP_HOME" \
        --create-home \
        --shell /usr/sbin/nologin \
        --comment "KVM MCP service account" \
        "$MCP_USER"
    echo "[+] Created user $MCP_USER"
fi

# ── Add to libvirt group ──────────────────────────────────────────────────

LIBVIRT_GROUP=""
for grp in libvirt libvirtd; do
    if getent group "$grp" &>/dev/null; then
        LIBVIRT_GROUP="$grp"
        break
    fi
done

if [ -n "$LIBVIRT_GROUP" ]; then
    usermod -aG "$LIBVIRT_GROUP" "$MCP_USER"
    echo "[+] Added $MCP_USER to group $LIBVIRT_GROUP"
else
    echo "[!] No libvirt group found -- socket access may require sudo"
fi

# ── Setup SSH directory ───────────────────────────────────────────────────

SSH_DIR="$MCP_HOME/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"

mkdir -p "$SSH_DIR"
touch "$AUTH_KEYS"

chown -R "$MCP_USER:$MCP_USER" "$SSH_DIR"
chmod 700 "$SSH_DIR"
chmod 600 "$AUTH_KEYS"

echo "[+] SSH directory ready at $SSH_DIR"

# ── Scoped sudoers ────────────────────────────────────────────────────────

VIRSH_PATH=$(command -v virsh)
VIRT_INSTALL_PATH=$(command -v virt-install)
QEMU_IMG_PATH=$(command -v qemu-img)
VIRT_CLONE_PATH=$(command -v virt-clone 2>/dev/null || echo "/usr/bin/virt-clone")

cat > "$SUDOERS_FILE" <<EOF
# KVM MCP service account -- scoped to virtualisation commands only.
# Managed by setup_remote_host.sh -- do not edit manually.
Defaults:${MCP_USER} !requiretty
Defaults:${MCP_USER} log_output
Defaults:${MCP_USER} logfile="/var/log/kvm-mcp-sudo.log"

${MCP_USER} ALL=(root) NOPASSWD: ${VIRSH_PATH}
${MCP_USER} ALL=(root) NOPASSWD: ${VIRSH_PATH} *
${MCP_USER} ALL=(root) NOPASSWD: ${VIRT_INSTALL_PATH}
${MCP_USER} ALL=(root) NOPASSWD: ${VIRT_INSTALL_PATH} *
${MCP_USER} ALL=(root) NOPASSWD: ${QEMU_IMG_PATH}
${MCP_USER} ALL=(root) NOPASSWD: ${QEMU_IMG_PATH} *
${MCP_USER} ALL=(root) NOPASSWD: ${VIRT_CLONE_PATH}
${MCP_USER} ALL=(root) NOPASSWD: ${VIRT_CLONE_PATH} *
EOF

chmod 440 "$SUDOERS_FILE"

if visudo -cf "$SUDOERS_FILE" &>/dev/null; then
    echo "[+] Sudoers file validated: $SUDOERS_FILE"
else
    echo "ERROR: Sudoers syntax check failed -- removing broken file" >&2
    rm -f "$SUDOERS_FILE"
    exit 1
fi

# ── Harden SSHD for this user ────────────────────────────────────────────

SSHD_CONF="/etc/ssh/sshd_config.d/kvm-mcp.conf"

if [ -d /etc/ssh/sshd_config.d ]; then
    cat > "$SSHD_CONF" <<EOF
# KVM MCP service account SSH restrictions.
Match User ${MCP_USER}
    PasswordAuthentication no
    PermitEmptyPasswords no
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTunnel no
    MaxAuthTries 3
    MaxSessions 5
    ForceCommand none
EOF
    echo "[+] SSH hardening applied: $SSHD_CONF"

    if sshd -t &>/dev/null; then
        systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
        echo "[+] SSHD configuration reloaded"
    else
        echo "ERROR: SSHD config test failed -- removing broken config" >&2
        rm -f "$SSHD_CONF"
        exit 1
    fi
else
    echo "[!] /etc/ssh/sshd_config.d/ not found -- add Match block manually"
fi

# ── Create sudo audit log ────────────────────────────────────────────────

touch /var/log/kvm-mcp-sudo.log
chmod 600 /var/log/kvm-mcp-sudo.log

cat > /etc/logrotate.d/kvm-mcp-sudo <<EOF
/var/log/kvm-mcp-sudo.log {
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    create 0600 root root
}
EOF

echo "[+] Sudo audit log configured: /var/log/kvm-mcp-sudo.log"

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "=== Setup Complete ==="
echo ""
echo "User:        $MCP_USER"
echo "Home:        $MCP_HOME"
echo "SSH keys:    $AUTH_KEYS"
echo "Sudoers:     $SUDOERS_FILE"
echo "Sudo log:    /var/log/kvm-mcp-sudo.log"
echo ""
echo "Next steps:"
echo "  1. Run setup_ssh_keys.sh on the MCP CLIENT machine to generate"
echo "     and deploy a restricted SSH key pair."
echo "  2. Set these in your .env on the client:"
echo "     KVM_HOST=<this-host>"
echo "     KVM_HOST_USER=$MCP_USER"
echo "     KVM_HOST_SSH_KEY=~/.ssh/kvm-mcp"
echo ""
