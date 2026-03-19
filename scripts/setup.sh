#!/bin/bash
# KVM MCP server setup script

set -e

echo "=== KVM MCP Setup Script ==="
echo ""

# Check if Python 3.11+ is installed
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Detected Python version: $PYTHON_VERSION"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv .venv

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -e .[dev]

# Create .env file from example if it doesn't exist
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file from env.example..."
    cp env.example .env
    echo "Please edit .env file with your configuration"
else
    echo ""
    echo ".env file already exists, skipping creation"
fi

# Create logs directory
mkdir -p logs

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit .env file with your KVM host configuration"
echo "2. Run standalone: source .venv/bin/activate && python -m app.mcp_server"
echo "3. Or add .cursor/mcp.json to your Cursor workspace"
echo ""
