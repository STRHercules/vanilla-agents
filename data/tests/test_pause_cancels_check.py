import sys
import threading
import time
import unittest

from vanilla_agents.verify import runner


class PauseCancelsCheck(unittest.TestCase):
    def tearDown(self):
        runner.CANCEL.clear()

    def test_cancel_kills_running_check_fast(self):
        threading.Timer(1.0, runner.CANCEL.set).start()
        t0 = time.time()
        r = runner.run_check([sys.executable, "-c", "import time; time.sleep(60)"], ".", {"SystemRoot": r"C:\Windows", "PATH": ""}, 120)
        self.assertTrue(r["cancelled"])
        self.assertFalse(r["timed_out"])
        self.assertLess(time.time() - t0, 15)

    def test_cancel_already_set_does_not_start(self):
        runner.CANCEL.set()
        r = runner.run_check([sys.executable, "-c", "pass"], ".", {}, 5)
        self.assertTrue(r["cancelled"])
        self.assertIsNone(r["exit_code"])


if __name__ == "__main__":
    unittest.main()
