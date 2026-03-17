#!/bin/bash
echo "# Step 2: Running the README example as-is"
sleep 0.5
echo '--- Code from README ---'
cat /output/recordings/readme_example.py
echo ""
sleep 0.5
echo '$ python readme_example.py'
/output/.venv/bin/python /output/recordings/readme_example.py 2>&1
sleep 1.5
