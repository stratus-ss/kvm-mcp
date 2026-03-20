"""Tests for security fixes implemented in the KVM MCP server."""

import pytest
import tempfile
import os
from pathlib import Path

from app.services.kvm_service import KVMService
from app.services.connection_manager import ConnectionManager
from app.models.rbac import User, Role, Permission, RBACContext
from app.services.rbac_service import RBACService


class TestPathValidation:
    """Test path validation security fixes."""

    def setup_method(self):
        """Set up test environment."""
        self.conn_mgr = ConnectionManager()
        self.kvm_service = KVMService(self.conn_mgr)

    def test_validate_disk_path_allows_valid_paths(self):
        """Test that valid disk paths are allowed."""
        # Set up allowed paths
        os.environ["ALLOWED_DISK_PATHS"] = "/var/lib/libvirt/images,/tmp"
        
        # Valid path should not raise exception
        try:
            self.kvm_service._validate_disk_path("/var/lib/libvirt/images/test.qcow2")
        except ValueError:
            pytest.fail("Valid disk path was rejected")

    def test_validate_disk_path_rejects_path_traversal(self):
        """Test that path traversal attempts are blocked."""
        os.environ["ALLOWED_DISK_PATHS"] = "/var/lib/libvirt/images"
        
        # Path traversal should be rejected
        with pytest.raises(ValueError, match="Path traversal"):
            self.kvm_service._validate_disk_path("/var/lib/libvirt/../../../etc/passwd")

    def test_validate_disk_path_rejects_unauthorized_paths(self):
        """Test that paths outside allowed directories are rejected."""
        os.environ["ALLOWED_DISK_PATHS"] = "/var/lib/libvirt/images"
        
        # Unauthorized path should be rejected
        with pytest.raises(ValueError, match="must be under one of"):
            self.kvm_service._validate_disk_path("/etc/passwd")

    def test_validate_iso_path_allows_valid_paths(self):
        """Test that valid ISO paths are allowed."""
        os.environ["ALLOWED_ISO_PATHS"] = "/var/lib/libvirt/images,/home"
        
        # Valid ISO path should not raise exception
        try:
            self.kvm_service._validate_iso_path("/var/lib/libvirt/images/ubuntu.iso")
        except ValueError:
            pytest.fail("Valid ISO path was rejected")

    def test_validate_iso_path_handles_none(self):
        """Test that None ISO path is handled correctly."""
        try:
            self.kvm_service._validate_iso_path(None)
        except ValueError:
            pytest.fail("None ISO path should be allowed")


class TestRBACSystem:
    """Test RBAC system functionality."""

    def setup_method(self):
        """Set up test environment."""
        self.rbac = RBACService()
        # Disable file loading for tests
        self.rbac.policy.enabled = True

    def test_default_roles_exist(self):
        """Test that default roles are properly configured."""
        assert Role.ADMIN in self.rbac.roles
        assert Role.OPERATOR in self.rbac.roles
        assert Role.VIEWER in self.rbac.roles
        assert Role.GUEST in self.rbac.roles

    def test_admin_has_all_permissions(self):
        """Test that admin role has all permissions."""
        admin_user = User(
            id="test-admin",
            name="Test Admin",
            roles=[Role.ADMIN],
            enabled=True
        )
        self.rbac.add_user(admin_user)
        
        permissions = self.rbac.get_user_permissions("test-admin")
        # Admin should have ADMIN_ALL permission which grants everything
        assert Permission.ADMIN_ALL in permissions

    def test_viewer_has_limited_permissions(self):
        """Test that viewer role has only read permissions."""
        viewer_user = User(
            id="test-viewer",
            name="Test Viewer",
            roles=[Role.VIEWER],
            enabled=True
        )
        self.rbac.add_user(viewer_user)
        
        permissions = self.rbac.get_user_permissions("test-viewer")
        
        # Viewer should have read permissions
        assert Permission.VM_LIST in permissions
        assert Permission.VM_STATUS in permissions
        assert Permission.RESOURCE_READ in permissions
        
        # Viewer should NOT have write permissions
        assert Permission.VM_CREATE not in permissions
        assert Permission.VM_DELETE not in permissions
        assert Permission.SNAPSHOT_RESTORE not in permissions

    def test_permission_checking(self):
        """Test permission checking functionality."""
        # Create a developer user
        dev_user = User(
            id="test-dev",
            name="Test Developer",
            roles=[Role.DEVELOPER],
            vm_name_patterns=["dev-.*", "test-.*"],
            enabled=True
        )
        self.rbac.add_user(dev_user)
        
        # Test allowed operation
        context = RBACContext(
            user_id="test-dev",
            operation="kvm_list_vms",
            resource_type="vm",
            resource_name="dev-vm-1",
            host="development"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert allowed, f"Permission should be allowed: {reason}"
        
        # Test VM name pattern restriction
        context = RBACContext(
            user_id="test-dev",
            operation="kvm_start_vm",
            resource_type="vm",
            resource_name="prod-vm-1",  # Not matching dev-.* pattern
            host="development"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert not allowed, "Permission should be denied for non-matching VM pattern"
        assert "not allowed by patterns" in reason

    def test_disabled_user_access(self):
        """Test that disabled users cannot perform operations."""
        disabled_user = User(
            id="disabled-user",
            name="Disabled User",
            roles=[Role.ADMIN],
            enabled=False
        )
        self.rbac.add_user(disabled_user)
        
        context = RBACContext(
            user_id="disabled-user",
            operation="kvm_list_vms",
            resource_type="vm"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert not allowed
        assert "disabled" in reason

    def test_unknown_user_access(self):
        """Test that unknown users are denied access."""
        context = RBACContext(
            user_id="unknown-user",
            operation="kvm_list_vms",
            resource_type="vm"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert not allowed
        assert "not found" in reason

    def test_host_restrictions(self):
        """Test host-based access restrictions."""
        restricted_user = User(
            id="restricted-user",
            name="Restricted User",
            roles=[Role.OPERATOR],
            host_restrictions=["development", "staging"],
            enabled=True
        )
        self.rbac.add_user(restricted_user)
        
        # Allowed host
        context = RBACContext(
            user_id="restricted-user",
            operation="kvm_list_vms",
            resource_type="vm",
            host="development"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert allowed, f"Access to allowed host should be granted: {reason}"
        
        # Restricted host
        context = RBACContext(
            user_id="restricted-user",
            operation="kvm_list_vms",
            resource_type="vm",
            host="production"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert not allowed
        assert "not allowed" in reason

    def test_rbac_disabled(self):
        """Test that when RBAC is disabled, all access is allowed."""
        self.rbac.policy.enabled = False
        
        context = RBACContext(
            user_id="nonexistent-user",
            operation="kvm_delete_vm",
            resource_type="vm"
        )
        allowed, reason = self.rbac.check_permission(context)
        assert allowed
        assert "disabled" in reason


class TestConfirmationPrompts:
    """Test confirmation prompt functionality."""

    def test_confirmation_message_format(self):
        """Test that confirmation messages are properly formatted."""
        from app.mcp_server import _requires_confirmation
        
        message = _requires_confirmation(
            "Delete VM 'test-vm'", 
            "VM: test-vm, Remove storage: true"
        )
        
        assert "DESTRUCTIVE OPERATION" in message
        assert "test-vm" in message
        assert "confirm=True" in message
        assert "cannot be undone" in message


if __name__ == "__main__":
    # Run tests if called directly
    pytest.main([__file__])