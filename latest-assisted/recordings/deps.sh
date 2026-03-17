#!/bin/bash
echo "# Step: Dependencies & Weight"
sleep 0.5
echo '--- Direct dependencies ---'
/output/.venv/bin/python -c "
import importlib.metadata
deps = importlib.metadata.requires('flyte')
if deps:
    core = [d for d in deps if '; extra' not in d]
    print(f'Direct: {len(core)}')
    for d in sorted(core):
        print(f'  {d}')
else:
    print('No dependencies metadata found')
" 2>&1
sleep 0.5
echo ""
echo '--- Total packages ---'
/output/.venv/bin/python -c "
import importlib.metadata
print(f'Total: {len(list(importlib.metadata.distributions()))}')
" 2>&1
sleep 0.5
echo ""
echo '--- Import time ---'
/output/.venv/bin/python -c "
import time
start = time.time()
import flyte
print(f'Import time: {time.time() - start:.3f}s')
" 2>&1
sleep 1
