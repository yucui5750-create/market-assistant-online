import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import generate
from storage import read, write


class PublicHistoryTests(unittest.TestCase):
    def test_fresh_cloud_runner_preserves_only_public_report_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / 'public-state.json'
            old = {'id': 'old', 'as_of': '2026-01-01', 'market': [1],
                   'portfolio': {'synthetic_private_value': 123}}
            fresh = {'id': 'new', 'as_of': '2026-01-02', 'market': [2]}
            write(public, {'reports': [old]})
            write(root / 'reports' / 'baseline-new.json', fresh)
            with patch.object(generate, 'DATA', root), patch.object(generate, 'PUBLIC', public), patch.object(generate, 'refresh', return_value=fresh):
                result = generate.export()
            self.assertEqual([r['id'] for r in result['reports']], ['new', 'old'])
            self.assertNotIn('portfolio', result['reports'][1])
            self.assertEqual(read(public)['reports'], result['reports'])


if __name__ == '__main__':
    unittest.main()
