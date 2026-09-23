import unittest
from hashlib import sha256

from workbench_api.packwiz import client_dependencies
from workbench_api.prism import encode_launch_script, parse_launch_script


def sources():
    return {
        "pack.toml": b'name="Pack"\npack-format="packwiz:1.1.0"\n[index]\nfile="index.toml"\nhash-format="sha256"\nhash="derived"\n[versions]\nminecraft="1.12.2"\n',
        "index.toml": b'hash-format="sha256"\n',
        "mods/a.pw.toml": (
            'name="A"\nfilename="a.jar"\n[download]\nurl="https://example.org/a.jar"\nhash-format="sha256"\nhash="'
            + sha256(b"artifact").hexdigest()
            + '"\n'
        ).encode(),
        "groovy/a.groovy": b"saved source",
        "README.md": b"documentation",
    }


class EnvironmentContractTests(unittest.TestCase):
    def test_source_documentation_and_derived_hash_changes_reuse_dependencies(self):
        files = sources()
        before = client_dependencies(files, source_roots=("groovy",))
        files["groovy/a.groovy"] = b"changed"
        files["README.md"] = b"more documentation"
        files["pack.toml"] = (
            files["pack.toml"].replace(b"derived", b"refreshed") + b"# comment\n"
        )
        self.assertEqual(before, client_dependencies(files, source_roots=("groovy",)))
        files["mods/a.pw.toml"] += b"[option]\noptional=true\n"
        changed = client_dependencies(files, source_roots=("groovy",))
        self.assertNotEqual(before["id"], changed["id"])
        self.assertFalse(changed["artifacts"][0]["enabled"])

    def test_side_defaults_and_metadata_paths_fail_closed(self):
        files = sources()
        files["mods/a.pw.toml"] += b"[option]\noptional=true\ndefault=true\n"
        self.assertTrue(
            client_dependencies(files, source_roots=())["artifacts"][0]["enabled"]
        )
        files["mods/a.pw.toml"] = b'side="server"\n' + files["mods/a.pw.toml"]
        self.assertFalse(
            client_dependencies(files, source_roots=())["artifacts"][0]["enabled"]
        )
        for bad in (b'filename="../a.jar"', b'filename="a\\\\b.jar"'):
            broken = sources()
            broken["mods/a.pw.toml"] = broken["mods/a.pw.toml"].replace(
                b'filename="a.jar"', bad
            )
            with self.assertRaises(ValueError):
                client_dependencies(broken, source_roots=())
        with self.assertRaises(ValueError):
            client_dependencies(files, source_roots=("mods",))

    def test_protocol_preserves_arrays_spaces_and_rejects_ambiguity(self):
        fields = {
            "mainClass": "example.Main",
            "launcher": "standard",
            "param": ["--gameDir", "path with spaces", "--accessToken", "0"],
        }
        raw = encode_launch_script(fields)
        self.assertEqual(parse_launch_script(raw), fields)
        for bad in (
            raw[:-7],
            raw + b"more\n",
            b"mainClass duplicate\n" + raw,
            b"unknown secret\n" + raw,
            raw.replace(b"path with spaces", b"secret\rvalue"),
        ):
            with self.assertRaises(ValueError) as caught:
                parse_launch_script(bad)
            self.assertNotIn("secret", str(caught.exception))
