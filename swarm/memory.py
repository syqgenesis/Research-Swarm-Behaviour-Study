"""Agent C, part three — private per-agent memory on disk.

The paper's agents kept files that outlived a turn: an append-only RESEARCH.md
protocol and wiki pages under /tmp/w/memory/wiki/. prover-theta wrote its
discovery into both, and prover-mu catalogued what it had reverse-engineered in
a wiki page of its own. Without durable private notes an agent starts every step
amnesiac, and the behaviour under study — carrying a technique forward, building
on your own earlier conclusion — cannot happen at all.

Three properties this module exists to guarantee:

  * an agent can only ever touch its OWN directory. The path whitelist admits
    nothing but wiki/<lowercase-name>.md, so `..`, absolute paths, backslashes
    and nested directories are impossible by construction rather than filtered
    out afterwards; realpath containment is checked anyway, as defence in depth.
  * the journal is APPEND-only. No tool can rewrite or truncate it, so the
    forensic record of what an agent believed, and when, cannot be tidied up
    after the fact.
  * every write is bounded. Per file, per journal entry, per journal, per agent,
    and in number of pages. A cap failure writes nothing at all.

Memory is private, so it is NOT an exposure channel: nothing written here can
reach another agent. It never appears in `exposure`, only in `ctx`.

RECOVERY.md is a separate, harness-owned bounded excerpt, automatically shown
to its owner. It does not consume the agent-written journal/wiki quota and
cannot be edited through the agent's memory tools. Exact history stays in logs.

No network. No logging — run.py writes the events. Only config and the stdlib.
"""
import json
import os
import re
import threading

from swarm import config

_JOURNAL_HEADER = "# RESEARCH.md — %s\n"
_ENTRY = "\n## step %s\n%s\n"
_TRUNCATED = ("[%d earlier bytes are not shown. Call read_memory on RESEARCH.md "
              "for the whole file.]\n")


def validate_path(path, for_write):
    """-> (path, None) if the agent may touch it, else (None, reason).

    Pure, and deliberately strict. RESEARCH.md is readable but never writable:
    append_journal is the only way in, which is what makes it append-only.
    """
    if not isinstance(path, str) or not path:
        return None, "path must be a non-empty string"
    if path == config.MEMORY_JOURNAL:
        if for_write:
            return None, ("%s is append-only; use append_journal"
                          % config.MEMORY_JOURNAL)
        return path, None
    if re.fullmatch(config.MEMORY_WIKI_RE, path):
        return path, None
    return None, ("path must be %s or wiki/<name>.md, where <name> is 1 to 40 "
                  "characters from a-z, 0-9, _ and -" % config.MEMORY_JOURNAL)


def _encode(text):
    """Bytes for any string, including one carrying lone surrogates from JSON."""
    if not isinstance(text, str):
        return None
    return text.encode("utf-8", errors="replace")


