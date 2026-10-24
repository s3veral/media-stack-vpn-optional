import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'windows'))
from build_configure import build

SOURCE = (ROOT / 'truenas-file-structure.sh').read_text(encoding='utf-8')
BASH = shutil.which('bash')


class InstallerTests(unittest.TestCase):
    def bash(self, text, stdin=''):
        # Binary stdin preserves LF even when the test runner is on Windows.
        result = subprocess.run([BASH, '-c', text], input=stdin.encode('utf-8'), capture_output=True)
        result.stdout = result.stdout.decode('utf-8')
        result.stderr = result.stderr.decode('utf-8')
        return result

    def choose(self, choice='', state=None, old_compose=None, answer=''):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            if state is not None:
                (folder / '.vpn-enabled').write_text(state)
            if old_compose:
                (folder / 'docker-compose.yml').write_text(old_compose)
            block = SOURCE[SOURCE.index('VPN_STATE_FILE='):SOURCE.index('# set_perms')]
            script = f'''set -uo pipefail
die() {{ echo "$*" >&2; exit 1; }}
DOCKER_COMPOSE_PATH='{folder.as_posix()}'
DOCKER_COMPOSE_FILE="$DOCKER_COMPOSE_PATH/docker-compose.yml"
VPN_CHOICE='{choice}'
{block}
printf '\\nRESULT=%s|%s|%s|%s\\n' "$VPN_ENABLED" "$QBIT_NETWORK_INTERFACE" "$VPN_NAMESERVERS" "$QBIT_PEER_PORTS"
'''
            return self.bash(script, answer)

    def test_fresh_install_defaults_to_direct(self):
        result = self.choose(answer='\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RESULT=false|||', result.stdout)
        self.assertIn('6881:6881/udp', result.stdout)

    def test_saved_vpn_stays_vpn(self):
        result = self.choose(state='true\n', answer='\n')
        self.assertIn('RESULT=true|wg0|wg|', result.stdout)
        self.assertNotIn('6881:6881', result.stdout)

    def test_migrate_existing_vpn_preserves_default(self):
        self.assertIn('RESULT=true|wg0|wg|', self.choose(old_compose='- VPN_ENABLED=true', answer='\n').stdout)

    def test_disable_existing_vpn_clears_binding_and_dns(self):
        result = self.choose(choice='false', state='true')
        self.assertIn('RESULT=false|||', result.stdout)

    def test_enable_from_saved_direct(self):
        self.assertIn('RESULT=true|wg0|wg|', self.choose(state='false', answer='s\n').stdout)

    def test_no_input_does_not_loop_forever(self):
        self.assertNotEqual(self.choose().returncode, 0)

    def test_saved_state_is_never_executed(self):
        result = self.choose(state='$(echo SHOULD_NOT_EXECUTE)')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SHOULD_NOT_EXECUTE', result.stdout)

    def test_wireguard_prompt_is_conditional_and_fails_closed(self):
        block = SOURCE[SOURCE.index('# WireGuard config for qBittorrent'):SOURCE.index('cd "$DOCKER_COMPOSE_PATH" ||')]
        self.assertIn('if [ "$VPN_ENABLED" != true ]', block)
        self.assertIn('die "VPN selected but no WireGuard profile', block)

    def test_preferences_use_selected_interface(self):
        start = SOURCE.index('    code=$(qb_code --data-urlencode "json=$(jbuild', SOURCE.index('# Connection / BitTorrent / Advanced'))
        end = SOURCE.index('\n    if qb_2xx', start)
        block = SOURCE[start:end]
        for interface in ['', 'wg0']:
            # Run the actual JSON expression, without qBittorrent or side effects.
            expression = block.split("$(jbuild '", 1)[1].split("')", 1)[0]
            prefs = eval(expression, {'os': type('OS', (), {'environ': {'QBIT_NETWORK_INTERFACE': interface}})})
            self.assertEqual(prefs['current_network_interface'], interface)
            self.assertEqual(prefs['listen_port'], 6881)

    def test_portable_setup_has_no_zfs_or_host_paths(self):
        script = build(SOURCE)
        self.assertNotIn('/mnt/$CONFIG_POOL', script)
        self.assertNotIn('zfs create', script)
        self.assertIn('http://radarr:7878', script)
        self.assertIn('cfg="/configs/$1/config.xml"', script)
        self.assertIn('"${#TODO_LIST[@]}" -eq 0 ] || exit 2', script)
        result = subprocess.run([BASH, '-n'], input=script.encode('utf-8'), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
