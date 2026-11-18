"""Owned subprocess crash injection; never an installed production entry point."""
import os
from pathlib import Path
import sys

from agentbus.execution.state_store import StateStore
from agentbus.repair.live.service import ConnectedRepairService
from agentbus.repair.live.transport import ApplicationConnector
from agentbus.repair.service import RepairService

workspace, root, database, plan_id, phase = sys.argv[1:]
if os.environ.get("SYNDRA_TEST_POSTGRES") != "1":
    raise SystemExit("Disposable integration test authorization is required.")


class CrashingConnector(ApplicationConnector):
    def apply(self, operation):
        if phase == "before_dispatch":
            os._exit(71)
        result = super().apply(operation)
        if phase == "after_target_commit":
            os._exit(72)
        return result

    def verify(self, operation_id):
        if phase == "during_verification":
            os._exit(73)
        return super().verify(operation_id)


if phase == "before_execute":
    os._exit(74)
service = ConnectedRepairService(RepairService(root), Path(workspace), StateStore(database), connector_factory=CrashingConnector)
service.execute(plan_id)
