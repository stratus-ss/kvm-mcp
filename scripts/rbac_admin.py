#!/usr/bin/env python3
"""RBAC administration CLI for KVM MCP server."""

import argparse
import json
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.rbac import User, Role, Permission, DEFAULT_ROLE_DEFINITIONS
from app.services.rbac_service import RBACService


def main():
    parser = argparse.ArgumentParser(description="RBAC administration for KVM MCP server")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List commands
    list_parser = subparsers.add_parser("list", help="List users or roles")
    list_parser.add_argument("type", choices=["users", "roles"], help="What to list")

    # Add user command
    add_user_parser = subparsers.add_parser("add-user", help="Add a new user")
    add_user_parser.add_argument("--id", required=True, help="User ID")
    add_user_parser.add_argument("--name", required=True, help="Display name")
    add_user_parser.add_argument("--email", help="Email address")
    add_user_parser.add_argument("--roles", nargs="+", choices=[r.value for r in Role], 
                               help="Assigned roles")
    add_user_parser.add_argument("--host-restrictions", nargs="+", 
                               help="Allowed hosts (empty = all)")
    add_user_parser.add_argument("--vm-patterns", nargs="+", 
                               help="Allowed VM name patterns")

    # Remove user command
    remove_user_parser = subparsers.add_parser("remove-user", help="Remove a user")
    remove_user_parser.add_argument("user_id", help="User ID to remove")

    # Check permission command
    check_parser = subparsers.add_parser("check", help="Check user permissions")
    check_parser.add_argument("user_id", help="User ID")
    check_parser.add_argument("operation", help="Operation to check")
    check_parser.add_argument("--vm-name", help="VM name (if applicable)")
    check_parser.add_argument("--host", help="Host name (if applicable)")

    # File argument
    parser.add_argument("--users-file", default="rbac_users.json", 
                       help="Users configuration file")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Initialize RBAC service
    rbac = RBACService()
    
    # Load existing users if file exists
    if Path(args.users_file).exists():
        rbac._load_users_from_file(args.users_file)

    if args.command == "list":
        if args.type == "users":
            list_users(rbac)
        elif args.type == "roles":
            list_roles()

    elif args.command == "add-user":
        add_user(rbac, args, args.users_file)

    elif args.command == "remove-user":
        remove_user(rbac, args.user_id, args.users_file)

    elif args.command == "check":
        check_permission(rbac, args)


def list_users(rbac: RBACService):
    """List all users."""
    users = rbac.list_users()
    if not users:
        print("No users found.")
        return

    print(f"{'ID':<15} {'Name':<20} {'Roles':<20} {'Enabled':<8}")
    print("-" * 65)
    for user in users:
        roles_str = ",".join([r.value for r in user.roles])
        status = "Yes" if user.enabled else "No"
        print(f"{user.id:<15} {user.name:<20} {roles_str:<20} {status:<8}")


def list_roles():
    """List all available roles."""
    print(f"{'Role':<12} {'Name':<20} {'Description'}")
    print("-" * 60)
    for role_def in DEFAULT_ROLE_DEFINITIONS:
        print(f"{role_def.role.value:<12} {role_def.name:<20} {role_def.description}")
    
    print("\nPermissions by role:")
    for role_def in DEFAULT_ROLE_DEFINITIONS:
        print(f"\n{role_def.name}:")
        for perm in role_def.permissions:
            print(f"  - {perm.value}")


def add_user(rbac: RBACService, args, users_file: str):
    """Add a new user."""
    user = User(
        id=args.id,
        name=args.name,
        email=args.email,
        roles=[Role(r) for r in (args.roles or [])],
        host_restrictions=args.host_restrictions or [],
        vm_name_patterns=args.vm_patterns or [],
        enabled=True
    )
    
    rbac.add_user(user)
    rbac.save_users_to_file(users_file)
    print(f"User '{args.id}' added successfully.")


def remove_user(rbac: RBACService, user_id: str, users_file: str):
    """Remove a user."""
    if rbac.remove_user(user_id):
        rbac.save_users_to_file(users_file)
        print(f"User '{user_id}' removed successfully.")
    else:
        print(f"User '{user_id}' not found.")


def check_permission(rbac: RBACService, args):
    """Check if user has permission for an operation."""
    from app.models.rbac import RBACContext
    
    context = RBACContext(
        user_id=args.user_id,
        operation=args.operation,
        resource_type="vm",
        resource_name=args.vm_name,
        host=args.host or ""
    )
    
    allowed, reason = rbac.check_permission(context)
    
    print(f"User: {args.user_id}")
    print(f"Operation: {args.operation}")
    print(f"VM: {args.vm_name or 'N/A'}")
    print(f"Host: {args.host or 'default'}")
    print(f"Result: {'✅ ALLOWED' if allowed else '❌ DENIED'}")
    print(f"Reason: {reason}")
    
    # Show user permissions
    user = rbac.get_user(args.user_id)
    if user:
        permissions = rbac.get_user_permissions(args.user_id)
        print(f"\nUser roles: {[r.value for r in user.roles]}")
        print(f"User permissions: {len(permissions)} total")
        if len(permissions) <= 10:  # Don't spam for admin users
            for perm in sorted(permissions, key=lambda x: x.value):
                print(f"  - {perm.value}")


if __name__ == "__main__":
    main()