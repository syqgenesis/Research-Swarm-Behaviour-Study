"""Small deterministic persistent memory for collaborative agents."""
import json
import threading
from pathlib import Path

from swarm import team_config


class TeamMemory:
    """Per-agent notes plus a mechanically generated previous-turn summary."""

    def __init__(self, root, agent_ids):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.agent_ids = set(agent_ids)
        self._lock = threading.RLock()
        for agent_id in agent_ids:
            path = self._path(agent_id)
            if not path.exists():
                self._write(agent_id, {"notes": "", "previous_turn": ""})

    def _path(self, agent_id):
        if agent_id not in self.agent_ids:
            raise ValueError("unknown agent")
        return self.root / f"{agent_id}.json"

    def _read(self, agent_id):
        path = self._path(agent_id)
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        return {
            "notes": str(data.get("notes", "")),
            "previous_turn": str(data.get("previous_turn", "")),
        }

    def _write(self, agent_id, data):
        path = self._path(agent_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)

    def snapshot(self, agent_id):
        with self._lock:
            return self._read(agent_id)

    def save_notes(self, agent_id, text):
        if not isinstance(text, str):
            raise ValueError("memory text must be a string")
        text = text[-team_config.PRIVATE_NOTES_MAX_CHARS:]
        with self._lock:
            data = self._read(agent_id)
            data["notes"] = text
            self._write(agent_id, data)
        return len(text)

    def save_previous_turn(self, agent_id, lines):
        """Persist a deterministic action summary; no LLM summarisation is used."""
        text = "\n".join(f"- {line}" for line in lines if line)
        text = text[-team_config.PREVIOUS_TURN_SUMMARY_MAX_CHARS:]
        with self._lock:
            data = self._read(agent_id)
            data["previous_turn"] = text
            self._write(agent_id, data)
        return text