class MemoryStore:
    """One directory per agent, created up front so index() never has to guess.

    One lock for the whole store: writes are small, agents are few, and a lock
    per agent would buy nothing but a way to get it wrong.
    """

    def __init__(self, root, agent_ids):
        self.root = os.path.abspath(root)
        if os.path.realpath(self.root) != self.root:
            raise ValueError("memory root must not use symlinks")
        self.agent_ids = list(agent_ids)
        if any(not isinstance(agent, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", agent)
               for agent in self.agent_ids):
            raise ValueError("invalid memory owner")
        self.lock = threading.Lock()
        self._written = {}          # (agent, path) -> step, for the index
        for agent in self.agent_ids:
            directory = os.path.join(self.root, agent)
            wiki = os.path.join(directory, "wiki")
            worklog = os.path.join(directory, config.WORKLOG_DIR)
            checkpoints = os.path.join(directory, config.CHECKPOINT_DIR)
            if (os.path.realpath(directory) != directory or
                    os.path.realpath(wiki) != wiki or
                    os.path.realpath(worklog) != worklog or
                    os.path.realpath(checkpoints) != checkpoints):
                raise ValueError("memory directories must not use symlinks")
            os.makedirs(wiki, exist_ok=True)
            os.makedirs(worklog, exist_ok=True)
            os.makedirs(checkpoints, exist_ok=True)

    # ------------------------------------------------------------- internals
    def _agent_root(self, agent):
        return os.path.join(self.root, agent)

    def _resolve(self, agent, path):
        """Absolute path, or None if it would escape the agent's directory."""
        if agent not in self.agent_ids:
            return None
        base = self._agent_root(agent)
        candidate = os.path.join(base, path)
        target = os.path.realpath(candidate)
        if target != candidate:
            return None
        if target != base and not target.startswith(base + os.sep):
            return None
        return target

    def _size(self, agent, path):
        target = self._resolve(agent, path)
        if target is None:
            return 0
        try:
            return os.path.getsize(target)
        except OSError:
            return 0

    def _pages(self, agent):
        wiki = os.path.join(self._agent_root(agent), "wiki")
        if agent not in self.agent_ids or os.path.realpath(wiki) != wiki:
            return []
        try:
            return sorted(name for name in os.listdir(wiki) if name.endswith(".md"))
        except OSError:
            return []

    def _total_bytes(self, agent):
        total = self._size(agent, config.MEMORY_JOURNAL)
        for name in self._pages(agent):
            total += self._size(agent, "wiki/" + name)
        return total

    def _worklog_dir(self, agent):
        if agent not in self.agent_ids:
            return None
        directory = os.path.join(self._agent_root(agent), config.WORKLOG_DIR)
        if os.path.realpath(directory) != directory:
            return None
        return directory

    def _worklog_paths(self, agent):
        directory = self._worklog_dir(agent)
        if directory is None:
            return []
        try:
            names = [name for name in os.listdir(directory)
                     if re.fullmatch(r"[0-9]{8}\.json", name)]
        except OSError:
            return []
        return [os.path.join(directory, name) for name in sorted(names)]

    def _worklog_total_bytes(self, agent):
        total = 0
        for path in self._worklog_paths(agent):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    def _checkpoint_dir(self, agent):
        if agent not in self.agent_ids:
            return None
        directory = os.path.join(self._agent_root(agent), config.CHECKPOINT_DIR)
        if os.path.realpath(directory) != directory:
            return None
        return directory

    def _checkpoint_paths(self, agent):
        directory = self._checkpoint_dir(agent)
        if directory is None:
            return []
        try:
            names = [name for name in os.listdir(directory)
                     if re.fullmatch(r"[0-9]{8}\.json", name)]
        except OSError:
            return []
        return [os.path.join(directory, name) for name in sorted(names)]

    def _checkpoint_total_bytes(self, agent):
        return sum(self._safe_size(path) for path in self._checkpoint_paths(agent))

    def _write_atomic(self, target, data):
        tmp = target + ".tmp"
        if os.path.islink(tmp):
            raise OSError("unsafe temporary memory path")
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)

    # ---------------------------------------------------------------- public
    def save_recovery(self, agent, text):
        """Harness-owned bounded checkpoint; never edits the agent's notes.

        This reserved file is not writable through the memory tools. Historical
        checkpoints remain in the transcript log; this file holds the latest.
        """
        data = _encode(text)
        if agent not in self.agent_ids or data is None or len(data) > config.RECOVERY_MAX_BYTES:
            return None, "invalid or oversized recovery checkpoint"
        with self.lock:
            target = self._resolve(agent, config.RECOVERY_FILE)
            if (target is None or os.path.islink(os.path.join(self._agent_root(agent),
                                                            config.RECOVERY_FILE))
                    or os.path.islink(target + ".tmp")):
                return None, "unsafe recovery path"
            try:
                self._write_atomic(target, data)
            except OSError as exc:
                return None, "could not save recovery"
        return len(data), None

    def recovery(self, agent):
        """Private prompt context, with a hard read bound and no channel exposure."""
        if agent not in self.agent_ids:
            return ""
        with self.lock:
            target = self._resolve(agent, config.RECOVERY_FILE)
            if target is None:
                return ""
            try:
                with open(target, "rb") as handle:
                    return handle.read(config.RECOVERY_MAX_BYTES).decode("utf-8", errors="replace")
            except FileNotFoundError:
                return ""

    def archive_work(self, agent, step, hop, result, tool_results):
        """Persist one private, immutable response record before it can be lost.

        The record is deliberately outside the writable memory namespace. It is
        paged back only through the dedicated methods below, and a quota failure
        is reported so run.py can stop rather than silently dropping work.
        """
        if agent not in self.agent_ids or not isinstance(step, int) or not isinstance(hop, int):
            return None, None, "invalid research record"
        record = {
            "step": step,
            "hop": hop,
            "finish_reason": (result or {}).get("finish_reason"),
            "reasoning_content": (result or {}).get("reasoning_content"),
            "content": (result or {}).get("content"),
            "tool_calls": (result or {}).get("tool_calls"),
            "tool_results": tool_results or [],
        }
        try:
            data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8", errors="replace")
        except (TypeError, ValueError):
            return None, None, "could not encode research record"
        if len(data) > config.WORKLOG_ENTRY_MAX_BYTES:
            return None, None, "research record exceeds its private storage limit"
        with self.lock:
            paths = self._worklog_paths(agent)
            record_id = len(paths) + 1
            record["record_id"] = record_id
            try:
                data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8", errors="replace")
            except (TypeError, ValueError):
                return None, None, "could not encode research record"
            if self._worklog_total_bytes(agent) + len(data) > config.WORKLOG_AGENT_MAX_BYTES:
                return None, None, "private research storage is full"
            directory = self._worklog_dir(agent)
            target = os.path.join(directory, "%08d.json" % record_id) if directory else None
            if target is None or os.path.islink(target) or os.path.islink(target + ".tmp"):
                return None, None, "unsafe research record path"
            try:
                self._write_atomic(target, data)
            except OSError:
                return None, None, "could not save research record"
        return record_id, len(data), None

    def research_records(self, agent):
        """Metadata for the agent's immutable records, newest first."""
        if agent not in self.agent_ids:
            return []
        with self.lock:
            out = []
            for path in reversed(self._worklog_paths(agent)[-config.WORKLOG_INDEX_MAX:]):
                try:
                    with open(path, "rb") as handle:
                        record = json.loads(handle.read().decode("utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("record_id"), int):
                    continue
                out.append({"record_id": record["record_id"], "step": record.get("step"),
                            "hop": record.get("hop"),
                            "finish_reason": record.get("finish_reason"),
                            "bytes": self._safe_size(path)})
            return out

    def _safe_size(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def read_research_record(self, agent, record_id, offset=0, limit=None):
        """Return one UTF-8-safe page of an immutable private research record."""
        limit = config.WORKLOG_READ_MAX_BYTES if limit is None else limit
        if (agent not in self.agent_ids or isinstance(record_id, bool) or
                not isinstance(record_id, int) or record_id < 1 or
                isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or
                isinstance(limit, bool) or not isinstance(limit, int) or
                limit < 1 or limit > config.WORKLOG_READ_MAX_BYTES):
            return None, "invalid research record request"
        with self.lock:
            directory = self._worklog_dir(agent)
            target = os.path.join(directory, "%08d.json" % record_id) if directory else None
            if target is None or os.path.realpath(target) != target:
                return None, "unsafe research record path"
            try:
                with open(target, "rb") as handle:
                    data = handle.read()
            except OSError:
                return None, "no such research record"
        if offset > len(data):
            return None, "offset is beyond this research record"
        page = data[offset:offset + limit]
        next_offset = offset + len(page)
        return {"record_id": record_id, "offset": offset,
                "next_offset": next_offset if next_offset < len(data) else None,
                "bytes_total": len(data),
                "text": page.decode("utf-8", errors="replace")}, None

    def save_checkpoint(self, agent, fields, step):
        """Append an immutable, structured private checkpoint."""
        required = ("problem_id", "approach", "next_action")
        allowed = required + ("partial_result", "checked", "failed_branches", "question")
        if (agent not in self.agent_ids or isinstance(step, bool) or not isinstance(step, int) or
                not isinstance(fields, dict) or any(key not in allowed for key in fields) or
                any(not isinstance(fields.get(key), str) or not fields[key].strip()
                    for key in required)):
            return None, "invalid checkpoint"
        record = {key: value.strip() for key, value in fields.items()
                  if isinstance(value, str) and value.strip()}
        record["step"] = step
        with self.lock:
            paths = self._checkpoint_paths(agent)
            record_id = len(paths) + 1
            record["checkpoint_id"] = record_id
            try:
                data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8", errors="replace")
            except (TypeError, ValueError):
                return None, "could not encode checkpoint"
            if len(data) > config.CHECKPOINT_ENTRY_MAX_BYTES:
                return None, "checkpoint is too large"
            if self._checkpoint_total_bytes(agent) + len(data) > config.CHECKPOINT_AGENT_MAX_BYTES:
                return None, "checkpoint storage is full"
            directory = self._checkpoint_dir(agent)
            target = os.path.join(directory, "%08d.json" % record_id) if directory else None
            if target is None or os.path.islink(target) or os.path.islink(target + ".tmp"):
                return None, "unsafe checkpoint path"
            try:
                self._write_atomic(target, data)
            except OSError:
                return None, "could not save checkpoint"
        return {"checkpoint_id": record_id, "bytes": len(data), "step": step}, None

    def latest_checkpoint(self, agent):
        if agent not in self.agent_ids:
            return None
        with self.lock:
            paths = self._checkpoint_paths(agent)
            if not paths:
                return None
            try:
                with open(paths[-1], "rb") as handle:
                    record = json.loads(handle.read().decode("utf-8"))
            except (OSError, ValueError, TypeError):
                return None
        return record if isinstance(record, dict) else None

    def index(self, agent):
        """-> [{path, bytes, step}], the journal first, then wiki pages sorted.

        `step` is the step this process last wrote the file on, or None for a
        file that was already on disk when the run started.
        """
        with self.lock:
            out = []
            journal_bytes = self._size(agent, config.MEMORY_JOURNAL)
            if journal_bytes:
                out.append({"path": config.MEMORY_JOURNAL, "bytes": journal_bytes,
                            "step": self._written.get((agent, config.MEMORY_JOURNAL))})
            for name in self._pages(agent):
                path = "wiki/" + name
                out.append({"path": path, "bytes": self._size(agent, path),
                            "step": self._written.get((agent, path))})
            return out

    def read(self, agent, path):
        """-> (text, None) or (None, reason). Never raises."""
        ok, why = validate_path(path, for_write=False)
        if ok is None:
            return None, why
        with self.lock:
            target = self._resolve(agent, path)
            if target is None:
                return None, "path escapes your memory directory"
            try:
                with open(target, "rb") as handle:
                    return handle.read().decode("utf-8", errors="replace"), None
            except OSError:
                return None, "no such file: %s" % path

    def write(self, agent, path, text, step):
        """Create or overwrite a wiki page. -> (bytes_written, None) or (None, reason).

        Every cap is checked before anything is written, so a rejected write
        leaves the previous contents exactly as they were.
        """
        ok, why = validate_path(path, for_write=True)
        if ok is None:
            return None, why
        data = _encode(text)
        if data is None:
            return None, "text must be a string"
        if len(data) > config.MEMORY_FILE_MAX_BYTES:
            return None, ("a page holds at most %d bytes; that one is %d"
                          % (config.MEMORY_FILE_MAX_BYTES, len(data)))
        with self.lock:
            target = self._resolve(agent, path)
            if target is None:
                return None, "path escapes your memory directory"
            existing = self._size(agent, path)
            if not existing and len(self._pages(agent)) >= config.MEMORY_MAX_WIKI_PAGES:
                return None, ("you already have %d wiki pages, the maximum; overwrite "
                              "one instead" % config.MEMORY_MAX_WIKI_PAGES)
            after = self._total_bytes(agent) - existing + len(data)
            if after > config.MEMORY_AGENT_MAX_BYTES:
                return None, ("your memory would exceed %d bytes in total"
                              % config.MEMORY_AGENT_MAX_BYTES)
            try:
                self._write_atomic(target, data)
            except OSError as exc:
                return None, "could not write memory page"
            self._written[(agent, path)] = step
            return len(data), None

    def append_journal(self, agent, text, step):
        """Append one stamped entry. -> (total_bytes, None) or (None, reason)."""
        data = _encode(text)
        if data is None:
            return None, "text must be a string"
        stripped = (text or "").strip()
        if not stripped:
            return None, "an empty entry is not worth a tool call"
        entry = _encode(_ENTRY % (step, stripped))
        if len(entry) > config.MEMORY_JOURNAL_ENTRY_MAX_BYTES:
            return None, ("one entry holds at most %d bytes; that one is %d"
                          % (config.MEMORY_JOURNAL_ENTRY_MAX_BYTES, len(entry)))
        with self.lock:
            target = self._resolve(agent, config.MEMORY_JOURNAL)
            if target is None:
                return None, "path escapes your memory directory"
            existing = self._size(agent, config.MEMORY_JOURNAL)
            head = b"" if existing else _encode(_JOURNAL_HEADER % agent)
            after = existing + len(head) + len(entry)
            if after > config.MEMORY_JOURNAL_MAX_BYTES:
                return None, ("your journal is full at %d bytes; put new material in a "
                              "wiki page" % config.MEMORY_JOURNAL_MAX_BYTES)
            if (self._total_bytes(agent) + len(head) + len(entry)
                    > config.MEMORY_AGENT_MAX_BYTES):
                return None, ("your memory would exceed %d bytes in total"
                              % config.MEMORY_AGENT_MAX_BYTES)
            try:
                with open(target, "ab") as handle:
                    handle.write(head + entry)
            except OSError as exc:
                return None, "could not write the journal"
            self._written[(agent, config.MEMORY_JOURNAL)] = step
            return after, None

    def journal_tail(self, agent, max_bytes=None):
        """The last max_bytes of the journal, cut at an entry boundary.

        This is the one piece of memory shown without being asked for. Cutting on
        an entry boundary matters: half an entry reads as a complete thought that
        happens to be wrong.
        """
        max_bytes = config.MEMORY_JOURNAL_TAIL_BYTES if max_bytes is None else max_bytes
        with self.lock:
            target = self._resolve(agent, config.MEMORY_JOURNAL)
            if target is None:
                return ""
            try:
                with open(target, "rb") as handle:
                    data = handle.read()
            except OSError:
                return ""
        if len(data) <= max_bytes:
            return data.decode("utf-8", errors="replace")
        tail = data[-max_bytes:]
        boundary = tail.find(b"\n## step ")
        omitted = len(data) - len(tail)
        if boundary != -1:
            omitted += boundary
            tail = tail[boundary + 1:]
        return (_TRUNCATED % omitted) + tail.decode("utf-8", errors="replace")


# --------------------------------------------------------------- self-test
if __name__ == "__main__":
    import shutil
    import tempfile

    passed = failed = 0

    def check(name, condition):
        global passed, failed
        if condition:
            passed += 1
            print("PASS  " + name)
        else:
            failed += 1
            print("FAIL  " + name)

    os.makedirs(config.RUN_DIR, exist_ok=True)
    root = tempfile.mkdtemp(dir=config.RUN_DIR)
    store = MemoryStore(root, ["agent-01", "agent-02"])

    # ---- the path whitelist
    for bad in ("../x.md", "/tmp/x.md", "wiki/../RESEARCH.md", "wiki/A.md", "wiki/a.txt",
                "wiki/" + "a" * 41 + ".md", "wiki/a b.md", "wiki\\a.md", "wiki/", "",
                "x.md", None, 7, "wiki/sub/a.md"):
        ok, why = validate_path(bad, for_write=True)
        check("write path rejected: %r" % (bad,), ok is None and isinstance(why, str))
    check("a well-formed wiki page is accepted",
          validate_path("wiki/plan_v2-final.md", for_write=True)[0] == "wiki/plan_v2-final.md")
    check("the journal is readable", validate_path("RESEARCH.md", for_write=False)[0])
    check("the journal is never writable", validate_path("RESEARCH.md", for_write=True)[0] is None)

    # ---- ordinary use
    check("an empty store lists nothing", store.index("agent-01") == [])
    written, why = store.write("agent-01", "wiki/plan.md", "range a01-0000..a01-0999", 1)
    check("a page is written", written == 24 and why is None)
    check("the page reads back",
          store.read("agent-01", "wiki/plan.md")[0] == "range a01-0000..a01-0999")
    total, why = store.append_journal("agent-01", "taking the 10-bit set", 1)
    check("a journal entry is appended", total and why is None)
    store.append_journal("agent-01", "no hit in 200 candidates", 2)
    body = store.read("agent-01", "RESEARCH.md")[0]
    check("the journal is stamped with the step", "## step 1" in body and "## step 2" in body)
    check("the journal keeps earlier entries", "taking the 10-bit set" in body)
    index = store.index("agent-01")
    check("the index lists the journal first", index[0]["path"] == "RESEARCH.md")
    check("the index carries sizes and steps",
          index[1]["path"] == "wiki/plan.md" and index[1]["bytes"] == 24
          and index[1]["step"] == 1)
    check("memory is private to one agent", store.index("agent-02") == [])
    check("reading another agent's page is impossible through this API",
          store.read("agent-02", "wiki/plan.md")[0] is None)

    # ---- containment, checked on disk rather than argued
    on_disk = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            on_disk.append(os.path.relpath(os.path.join(base, name), root))
    check("nothing was written outside the agent's own directory",
          all(path.startswith("agent-01" + os.sep) for path in on_disk))

    # ---- caps
    ok, why = store.write("agent-01", "wiki/plan.md", "x" * (config.MEMORY_FILE_MAX_BYTES + 1), 3)
    check("an oversized page is refused", ok is None and "at most" in why)
    check("and the previous contents survive",
          store.read("agent-01", "wiki/plan.md")[0] == "range a01-0000..a01-0999")
    ok, why = store.append_journal("agent-01", "y" * config.MEMORY_JOURNAL_ENTRY_MAX_BYTES, 3)
    check("an oversized journal entry is refused", ok is None and "one entry" in why)
    check("an empty journal entry is refused",
          store.append_journal("agent-01", "   ", 3)[0] is None)
    check("a non-string is refused", store.write("agent-01", "wiki/a.md", 7, 3)[0] is None)
    check("a lone surrogate is encoded rather than crashing",
          store.write("agent-01", "wiki/surrogate.md", "a\ud800b", 3)[0] is not None)

    for i in range(config.MEMORY_MAX_WIKI_PAGES + 4):
        store.write("agent-02", "wiki/p%d.md" % i, "page %d" % i, 1)
    check("the page count is capped",
          len(store._pages("agent-02")) == config.MEMORY_MAX_WIKI_PAGES)
    check("an existing page can still be overwritten at the page cap",
          store.write("agent-02", "wiki/p0.md", "rewritten", 2)[0] == 9)

    big_root = tempfile.mkdtemp(dir=config.RUN_DIR)
    big = MemoryStore(big_root, ["a"])
    for i in range(20):
        big.write("a", "wiki/p%d.md" % i, "z" * config.MEMORY_FILE_MAX_BYTES, 1)
    check("the per-agent total is capped",
          big._total_bytes("a") <= config.MEMORY_AGENT_MAX_BYTES)

    # ---- the journal tail
    check("the tail of an empty journal is empty", store.journal_tail("agent-02") == "")
    tail = store.journal_tail("agent-01")
    check("a short journal is shown whole", "taking the 10-bit set" in tail
          and "earlier bytes are not shown" not in tail)
    long_root = tempfile.mkdtemp(dir=config.RUN_DIR)
    long_store = MemoryStore(long_root, ["a"])
    for i in range(1, 40):
        long_store.append_journal("a", "entry number %d %s" % (i, "w" * 60), i)
    tail = long_store.journal_tail("a")
    check("a long journal is cut to the cap",
          len(tail.encode("utf-8")) <= config.MEMORY_JOURNAL_TAIL_BYTES + len(_TRUNCATED % 0) + 8)
    check("the cut says how much is hidden", "earlier bytes are not shown" in tail)
    check("the cut lands on an entry boundary",
          tail.split("]\n", 1)[1].startswith("## step "))
    check("the newest entry survives the cut", "entry number 39" in tail)
    check("the journal itself was not truncated by reading its tail",
          "entry number 1 " in long_store.read("a", "RESEARCH.md")[0])

    for path in (root, big_root, long_root):
        shutil.rmtree(path, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, failed))
    raise SystemExit(1 if failed else 0)
