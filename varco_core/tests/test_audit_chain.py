"""
tests.test_audit_chain
========================
Plan 009, Phase 12 (R8) — audit tamper-evidence (hash chaining).

RED until ``AuditEntry.prev_hash``/``seq``/``entry_hash()`` and
``AuditRepository.verify_chain()`` (portable default, ``@staticmethod``) land.

Pure unit tests — no DB required.
"""

from __future__ import annotations

from varco_core.service.audit import AuditEntry, AuditRepository


def _entry(**kwargs) -> AuditEntry:
    defaults = dict(entity_type="Order", entity_id="1", action="create")
    defaults.update(kwargs)
    return AuditEntry(**defaults)


class TestAuditEntryHashFields:
    def test_prev_hash_defaults_to_none(self) -> None:
        entry = _entry()
        assert entry.prev_hash is None

    def test_seq_defaults_to_none(self) -> None:
        entry = _entry()
        assert entry.seq is None

    def test_entry_hash_is_deterministic(self) -> None:
        entry = _entry(seq=1, prev_hash=None)
        assert entry.entry_hash() == entry.entry_hash()

    def test_entry_hash_changes_when_diff_changes(self) -> None:
        entry_a = _entry(seq=1, prev_hash=None, diff={"total": 1})
        entry_b = _entry(
            seq=1, prev_hash=None, diff={"total": 2}, entry_id=entry_a.entry_id
        )
        assert entry_a.entry_hash() != entry_b.entry_hash()

    def test_genesis_entry_hashes_prev_hash_as_json_null(self) -> None:
        """Genesis entry: prev_hash=None, hashed as the JSON literal null."""
        entry = _entry(seq=1, prev_hash=None)
        # Sanity: changing prev_hash to a non-null value changes the hash.
        chained = _entry(
            seq=1, prev_hash="deadbeef", entry_id=entry.entry_id, diff=entry.diff
        )
        assert entry.entry_hash() != chained.entry_hash()


class TestVerifyChainPositive:
    def test_empty_chain_is_vacuously_true(self) -> None:
        assert AuditRepository.verify_chain([]) is True

    def test_unbroken_chain_verifies(self) -> None:
        e1 = _entry(seq=1, prev_hash=None)
        h1 = e1.entry_hash()
        e2 = _entry(seq=2, prev_hash=h1)
        h2 = e2.entry_hash()
        e3 = _entry(seq=3, prev_hash=h2)

        assert AuditRepository.verify_chain([e1, e2, e3]) is True


class TestVerifyChainNegative:
    def test_hash_mismatch_detected(self) -> None:
        e1 = _entry(seq=1, prev_hash=None)
        # e2's prev_hash does not match e1's actual hash -- tampering.
        e2 = _entry(seq=2, prev_hash="not-the-real-hash")

        result = AuditRepository.verify_chain([e1, e2])
        assert result is not True

    def test_chain_gap_is_a_distinct_finding_from_hash_mismatch(self) -> None:
        """A gap in seq (a deleted row) must be reported as a specific
        ChainGap finding, distinct from HashMismatch (an edited row)."""
        from varco_core.service.audit import ChainGap

        e1 = _entry(seq=1, prev_hash=None)
        h1 = e1.entry_hash()
        e3 = _entry(seq=3, prev_hash=h1)  # seq=2 missing

        result = AuditRepository.verify_chain([e1, e3])
        assert result is not True
        assert any(isinstance(f, ChainGap) for f in result) or isinstance(
            result, ChainGap
        )
