"""D-415: two processes writing the approvals file must not lose a veto."""
import multiprocessing
import os
import tempfile
import unittest

from vanilla_agents.app import approvals


def _worker(p, i):
    approvals.add(p, "proj", "t%d" % i, "x", float(i), 100, 5)


class Lock(unittest.TestCase):
    def test_parallel_writers_lose_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "a.json")
            ps = [multiprocessing.Process(target=_worker, args=(p, i)) for i in range(6)]
            [x.start() for x in ps]
            [x.join(60) for x in ps]
            self.assertEqual(len(approvals.load(p, "proj")), 6)
            self.assertFalse(os.path.exists(p + ".lock"))

    def test_stale_lock_is_broken(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "a.json")
            open(p + ".lock", "w").close()
            os.utime(p + ".lock", (1, 1))
            self.assertTrue(approvals.add(p, "proj", "t", "x", 1.0, 10, 5))


if __name__ == "__main__":
    unittest.main()
