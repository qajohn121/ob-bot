#!/bin/bash
# Run this ON the VPS: bash fix_imports.sh
cd ~/ob-bot
source venv/bin/activate

# Check what's missing
echo "=== Checking bot.py for missing imports ==="
python -c "
import ast, sys
try:
    ast.parse(open('bot.py').read())
    print('Syntax OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR line {e.lineno}: {e.msg}')
    lines = open('bot.py').readlines()
    for i in range(max(0,e.lineno-3), min(len(lines),e.lineno+2)):
        print(f'  {i+1}: {lines[i]}', end='')
"

# Add missing imports at top of bot.py if not present
python -c "
src = open('bot.py').read()
additions = []
if 'from zoneinfo import ZoneInfo' not in src:
    additions.append('from zoneinfo import ZoneInfo')
if 'import pytz' not in src and 'ZoneInfo' not in src:
    additions.append('import pytz')
if 'import datetime' not in src:
    additions.append('import datetime')
if 'from pathlib import Path' not in src:
    additions.append('from pathlib import Path')
if additions:
    inject = '\n'.join(additions) + '\n'
    # Insert after first import line
    idx = src.find('import ')
    eol = src.find('\n', idx) + 1
    src = src[:eol] + inject + src[eol:]
    open('bot.py','w').write(src)
    print('Fixed: added', additions)
else:
    print('All imports already present')
"

echo ""
echo "=== Testing bot.py import ==="
timeout 5 python -c "import bot" 2>&1 | head -20 || true

echo ""
echo "=== Restarting service ==="
sudo systemctl restart ob-bot
sleep 3
sudo systemctl status ob-bot --no-pager -l | tail -15
