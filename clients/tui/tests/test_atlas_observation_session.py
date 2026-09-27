"""Persistent Atlas observation transport through the selected Core command."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from workbench_tui.core_client import CoreClient, CoreClientError


GRAPH_ID = "workbench-atlas-graph-set-v3:sha256:" + "a" * 64


class AtlasObservationSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_verified_session_serves_requests_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = root / "graph"
            graph.mkdir()
            log = root / "requests.jsonl"
            launcher = root / "fake-core.py"
            launcher.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                f"graph_id = {GRAPH_ID!r}\n"
                f"log = Path({str(log)!r})\n"
                "assert sys.argv[1:5] == ['atlas', 'observations', 'session', "
                f"{str(graph)!r}]\n"
                "print('Verifying graph...', file=sys.stderr, flush=True)\n"
                "print(json.dumps({'format':'workbench-atlas-observation-session-ready-v1',"
                "'schema_version':1,'state':'ready','graph_set_id':graph_id,"
                "'context':{'graph_set_id':graph_id,'root':sys.argv[4]},"
                "'operations':['context','search','inspect','relationships','close']}),flush=True)\n"
                "for line in sys.stdin:\n"
                " request=json.loads(line)\n"
                " with log.open('a',encoding='utf-8') as out: out.write(line)\n"
                " response={'format':'workbench-atlas-observation-session-response-v1',"
                "'schema_version':1,'request_id':request['request_id'],"
                "'graph_set_id':graph_id}\n"
                " if request['operation']=='close':\n"
                "  print(json.dumps({**response,'state':'closed'}),flush=True)\n"
                "  break\n"
                " if request['operation']=='relationships':\n"
                "  print(json.dumps({**response,'state':'error',"
                "'error':{'code':'operation-failed','message':'selected link unavailable'}}),flush=True)\n"
                "  continue\n"
                " print(json.dumps({**response,'state':'complete',"
                "'result':{'format':'workbench-atlas-observation-'+request['operation']+'-v1',"
                "'arguments':request['arguments']}}),flush=True)\n",
                encoding="utf-8",
            )
            client = CoreClient((sys.executable, str(launcher)))
            session = await client.open_atlas_observation_session(graph)
            self.assertEqual(GRAPH_ID, session.graph_set_id)
            self.assertEqual(str(graph), session.context["root"])
            search = await session.request("search", {"query": "circuit", "limit": 3})
            self.assertEqual("circuit", search["arguments"]["query"])
            selected = await session.request("inspect", {"selection_id": "node-1"})
            self.assertEqual("node-1", selected["arguments"]["selection_id"])
            with self.assertRaisesRegex(CoreClientError, "selected link unavailable"):
                await session.request("relationships", {"selection_id": "node-1"})
            await session.close()
            self.assertEqual(0, session.process.returncode)
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(["search", "inspect", "relationships", "close"],
                             [row["operation"] for row in requests])
            self.assertTrue(all(row["graph_set_id"] == GRAPH_ID for row in requests))
            with self.assertRaisesRegex(CoreClientError, "closed"):
                await session.request("search", {"query": "circuit"})

    async def test_missing_graph_does_not_start_core(self) -> None:
        client = CoreClient((sys.executable, "unused-core.py"))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CoreClientError, "existing absolute Atlas graph"):
                await client.open_atlas_observation_session(Path(temporary) / "missing")

    async def test_mismatched_response_stops_session_before_another_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = root / "graph"
            graph.mkdir()
            launcher = root / "wrong-response.py"
            launcher.write_text(
                "import json, sys, time\n"
                f"graph_id={GRAPH_ID!r}\n"
                "print(json.dumps({'format':'workbench-atlas-observation-session-ready-v1',"
                "'schema_version':1,'state':'ready','graph_set_id':graph_id,"
                "'context':{'graph_set_id':graph_id},"
                "'operations':['search','inspect','relationships','close']}),flush=True)\n"
                "request=json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'format':'workbench-atlas-observation-session-response-v1',"
                "'schema_version':1,'request_id':'different',"
                "'graph_set_id':graph_id,'state':'complete','result':{}}),flush=True)\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            session = await CoreClient((sys.executable, str(launcher))).open_atlas_observation_session(graph)
            with self.assertRaisesRegex(CoreClientError, "does not match"):
                await session.request("search", {"query": "circuit"})
            self.assertIsNotNone(session.process.returncode)
            with self.assertRaisesRegex(CoreClientError, "closed"):
                await session.request("search", {"query": "other"})


if __name__ == "__main__":
    unittest.main()
