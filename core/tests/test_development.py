from importlib import metadata
from pathlib import Path
import tempfile
import unittest

from workbench_core.development import SourceDistribution, SourceFinder


class DevelopmentMetadataTests(unittest.TestCase):
    def test_source_distribution_uses_native_version_dependencies_and_entry_points(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary)/'pyproject.toml'
            manifest.write_text('[project]\nname="workbench-example"\nversion="3.2.1"\ndependencies=["workbench-api>=0.1,<0.2"]\n[project.entry-points."workbench.modules"]\nexample="example:module"\n')
            distribution = SourceDistribution(manifest)
            self.assertEqual('3.2.1',distribution.version)
            self.assertEqual(['workbench-api>=0.1,<0.2'],distribution.requires)
            entry, = distribution.entry_points
            self.assertEqual(('example','example:module'), (entry.name,entry.value))
            finder = SourceFinder(manifest.parent,(distribution,))
            self.assertEqual([distribution],list(finder.find_distributions(metadata.DistributionFinder.Context(name='Workbench_Example'))))
            self.assertEqual([],list(finder.find_distributions(metadata.DistributionFinder.Context(name='absent'))))
