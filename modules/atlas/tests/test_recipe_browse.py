"""Compare paged neighborhoods with independent canonical fixture edges."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from workbench_atlas_recipe_health import open_recipe_health, RecipeHealthError
from workbench_atlas_recipe_health.browse import browse_recipe_evidence
from workbench_atlas_recipe_health.cli import main
from test_captured_recipe_routes import route_graph, quest_target_fixture


class RecipeBrowseTests(unittest.TestCase):
    def test_all_pages_preserve_exact_edges_quantities_and_node_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            with open_recipe_health(graph['path']) as view:
                for node in [*graph['resources'].values(), *graph['recipes'].values(), *graph['selectors'].values()]:
                    expected = {(edge['id'], direction) for edge in graph['edges'].values()
                                for key, direction in [('source', 'outgoing'), ('target', 'incoming')]
                                if edge[key] == node['id']}
                    actual, offset = set(), 0
                    while True:
                        page = browse_recipe_evidence(view, node['id'], offset=offset, limit=2)
                        self.assertEqual(node['properties'], page['selection']['properties'])
                        self.assertEqual(len(expected), page['page']['total'])
                        for row in page['links']:
                            edge = row['relationship']
                            self.assertEqual(graph['edges'][edge['id']], edge)
                            key = (edge['id'], row['direction'])
                            self.assertNotIn(key, actual)
                            actual.add(key)
                            peer = 'target' if row['direction'] == 'outgoing' else 'source'
                            self.assertEqual(edge[peer], row['node']['selection_id'])
                        offset = page['page']['next_offset']
                        if offset is None:
                            break
                    self.assertEqual(expected, actual)

    def test_cycle_and_representatives_remain_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = route_graph(Path(temporary) / 'graph', [{'name': 'loop',
                'inputs': [{'members': ['target'], 'observed': ['unknown'], 'complete': False}],
                'outputs': ['target']}])
            selector = graph['selectors'][('loop', 0)]['id']
            with open_recipe_health(graph['path']) as view:
                page = browse_recipe_evidence(view, selector)
                self.assertFalse(page['selection']['properties']['acceptance_complete'])
                self.assertEqual({'has-item-input-selector', 'accepts-gt-item-alternative',
                                  'observes-gt-item-input-representative'},
                                 {row['relationship']['relation'] for row in page['links']})
                self.assertIn('Craftability', page['scope'])
                with self.assertRaisesRegex(RecipeHealthError, 'graph changed'):
                    browse_recipe_evidence(view, selector, expected_graph='another')
                for offset in (-1, True):
                    with self.assertRaises(RecipeHealthError):
                        browse_recipe_evidence(view, selector, offset=offset)

    def test_cli_binds_graph_and_paging(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = quest_target_fixture(Path(temporary) / 'graph')
            output = io.StringIO()
            self.assertEqual(0, main(['browse', str(graph['path']), graph['recipes']['final']['id'],
                                     '--limit', '1', '--json'], output=output))
            page = json.loads(output.getvalue())
            self.assertEqual(1, len(page['links']))
            self.assertEqual(1, page['page']['next_offset'])


if __name__ == '__main__':
    unittest.main()
