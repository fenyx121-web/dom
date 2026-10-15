import unittest

import numpy as np

from storybot.analyze import Analysis, MediaInfo
from storybot.config import Config
from storybot.plan import Shot, choose_unit, group_stories, select_shots, snap_to_beats


def _analysis(score, speech=None, valid=None):
    n = len(score)
    return Analysis(MediaInfo("x.mp4", float(n), 1080, 1440, True),
                    np.array(score, float),
                    np.array(speech if speech is not None else [False] * n),
                    np.array(valid if valid is not None else [True] * n))


class PlanTest(unittest.TestCase):
    def test_dark_seconds_never_selected(self):
        score = [0.9] * 10 + [0.0] * 5 + [0.9] * 10
        valid = [True] * 10 + [False] * 5 + [True] * 10
        shots = select_shots(_analysis(score, valid=valid), 0, Config())
        for s in shots:
            self.assertFalse(10 <= s.start < 15 or 10 < s.end <= 15, s)

    def test_long_runs_split(self):
        shots = select_shots(_analysis([0.9] * 20), 0, Config(keep_ratio=1.0))
        self.assertTrue(all(s.dur <= Config().shot_max + 1e-9 for s in shots))

    def test_unit_near_two_seconds(self):
        self.assertAlmostEqual(choose_unit(120), 2.0)
        self.assertAlmostEqual(choose_unit(60), 2.0)

    def test_snap_lands_on_grid_without_overlap(self):
        shots = [Shot(0, 4, 9, 1, 0), Shot(0, 9, 14, 1, 0), Shot(0, 20, 23.3, 1, 0)]
        out = snap_to_beats(shots, 2.0, [30.0])
        for s in out:
            self.assertAlmostEqual(s.dur / 2.0, round(s.dur / 2.0))
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(a.end, b.start + 1e-9)
        self.assertGreaterEqual(out[0].start, 4)   # never grows back into unselected footage

    def test_speech_rounds_up(self):
        out = snap_to_beats([Shot(0, 2, 7.2, 1, 1.0)], 2.0, [30.0])
        self.assertGreaterEqual(out[0].dur, 5.2)

    def test_story_lengths(self):
        cfg = Config()
        shots = [Shot(0, i * 4, i * 4 + 4, 1, 0) for i in range(40)]   # 160 s
        stories = group_stories(shots, cfg)
        for st in stories:
            self.assertLessEqual(sum(s.dur for s in st), cfg.story_max)
        self.assertEqual(sum(len(st) for st in stories), 40)


if __name__ == "__main__":
    unittest.main()
