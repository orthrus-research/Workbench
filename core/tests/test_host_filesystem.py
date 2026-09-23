"""Host-private filesystem policy regression tests."""

from __future__ import annotations

import unittest


from workbench_core.host_filesystem import (
    _windows_private_descriptor,
)


class WindowsPrivateDescriptorTests(unittest.TestCase):
    SID = "S-1-5-21-1-2-3-1001"

    def assert_private(self, dacl: str, *, directory: bool = True) -> None:
        self.assertTrue(
            _windows_private_descriptor(
                f"O:{self.SID}G:{self.SID}{dacl}",
                user_sid=self.SID,
                directory=directory,
            )
        )

    def test_accepts_equivalent_directory_sddl_serializations(self) -> None:
        self.assert_private(
            f"D:PAI(A;CIOI;0x001f01ff;;;S-1-5-18)(A;OICI;FA;;;{self.SID})"
        )

    def test_accepts_exact_file_dacl(self) -> None:
        self.assert_private(
            f"D:P(A;;FA;;;SY)(A;;FA;;;{self.SID})",
            directory=False,
        )

    def test_rejects_unprotected_or_broad_access(self) -> None:
        self.assertFalse(
            _windows_private_descriptor(
                f"D:AI(A;OICI;FA;;;SY)(A;OICI;FA;;;{self.SID})",
                user_sid=self.SID,
                directory=True,
            )
        )
        self.assertFalse(
            _windows_private_descriptor(
                f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{self.SID})(A;OICI;FR;;;WD)",
                user_sid=self.SID,
                directory=True,
            )
        )

    def test_rejects_inherit_only_or_partial_owner_access(self) -> None:
        self.assertFalse(
            _windows_private_descriptor(
                f"D:P(A;OICIIO;FA;;;SY)(A;OICI;FA;;;{self.SID})",
                user_sid=self.SID,
                directory=True,
            )
        )
        self.assertFalse(
            _windows_private_descriptor(
                f"D:P(A;OICI;FA;;;SY)(A;OICI;FR;;;{self.SID})",
                user_sid=self.SID,
                directory=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
