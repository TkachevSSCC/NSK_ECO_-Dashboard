"""Small, deterministic contract-test runner for the dashboard UI."""
from __future__ import annotations

import io
import unittest
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/tests", tags=["tests"])
BACKEND_DIR = Path(__file__).resolve().parents[3]
TESTS_DIR = BACKEND_DIR / "tests"
CONTRACT_TEST_PATTERN = "test_environmental_data_contract.py"


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_tests: list[str] = []

    def addSuccess(self, test) -> None:
        super().addSuccess(test)
        self.passed_tests.append(test.id().rsplit(".", 1)[-1])


@router.post("/run")
def run_contract_tests() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(TESTS_DIR),
        pattern=CONTRACT_TEST_PATTERN,
        top_level_dir=str(BACKEND_DIR),
    )
    output = io.StringIO()
    result = unittest.TextTestRunner(
        stream=output, verbosity=2, resultclass=RecordingResult
    ).run(suite)

    issues = [
        {"test": str(test), "message": detail}
        for test, detail in [*result.failures, *result.errors]
    ]
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    return {
        "status": "passed" if result.wasSuccessful() else "failed",
        "total": result.testsRun,
        "passed": passed,
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "tests": result.passed_tests,
        "issues": issues,
    }
