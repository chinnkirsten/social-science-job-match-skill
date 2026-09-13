import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_job_match import private_destination
from jobmatch_runtime.common import AdapterError

class PreparationOutputScopeTests(unittest.TestCase):
    def test_public_skill_outputs_rejected(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root,root/'inputs'/'resume.json',root/'cache'):
            with self.subTest(path=path.name), self.assertRaises(AdapterError) as error:
                private_destination(path)
            self.assertEqual(error.exception.code,'private_output_location')

    def test_external_private_directory_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(private_destination(Path(folder)/'draft.json'),Path(folder).resolve()/'draft.json')
