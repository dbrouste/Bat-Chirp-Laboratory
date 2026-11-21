"""P2 scientific experiment contracts: preserve baseline and fail closed."""
import unittest

from experiments.p2_hybrid_trajectory import identity, hybrid_candidates, track_negative


def candidate(time, freq, *, width=2., height=25000.):
    return dict(time_mid=time, peak_freq_hz=freq, t_start=time-0.001,
                t_end=time+0.001, f_low=freq-10000, f_high=freq+height-10000,
                detector_branch="general", size=20, height_hz=height,
                width_ms=width, slope_hz_per_ms=-3000., peak_db=15.)


class FakeDetector:
    def _extract_candidates(self, y, sr, *, snr_threshold_db, **kwargs):
        return [candidate(0.002, 50000),
                candidate(0.003, 80000, height=30000),
                candidate(0.005, 110000, width=1.1, height=12000)]

    def _time_freq_nms(self, values, window_ms, freq_window_hz):
        # Synthetic deterministic NMS: only suppress the close second seed in 20ms,
        # while 6ms is allowed to include the nearby second seed in this fixture.
        if window_ms == 20:
            return [c for c in values if c["time_mid"] != 0.003]
        return list(values)

    def detect_candidates_snr_blobs(self, y, sr, *, slope_filter_mode):
        assert slope_filter_mode == "adaptive_v4"
        return self._time_freq_nms(
            [candidate(0.002, 50000), candidate(0.003, 80000, height=30000)],
            20, 25000)


class Track:
    @staticmethod
    def track_reassigned_candidate(y, sr, candidate, **kwargs):
        return [(0.01, 30000), (0.02, 20000)]


class Failing:
    @staticmethod
    def track_reassigned_candidate(y, sr, candidate, **kwargs):
        raise RuntimeError("synthetic failure")


class P2Contracts(unittest.TestCase):
    def test_hybrid_keeps_original_candidates_and_adds_eligible_only(self):
        a, b, extra = hybrid_candidates([], 384000, FakeDetector())
        self.assertEqual(len(a), 1)
        self.assertEqual(len(extra), 2)
        self.assertEqual([identity(c) for c in a], [identity(c) for c in b[:len(a)]])
        self.assertEqual(len(set(map(identity, b))), len(b))

    def test_negative_is_tracker_result_not_candidate_count(self):
        opts = {"n_fft": None, "hop": None, "search_margin_ms": None,
                "trim_echo_rebound": True}
        self.assertTrue(track_negative([], 384000, candidate(0.002, 50000),
                                       Track(), opts)["tracked"])
        failure = track_negative([], 384000, candidate(0.002, 50000),
                                 Failing(), opts)
        self.assertFalse(failure["tracked"])
        self.assertEqual(failure["error"], "RuntimeError")

    def test_candidate_id_is_stable(self):
        x = candidate(0.002, 50000)
        self.assertEqual(identity(x), identity(dict(x)))
        self.assertNotEqual(identity(x), identity({**x, "peak_freq_hz": 51000}))


if __name__ == "__main__":
    unittest.main()
