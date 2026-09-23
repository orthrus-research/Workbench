from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_kernel import (  # noqa: E402
    AuthorityBinding,
    KernelValidationError,
    PinnedGraphQueryInput,
    PinnedGraphQueryMember,
    PinnedGraphQueryObject,
    PinnedQueryKernelConfig,
    PinnedQueryOwnerResult,
    execute_pinned_graph_query,
)
from workbench_api.canonical import canonical_json_bytes
from workbench_crucible.synthetic import (  # noqa: E402
    build_synthetic_publication,
)
import workbench_crucible_kernel.query as query_module  # noqa: E402


def _id(kind: str, label: str) -> str:
    return f"{kind}:sha256:{hashlib.sha256(label.encode('utf-8')).hexdigest()}"


class CruciblePinnedQueryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = build_synthetic_publication()
        graph = cls.publication.record("graph-revision")
        graph_set = cls.publication.record("graph-set-revision")
        graph_rows = tuple(
            record
            for record in cls.publication.records
            if record.kind == "graph-record"
            and record.to_dict()["graph_family_id"] == graph["graph_family_id"]
            and record.to_dict()["category_id"] == graph["category_id"]
            and record.to_dict()["resolution_id"] == graph["resolution_id"]
        )
        cls.rows = tuple(record.canonical_bytes for record in graph_rows)
        cls.row_ids = tuple(record.id for record in graph_rows)
        cls.context_ref_id = graph["context_ref_id"]
        cls.graph_revision_id = graph["id"]
        cls.graph_set_revision_id = graph_set["id"]
        cls.query_input = PinnedGraphQueryInput(
            context_ref_id=cls.context_ref_id,
            graph_set_revision_id=cls.graph_set_revision_id,
            publication_proof_id=_id(
                "graph-bundle-publication-proof", "synthetic-query-proof-v1"
            ),
            members=(
                PinnedGraphQueryMember(
                    role="primary",
                    graph_revision_id=cls.graph_revision_id,
                    graph_records=cls.rows,
                ),
            ),
        )
        owner = graph["authority_owner"]
        cls.config = PinnedQueryKernelConfig(
            query_algorithm_id=_id("query-algorithm", "synthetic-count-v1"),
            query_implementation_id=_id(
                "query-implementation", "synthetic-count-python-v1"
            ),
            query_owner=AuthorityBinding(**owner),
            maximum_records=64,
            maximum_input_bytes=1024 * 1024,
            maximum_result_bytes=4096,
        )

    def assertCode(self, caught, code: str) -> None:
        self.assertEqual(code, caught.exception.code)

    def assertDiagnostic(
        self,
        caught,
        code: str,
        path: str,
        message: str,
    ) -> None:
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(path, caught.exception.path)
        self.assertEqual(message, caught.exception.message)
        self.assertEqual(
            f"{code} at {path}: {message}",
            str(caught.exception),
        )

    @classmethod
    def _port(cls, view):
        records = view.records_by_role["primary"]
        answer = {
            "format": "synthetic-pinned-answer-v1",
            "graph_set_revision_id": view.graph_set_revision_id,
            "minimum_kind": view.parameters["minimum_kind"],
            "record_count": len(records),
        }
        return PinnedQueryOwnerResult(
            canonical_result_bytes=canonical_json_bytes(answer),
            inspected_graph_record_ids=tuple(
                reversed(tuple(record.id for record in records))
            ),
        )

    def test_query_binds_exact_revisions_parameters_owner_and_inspections(self) -> None:
        parameters = canonical_json_bytes({"minimum_kind": "node"})
        first = execute_pinned_graph_query(
            self.query_input,
            config=self.config,
            parameter_bytes=parameters,
            query_port=self._port,
        )
        second = execute_pinned_graph_query(
            self.query_input,
            config=self.config,
            parameter_bytes=parameters,
            query_port=self._port,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            (("primary", self.graph_revision_id),),
            first.graph_revision_ids,
        )
        self.assertEqual(
            hashlib.sha256(parameters).hexdigest(), first.parameter_sha256
        )
        self.assertEqual(
            tuple(sorted(self.row_ids, key=lambda item: item.encode("utf-8"))),
            first.inspected_graph_record_ids,
        )
        self.assertEqual(
            hashlib.sha256(first.canonical_result_bytes).hexdigest(),
            first.result_sha256,
        )
        self.assertEqual(64, len(first.input_closure_sha256))

    def test_input_is_isolated_and_query_port_cannot_mutate_it(self) -> None:
        def mutating(view):
            view.records_by_role["other"] = ()

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=self.config,
                parameter_bytes=b"{}",
                query_port=mutating,
            )
        self.assertCode(caught, "kernel.query-port-failed")

        def mutate_nested_parameter(view):
            view.parameters["values"].append("changed")
            return PinnedQueryOwnerResult(b"{}", ())

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=self.config,
                parameter_bytes=b'{"values":[]}',
                query_port=mutate_nested_parameter,
            )
        self.assertCode(caught, "kernel.query-port-mutation")

    def test_noncanonical_parameters_and_wrong_context_fail_before_port(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=self.config,
                parameter_bytes=b'{"minimum_kind": "node"}',
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-invalid-parameters")

        wrong = replace(
            self.query_input,
            context_ref_id="context-ref:sha256:" + "0" * 64,
        )
        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                wrong,
                config=self.config,
                parameter_bytes=b"{}",
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-record-scope")
        self.assertFalse(called)

    def test_record_and_byte_bounds_fail_before_port(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=replace(self.config, maximum_records=1),
                parameter_bytes=b"{}",
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-record-bound")
        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=replace(self.config, maximum_input_bytes=1),
                parameter_bytes=b"{}",
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-input-bound")
        self.assertFalse(called)

        oversized_role = replace(
            self.query_input.members[0], role="a" * 8193
        )
        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                replace(self.query_input, members=(oversized_role,)),
                config=self.config,
                parameter_bytes=b"{}",
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-invalid-role")
        self.assertFalse(called)

    def test_record_count_bound_precedes_parsing_the_excess_row(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        malformed = b"not-canonical-c01"
        member = replace(
            self.query_input.members[0],
            graph_records=(self.rows[0], malformed),
        )
        query_input = replace(self.query_input, members=(member,))
        loader = query_module.load_canonical_record
        with mock.patch.object(
            query_module,
            "load_canonical_record",
            wraps=loader,
        ) as parser:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    query_input,
                    config=replace(self.config, maximum_records=1),
                    parameter_bytes=b"{}",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-record-bound",
            "/input/members/0/graph_records/1",
            "graph rows exceed the configured query bound",
        )
        self.assertNotIn(malformed, tuple(call.args[0] for call in parser.call_args_list))
        self.assertFalse(called)

    def test_hard_object_count_bound_precedes_record_or_object_parsing(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        query_input = replace(
            self.query_input,
            objects=(None,) * 100_001,
        )
        with mock.patch.object(
            query_module,
            "load_canonical_record",
            side_effect=AssertionError("count bound must precede record parsing"),
        ) as parser:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    query_input,
                    config=self.config,
                    parameter_bytes=b"{}",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-object-bound",
            "/input/objects",
            "query objects exceed the hard count bound",
        )
        parser.assert_not_called()
        self.assertFalse(called)

    def test_oversized_malformed_inputs_fail_before_parser_or_port(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        with mock.patch.object(
            query_module,
            "parse_canonical_json",
            side_effect=AssertionError("parameter byte bound must precede parsing"),
        ) as parameter_parser:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    self.query_input,
                    config=replace(self.config, maximum_input_bytes=1),
                    parameter_bytes=b"xx",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-input-bound",
            "/parameters",
            "query closure exceeds the configured byte bound",
        )
        parameter_parser.assert_not_called()

        malformed_row = b"not-canonical-c01"
        member = replace(
            self.query_input.members[0],
            graph_records=(malformed_row,),
        )
        query_input = replace(self.query_input, members=(member,))
        row_bound = len(b"{}") + len(malformed_row) - 1
        with mock.patch.object(
            query_module,
            "load_canonical_record",
            side_effect=AssertionError("row byte bound must precede parsing"),
        ) as row_parser:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    query_input,
                    config=replace(self.config, maximum_input_bytes=row_bound),
                    parameter_bytes=b"{}",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-input-bound",
            "/input/members/0/graph_records/0",
            "query closure exceeds the configured byte bound",
        )
        row_parser.assert_not_called()

        descriptor_bytes = b"not-canonical-descriptor"
        object_raw = b"object"
        query_input = replace(
            self.query_input,
            objects=(PinnedGraphQueryObject(descriptor_bytes, object_raw),),
        )
        object_bound = (
            len(b"{}")
            + sum(len(row) for row in self.rows)
            + len(descriptor_bytes)
            + len(object_raw)
            - 1
        )
        loader = query_module.load_canonical_record
        with mock.patch.object(
            query_module,
            "load_canonical_record",
            wraps=loader,
        ) as object_parser:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    query_input,
                    config=replace(self.config, maximum_input_bytes=object_bound),
                    parameter_bytes=b"{}",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-input-bound",
            "/input/objects/0",
            "query closure exceeds the configured byte bound",
        )
        self.assertNotIn(
            descriptor_bytes,
            tuple(call.args[0] for call in object_parser.call_args_list),
        )
        self.assertFalse(called)

    def test_hard_per_object_byte_bound_precedes_descriptor_parsing(self) -> None:
        called = False

        def port(view):
            nonlocal called
            called = True
            return self._port(view)

        descriptor_bytes = b"not-canonical-descriptor"
        object_raw = b"oversized"
        query_input = replace(
            self.query_input,
            objects=(PinnedGraphQueryObject(descriptor_bytes, object_raw),),
        )
        loader = query_module.load_canonical_record
        with (
            mock.patch.object(query_module, "_MAX_OBJECT_BYTES", 1),
            mock.patch.object(
                query_module,
                "load_canonical_record",
                wraps=loader,
            ) as parser,
        ):
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    query_input,
                    config=self.config,
                    parameter_bytes=b"{}",
                    query_port=port,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-object-byte-bound",
            "/input/objects/0/object_bytes",
            "immutable object exceeds the hard byte bound",
        )
        self.assertNotIn(
            descriptor_bytes,
            tuple(call.args[0] for call in parser.call_args_list),
        )
        self.assertFalse(called)

    def test_object_closure_is_digest_checked_and_available_to_owner(self) -> None:
        descriptor = next(
            record
            for record in self.publication.records
            if record.kind == "object-descriptor"
            and record.to_dict()["object_id"] in self.publication.blobs_by_id
        )
        object_id = descriptor.to_dict()["object_id"]
        object_bytes = self.publication.blobs_by_id[object_id]
        query_input = replace(
            self.query_input,
            objects=(
                PinnedGraphQueryObject(descriptor.canonical_bytes, object_bytes),
            ),
        )

        def port(view):
            self.assertEqual(descriptor.canonical_bytes, view.descriptor(descriptor.id))
            self.assertEqual(object_bytes, view.object(object_id))
            return PinnedQueryOwnerResult(b"{}", ())

        execute_pinned_graph_query(
            query_input,
            config=self.config,
            parameter_bytes=b"{}",
            query_port=port,
        )
        tampered = replace(
            query_input,
            objects=(
                PinnedGraphQueryObject(descriptor.canonical_bytes, b"tampered"),
            ),
        )
        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                tampered,
                config=self.config,
                parameter_bytes=b"{}",
                query_port=port,
            )
        self.assertCode(caught, "kernel.query-object-binding")

    def test_owner_failures_and_forged_inspection_are_rejected(self) -> None:
        def raising(view):
            del view
            raise KernelValidationError("owner.forged", "/x", "forged")

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=self.config,
                parameter_bytes=b"{}",
                query_port=raising,
            )
        self.assertCode(caught, "kernel.query-port-failed")

        def forged(view):
            del view
            return PinnedQueryOwnerResult(
                b"{}", ("graph-record:sha256:" + "f" * 64,)
            )

        with self.assertRaises(KernelValidationError) as caught:
            execute_pinned_graph_query(
                self.query_input,
                config=self.config,
                parameter_bytes=b"{}",
                query_port=forged,
            )
        self.assertCode(caught, "kernel.query-invalid-inspection")

    def test_owner_inspection_count_is_bounded_before_scanning_or_result_parse(
        self,
    ) -> None:
        class ExplosiveInspectionId:
            def __hash__(self):
                raise AssertionError("inspection IDs must not be hashed")

            def __eq__(self, other):
                del other
                raise AssertionError("inspection IDs must not be compared")

        explosive = ExplosiveInspectionId()
        oversized = (explosive,) * (query_module._MAX_RECORDS + 1)

        def oversized_result(view):
            del view
            return PinnedQueryOwnerResult(b"not-canonical-json", oversized)

        result_bytes = b"not-canonical-json"
        parser = query_module.parse_canonical_json
        with mock.patch.object(
            query_module,
            "parse_canonical_json",
            wraps=parser,
        ) as parse:
            with self.assertRaises(KernelValidationError) as caught:
                execute_pinned_graph_query(
                    self.query_input,
                    config=self.config,
                    parameter_bytes=b"{}",
                    query_port=oversized_result,
                )
        self.assertDiagnostic(
            caught,
            "kernel.query-inspection-bound",
            "/query_port/result/inspected_graph_record_ids",
            "inspected row count exceeds the pinned graph closure or hard bound",
        )
        self.assertNotIn(
            result_bytes,
            tuple(call.args[0] for call in parse.call_args_list),
        )

    def test_result_must_be_canonical_bounded_json_object(self) -> None:
        cases = (
            (b'{"answer": 1}', "kernel.query-invalid-result"),
            (b"[]", "kernel.query-invalid-result"),
            (b"{}", "kernel.query-result-bound"),
        )
        for raw, code in cases:
            config = (
                replace(self.config, maximum_result_bytes=1)
                if code == "kernel.query-result-bound"
                else self.config
            )

            def port(view, value=raw):
                del view
                return PinnedQueryOwnerResult(value, ())

            with self.subTest(code=code):
                with self.assertRaises(KernelValidationError) as caught:
                    execute_pinned_graph_query(
                        self.query_input,
                        config=config,
                        parameter_bytes=b"{}",
                        query_port=port,
                    )
                self.assertCode(caught, code)


if __name__ == "__main__":
    unittest.main()
