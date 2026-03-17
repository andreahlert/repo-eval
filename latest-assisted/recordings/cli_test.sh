#!/bin/bash
echo "# Step 3: CLI Usage"
sleep 0.5
echo '$ flyte --version'
flyte --version 2>&1 | head -40
sleep 0.5
echo ""
echo '$ flyte --help'
flyte --help 2>&1 | head -40
sleep 0.5
echo ""
sleep 1