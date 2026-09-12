"""Offline regression tests for the persistent monitor watchdog."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


class MonitorWatchdog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="runs")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restarts_a_child_that_exits(self):
        counter = os.path.join(self.tmp, "starts.txt")
        state = os.path.join(self.tmp, "watchdog.state")
        worker = os.path.join(self.tmp, "worker.py")
        with open(worker, "w", encoding="utf-8") as handle:
            handle.write(
                "import pathlib, sys\n"
                "path = pathlib.Path(sys.argv[1])\n"
                "with path.open('a', encoding='utf-8') as out:\n"
                "    out.write('started\\n')\n"
                "raise SystemExit(7)\n"
            )

        env = dict(os.environ)
        env["MONITOR_RESTART_DELAY_S"] = "0"
        env["MONITOR_WATCHDOG_MAX_STARTS"] = "3"
        env["MONITOR_WATCHDOG_STATE_FILE"] = state
        result = subprocess.run(
            ["ops/monitor_watchdog.sh", sys.executable, worker, counter],
            cwd=os.getcwd(), env=env, capture_output=True, text=True,
            timeout=10, check=False,
        )

        self.assertEqual(result.returncode, 7)
        with open(counter, encoding="utf-8") as handle:
            self.assertEqual(handle.read().splitlines(),
                             ["started", "started", "started"])
        self.assertEqual(result.stderr.count("restarting"), 2)
        self.assertFalse(os.path.exists(state))

    def test_termination_stops_the_child_and_removes_state(self):
        state = os.path.join(self.tmp, "watchdog.state")
        env = dict(os.environ)
        env["MONITOR_WATCHDOG_STATE_FILE"] = state
        process = subprocess.Popen(
            ["ops/monitor_watchdog.sh", sys.executable, "-c",
             "import time; time.sleep(60)"],
            cwd=os.getcwd(), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())

        child_pid = 0
        for _ in range(100):
            if os.path.exists(state):
                with open(state, encoding="utf-8") as handle:
                    _, child = handle.read().split()
                child_pid = int(child)
                if child_pid:
                    break
            time.sleep(0.01)
        self.assertGreater(child_pid, 0)

        process.terminate()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(os.path.exists(state))
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
