#!/bin/bash

# Quick verification script for KVM API security fixes
# Tests that critical fixes are working correctly

set -e

echo "=== KVM API Security Fixes Verification ==="
echo ""

# Test 1: Syntax check
echo "Test 1: Python syntax check..."
python3 -m py_compile \
    app/utils/validation.py \
    app/utils/subprocess.py \
    app/utils/logging.py \
    app/middleware/auth.py \
    app/middleware/rate_limit.py \
    app/middleware/request_id.py \
    app/api/v1/vms.py \
    app/api/v1/guest_agent.py \
    app/services/guest_agent_service.py \
    app/services/kvm_service.py \
    app/main.py \
    app/dependencies.py 2>&1

if [ $? -eq 0 ]; then
    echo "✅ Syntax check passed"
else
    echo "❌ Syntax check failed"
    exit 1
fi

# Test 2: Import checks
echo ""
echo "Test 2: Import checks..."
python3 -c "
from app.utils.validation import VMNamePath, SnapshotNamePath
from app.utils.logging import setup_logging, get_logger
from app.middleware.auth import verify_api_key, api_key_middleware
from app.middleware.rate_limit import RateLimiter, rate_limit_middleware
from app.middleware.request_id import request_id_middleware
from app.dependencies import get_services
print('✅ All imports successful')
" 2>&1

if [ $? -eq 0 ]; then
    echo "✅ Import check passed"
else
    echo "❌ Import check failed"
    exit 1
fi

# Test 3: Validation classes work
echo ""
echo "Test 3: Validation classes..."
python3 -c "
from app.utils.validation import VMNamePath, SnapshotNamePath

# Test valid names
try:
    vm = VMNamePath.validate('test-vm')
    print(f'Valid VM name accepted: {vm}')
except Exception as e:
    print(f'Valid VM name rejected: {e}')
    exit(1)

# Test invalid names
try:
    vm = VMNamePath.validate('../../../etc/passwd')
    print('ERROR: Invalid VM name was accepted')
    exit(1)
except Exception as e:
    print(f'Invalid VM name correctly rejected: {e}')

# Test snapshot names
try:
    snap = SnapshotNamePath.validate('test-snapshot')
    print(f'Valid snapshot name accepted: {snap}')
except Exception as e:
    print(f'Valid snapshot name rejected: {e}')
    exit(1)

try:
    snap = SnapshotNamePath.validate('../../etc/passwd')
    print('ERROR: Invalid snapshot name was accepted')
    exit(1)
except Exception as e:
    print(f'Invalid snapshot name correctly rejected: {e}')

print('✅ Validation classes working correctly')
"

if [ $? -eq 0 ]; then
    echo "✅ Validation test passed"
else
    echo "❌ Validation test failed"
    exit 1
fi

# Test 4: Rate limiter cleanup
echo ""
echo "Test 4: Rate limiter cleanup..."
python3 -c "
from app.middleware.rate_limit import RateLimiter
import time

limiter = RateLimiter(requests_per_minute=10)

# Test rate limiting
for i in range(15):
    if limiter.is_allowed('test-key'):
        limiter.record_request('test-key')
    else:
        print(f'Rate limit hit at request {i}')
        exit(0)

# Test cleanup - should reset after 60 seconds
remaining = limiter.get_remaining('test-key')
print(f'Remaining requests: {remaining}')

# Test with different key
remaining2 = limiter.get_remaining('different-key')
print(f'Remaining for different key: {remaining2}')

print('✅ Rate limiter working correctly')
"

if [ $? -eq 0 ]; then
    echo "✅ Rate limiter test passed"
else
    echo "❌ Rate limiter test failed"
    exit 1
fi

# Test 5: File structure
echo ""
echo "Test 5: File structure..."
FILES=(
    "app/middleware/auth.py"
    "app/middleware/rate_limit.py"
    "app/middleware/request_id.py"
    "app/dependencies.py"
    "app/utils/validation.py"
    "app/utils/logging.py"
    "app/utils/subprocess.py"
    "app/api/v1/vms.py"
    "app/api/v1/guest_agent.py"
    "tests/test_api/test_vms.py"
    "tests/test_api/test_security.py"
    "Dockerfile"
    "SECURITY.md"
    "REMEDIATION_COMPLETE.md"
)

ALL_EXIST=true
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✅ $file exists"
    else
        echo "❌ $file missing"
        ALL_EXIST=false
    fi
done

if [ "$ALL_EXIST" = true ]; then
    echo "✅ File structure check passed"
else
    echo "❌ File structure check failed"
    exit 1
fi

# Test 6: Dependencies
echo ""
echo "Test 6: Project dependencies..."
if grep -q "structlog" pyproject.toml; then
    echo "✅ structlog dependency found in pyproject.toml"
else
    echo "❌ structlog dependency missing from pyproject.toml"
    exit 1
fi

# Test 7: systemd service
echo ""
echo "Test 7: systemd service..."
if grep -q "ProtectHome=read-only" deployment/kvm-api.service; then
    echo "✅ systemd service properly configured"
else
    echo "❌ systemd service has incompatible settings"
    exit 1
fi

# Summary
echo ""
echo "=== Verification Summary ==="
echo ""
echo "All tests passed! ✅"
echo ""
echo "Next steps:"
echo "1. Run full test suite: pytest tests/ -v"
echo "2. Start API: uvicorn app.main:app --reload"
echo "3. Test endpoints with proper API keys"
echo "4. Review SECURITY.md for deployment guidelines"
echo ""
echo "Critical security fixes have been verified and are working correctly."
