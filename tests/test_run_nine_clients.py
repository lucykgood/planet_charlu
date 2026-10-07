import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_nine_clients.py'


@pytest.mark.parametrize('problem', [None, 'duplicate_key', 'duplicate_station', 'missing_player', 'placeholder', 'malformed'])
def test_launcher_preflight_never_connects_or_prints_keys(tmp_path, problem):
    players = [{'station_id': f'P{i:02d}', 'token': f'secret-test-key-{i}'} for i in range(1, 10)]
    if problem == 'duplicate_key':
        players[1]['token'] = players[0]['token']
    elif problem == 'duplicate_station':
        players[1]['station_id'] = players[0]['station_id']
    elif problem == 'missing_player':
        players.pop()
    elif problem == 'placeholder':
        players[0]['token'] = 'REPLACE_WITH_KEY_1'
    path = tmp_path / 'credentials.json'
    path.write_text('{secret-test-key-1' if problem == 'malformed' else json.dumps({'players': players}))
    result = subprocess.run([sys.executable, str(SCRIPT), '--credentials-file', str(path), '--dry-run'],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == (0 if problem is None else 2)
    assert 'secret-test-key-' not in result.stdout + result.stderr
    if problem is None:
        assert 'ConservativeTradingStrategy' in result.stdout
        assert 'No connections opened' in result.stdout
