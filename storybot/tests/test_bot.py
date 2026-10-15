import asyncio
import tempfile
import unittest
from pathlib import Path

from storybot.config import Config
from storybot.jobs import Batcher, Job, JobQueue, cleanup
from storybot.users import UserStore


class FakeMessenger:
    def __init__(self):
        self.log = []

    async def send_text(self, chat_id, text):
        self.log.append(("text", chat_id, text))

    async def send_video(self, chat_id, path, caption):
        self.log.append(("video", chat_id, Path(path).name, caption))


def cfg(tmp):
    return Config(data_dir=tmp, admin_ids=(1,), music_source="local")


class UsersTest(unittest.TestCase):
    def test_access_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = UserStore(cfg(tmp))
            self.assertTrue(u.is_allowed(1))
            self.assertFalse(u.is_allowed(2))
            u.add(2)
            self.assertTrue(UserStore(cfg(tmp)).is_allowed(2))   # survives restart
            self.assertTrue(u.remove(2))
            self.assertFalse(UserStore(cfg(tmp)).is_allowed(2))

    def test_settings_shape_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = UserStore(cfg(tmp))
            u.set(5, "fit", "blur")
            u.set(5, "length", 20)
            u.set(5, "mood", "chill")
            c = u.config_for(5, cfg(tmp))
            self.assertEqual((c.fit, c.story_target, c.music_tags), ("blur", 20.0, "chill+lounge"))
            self.assertLessEqual(c.story_min, c.story_target)
            u.set(5, "music", False)
            self.assertEqual(u.config_for(5, cfg(tmp)).music_source, "none")
            self.assertEqual(u.config_for(6, cfg(tmp)).fit, "crop")   # defaults for others


class BatcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_window_groups_items(self):
        got = []

        async def ready(key, items, ctx):
            got.append((key, items, ctx))

        b = Batcher(0.05, ready)
        b.open(7, "dirA")
        b.add(7, "a")
        await asyncio.sleep(0.02)
        self.assertEqual(b.open(7, "dirB"), "dirA")   # joins the open batch
        b.add(7, "b")
        await asyncio.sleep(0.1)
        self.assertEqual(got, [(7, ["a", "b"], "dirA")])

    async def test_flush_now(self):
        got = []

        async def ready(key, items, ctx):
            got.append(items)

        b = Batcher(10, ready)
        b.open(1, None)
        b.add(1, "x")
        await b.flush(1)
        self.assertEqual(got, [["x"]])
        self.assertEqual(b.count(1), 0)


class JobQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_with_queue_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg(tmp)
            order = []

            def maker(files, out_dir, cfg_):
                order.append(files[0])
                out_dir.mkdir(parents=True, exist_ok=True)
                p = out_dir / "story_01.mp4"
                p.write_bytes(b"x")
                (out_dir / "credits.txt").write_text("Music: T - A (CC by/4.0)")
                return [p]

            m = FakeMessenger()
            q = JobQueue(c, UserStore(c), m, maker=maker)
            q.start()
            await q.submit(Job(1, 1, ["a.mp4"], q.new_work_dir(1)))
            await q.submit(Job(2, 2, ["b.mp4"], q.new_work_dir(2)))
            await asyncio.wait_for(q.queue.join(), 5)
            self.assertEqual(order, ["a.mp4", "b.mp4"])
            self.assertTrue(any(t[0] == "text" and t[1] == 2 and "перед вами 1" in t[2] for t in m.log))
            vids = [t for t in m.log if t[0] == "video"]
            self.assertEqual(len(vids), 2)
            self.assertIn("CC by/4.0", vids[0][3])

    async def test_failure_is_reported_and_queue_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg(tmp)
            calls = []

            def maker(files, out_dir, cfg_):
                calls.append(files[0])
                if files[0] == "bad.mp4":
                    raise RuntimeError("boom")
                return []

            m = FakeMessenger()
            q = JobQueue(c, UserStore(c), m, maker=maker)
            q.start()
            await q.submit(Job(1, 1, ["bad.mp4"], q.new_work_dir(1)))
            await q.submit(Job(1, 1, ["ok.mp4"], q.new_work_dir(1)))
            await asyncio.wait_for(q.queue.join(), 5)
            self.assertEqual(calls, ["bad.mp4", "ok.mp4"])
            texts = [t[2] for t in m.log if t[0] == "text"]
            self.assertTrue(any("Не получилось" in t for t in texts))
            self.assertTrue(any("ничего пригодного" in t for t in texts))


class CleanupTest(unittest.TestCase):
    def test_removes_only_old(self):
        import os
        import time
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg(tmp)
            old = Path(tmp) / "jobs" / "1" / "old"
            new = Path(tmp) / "jobs" / "1" / "new"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            t = time.time() - 48 * 3600
            os.utime(old, (t, t))
            self.assertEqual(cleanup(c), 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
