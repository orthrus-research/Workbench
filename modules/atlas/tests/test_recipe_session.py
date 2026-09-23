import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from workbench_atlas_categorical_graph import AtlasCategoricalGraphError
from workbench_atlas_recipe_health.session import serve_recipe_session, PREFIX
from test_captured_recipe_routes import quest_target_fixture


class RecipeSessionTests(unittest.TestCase):
    def test_native_pipe_session_preserves_unicode_and_closes_on_eof(self):
        with tempfile.TemporaryDirectory(prefix='Atlas session é ') as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            process = subprocess.Popen([sys.executable, '-m', 'workbench_atlas_recipe_health.cli',
                                        'session', str(graph['path'])], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                ready = json.loads(process.stdout.readline())
                request = {'format': PREFIX + 'request-v1', 'schema_version': 1,
                           'request_id': '1', 'graph_set_id': ready['graph_set_id'],
                           'operation': 'search', 'arguments': {'query': 'target é'}}
                process.stdin.write((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                self.assertEqual('target é', response['result']['query'])
                process.stdin.close(); process.stdin = None
                remaining, errors = process.communicate(timeout=15)
                self.assertEqual((0, b'', b''), (process.returncode, remaining, errors))
            finally:
                if process.poll() is None:
                    process.kill(); process.communicate()
    def test_linked_search_and_browse_reuse_the_exact_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            identifier = json.loads((graph['path'] / 'manifest.json').read_text())['graph_set_id']
            requests = []
            for index, (operation, arguments) in enumerate([
                ('search', {'query': 'target', 'limit': 50}),
                ('browse', {'selection_id': graph['resources']['target']['id'], 'limit': 1}),
                ('browse', {'selection_id': graph['recipes']['final']['id'], 'limit': 2}),
                ('close', {}),
            ]):
                requests.append({'format': PREFIX + 'request-v1', 'schema_version': 1,
                                 'request_id': str(index), 'graph_set_id': identifier,
                                 'operation': operation, 'arguments': arguments})
            output = io.StringIO()
            self.assertEqual(0, serve_recipe_session(graph['path'],
                input=io.StringIO(''.join(json.dumps(row) + '\n' for row in requests)), output=output))
            results = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(['ready', 'complete', 'complete', 'complete', 'closed'], [r['state'] for r in results])
            self.assertEqual([identifier] * 5, [r['graph_set_id'] for r in results])
            self.assertEqual(200, results[3]['result']['selection']['properties']['duration'])
            self.assertEqual(2, results[3]['result']['page']['next_offset'])

    def test_changed_files_invalidate_reused_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            manifest = graph['path'] / 'manifest.json'
            identifier = json.loads(manifest.read_text())['graph_set_id']
            request = {'format': PREFIX + 'request-v1', 'schema_version': 1,
                       'request_id': '1', 'graph_set_id': identifier, 'operation': 'search', 'arguments': {'query': 'target'}}
            class ChangedAfterReady(io.StringIO):
                def flush(self):
                    if 'ready-v1' in self.getvalue():
                        manifest.write_bytes(manifest.read_bytes() + b'\n')
            output = ChangedAfterReady()
            with self.assertRaisesRegex(AtlasCategoricalGraphError, 'changed'):
                serve_recipe_session(graph['path'], input=io.StringIO(json.dumps(request) + '\n'), output=output)
            self.assertNotIn('"state":"complete"', output.getvalue())

    def test_wrong_graph_is_an_error_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            request = {'format': PREFIX + 'request-v1', 'schema_version': 1,
                       'request_id': '1', 'graph_set_id': 'changed', 'operation': 'search', 'arguments': {'query': 'target'}}
            output = io.StringIO()
            serve_recipe_session(graph['path'], input=io.StringIO(json.dumps(request) + '\n'), output=output)
            self.assertEqual('error', json.loads(output.getvalue().splitlines()[-1])['state'])


if __name__ == '__main__':
    unittest.main()
