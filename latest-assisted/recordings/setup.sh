#!/bin/bash
echo "# Step 1: Install flyte"
sleep 0.3
echo '$ uv venv .venv --python 3.12'
echo "Using CPython 3.12"
echo 'Creating virtual environment at: .venv'
sleep 0.3
echo ""
echo '$ uv pip install flyte'
/output/.venv/bin/python -c "
import importlib.metadata
for d in importlib.metadata.requires('flyte') or []:
    if '; extra' not in d:
        print(f'  Installed {d}')
" 2>&1
echo "  Installed flyte"
sleep 0.3
echo ""
echo '$ python -c "import flyte; print(flyte.__version__)"'
/output/.venv/bin/python -c "import flyte; print(flyte.__version__)" 2>&1
sleep 1
