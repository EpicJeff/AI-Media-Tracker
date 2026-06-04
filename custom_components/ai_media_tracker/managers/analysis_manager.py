import asyncio
import time

class AnalysisManager:
    def __init__(self):
        self._active_tasks = {}
        self._cooldowns = {}
        self._frame_hashes = {}

    def should_analyze(self, player_name, frame_hash):
        now = time.time()
        cooldown_until = self._cooldowns.get(player_name, 0)

        if now < cooldown_until:
            return False

        if self._frame_hashes.get(player_name) == frame_hash:
            return False

        self._frame_hashes[player_name] = frame_hash
        self._cooldowns[player_name] = now + 8
        return True

    def cancel_existing(self, player_name):
        task = self._active_tasks.get(player_name)
        if task and not task.done():
            task.cancel()

    def register(self, player_name, task):
        self.cancel_existing(player_name)
        self._active_tasks[player_name] = task

